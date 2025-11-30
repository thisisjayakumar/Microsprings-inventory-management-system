from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Prefetch, Sum
from django.contrib.auth import get_user_model
from django.utils import timezone
import logging

from .permissions import IsManager, IsManagerOrSupervisor, IsManagerOrRMStore

logger = logging.getLogger(__name__)

from .models import (
    ManufacturingOrder, PurchaseOrder, MOStatusHistory, POStatusHistory,
    MOTransactionHistory, POTransactionHistory,
    MOProcessExecution, MOProcessStepExecution, MOProcessAlert, Batch,
    OutsourcingRequest, OutsourcedItem, MOApprovalWorkflow, ProcessAssignment,
    BatchAllocation, ProcessExecutionLog, FinishedGoodsVerification,
    RawMaterialAllocation, RMAllocationHistory
)
from .serializers import (
    ManufacturingOrderListSerializer, ManufacturingOrderDetailSerializer,
    PurchaseOrderListSerializer, PurchaseOrderDetailSerializer,
    ProductDropdownSerializer, RawMaterialDropdownSerializer,
    VendorDropdownSerializer, UserDropdownSerializer,
    ProductBasicSerializer, RawMaterialBasicSerializer, VendorBasicSerializer,
    ManufacturingOrderWithProcessesSerializer, MOProcessExecutionListSerializer,
    MOProcessExecutionDetailSerializer, MOProcessStepExecutionSerializer,
    MOProcessAlertSerializer, MOProcessAlertMinimalSerializer, BatchListSerializer, BatchDetailSerializer,
    OutsourcingRequestListSerializer, OutsourcingRequestDetailSerializer,
    OutsourcingRequestSendSerializer, OutsourcingRequestReturnSerializer,
    RawMaterialAllocationSerializer, RawMaterialAllocationMinimalSerializer, RMAllocationHistorySerializer,
    RMAllocationSwapSerializer, RMAllocationCheckSerializer
)
from products.models import Product
from inventory.models import RawMaterial, RMStockBalance, GRMReceipt, HeatNumber
from inventory.transaction_manager import InventoryTransactionManager
from third_party.models import Vendor
from processes.models import Process
from .services.rm_calculator import RMCalculator
from decimal import Decimal

User = get_user_model()


class ManufacturingOrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Manufacturing Orders with optimized queries and filtering
    Only managers can create/edit MOs, supervisors can view and change status
    """
    permission_classes = [IsManagerOrSupervisor]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'priority', 'shift', 'material_type']
    search_fields = ['mo_id', 'product_code__product_code', 'product_code__spring_type', 'material_name', 'grade', 'customer_name', 'special_instructions']
    ordering_fields = ['created_at', 'planned_start_date', 'delivery_date', 'mo_id']
    ordering = ['-created_at']

    def get_queryset(self):
        """Optimized queryset with select_related and prefetch_related"""
        queryset = ManufacturingOrder.objects.select_related(
            'product_code', 'product_code__customer_c_id', 'customer_c_id', 'created_by', 
            'gm_approved_by', 'rm_allocated_by'
        ).prefetch_related(
            Prefetch('status_history', queryset=MOStatusHistory.objects.select_related('changed_by'))
        )
        
        # Filter by date range if provided
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
        
        # Filter based on user role and department
        user = self.request.user
        user_roles = user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        
        # Admin, manager, production_head can see all MOs
        if any(role in ['admin', 'manager', 'production_head'] for role in user_roles):
            return queryset
        
        # Supervisors can only see MOs with processes in their department
        if 'supervisor' in user_roles:
            try:
                user_profile = user.userprofile
                from authentication.models import ProcessSupervisor
                
                # Get processes this supervisor can handle
                process_supervisor = ProcessSupervisor.objects.filter(
                    supervisor=user,
                    department=user_profile.department,
                    is_active=True
                ).first()
                
                if process_supervisor:
                    # Filter to only show MOs with processes this supervisor handles
                    queryset = queryset.filter(
                        Q(process_executions__assigned_supervisor=user) | 
                        Q(process_executions__process__name__in=process_supervisor.process_names)
                    ).distinct()
                else:
                    # If no process supervisor record, only show MOs with assigned process executions
                    queryset = queryset.filter(process_executions__assigned_supervisor=user).distinct()
                    
            except Exception as e:
                # If there's an error, only show MOs with assigned process executions
                queryset = queryset.filter(process_executions__assigned_supervisor=user).distinct()
        
        # RM Store and FG Store users can only see MOs assigned to them
        elif any(role in ['rm_store', 'fg_store'] for role in user_roles):
            queryset = queryset.filter(assigned_rm_store=user)
        
        # Other users see no MOs
        else:
            queryset = queryset.none()
            
        return queryset

    def get_serializer_class(self):
        """Use different serializers for list and detail views"""
        if self.action == 'list':
            return ManufacturingOrderListSerializer
        return ManufacturingOrderDetailSerializer

    @action(detail=True, methods=['post'])
    def change_status(self, request, pk=None):
        """Change MO status with validation"""
        mo = self.get_object()
        new_status = request.data.get('status')
        notes = request.data.get('notes', '')
        
        if not new_status:
            return Response({'error': 'Status is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate status transition
        from utils.enums import MOStatusChoices
        valid_statuses = [choice[0] for choice in MOStatusChoices.choices]
        if new_status not in valid_statuses:
            return Response({'error': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
        
        old_status = mo.status
        mo.status = new_status
        
        # Update workflow timestamps based on status
        if new_status == 'submitted':
            mo.submitted_at = timezone.now()
        elif new_status == 'gm_approved':
            mo.gm_approved_at = timezone.now()
            mo.gm_approved_by = request.user
        elif new_status == 'rm_allocated':
            mo.rm_allocated_at = timezone.now()
            mo.rm_allocated_by = request.user
        elif new_status == 'in_progress':
            if not mo.actual_start_date:
                mo.actual_start_date = timezone.now()
            # Ensure RM is reserved when status changes to in_progress (no locking)
            try:
                from manufacturing.services.rm_allocation import RMAllocationService
                from manufacturing.models import RawMaterialAllocation
                from inventory.models import RMStockBalance
                
                existing_allocations = RawMaterialAllocation.objects.filter(mo=mo)
                logger.info(f"[DEBUG] change_status to in_progress - MO {mo.mo_id} - Existing allocations: {existing_allocations.count()}")
                for alloc in existing_allocations:
                    logger.info(f"[DEBUG]   - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg}kg")
                
                if not existing_allocations.exists():
                    logger.info(f"[DEBUG] change_status to in_progress - MO {mo.mo_id} - Creating RM reservations...")
                    try:
                        result = RMAllocationService.allocate_rm_for_mo(mo, request.user)
                        logger.info(f"[DEBUG] change_status to in_progress - MO {mo.mo_id} - Reservations created: {len(result) if result else 0}")
                        if result:
                            for alloc in result:
                                logger.info(f"[DEBUG]   - Created Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg}kg")
                            # Decrement RMStockBalance available_quantity for reserved qty (once per MO)
                            try:
                                raw_material = mo.product_code.material
                                if raw_material:
                                    total_reserved_kg = sum(a.allocated_quantity_kg for a in result)
                                    stock_balance, _ = RMStockBalance.objects.get_or_create(
                                        raw_material=raw_material,
                                        defaults={'available_quantity': Decimal('0')}
                                    )
                                    stock_balance.available_quantity -= Decimal(str(total_reserved_kg))
                                    stock_balance.save()
                                    logger.info(f"[DEBUG] change_status to in_progress - Deducted {total_reserved_kg}kg from RMStockBalance for material {raw_material.material_code}")
                            except Exception as sb_err:
                                logger.warning(f"[DEBUG] change_status to in_progress - Failed to decrement RMStockBalance: {sb_err}")
                    except Exception as alloc_error:
                        logger.error(f"[DEBUG] change_status to in_progress - MO {mo.mo_id} - Failed to create RM reservations: {str(alloc_error)}")
                        logger.exception(alloc_error)
                        
            except Exception as e:
                logger.error(f"[DEBUG] change_status to in_progress - Failed to ensure RM reservation for MO {mo.mo_id}: {str(e)}")
                logger.exception(e)
        elif new_status == 'completed' and not mo.actual_end_date:
            mo.actual_end_date = timezone.now()
        
        mo.save()
        
        # Create status history
        MOStatusHistory.objects.create(
            mo=mo,
            from_status=old_status,
            to_status=new_status,
            changed_by=request.user,
            notes=notes
        )
        
        serializer = self.get_serializer(mo)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='complete-rm-allocation', permission_classes=[IsAuthenticated])
    def complete_rm_allocation(self, request, pk=None):
        """
        Complete RM allocation (RM Store only) - changes status to rm_allocated
        """
        mo = self.get_object()
        
        # Check if user is RM Store
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'rm_store' not in user_roles:
            return Response(
                {'error': 'Only RM Store users can complete RM allocation'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate MO is assigned to this RM Store user
        if mo.assigned_rm_store != request.user:
            return Response(
                {'error': 'This MO is not assigned to you'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate MO status
        if mo.status not in ['on_hold', 'in_progress']:
            return Response(
                {'error': f'Cannot complete allocation for MO in {mo.status} status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Handle status change based on current status
        old_status = mo.status
        
        # Only change status to rm_allocated if MO is not already in progress
        if mo.status != 'in_progress':
            mo.status = 'rm_allocated'
            mo.rm_allocated_at = timezone.now()
            mo.rm_allocated_by = request.user
            mo.save()
            
            # Create status history
            MOStatusHistory.objects.create(
                mo=mo,
                from_status=old_status,
                to_status='rm_allocated',
                changed_by=request.user,
                notes=request.data.get('notes', 'All RM allocated to batches by RM Store')
            )
        else:
            # For in-progress MOs, just update the allocation fields without changing status
            mo.rm_allocated_at = timezone.now()
            mo.rm_allocated_by = request.user
            mo.save()
            
            # Create status history to track the RM allocation completion
            MOStatusHistory.objects.create(
                mo=mo,
                from_status=old_status,
                to_status=old_status,  # Status remains the same
                changed_by=request.user,
                notes=request.data.get('notes', 'RM allocation completed for in-progress MO - status unchanged')
            )
        
        serializer = self.get_serializer(mo)
        return Response({
            'message': f'RM allocation completed for MO {mo.mo_id}',
            'mo': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='send-remaining-to-scrap', permission_classes=[IsAuthenticated])
    def send_remaining_to_scrap(self, request, pk=None):
        """
        Send remaining RM to scrap for this MO
        Expected payload: { "scrap_rm_kg": 0.26 } or { "send_all_remaining": true }
        """
        mo = self.get_object()
        send_all = request.data.get('send_all_remaining', False)
        scrap_rm_kg = request.data.get('scrap_rm_kg')
        
        # Calculate remaining RM
        product = mo.product_code
        total_rm_required = None
        
        if product.material_type == 'coil' and product.grams_per_product:
            total_grams = mo.quantity * product.grams_per_product
            base_rm_kg = Decimal(str(total_grams / 1000))
            tolerance = mo.tolerance_percentage or Decimal('2.00')
            tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
            total_rm_required = float(base_rm_kg * tolerance_factor)
        
        if total_rm_required is None:
            return Response(
                {'error': 'Cannot calculate RM for this product type'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Calculate cumulative RM released from batches
        batches = Batch.objects.filter(mo=mo).exclude(status='cancelled')
        cumulative_rm_released = Decimal('0')
        
        for batch in batches:
            batch_quantity_grams = batch.planned_quantity
            batch_rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
            tolerance = mo.tolerance_percentage or Decimal('2.00')
            tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
            batch_rm = batch_rm_base_kg * tolerance_factor
            cumulative_rm_released += batch_rm
        
        remaining_rm_kg = float(Decimal(str(total_rm_required)) - cumulative_rm_released)
        
        if remaining_rm_kg <= 0:
            return Response(
                {'error': 'No remaining RM to send to scrap'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Determine scrap amount
        if send_all:
            scrap_kg = remaining_rm_kg
        elif scrap_rm_kg is not None:
            try:
                scrap_kg = float(scrap_rm_kg)
                if scrap_kg <= 0:
                    return Response(
                        {'error': 'scrap_rm_kg must be positive'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if scrap_kg > remaining_rm_kg:
                    return Response(
                        {'error': f'Scrap amount ({scrap_kg} kg) exceeds remaining RM ({remaining_rm_kg:.3f} kg)'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {'error': 'scrap_rm_kg must be a valid number'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {'error': 'Either scrap_rm_kg or send_all_remaining must be provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Add scrap to MO
        scrap_grams = int(scrap_kg * 1000)
        mo.scrap_rm_weight += scrap_grams
        mo.save()
        
        serializer = self.get_serializer(mo)
        return Response({
            'message': f'Sent {scrap_kg:.3f} kg of RM to scrap for MO {mo.mo_id}',
            'mo': serializer.data,
            'scrap_rm_kg': mo.scrap_rm_weight / 1000,
            'remaining_rm_after': max(0, remaining_rm_kg - scrap_kg)
        })

    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics for MOs - optimized to only return fields used in frontend"""
        queryset = self.get_queryset()
        
        # Optimized: Use single queryset evaluation with aggregations where possible
        # Only calculate what's actually displayed in the frontend DashboardStats component
        stats = {
            'total': queryset.count(),
            'in_progress': queryset.filter(status='in_progress').count(),
            'completed': queryset.filter(status='completed').count(),
            'overdue': queryset.filter(
                planned_end_date__lt=timezone.now(),
                status__in=['draft', 'approved', 'in_progress']
            ).count(),
            'by_priority': {
                'high': queryset.filter(priority='high').count(),
                'medium': queryset.filter(priority='medium').count(),
                'low': queryset.filter(priority='low').count(),
            }
        }
        
        return Response(stats)

    @action(detail=False, methods=['get'])
    def products(self, request):
        """Get products for dropdown"""
        products = Product.objects.all().order_by('product_code')
        
        # If no products in Product table, get unique products from BOM
        if not products.exists():
            from processes.models import BOM
            
            # Get unique product codes from BOM
            bom_products = BOM.objects.filter(is_active=True).values('product_code').distinct().order_by('product_code')
            
            # Create a list of simplified product dicts for the dropdown
            product_list = []
            for bom_product in bom_products:
                product_code = bom_product['product_code']
                product_list.append({
                    'id': product_code,  # Use product_code as ID temporarily
                    'product_code': product_code,
                })
            
            return Response(product_list)
        
        serializer = ProductDropdownSerializer(products, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def product_details(self, request):
        """Get complete product details with BOM and material info for MO creation"""
        product_code = request.query_params.get('product_code')
        if not product_code:
            return Response(
                {'error': 'product_code is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Try to get product details from Product table
            try:
                product = Product.objects.select_related('customer_c_id', 'material').get(product_code=product_code)
            except Product.DoesNotExist:
                # If product doesn't exist in Product table, create a minimal product object from BOM
                bom_item = BOM.objects.filter(product_code=product_code, is_active=True).first()
                if not bom_item:
                    return Response(
                        {'error': 'Product not found in BOM'}, 
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Create a minimal product-like object from BOM
                material = bom_item.material if bom_item.material else None
                
                product = type('Product', (), {
                    'id': product_code,
                    'product_code': product_code,
                    'product_type': bom_item.type,
                    'material': material,
                    'material_type': material.material_type if material else '',
                    'material_name': material.material_name if material else '',
                    'grade': material.grade if material else '',
                    'wire_diameter_mm': material.wire_diameter_mm if material else None,
                    'thickness_mm': material.thickness_mm if material else None,
                    'finishing': material.get_finishing_display() if material else '',
                    'weight_kg': material.weight_kg if material else None,
                    'material_type_display': material.get_material_type_display() if material else '',
                    'customer_c_id': None,  # No customer info available from BOM
                    'customer_name': None,
                    'customer_id': None,
                    'customer_industry': None,
                    'get_product_type_display': lambda: 'Spring' if bom_item.type == 'spring' else 'Stamping Part'
                })()
            
            # Get BOM data for this product
            from processes.models import BOM
            
            bom_items = BOM.objects.filter(
                product_code=product_code, 
                is_active=True
            ).select_related(
                'process_step__process', 
                'process_step__subprocess', 
                'material'
            ).order_by('process_step__sequence_order')
            
            # Serialize the data
            product_data = ProductBasicSerializer(product).data
            
            # Extract unique processes and materials (optimized)
            processes = []
            materials = []
            process_steps = []
            process_ids = set()
            material_ids = set()
            bom_dimensions = None  # Store BOM dimensions from first item
            
            for bom_item in bom_items:
                # Collect BOM dimensions from first item (they should be same for all)
                if not bom_dimensions:
                    bom_dimensions = {
                        'sheet_length': float(bom_item.sheet_length) if bom_item.sheet_length else None,
                        'sheet_breadth': float(bom_item.sheet_breadth) if bom_item.sheet_breadth else None,
                        'strip_length': float(bom_item.strip_length) if bom_item.strip_length else None,
                        'strip_breadth': float(bom_item.strip_breadth) if bom_item.strip_breadth else None,
                        'strip_count': bom_item.strip_count,
                        'pcs_per_strip': bom_item.pcs_per_strip,
                        'pcs_per_sheet': bom_item.pcs_per_sheet
                    }
                
                # Collect unique processes
                if bom_item.process_step.process.id not in process_ids:
                    processes.append({
                        'id': bom_item.process_step.process.id,
                        'name': bom_item.process_step.process.name,
                        'code': bom_item.process_step.process.code
                    })
                    process_ids.add(bom_item.process_step.process.id)
                
                # Collect unique materials with available_quantity
                if bom_item.material and bom_item.material.id not in material_ids:
                    material_data = RawMaterialBasicSerializer(bom_item.material).data
                    materials.append(material_data)
                    material_ids.add(bom_item.material.id)
                
                # Collect simplified process steps (without redundant material data)
                process_steps.append({
                    'process_step_name': bom_item.process_step.step_name,
                    'process_name': bom_item.process_step.process.name,
                    'sequence_order': bom_item.process_step.sequence_order,
                    'material_id': bom_item.material.id if bom_item.material else None,
                    'material_code': bom_item.material.material_code if bom_item.material else None
                })
            
            response_data = {
                'product': product_data,
                'process_steps': sorted(process_steps, key=lambda x: x['sequence_order']),
                'processes': processes,
                'materials': materials,
                'bom_dimensions': bom_dimensions  # Add BOM dimensions to response
            }
            
            return Response(response_data)
            
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['get'])
    def supervisors(self, request):
        """Get supervisors for dropdown"""
        # Filter by supervisor role
        supervisors = User.objects.filter(
            is_active=True,
            user_roles__role__name='supervisor'
        ).distinct().order_by('first_name', 'last_name')
        serializer = UserDropdownSerializer(supervisors, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def rm_store_users(self, request):
        """Get RM store users for dropdown"""
        # Filter by rm_store role
        rm_store_users = User.objects.filter(
            is_active=True,
            user_roles__role__name='rm_store'
        ).distinct().order_by('first_name', 'last_name')
        serializer = UserDropdownSerializer(rm_store_users, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def customers(self, request):
        """Get customers for dropdown"""
        from third_party.models import Customer
        from third_party.serializers import CustomerListSerializer
        
        customers = Customer.objects.filter(is_active=True).order_by('name')
        serializer = CustomerListSerializer(customers, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def calculate_rm_requirement(self, request):
        """
        Calculate RM requirement for a Manufacturing Order
        Supports both coil and sheet materials
        """
        try:
            product_code = request.data.get('product_code')
            quantity = request.data.get('quantity')
            tolerance_percentage = request.data.get('tolerance_percentage', 2.00)
            scrap_percentage = request.data.get('scrap_percentage')
            
            if not product_code or not quantity:
                return Response(
                    {'error': 'product_code and quantity are required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get product
            try:
                product = Product.objects.select_related('material').get(product_code=product_code)
            except Product.DoesNotExist:
                return Response(
                    {'error': 'Product not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            material = product.material
            if not material:
                return Response(
                    {'error': 'Product has no associated material'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get available stock
            try:
                stock_balance = RMStockBalance.objects.get(raw_material=material)
                available_quantity = stock_balance.available_quantity
            except RMStockBalance.DoesNotExist:
                available_quantity = Decimal('0')
            
            calculator = RMCalculator()
            
            # Calculate based on material type
            if material.material_type == 'coil':
                # For coil materials
                if not product.grams_per_product:
                    return Response(
                        {'error': 'Product must have grams_per_product defined for coil materials'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                calculation = calculator.calculate_rm_for_coil(
                    quantity=int(quantity),
                    grams_per_product=product.grams_per_product,
                    tolerance_percentage=Decimal(str(tolerance_percentage)),
                    scrap_percentage=Decimal(str(scrap_percentage)) if scrap_percentage else None
                )
                
                # Check availability
                availability = calculator.check_rm_availability(
                    required_amount=calculation['final_required_kg'],
                    available_amount=available_quantity,
                    material_type='coil'
                )
                
                return Response({
                    'material_type': 'coil',
                    'calculation': calculation,
                    'availability': availability,
                    'material_info': {
                        'material_code': material.material_code,
                        'material_name': material.material_name,
                        'grade': material.grade,
                        'wire_diameter_mm': str(material.wire_diameter_mm) if material.wire_diameter_mm else None,
                    }
                })
            
            elif material.material_type == 'sheet':
                # For sheet materials
                if not all([product.length_mm, product.breadth_mm, material.length_mm, material.breadth_mm]):
                    return Response(
                        {'error': 'Product and material must have length and breadth defined for sheet materials'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                calculation = calculator.calculate_rm_for_sheet(
                    quantity=int(quantity),
                    product_length_mm=product.length_mm,
                    product_breadth_mm=product.breadth_mm,
                    sheet_length_mm=material.length_mm,
                    sheet_breadth_mm=material.breadth_mm,
                    tolerance_percentage=Decimal(str(tolerance_percentage)),
                    scrap_percentage=Decimal(str(scrap_percentage)) if scrap_percentage else None
                )
                
                # Check availability (in sheets)
                availability = calculator.check_rm_availability(
                    required_amount=Decimal(str(calculation['final_required_sheets'])),
                    available_amount=available_quantity,
                    material_type='sheet'
                )
                
                return Response({
                    'material_type': 'sheet',
                    'calculation': calculation,
                    'availability': availability,
                    'material_info': {
                        'material_code': material.material_code,
                        'material_name': material.material_name,
                        'grade': material.grade,
                        'thickness_mm': str(material.thickness_mm) if material.thickness_mm else None,
                        'length_mm': str(material.length_mm) if material.length_mm else None,
                        'breadth_mm': str(material.breadth_mm) if material.breadth_mm else None,
                    }
                })
            
            else:
                return Response(
                    {'error': 'Invalid material type'},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        except ValueError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': f'Calculation error: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def process_tracking(self, request, pk=None):
        """Get MO with detailed process tracking information"""
        mo = self.get_object()
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def initialize_processes(self, request, pk=None):
        """Initialize process executions for an MO based on product BOM"""
        mo = self.get_object()
        
        # Allow initialization when status is mo_approved, rm_allocated, or in_progress
        if mo.status not in ['mo_approved', 'rm_allocated', 'in_progress']:
            return Response(
                {'error': f'MO must be in mo_approved, rm_allocated, or in_progress status to initialize processes. Current status: {mo.status}'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get processes from BOM for this product
        from processes.models import BOM
        bom_items = BOM.objects.filter(
            product_code=mo.product_code.product_code,
            is_active=True
        ).select_related('process_step__process')
        
        if not bom_items.exists():
            return Response(
                {'error': 'No processes found for this product'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get unique processes (avoid duplicates)
        unique_processes = {}
        for bom_item in bom_items:
            process = bom_item.process_step.process
            if process.id not in unique_processes:
                unique_processes[process.id] = process
        
        # Create process executions
        sequence = 1
        created_executions = []
        for process in unique_processes.values():
            
            # Check if execution already exists
            execution, created = MOProcessExecution.objects.get_or_create(
                mo=mo,
                process=process,
                defaults={
                    'sequence_order': sequence,
                    'status': 'pending'
                }
            )
            
            if created:
                sequence += 1
                created_executions.append(execution)
        
        # Auto-assign supervisors to newly created process executions
        from notifications.models import WorkflowNotification
        for execution in created_executions:
            try:
                execution.auto_assign_supervisor()
                logger.info(
                    f"Auto-assigned supervisor for {mo.mo_id} - {execution.process.name}: "
                    f"{execution.assigned_supervisor.get_full_name() if execution.assigned_supervisor else 'None'}"
                )
                
                # Send notification to the assigned supervisor
                if execution.assigned_supervisor:
                    WorkflowNotification.objects.create(
                        notification_type='supervisor_assigned',
                        title=f'Process Assigned: {execution.process.name}',
                        message=f'You have been automatically assigned as supervisor for process "{execution.process.name}" for MO {mo.mo_id}.',
                        recipient=execution.assigned_supervisor,
                        related_mo=mo,
                        action_required=True,
                        created_by=request.user
                    )
                    logger.info(
                        f"Sent notification to {execution.assigned_supervisor.get_full_name()} "
                        f"for {mo.mo_id} - {execution.process.name}"
                    )
            except Exception as e:
                logger.error(
                    f"Error auto-assigning supervisor for {mo.mo_id} - {execution.process.name}: {str(e)}"
                )
        
        # Update MO status and actual start date if not already in_progress
        # NOTE: Do NOT allocate RM here - RM will be reserved when production actually starts
        # via the start_production action (for production heads) or change_status to in_progress
        if mo.status in ['mo_approved', 'rm_allocated']:
            logger.info(f"[DEBUG] initialize_processes - MO {mo.mo_id} - Initializing processes. RM will be reserved when production starts.")
            mo.status = 'in_progress'
            mo.actual_start_date = timezone.now()
            mo.save()
        
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        response_data = serializer.data
        return Response(response_data)

    @action(detail=True, methods=['patch'])
    def update_mo_details(self, request, pk=None):
        """Unified endpoint for all MO operations - updates, approvals, status changes"""
        mo = self.get_object()
        
        # Get action type from request data
        action = request.data.get('action', 'update')
        
        # Handle different actions
        if action == 'approve':
            return self._handle_approve_mo(mo, request)
        elif action == 'start_production':
            return self._handle_start_production(mo, request)
        elif action == 'reject':
            return self._handle_reject_mo(mo, request)
        else:
            return self._handle_update_details(mo, request)
    
    def _handle_approve_mo(self, mo, request):
        """Handle MO approval by manager (on_hold → mo_approved) - ONLY STATUS CHANGE, NO RM OPERATIONS"""
        # Check if user is manager or production_head
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if not any(role in ['manager', 'production_head'] for role in user_roles):
            return Response(
                {'error': 'Only managers or production heads can approve MOs'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check current status
        if mo.status != 'on_hold':
            return Response(
                {'error': f'Cannot approve MO in {mo.status} status. MO must be in On Hold status.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update status ONLY - NO RM operations
        old_status = mo.status
        mo.status = 'mo_approved'
        mo.gm_approved_at = timezone.now()
        mo.gm_approved_by = request.user
        mo.save()
        logger.info(f"[DEBUG] MO {mo.mo_id} approved. Status: {old_status} → mo_approved. RM will be reserved when production starts.")
        
        # Create status history
        MOStatusHistory.objects.create(
            mo=mo,
            from_status=old_status,
            to_status='mo_approved',
            changed_by=request.user,
            notes=request.data.get('notes', 'MO approved by manager')
        )
        
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        return Response({
            'message': 'MO approved successfully! RM will be reserved when production starts.',
            'mo': serializer.data
        })
    
    def _handle_start_production(self, mo, request):
        """Handle production start (mo_approved → in_progress) - Production Head only"""
        # Check if user is production_head only
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'production_head' not in user_roles:
            return Response(
                {'error': 'Only production heads can start production'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check current status
        if mo.status != 'mo_approved':
            return Response(
                {'error': f'Cannot start production for MO in {mo.status} status. MO must be approved first.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # DEBUG: Check existing RM allocations
        from manufacturing.models import RawMaterialAllocation
        existing_allocations = RawMaterialAllocation.objects.filter(mo=mo)
        logger.info(f"[DEBUG] MO {mo.mo_id} - Existing RM allocations before production start: {existing_allocations.count()}")
        for alloc in existing_allocations:
            logger.info(f"[DEBUG]   - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg}kg, Material: {alloc.raw_material.material_code}")
        
        # Ensure RM is reserved (allocate if not exists or if needed)
        reservation_result = None
        try:
            from manufacturing.services.rm_allocation import RMAllocationService
            from inventory.models import RMStockBalance
            
            # Check if allocations exist
            if not existing_allocations.exists():
                logger.info(f"[DEBUG] MO {mo.mo_id} - No RM allocations found, creating new RESERVED allocations...")
                try:
                    reservation_result = RMAllocationService.allocate_rm_for_mo(mo, request.user)
                    logger.info(f"[DEBUG] MO {mo.mo_id} - RM allocated as RESERVED: {len(reservation_result) if reservation_result else 0}")
                    if reservation_result:
                        for alloc in reservation_result:
                            logger.info(f"[DEBUG]   - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg}kg")
                        # Decrement RMStockBalance available_quantity for reserved qty (once per MO)
                        try:
                            raw_material = mo.product_code.material
                            if raw_material:
                                total_reserved_kg = sum(a.allocated_quantity_kg for a in reservation_result)
                                stock_balance, _ = RMStockBalance.objects.get_or_create(
                                    raw_material=raw_material,
                                    defaults={'available_quantity': Decimal('0')}
                                )
                                stock_balance.available_quantity -= Decimal(str(total_reserved_kg))
                                stock_balance.save()
                                logger.info(f"[DEBUG] start_production - Deducted {total_reserved_kg}kg from RMStockBalance for material {raw_material.material_code}")
                        except Exception as sb_err:
                            logger.warning(f"[DEBUG] start_production - Failed to decrement RMStockBalance: {sb_err}")
                except Exception as alloc_error:
                    logger.error(f"[DEBUG] MO {mo.mo_id} - Failed to create RM allocations: {str(alloc_error)}")
                    logger.exception(alloc_error)
            else:
                # Check if we need to reserve additional RM
                total_reserved = sum(float(a.allocated_quantity_kg) for a in existing_allocations.filter(status='reserved'))
                total_locked = sum(float(a.allocated_quantity_kg) for a in existing_allocations.filter(status='locked'))
                required_qty = float(mo.rm_required_kg) if mo.rm_required_kg else 0
                logger.info(f"[DEBUG] MO {mo.mo_id} - Existing: Reserved {total_reserved}kg, Locked {total_locked}kg, Required {required_qty}kg")
                
                if (total_reserved + total_locked) < required_qty:
                    logger.info(f"[DEBUG] MO {mo.mo_id} - Partial allocation detected, creating additional RESERVED allocations...")
                    try:
                        reservation_result = RMAllocationService.allocate_rm_for_mo(mo, request.user)
                        if reservation_result:
                            logger.info(f"[DEBUG] MO {mo.mo_id} - Additional RESERVED allocations created: {len(reservation_result)}")
                    except Exception as alloc_error:
                        logger.warning(f"[DEBUG] MO {mo.mo_id} - Could not allocate additional RM: {str(alloc_error)}")
                        logger.exception(alloc_error)
                else:
                    logger.info(f"[DEBUG] MO {mo.mo_id} - RM fully allocated. Batch starts will lock per-batch quantities.")
            
            # Final check after reservation
            final_allocations = RawMaterialAllocation.objects.filter(mo=mo, status='reserved')
            total_final_reserved = sum(float(a.allocated_quantity_kg) for a in final_allocations)
            logger.info(f"[DEBUG] MO {mo.mo_id} - Final RESERVED RM: {total_final_reserved}kg (Locking will happen per batch)")
            
            # If still no allocations, this is a problem
            if total_final_reserved == 0 and mo.rm_required_kg and mo.rm_required_kg > 0:
                logger.error(f"[DEBUG] MO {mo.mo_id} - WARNING: No RM reserved but required {mo.rm_required_kg}kg!")
            
        except Exception as e:
            logger.error(f"[DEBUG] Failed to ensure RM reservation for MO {mo.mo_id}: {str(e)}")
            logger.exception(e)
        
        # Update status
        old_status = mo.status
        mo.status = 'in_progress'
        mo.actual_start_date = timezone.now()
        mo.save()
        
        # Create status history
        MOStatusHistory.objects.create(
            mo=mo,
            from_status=old_status,
            to_status='in_progress',
            changed_by=request.user,
            notes=request.data.get('notes', 'Production started by production head')
        )
        
        # Create notification for RM Store users
        try:
            # Create workflow notifications for RM Store users
            from notifications.models import WorkflowNotification
            from django.contrib.auth.models import Group
            
            # Get all RM Store users
            rm_store_users = User.objects.filter(user_roles__role__name='rm_store', user_roles__is_active=True)
            
            # Create notifications for each RM Store user
            for rm_user in rm_store_users:
                WorkflowNotification.objects.create(
                    notification_type='rm_allocation_required',
                    title=f'RM Allocation Required: {mo.mo_id}',
                    message=f'MO {mo.mo_id} has started production and requires RM allocation.',
                    recipient=rm_user,
                    related_mo=mo,
                    action_required=True,
                    priority='high',
                    created_by=request.user
                )
        except Exception as e:
            # Don't fail the main operation if notification fails
            logger.warning(f"Failed to create RM Store notifications: {e}")
        
        # Get final allocation status
        final_check = RawMaterialAllocation.objects.filter(mo=mo)
        reserved_count = final_check.filter(status='reserved').count()
        reserved_total_kg = sum(float(a.allocated_quantity_kg) for a in final_check.filter(status='reserved'))
        
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        response_data = {
            'message': 'Production started successfully! RM is reserved.',
            'mo': serializer.data,
            'rm_reservation_status': {
                'reserved_count': reserved_count,
                'reserved_kg': reserved_total_kg,
                'required_kg': float(mo.rm_required_kg) if mo.rm_required_kg else 0,
                'is_fully_reserved': reserved_total_kg >= (float(mo.rm_required_kg) if mo.rm_required_kg else 0)
            }
        }
        
        return Response(response_data)
    
    def _handle_reject_mo(self, mo, request):
        """Handle MO rejection by manager (any status → rejected)"""
        # Check if user is manager or production_head
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if not any(role in ['manager', 'production_head'] for role in user_roles):
            return Response(
                {'error': 'Only managers or production heads can reject MOs'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if MO is already rejected
        if mo.status == 'rejected':
            return Response(
                {'error': 'MO is already rejected'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get rejection reason
        rejection_reason = request.data.get('notes', 'MO rejected by manager')
        
        # Update status
        old_status = mo.status
        mo.status = 'rejected'
        mo.rejected_at = timezone.now()
        mo.rejected_by = request.user
        mo.rejection_reason = rejection_reason
        mo.save()
        
        # Create status history
        MOStatusHistory.objects.create(
            mo=mo,
            from_status=old_status,
            to_status='rejected',
            changed_by=request.user,
            notes=f'MO rejected: {rejection_reason}'
        )
        
        # Release any reserved RM allocations if they exist
        try:
            from manufacturing.services.rm_allocation import RMAllocationService
            release_result = RMAllocationService.release_allocations_for_mo(mo, request.user)
            print(f"RM allocation release result for rejected MO {mo.mo_id}: {release_result}")
        except Exception as e:
            print(f"Failed to release RM allocations for rejected MO {mo.mo_id}: {e}")
        
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        return Response({
            'message': 'MO rejected successfully!',
            'mo': serializer.data
        })
    
    def _handle_update_details(self, mo, request):
        """Handle regular field updates (shift, quantity, etc.) - Manager and Production Head"""
        # Check if user is manager or production_head
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if not any(role in ['manager', 'production_head'] for role in user_roles):
            return Response(
                {'error': 'Only managers and production heads can update MO details'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check status-based permissions
        if 'production_head' in user_roles and mo.status != 'on_hold':
            return Response(
                {'error': 'Production heads can only update MO details when status is On Hold'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Only allow updates for certain statuses
        if mo.status not in ['on_hold', 'mo_approved']:
            return Response(
                {'error': f'Cannot update MO in {mo.status} status.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update allowed fields - production heads can edit more fields when status is on_hold
        if 'production_head' in user_roles and mo.status == 'on_hold':
            allowed_fields = ['shift', 'quantity']
        else:
            allowed_fields = ['shift']
        
        updated_fields = []
        
        for field in allowed_fields:
            if field in request.data:
                if field == 'shift':
                    shift_value = request.data[field]
                    if not shift_value or shift_value == '':
                        mo.shift = None
                        updated_fields.append(field)
                    elif shift_value in ['I', 'II', 'III']:
                        mo.shift = shift_value
                        updated_fields.append(field)
                    else:
                        return Response(
                            {'error': 'Invalid shift value'}, 
                            status=status.HTTP_400_BAD_REQUEST
                        )
                elif field == 'quantity':
                    quantity_value = request.data[field]
                    if quantity_value is not None and quantity_value > 0:
                        mo.quantity = int(quantity_value)
                        updated_fields.append(field)
                        # Recalculate RM requirements when quantity changes
                        mo.calculate_rm_requirements()
                    else:
                        return Response(
                            {'error': 'Quantity must be a positive number'}, 
                            status=status.HTTP_400_BAD_REQUEST
                        )
        
        if updated_fields:
            mo.save()
            
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        return Response({
            'message': f'Updated fields: {", ".join(updated_fields)}' if updated_fields else 'No changes made',
            'mo': serializer.data
        })


    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def rm_store_dashboard(self, request):
        """Get all MOs grouped by status for RM Store users"""
        # Check if user is rm_store
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'rm_store' not in user_roles:
            return Response(
                {'error': 'Only RM store users can access this dashboard'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get all MOs (no assignment filtering - all RM store users see all MOs)
        base_queryset = ManufacturingOrder.objects.select_related(
            'product_code', 'product_code__customer_c_id', 'customer_c_id',
            'created_by'
        ).prefetch_related('batches')
        
        # Separate by status - simplified workflow
        # MOs approved by manager and ready for RM work
        approved_mos = base_queryset.filter(status='mo_approved').order_by('-created_at')
        # In progress MOs that don't have RM allocation completed yet
        in_progress_mos = base_queryset.filter(status='in_progress', rm_allocated_at__isnull=True).order_by('-created_at')
        # For RM Store, "completed" means in_progress with rm_allocated_at set OR fully completed
        completed_mos = base_queryset.filter(
            Q(status='completed') | 
            Q(status='in_progress', rm_allocated_at__isnull=False)
        ).order_by('-created_at')
        
        # Serialize data
        approved_serializer = ManufacturingOrderListSerializer(approved_mos, many=True)
        in_progress_serializer = ManufacturingOrderListSerializer(in_progress_mos, many=True)
        completed_serializer = ManufacturingOrderListSerializer(completed_mos, many=True)
        
        return Response({
            'summary': {
                'pending_approvals': approved_mos.count(),
                'in_progress': in_progress_mos.count(),
                'completed': completed_mos.count(),
                'total': base_queryset.count()
            },
            'on_hold': approved_serializer.data,  # Keep key name for backward compatibility
            'in_progress': in_progress_serializer.data,
            'completed': completed_serializer.data
        })

    @action(detail=False, methods=['get'])
    def supervisor_dashboard(self, request):
        """Get MOs assigned to current supervisor with process filtering"""
        # Check if user is supervisor
        if not request.user.user_roles.filter(role__name='supervisor').exists():
            return Response(
                {'error': 'Only supervisors can access this endpoint'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get MOs assigned to this supervisor or with processes in their department
        try:
            user_profile = request.user.userprofile
            from authentication.models import ProcessSupervisor
            
            # Get processes this supervisor can handle
            process_supervisor = ProcessSupervisor.objects.filter(
                supervisor=request.user,
                department=user_profile.department,
                is_active=True
            ).first()
            
            if process_supervisor:
                # Filter MOs with processes this supervisor handles
                assigned_mos = self.get_queryset().filter(
                    Q(process_executions__assigned_supervisor=request.user) | 
                    Q(process_executions__process__name__in=process_supervisor.process_names),
                    status__in=['gm_approved', 'mo_approved', 'rm_allocated', 'in_progress', 'on_hold']
                ).distinct().order_by('-created_at')
            else:
                # If no process supervisor record, only show MOs with assigned process executions
                assigned_mos = self.get_queryset().filter(
                    process_executions__assigned_supervisor=request.user,
                    status__in=['gm_approved', 'mo_approved', 'rm_allocated', 'in_progress', 'on_hold']
                ).distinct().order_by('-created_at')
                
        except Exception as e:
            # If there's an error, only show MOs with assigned process executions
            assigned_mos = self.get_queryset().filter(
                process_executions__assigned_supervisor=request.user,
                status__in=['gm_approved', 'mo_approved', 'rm_allocated', 'in_progress', 'on_hold']
            ).distinct().order_by('-created_at')
        
        # Separate by status
        approved_mos = assigned_mos.exclude(status='in_progress')
        in_progress_mos = assigned_mos.filter(status='in_progress')
        
        # Serialize data
        approved_serializer = ManufacturingOrderListSerializer(approved_mos, many=True)
        in_progress_serializer = ManufacturingOrderListSerializer(in_progress_mos, many=True)
        
        return Response({
            'summary': {
                'total_assigned': assigned_mos.count(),
                'pending_start': approved_mos.count(),
                'in_progress': in_progress_mos.count()
            },
            'pending_start': approved_serializer.data,
            'in_progress': in_progress_serializer.data
        })

    @action(detail=True, methods=['post'])
    def start_mo(self, request, pk=None):
        """Start MO (Supervisor only) - moves from mo_approved to in_progress"""
        mo = self.get_object()
        
        # Check if user is supervisor
        if not request.user.user_roles.filter(role__name='supervisor').exists():
            return Response(
                {'error': 'Only supervisors can start MOs'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # NOTE: Supervisor assignment check removed - supervisors are now assigned at process execution level
        # Check if MO has any process executions assigned to this supervisor
        has_assigned_process = mo.process_executions.filter(assigned_supervisor=request.user).exists()
        if not has_assigned_process:
            return Response(
                {'error': 'You can only start MOs that have processes assigned to you'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check current status
        if mo.status not in ['mo_approved', 'gm_approved', 'rm_allocated', 'on_hold']:
            return Response(
                {'error': f'Cannot start MO in {mo.status} status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Ensure RM is reserved when supervisor starts production (no locking)
        try:
            from manufacturing.services.rm_allocation import RMAllocationService
            from manufacturing.models import RawMaterialAllocation
            from inventory.models import RMStockBalance
            
            existing_allocations = RawMaterialAllocation.objects.filter(mo=mo)
            logger.info(f"[DEBUG] supervisor start_mo - MO {mo.mo_id} - Existing allocations: {existing_allocations.count()}")
            
            if not existing_allocations.exists():
                logger.info(f"[DEBUG] supervisor start_mo - MO {mo.mo_id} - Creating RM reservations...")
                created_allocs = RMAllocationService.allocate_rm_for_mo(mo, request.user)
                logger.info(f"[DEBUG] supervisor start_mo - MO {mo.mo_id} - Reservations created")
                # Decrement RMStockBalance available_quantity for reserved qty (once per MO)
                try:
                    raw_material = mo.product_code.material
                    if raw_material and created_allocs:
                        total_reserved_kg = sum(a.allocated_quantity_kg for a in created_allocs)
                        stock_balance, _ = RMStockBalance.objects.get_or_create(
                            raw_material=raw_material,
                            defaults={'available_quantity': Decimal('0')}
                        )
                        stock_balance.available_quantity -= Decimal(str(total_reserved_kg))
                        stock_balance.save()
                        logger.info(f"[DEBUG] supervisor start_mo - Deducted {total_reserved_kg}kg from RMStockBalance for material {raw_material.material_code}")
                except Exception as sb_err:
                    logger.warning(f"[DEBUG] supervisor start_mo - Failed to decrement RMStockBalance: {sb_err}")
        except Exception as e:
            logger.error(f"[DEBUG] Failed to ensure RM reservation when supervisor starts MO {mo.mo_id}: {str(e)}")
            logger.exception(e)
        
        # Update MO status
        old_status = mo.status
        mo.status = 'in_progress'
        mo.actual_start_date = timezone.now()
        mo.save()
        
        # Create status history
        MOStatusHistory.objects.create(
            mo=mo,
            from_status=old_status,
            to_status='in_progress',
            changed_by=request.user,
            notes=request.data.get('notes', 'MO started by supervisor')
        )
        
        # Initialize processes if not already done
        from processes.models import BOM
        if not mo.process_executions.exists():
            bom_items = BOM.objects.filter(
                product_code=mo.product_code.product_code,
                is_active=True
            ).select_related('process_step__process').values('process_step__process').distinct()
            
            sequence = 1
            for bom_item in bom_items:
                process_id = bom_item['process_step__process']
                process = Process.objects.get(id=process_id)
                
                MOProcessExecution.objects.get_or_create(
                    mo=mo,
                    process=process,
                    defaults={
                        'sequence_order': sequence,
                        'status': 'pending',
                        'assigned_operator': request.user
                    }
                )
                sequence += 1
        
        serializer = ManufacturingOrderWithProcessesSerializer(mo)
        return Response({
            'message': 'MO started successfully',
            'mo': serializer.data
        })
    
    @action(detail=True, methods=['post'])
    def dispatch_to_customer(self, request, pk=None):
        """
        Dispatch completed MO to customer
        Expected payload: {
            "dispatch_quantity": 1000,
            "dispatch_notes": "Shipped via DHL",
            "vehicle_number": "TN-01-AB-1234",
            "driver_name": "John Doe"
        }
        """
        mo = self.get_object()
        
        # Check if user is manager or fg_store
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if not any(role in ['manager', 'fg_store', 'production_head'] for role in user_roles):
            return Response(
                {'error': 'Only managers or FG store users can dispatch orders'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if MO is completed or has completed batches ready for dispatch
        if mo.status not in ['completed', 'in_progress']:
            return Response(
                {'error': 'MO must be completed or have completed batches ready for dispatch'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        dispatch_quantity = request.data.get('dispatch_quantity')
        dispatch_notes = request.data.get('dispatch_notes', '')
        vehicle_number = request.data.get('vehicle_number', '')
        driver_name = request.data.get('driver_name', '')
        
        if not dispatch_quantity or dispatch_quantity <= 0:
            return Response(
                {'error': 'Valid dispatch_quantity is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate customer exists
        if not mo.customer_c_id:
            return Response(
                {'error': 'MO must have a customer assigned for dispatch'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if there's enough completed quantity in FG Store
        completed_batches = mo.batches.filter(status='completed')
        total_completed = sum(b.actual_quantity_completed or b.planned_quantity for b in completed_batches)
        
        if dispatch_quantity > total_completed:
            return Response(
                {'error': f'Dispatch quantity ({dispatch_quantity}) exceeds completed quantity ({total_completed})'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create dispatch transaction
        try:
            dispatch_notes_full = dispatch_notes
            if vehicle_number:
                dispatch_notes_full += f" | Vehicle: {vehicle_number}"
            if driver_name:
                dispatch_notes_full += f" | Driver: {driver_name}"
            
            inv_transaction = InventoryTransactionManager.create_dispatch_transaction(
                mo, mo.customer_c_id, dispatch_quantity, request.user, dispatch_notes_full
            )
            
            # Update MO status if fully dispatched
            if dispatch_quantity >= mo.quantity:
                old_status = mo.status
                mo.status = 'completed'
                mo.actual_end_date = timezone.now()
                mo.save()
                
                # Create status history
                MOStatusHistory.objects.create(
                    mo=mo,
                    from_status=old_status,
                    to_status='completed',
                    changed_by=request.user,
                    notes=f'MO completed and dispatched to customer {mo.customer_c_id.name}'
                )
            
            # Get location summary
            location_summary = InventoryTransactionManager.get_mo_location_summary(mo)
            
            serializer = ManufacturingOrderDetailSerializer(mo)
            return Response({
                'message': f'Successfully dispatched {dispatch_quantity} units to {mo.customer_c_id.name}',
                'mo': serializer.data,
                'transaction_id': inv_transaction.transaction_id,
                'location_summary': location_summary,
                'dispatch_details': {
                    'quantity': dispatch_quantity,
                    'customer': mo.customer_c_id.name,
                    'vehicle_number': vehicle_number,
                    'driver_name': driver_name,
                    'dispatched_at': timezone.now().isoformat()
                }
            })
        except Exception as e:
            return Response(
                {'error': f'Failed to dispatch: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def location_tracking(self, request, pk=None):
        """
        Get comprehensive location tracking for all batches in an MO
        """
        mo = self.get_object()
        
        try:
            location_summary = InventoryTransactionManager.get_mo_location_summary(mo)
            
            # Get all transactions for this MO
            from inventory.models import InventoryTransaction
            transactions = InventoryTransaction.objects.filter(
                manufacturing_order=mo
            ).select_related(
                'location_from', 'location_to', 'created_by'
            ).order_by('-transaction_datetime')[:50]  # Last 50 transactions
            
            transaction_list = [
                {
                    'transaction_id': t.transaction_id,
                    'transaction_type': t.get_transaction_type_display(),
                    'location_from': t.location_from.get_location_name_display() if t.location_from else None,
                    'location_to': t.location_to.get_location_name_display() if t.location_to else None,
                    'quantity': float(t.quantity),
                    'transaction_datetime': t.transaction_datetime,
                    'created_by': f"{t.created_by.first_name} {t.created_by.last_name}",
                    'notes': t.notes
                }
                for t in transactions
            ]
            
            return Response({
                'mo_id': mo.mo_id,
                'location_summary': location_summary,
                'recent_transactions': transaction_list
            })
        except Exception as e:
            return Response(
                {'error': f'Failed to get location tracking: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='stop')
    def stop_mo(self, request, pk=None):
        """
        Stop an MO and release all reserved resources
        
        POST /api/manufacturing/manufacturing-orders/{id}/stop/
        Body: {"stop_reason": "High priority MO_002 needs this material"}
        """
        from .serializers import MOStopSerializer
        
        mo = self.get_object()
        
        # Validate input
        serializer = MOStopSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        stop_reason = serializer.validated_data['stop_reason']
        
        try:
            # Call the model method to stop the MO
            released_resources = mo.stop_mo(stop_reason, request.user)
            
            # Return summary of what was released
            return Response({
                'success': True,
                'message': f'MO {mo.mo_id} stopped successfully',
                'mo_id': mo.mo_id,
                'status': mo.status,
                'stopped_at': mo.stopped_at,
                'stop_reason': mo.stop_reason,
                'released_resources': released_resources
            }, status=status.HTTP_200_OK)
            
        except ValidationError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Failed to stop MO {mo.mo_id}: {str(e)}")
            return Response(
                {'error': f'Failed to stop MO: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='resource-status')
    def resource_status(self, request, pk=None):
        """
        Get detailed resource status for an MO
        
        GET /api/manufacturing/manufacturing-orders/{id}/resource-status/
        
        Returns:
        - Reserved RM allocations
        - Allocated (locked) RM
        - Reserved FG stock
        - In-progress batches
        - Pending batches
        """
        from .serializers import MOResourceStatusSerializer
        
        mo = self.get_object()
        
        try:
            serializer = MOResourceStatusSerializer(mo)
            return Response({
                'mo_id': mo.mo_id,
                'status': mo.status,
                'priority_level': mo.priority_level,
                'resources': serializer.data
            }, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to get resource status for MO {mo.mo_id}: {str(e)}")
            return Response(
                {'error': f'Failed to get resource status: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='priority-queue')
    def priority_queue(self, request):
        """
        Get list of MOs in priority order with resource information
        
        GET /api/manufacturing/manufacturing-orders/priority-queue/
        
        Query params:
        - status: Filter by status (comma-separated)
        - has_reserved_rm: true/false - filter by whether MO has reserved RM
        """
        from .serializers import MOPriorityQueueSerializer
        
        try:
            # Base queryset - active MOs only
            queryset = ManufacturingOrder.objects.filter(
                status__in=['on_hold', 'rm_allocated', 'in_progress', 'submitted']
            ).select_related(
                'product_code', 'customer_c_id'
            ).prefetch_related(
                'rm_allocations', 'fg_reservations', 'batches'
            )
            
            # Filter by status if provided
            status_filter = request.query_params.get('status')
            if status_filter:
                statuses = [s.strip() for s in status_filter.split(',')]
                queryset = queryset.filter(status__in=statuses)
            
            # Filter by has_reserved_rm if provided
            has_reserved_rm = request.query_params.get('has_reserved_rm')
            if has_reserved_rm == 'true':
                queryset = queryset.filter(rm_allocations__status='reserved').distinct()
            elif has_reserved_rm == 'false':
                queryset = queryset.exclude(rm_allocations__status='reserved')
            
            # Order by priority_level (desc), then created_at (desc)
            queryset = queryset.order_by('-priority_level', '-created_at')
            
            serializer = MOPriorityQueueSerializer(queryset, many=True)
            
            return Response({
                'count': queryset.count(),
                'results': serializer.data
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Failed to get priority queue: {str(e)}")
            return Response(
                {'error': f'Failed to get priority queue: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Purchase Orders with optimized queries and filtering
    Managers can create/edit POs, RM Store users can view and update status
    """
    permission_classes = [IsManagerOrRMStore]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'material_type', 'vendor_name', 'expected_date']
    search_fields = ['po_id', 'rm_code__product_code', 'vendor_name__name']
    ordering_fields = ['created_at', 'expected_date', 'po_id', 'total_amount']
    ordering = ['-created_at']

    def get_queryset(self):
        """Optimized queryset with select_related and prefetch_related"""
        queryset = PurchaseOrder.objects.select_related(
            'rm_code', 'vendor_name', 'created_by', 'approved_by', 'cancelled_by'
        ).prefetch_related(
            Prefetch('status_history', queryset=POStatusHistory.objects.select_related('changed_by'))
        )
        
        # Filter by date range if provided
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
        
        # Filter based on user role
        user = self.request.user
        user_roles = user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        
        # Admin, manager, production_head can see all POs
        if any(role in ['admin', 'manager', 'production_head'] for role in user_roles):
            return queryset
        
        # RM Store users can see all POs (they need to manage incoming materials)
        if 'rm_store' in user_roles:
            return queryset
        
        # Other users see no POs
        return queryset.none()

    def get_serializer_class(self):
        """Use different serializers for list and detail views"""
        if self.action == 'list':
            return PurchaseOrderListSerializer
        return PurchaseOrderDetailSerializer

    @action(detail=True, methods=['post'])
    def change_status(self, request, pk=None):
        """Change PO status with validation"""
        po = self.get_object()
        new_status = request.data.get('status')
        notes = request.data.get('notes', '')
        rejection_reason = request.data.get('rejection_reason', '')
        
        if not new_status:
            return Response({'error': 'Status is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate status transition
        from utils.enums import POStatusChoices
        valid_statuses = [choice[0] for choice in POStatusChoices.choices]
        if new_status not in valid_statuses:
            return Response({'error': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if GRM is required for completing the PO
        if new_status == 'rm_completed':
            grm_exists = GRMReceipt.objects.filter(purchase_order=po).exists()
            if not grm_exists:
                return Response(
                    {'error': 'Cannot complete PO without creating at least one GRM Receipt for RM'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        old_status = po.status
        po.status = new_status
        
        # Update workflow timestamps based on status
        if new_status == 'po_approved':
            po.approved_at = timezone.now()
            po.approved_by = request.user
            
            # Create inventory transaction for PO approval
            try:
                InventoryTransactionManager.create_po_approved_transaction(po, request.user)
            except Exception as e:
                print(f"Error creating PO approved transaction: {e}")
                # Don't fail the status change if transaction creation fails
                
        elif new_status == 'po_cancelled':
            po.cancelled_at = timezone.now()
            po.cancelled_by = request.user
            po.cancellation_reason = rejection_reason
        
        po.save()
        
        # Create status history
        POStatusHistory.objects.create(
            po=po,
            from_status=old_status,
            to_status=new_status,
            changed_by=request.user,
            notes=notes
        )
        
        serializer = self.get_serializer(po)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics for Purchase Orders"""
        queryset = self.get_queryset()
        
        stats = {
            'total': queryset.count(),
            'draft': queryset.filter(status='draft').count(),
            'po_approved': queryset.filter(status='po_approved').count(),
            'rm_completed': queryset.filter(status='rm_completed').count(),
            'po_cancelled': queryset.filter(status='po_cancelled').count(),
            'pending_approval': queryset.filter(status__in=['draft', 'po_submitted']).count(),
            'by_material_type': {
                'coil': queryset.filter(material_type='coil').count(),
                'sheet': queryset.filter(material_type='sheet').count(),
            },
            'overdue': queryset.filter(
                expected_date__lt=timezone.now(),
                status__in=['draft', 'po_approved', 'po_submitted']
            ).count(),
        }
        
        return Response(stats)


class MOProcessExecutionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for MO Process Execution tracking
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['mo', 'process', 'status', 'assigned_operator', 'assigned_supervisor']
    search_fields = ['mo__mo_id', 'process__name', 'assigned_operator__first_name', 'assigned_operator__last_name']
    ordering_fields = ['sequence_order', 'planned_start_time', 'actual_start_time', 'progress_percentage']
    ordering = ['mo', 'sequence_order']

    def get_queryset(self):
        """Optimized queryset with select_related and prefetch_related"""
        queryset = MOProcessExecution.objects.select_related(
            'mo', 'process', 'assigned_operator', 'assigned_supervisor'
        ).prefetch_related('step_executions', 'alerts')
        
        # Filter based on user role and department
        user = self.request.user
        user_roles = user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        
        # Admin, manager, production_head can see all processes
        if any(role in ['admin', 'manager', 'production_head'] for role in user_roles):
            return queryset
        
        # Supervisors can only see processes assigned to them or their department
        if 'supervisor' in user_roles:
            try:
                user_profile = user.userprofile
                from authentication.models import ProcessSupervisor
                
                # Get processes this supervisor can handle
                process_supervisor = ProcessSupervisor.objects.filter(
                    supervisor=user,
                    department=user_profile.department,
                    is_active=True
                ).first()
                
                if process_supervisor:
                    # Filter to only show processes this supervisor handles
                    queryset = queryset.filter(
                        Q(assigned_supervisor=user) | 
                        Q(process__name__in=process_supervisor.process_names)
                    )
                else:
                    # If no process supervisor record, only show assigned processes
                    queryset = queryset.filter(assigned_supervisor=user)
                    
            except Exception as e:
                # If there's an error, only show assigned processes
                queryset = queryset.filter(assigned_supervisor=user)
        
        # RM Store and FG Store users can only see processes assigned to them
        elif any(role in ['rm_store', 'fg_store'] for role in user_roles):
            queryset = queryset.filter(assigned_supervisor=user)
        
        # Other users see no processes
        else:
            queryset = queryset.none()
            
        return queryset

    def get_serializer_class(self):
        """Use different serializers for list and detail views"""
        if self.action == 'list':
            return MOProcessExecutionListSerializer
        return MOProcessExecutionDetailSerializer

    @action(detail=True, methods=['put'])
    def assign_supervisor(self, request, pk=None):
        """
        Assign or update supervisor for a specific process execution
        Only production head/manager can assign supervisors
        """
        try:
            # Check permissions
            user_roles = request.user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
            if not any(role in ['production_head', 'manager', 'admin'] for role in user_roles):
                return Response({
                    'error': 'Only production head/manager can assign supervisors'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Get process execution
            process_execution = self.get_object()
            
            # Get supervisor user
            supervisor_id = request.data.get('assigned_supervisor')
            notes = request.data.get('notes', '')
            
            if not supervisor_id:
                return Response({
                    'error': 'Supervisor ID is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                supervisor = User.objects.get(id=supervisor_id)
            except User.DoesNotExist:
                return Response({
                    'error': 'Supervisor user not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Update supervisor assignment
            process_execution.assigned_supervisor = supervisor
            process_execution.save(update_fields=['assigned_supervisor', 'updated_at'])
            
            # Create activity log entry
            if notes:
                process_execution.notes = f"{process_execution.notes}\n[Supervisor reassigned by {request.user.get_full_name()}]: {notes}"
                process_execution.save(update_fields=['notes'])
            
            # Send notification to the newly assigned supervisor
            from notifications.models import WorkflowNotification
            WorkflowNotification.objects.create(
                notification_type='supervisor_assigned',
                title=f'Process Assigned: {process_execution.process.name}',
                message=f'You have been assigned as supervisor for process "{process_execution.process.name}" for MO {process_execution.mo.mo_id}.',
                recipient=supervisor,
                related_mo=process_execution.mo,
                action_required=True,
                created_by=request.user
            )
            
            # Return simple response with just supervisor ID
            return Response({
                'assigned_supervisor': supervisor_id
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f'Error assigning supervisor: {str(e)}', exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def start_process(self, request, pk=None):
        """Start a process execution"""
        execution = self.get_object()
        
        if execution.status != 'pending':
            return Response(
                {'error': 'Process can only be started from pending status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user can access this process
        if not execution.can_user_access(request.user):
            return Response(
                {'error': 'You do not have permission to access this process'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        execution.status = 'in_progress'
        execution.actual_start_time = timezone.now()
        execution.assigned_operator = request.user
        execution.save()
        
        # Create inventory transaction for process start (track location movement)
        # Get all batches for this MO and track their movement to this process
        mo = execution.mo
        for batch in mo.batches.exclude(status='cancelled'):
            try:
                InventoryTransactionManager.create_process_start_transaction(
                    execution, batch, request.user
                )
            except Exception as e:
                print(f"Error creating process start transaction for batch {batch.batch_id}: {e}")
        
        # Create step executions if they don't exist
        process_steps = execution.process.process_steps.all().order_by('sequence_order')
        for step in process_steps:
            MOProcessStepExecution.objects.get_or_create(
                process_execution=execution,
                process_step=step,
                defaults={'status': 'pending'}
            )
        
        serializer = self.get_serializer(execution)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def complete_process(self, request, pk=None):
        """Complete a process execution and move to next process or FG store"""
        execution = self.get_object()
        
        # Allow completion if process is in progress OR if MO is stopped (for in-progress batches to complete)
        mo_is_stopped = execution.mo.status == 'stopped'
        if execution.status != 'in_progress' and not mo_is_stopped:
            return Response(
                {'error': 'Process must be in progress to complete'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user can access this process
        if not execution.can_user_access(request.user):
            return Response(
                {'error': 'You do not have permission to access this process'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Complete the process and move to next
        result = execution.complete_and_move_to_next(request.user)
        
        serializer = self.get_serializer(execution)
        response_data = {
            'process_execution': serializer.data,
            'movement_result': result
        }
        
        return Response(response_data)

    def _create_process_consumption_transaction(self, execution, user):
        """Create inventory transaction for process consumption"""
        try:
            from inventory.models import InventoryTransaction, RMStockBalance
            from inventory.utils import generate_transaction_id
            
            # Get the raw material associated with the MO's product
            mo = execution.mo
            raw_material = mo.product_code.material
            
            if not raw_material:
                return
            
            # Calculate consumption quantity (this could be based on BOM or process requirements)
            # For now, we'll use a small amount per process - this should be configurable
            consumption_quantity = 1.0  # kg or pieces
            
            # Create transaction ID
            transaction_id = generate_transaction_id('PROC')
            
            # Create inventory transaction
            transaction = InventoryTransaction.objects.create(
                transaction_id=transaction_id,
                transaction_type='consumption',
                product=mo.product_code,  # This should be the raw material product
                manufacturing_order=mo,
                quantity=consumption_quantity,
                transaction_datetime=timezone.now(),
                created_by=user,
                reference_type='process',
                reference_id=str(execution.id),
                notes=f'Process consumption for {execution.process.name}'
            )
            
            # Update stock balance
            stock_balance, created = RMStockBalance.objects.get_or_create(
                raw_material=raw_material,
                defaults={'available_quantity': 0}
            )
            
            if stock_balance.available_quantity >= consumption_quantity:
                stock_balance.available_quantity -= consumption_quantity
                stock_balance.save()
            else:
                # Log warning but don't fail the process
                print(f"Warning: Insufficient stock for {raw_material.material_code}. Required: {consumption_quantity}, Available: {stock_balance.available_quantity}")
                
        except Exception as e:
            print(f"Error creating process consumption transaction: {e}")
            # Don't fail the process start if inventory transaction fails

    def _create_process_completion_transaction(self, execution, user):
        """Create inventory transaction for process completion"""
        try:
            from inventory.models import InventoryTransaction
            from inventory.utils import generate_transaction_id
            
            # Create transaction ID
            transaction_id = generate_transaction_id('PROC_COMP')
            
            # Create inventory transaction for process completion
            transaction = InventoryTransaction.objects.create(
                transaction_id=transaction_id,
                transaction_type='production',
                product=execution.mo.product_code,
                manufacturing_order=execution.mo,
                quantity=execution.mo.quantity,  # This should be calculated based on actual output
                transaction_datetime=timezone.now(),
                created_by=user,
                reference_type='process',
                reference_id=str(execution.id),
                notes=f'Process completion for {execution.process.name}'
            )
                
        except Exception as e:
            print(f"Error creating process completion transaction: {e}")
            # Don't fail the process completion if inventory transaction fails

    def _update_process_progress_based_on_batches(self, execution):
        """Update process progress based on batch completion status"""
        try:
            mo = execution.mo
            mo_batches = mo.batches.exclude(status='cancelled')
            
            if not mo_batches.exists():
                # No batches, set progress to 0
                execution.progress_percentage = 0
                execution.save()
                return
            
            # Count batches that have completed this process
            completed_batches = 0
            total_batches = mo_batches.count()
            
            for batch in mo_batches:
                batch_proc_key = f"PROCESS_{execution.id}_STATUS"
                if f"{batch_proc_key}:completed;" in (batch.notes or ""):
                    completed_batches += 1
            
            # Calculate progress percentage
            if total_batches > 0:
                progress_percentage = (completed_batches / total_batches) * 100
                execution.progress_percentage = progress_percentage
                
                # If process was completed but not all batches are completed, revert to in_progress
                if execution.status == 'completed' and completed_batches < total_batches:
                    execution.status = 'in_progress'
                    execution.actual_end_time = None
                
                execution.save()
                
        except Exception as e:
            print(f"Error updating process progress based on batches: {e}")

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def complete_process(self, request, pk=None):
        """Complete a process execution"""
        execution = self.get_object()
        
        # Check if user has permission (supervisor or operator assigned to this process)
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'supervisor' not in user_roles and execution.assigned_operator != request.user:
            return Response(
                {'error': 'Only supervisors or assigned operators can complete processes'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Allow completion if process is in progress OR if MO is stopped (for in-progress batches to complete)
        mo_is_stopped = execution.mo.status == 'stopped'
        if execution.status != 'in_progress' and not mo_is_stopped:
            return Response(
                {
                    'error': 'Process must be in progress to complete',
                    'message': f'Process must be in progress to complete. Current status: {execution.status}',
                    'current_status': execution.status
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if all steps are completed
        incomplete_steps = execution.step_executions.exclude(status='completed').count()
        if incomplete_steps > 0:
            incomplete_step_names = list(execution.step_executions.exclude(status='completed').values_list('process_step__step_name', flat=True))
            return Response(
                {
                    'error': f'{incomplete_steps} steps are still incomplete',
                    'message': f'{incomplete_steps} steps are still incomplete: {", ".join(incomplete_step_names)}',
                    'incomplete_steps': incomplete_step_names
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # For batch-based production, check if all batches have completed this process
        mo = execution.mo
        mo_batches = mo.batches.exclude(status='cancelled')
        
        if mo_batches.exists():
            # Check if all batches have completed this process
            all_batches_completed_process = True
            for batch in mo_batches:
                batch_proc_key = f"PROCESS_{execution.id}_STATUS"
                if f"{batch_proc_key}:completed;" not in (batch.notes or ""):
                    all_batches_completed_process = False
                    break
            
            if not all_batches_completed_process:
                return Response(
                    {
                        'error': 'Cannot complete process - not all batches have completed this process',
                        'message': 'All batches must complete this process before marking the process as completed'
                    }, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        execution.status = 'completed'
        execution.actual_end_time = timezone.now()
        execution.progress_percentage = 100
        execution.save()
        
        # Update process progress based on batch completion
        self._update_process_progress_based_on_batches(execution)
        
        # Create inventory transactions for process completion
        # Track completion and movement to next location for each batch
        for batch in mo_batches:
            try:
                actual_quantity = batch.actual_quantity_completed or batch.planned_quantity
                InventoryTransactionManager.create_process_complete_transaction(
                    execution, batch, actual_quantity, request.user
                )
            except Exception as e:
                print(f"Error creating process completion transaction for batch {batch.batch_id}: {e}")
        
        serializer = self.get_serializer(execution)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def update_progress(self, request, pk=None):
        """Update process progress"""
        execution = self.get_object()
        progress = request.data.get('progress_percentage')
        notes = request.data.get('notes', '')
        
        if progress is None or not (0 <= float(progress) <= 100):
            return Response(
                {'error': 'Valid progress_percentage (0-100) is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        execution.progress_percentage = progress
        if notes:
            execution.notes = notes
        execution.save()
        
        serializer = self.get_serializer(execution)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_mo(self, request):
        """Get process executions for a specific MO"""
        mo_id = request.query_params.get('mo_id')
        if not mo_id:
            return Response(
                {'error': 'mo_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        executions = self.get_queryset().filter(mo_id=mo_id)
        serializer = self.get_serializer(executions, many=True)
        return Response(serializer.data)


class MOProcessStepExecutionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for MO Process Step Execution tracking
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['process_execution', 'process_step', 'status', 'quality_status', 'operator']
    search_fields = ['process_step__step_name', 'operator__first_name', 'operator__last_name']
    ordering_fields = ['process_step__sequence_order', 'actual_start_time', 'efficiency_percentage']
    ordering = ['process_execution', 'process_step__sequence_order']
    serializer_class = MOProcessStepExecutionSerializer

    def get_queryset(self):
        """Optimized queryset with select_related"""
        return MOProcessStepExecution.objects.select_related(
            'process_execution__mo', 'process_step', 'operator'
        )

    @action(detail=True, methods=['post'])
    def start_step(self, request, pk=None):
        """Start a process step"""
        step_execution = self.get_object()
        
        if step_execution.status != 'pending':
            return Response(
                {'error': 'Step can only be started from pending status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        step_execution.status = 'in_progress'
        step_execution.actual_start_time = timezone.now()
        step_execution.operator = request.user
        step_execution.save()
        
        serializer = self.get_serializer(step_execution)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def complete_step(self, request, pk=None):
        """Complete a process step with quality data"""
        step_execution = self.get_object()
        data = request.data
        
        if step_execution.status != 'in_progress':
            return Response(
                {'error': 'Step must be in progress to complete'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update step data
        step_execution.status = 'completed'
        step_execution.actual_end_time = timezone.now()
        step_execution.quantity_processed = data.get('quantity_processed', 0)
        step_execution.quantity_passed = data.get('quantity_passed', 0)
        step_execution.quantity_failed = data.get('quantity_failed', 0)
        step_execution.quality_status = data.get('quality_status', 'passed')
        step_execution.operator_notes = data.get('operator_notes', '')
        step_execution.quality_notes = data.get('quality_notes', '')
        
        # Calculate scrap percentage
        if step_execution.quantity_processed > 0:
            step_execution.scrap_percentage = (
                step_execution.quantity_failed / step_execution.quantity_processed
            ) * 100
        
        step_execution.save()
        
        # Update parent process progress
        process_execution = step_execution.process_execution
        total_steps = process_execution.step_executions.count()
        completed_steps = process_execution.step_executions.filter(status='completed').count()
        
        if total_steps > 0:
            progress = (completed_steps / total_steps) * 100
            process_execution.progress_percentage = progress
            process_execution.save()
        
        serializer = self.get_serializer(step_execution)
        return Response(serializer.data)


class MOProcessAlertViewSet(viewsets.ModelViewSet):
    """
    ViewSet for MO Process Alerts
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['process_execution', 'alert_type', 'severity', 'is_resolved']
    search_fields = ['title', 'description', 'process_execution__mo__mo_id']
    ordering_fields = ['created_at', 'severity']
    ordering = ['-created_at']
    serializer_class = MOProcessAlertSerializer

    def get_queryset(self):
        """Optimized queryset with select_related"""
        return MOProcessAlert.objects.select_related(
            'process_execution__mo', 'created_by', 'resolved_by'
        )

    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve an alert"""
        alert = self.get_object()
        resolution_notes = request.data.get('resolution_notes', '')
        
        alert.is_resolved = True
        alert.resolved_at = timezone.now()
        alert.resolved_by = request.user
        alert.resolution_notes = resolution_notes
        alert.save()
        
        serializer = self.get_serializer(alert)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def active_alerts(self, request):
        """Get active (unresolved) alerts"""
        alerts = self.get_queryset().filter(is_resolved=False)

        # Filter by MO if specified
        mo_id = request.query_params.get('mo_id')
        if mo_id:
            alerts = alerts.filter(process_execution__mo_id=mo_id)

        # Use minimal serializer for production-head MO detail page to reduce payload
        from .serializers import MOProcessAlertMinimalSerializer
        serializer = MOProcessAlertMinimalSerializer(alerts, many=True)
        return Response(serializer.data)


class BatchViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Batch management - RM Store users can create batches
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['mo', 'status', 'assigned_operator', 'assigned_supervisor']
    search_fields = ['batch_id', 'mo__mo_id', 'product_code__product_code']
    ordering_fields = ['created_at', 'planned_start_date', 'status', 'progress_percentage']
    ordering = ['-created_at']

    def get_queryset(self):
        """Optimized queryset with select_related and prefetch_related"""
        queryset = Batch.objects.select_related(
            'mo', 'product_code', 'assigned_operator', 'assigned_supervisor', 
            'created_by', 'current_process_step'
        ).prefetch_related('mo__product_code')
        
        # Filter by MO if specified
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo_id=mo_id)
        
        return queryset

    def get_serializer_class(self):
        """Use different serializers for list and detail views"""
        if self.action == 'list':
            return BatchListSerializer
        return BatchDetailSerializer

    def perform_create(self, serializer):
        """Create batch with location tracking"""
        batch = serializer.save()
        
        # Calculate RM quantity for the batch
        product = batch.mo.product_code
        rm_quantity_kg = 0
        
        if product.material_type == 'coil' and product.grams_per_product:
            # batch.planned_quantity is in grams
            batch_quantity_grams = batch.planned_quantity
            batch_rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
            
            # Apply tolerance
            tolerance = batch.mo.tolerance_percentage or Decimal('2.00')
            tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
            rm_quantity_kg = float(batch_rm_base_kg * tolerance_factor)
        
        # Create inventory transaction for RM allocation
        try:
            InventoryTransactionManager.create_batch_allocation_transaction(
                batch, rm_quantity_kg, self.request.user
            )
        except Exception as e:
            print(f"Error creating batch allocation transaction: {e}")
            # Don't fail batch creation if transaction fails
        
        # Recalculate process progress for all process executions when a new batch is created
        # This ensures that if a process was marked as completed, adding a new batch will update the progress
        # Progress should decrease when a new batch is added (e.g., 1/1=100% → 1/2=50%)
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            process_executions = batch.mo.process_executions.all()
            mo_batches = batch.mo.batches.exclude(status='cancelled')
            total_batches = mo_batches.count()
            
            for execution in process_executions:
                # Count batches that have completed this process
                completed_batches = 0
                for mo_batch in mo_batches:
                    batch_proc_key = f"PROCESS_{execution.id}_STATUS"
                    if f"{batch_proc_key}:completed;" in (mo_batch.notes or ""):
                        completed_batches += 1
                
                # Calculate progress percentage based on batch completion
                if total_batches > 0:
                    progress_percentage = (completed_batches / total_batches) * 100
                    old_progress = execution.progress_percentage
                    execution.progress_percentage = progress_percentage
                    
                    # If process was completed but new batch added, revert to in_progress
                    if execution.status == 'completed' and completed_batches < total_batches:
                        execution.status = 'in_progress'
                        execution.actual_end_time = None
                        logger.info(
                            f"[Batch Creation] Process {execution.id} ({execution.process.name}) reverted from completed to in_progress "
                            f"because new batch {batch.batch_id} was added. Progress: {old_progress}% → {progress_percentage}% "
                            f"({completed_batches}/{total_batches} batches completed)"
                        )
                    else:
                        logger.info(
                            f"[Batch Creation] Updated process {execution.id} ({execution.process.name}) progress: "
                            f"{old_progress}% → {progress_percentage}% ({completed_batches}/{total_batches} batches completed)"
                        )
                    
                    execution.save(update_fields=['progress_percentage', 'status', 'actual_end_time'])
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error updating process progress after batch creation: {e}", exc_info=True)
            # Don't fail batch creation if progress update fails

    @action(detail=False, methods=['get'], url_path='mo-batch-summary/(?P<mo_id>[^/.]+)')
    def mo_batch_summary(self, request, mo_id=None):
        """
        Get comprehensive batch summary for an MO including:
        - Total RM required for MO (with tolerance)
        - Cumulative RM released across all batches
        - Remaining RM that can be allocated
        - % completion based on batches
        """
        try:
            mo = ManufacturingOrder.objects.select_related('product_code').get(id=mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'Manufacturing Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        product = mo.product_code
        
        # Calculate total RM required for entire MO
        total_rm_required = None
        rm_unit = None
        
        if product.material_type == 'coil' and product.grams_per_product:
            # For coil-based products (springs)
            total_grams = mo.quantity * product.grams_per_product
            base_rm_kg = Decimal(str(total_grams / 1000))
            
            # Apply tolerance
            tolerance = mo.tolerance_percentage or Decimal('2.00')
            tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
            total_rm_required = float(base_rm_kg * tolerance_factor)
            rm_unit = 'kg'
            
        elif product.material_type == 'sheet' and product.pcs_per_strip:
            # For sheet-based products (press components)
            strips_calc = product.calculate_strips_required(mo.quantity)
            total_rm_required = strips_calc.get('strips_required', 0)
            rm_unit = 'strips'
        
        # Get all batches for this MO
        batches = Batch.objects.filter(mo=mo).exclude(status='cancelled')
        
        # Calculate cumulative RM released and scrapped
        cumulative_rm_released = Decimal('0')
        cumulative_scrap_rm = Decimal('0')
        batch_details = []
        
        for batch in batches:
            batch_rm = Decimal('0')
            
            # NOTE: planned_quantity is now stored in GRAMS (not pieces)
            # User enters RM in kg, frontend converts to grams
            batch_quantity_grams = batch.planned_quantity
            
            # Convert grams to kg
            batch_rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
            
            # Apply tolerance
            tolerance = mo.tolerance_percentage or Decimal('2.00')
            tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
            batch_rm = batch_rm_base_kg * tolerance_factor

            cumulative_rm_released += batch_rm
            
            # Add scrap RM (stored in grams)
            batch_scrap_kg = Decimal(str(batch.scrap_rm_weight / 1000))
            cumulative_scrap_rm += batch_scrap_kg
            
            batch_details.append({
                'batch_id': batch.batch_id,
                'planned_quantity': batch.planned_quantity,  # in grams
                'rm_base_kg': float(batch_rm_base_kg),
                'rm_released': float(batch_rm),
                'scrap_rm_kg': float(batch_scrap_kg),
                'status': batch.status,
                'created_at': batch.created_at
            })
        
        # Add MO-level scrap (remaining RM sent to scrap)
        mo_scrap_kg = Decimal(str(mo.scrap_rm_weight / 1000))
        
        # Calculate remaining and percentage
        remaining_rm = None
        completion_percentage = 0
        
        if total_rm_required is not None:
            # Remaining = Total - Released - Already scrapped at MO level
            remaining_rm = float(Decimal(str(total_rm_required)) - cumulative_rm_released - mo_scrap_kg)
            if remaining_rm < 0:
                remaining_rm = 0
            
            if total_rm_required > 0:
                completion_percentage = min(
                    100, 
                    float((cumulative_rm_released / Decimal(str(total_rm_required))) * Decimal('100'))
                )
        
        return Response({
            'mo_id': mo.mo_id,
            'mo_quantity': mo.quantity,
            'material_type': product.material_type,
            'total_rm_required': total_rm_required,
            'rm_unit': rm_unit,
            'cumulative_rm_released': float(cumulative_rm_released),
            'cumulative_scrap_rm': float(cumulative_scrap_rm),
            'mo_scrap_rm': float(mo_scrap_kg),
            'remaining_rm': remaining_rm,
            'completion_percentage': round(completion_percentage, 2),
            'batch_count': batches.count(),
            'batches': batch_details,
            'tolerance_percentage': float(mo.tolerance_percentage) if mo.tolerance_percentage else 2.00
        })
    
    @action(detail=True, methods=['post'], url_path='add-scrap-rm')
    def add_scrap_rm(self, request, pk=None):
        """
        Add scrap RM weight to a batch
        Expected payload: { "scrap_rm_kg": 1.5 }
        """
        batch = self.get_object()
        scrap_rm_kg = request.data.get('scrap_rm_kg')
        
        if scrap_rm_kg is None:
            return Response(
                {'error': 'scrap_rm_kg is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            scrap_rm_kg = float(scrap_rm_kg)
            if scrap_rm_kg < 0:
                return Response(
                    {'error': 'scrap_rm_kg must be non-negative'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (ValueError, TypeError):
            return Response(
                {'error': 'scrap_rm_kg must be a valid number'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Convert kg to grams and add to existing scrap
        scrap_rm_grams = int(scrap_rm_kg * 1000)
        batch.scrap_rm_weight += scrap_rm_grams
        batch.save()
        
        serializer = self.get_serializer(batch)
        return Response({
            'message': f'Added {scrap_rm_kg} kg of scrap RM to batch {batch.batch_id}',
            'batch': serializer.data,
            'total_scrap_rm_kg': batch.scrap_rm_weight / 1000
        })
    
    @action(detail=False, methods=['get'])
    def by_mo(self, request):
        """Get batches for a specific MO"""
        mo_id = request.query_params.get('mo_id')
        if not mo_id:
            return Response(
                {'error': 'mo_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        batches = self.get_queryset().filter(mo_id=mo_id)
        # Use minimal serializer for production-head MO detail page to reduce payload
        from .serializers import BatchMinimalSerializer
        serializer = BatchMinimalSerializer(batches, many=True)
        
        # Calculate summary
        total_planned = sum(b.planned_quantity for b in batches)
        total_completed = sum(b.actual_quantity_completed for b in batches)
        
        return Response({
            'batches': serializer.data,
            'summary': {
                'total_batches': batches.count(),
                'total_planned_quantity': total_planned,
                'total_completed_quantity': total_completed,
                'completion_percentage': (total_completed / total_planned * 100) if total_planned > 0 else 0
            }
        })

    @action(detail=True, methods=['post'])
    def verify_batch(self, request, pk=None):
        """Verify a batch before starting - Supervisor only"""
        batch = self.get_object()
        
        # Check if user is supervisor
        if not request.user.user_roles.filter(role__name='supervisor').exists():
            return Response(
                {'error': 'Only supervisors can verify batches'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if batch is in created status (pending verification)
        if batch.status != 'created':
            return Response(
                {'error': f'Batch can only be verified when in created status. Current status: {batch.status}'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Mark batch as verified by adding verification note
        verification_note = f"\n[BATCH_VERIFIED] Verified by {request.user.get_full_name()} on {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
        batch.notes = (batch.notes or '') + verification_note
        batch.save(update_fields=['notes', 'updated_at'])
        
        # Log the verification
        from manufacturing.models.activity_log import ProcessActivityLog
        first_process_execution = batch.mo.process_executions.first()
        ProcessActivityLog.objects.create(
            batch=batch,
            mo=batch.mo,
            process=first_process_execution.process if first_process_execution else None,
            process_execution=first_process_execution,
            activity_type='batch_verified',
            performed_by=request.user,
            remarks=f'Batch {batch.batch_id} verified by supervisor before starting'
        )
        
        serializer = self.get_serializer(batch)
        return Response({
            'message': f'Batch {batch.batch_id} verified successfully',
            'batch': serializer.data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def start_batch(self, request, pk=None):
        """Start a batch - updates status to in_process and locks RM allocations"""
        batch = self.get_object()
        
        if batch.status != 'created':
            return Response(
                {'error': 'Batch can only be started from created status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if batch is verified (has verification note)
        if not batch.notes or '[BATCH_VERIFIED]' not in batch.notes:
            return Response(
                {'error': 'Batch must be verified by supervisor before starting'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Lock RM allocations for this batch
        from manufacturing.services.rm_allocation import RMAllocationService
        lock_result = RMAllocationService.lock_allocations_for_batch(
            batch=batch,
            locked_by_user=request.user
        )
        
        # Log the result but don't fail if locking fails (log for debugging)
        if not lock_result.get('success'):
            logger.warning(
                f"Failed to lock RM allocations for batch {batch.batch_id}: {lock_result.get('message')}"
            )
        else:
            logger.info(
                f"Locked {lock_result.get('locked_count', 0)} RM allocations "
                f"({lock_result.get('locked_quantity_kg', 0)}kg) for batch {batch.batch_id}"
            )
        
        batch.status = 'in_process'
        batch.actual_start_date = timezone.now()
        batch.save()
        
        serializer = self.get_serializer(batch)
        response_data = serializer.data
        
        # Include RM locking info in response
        response_data['rm_locking'] = lock_result
        
        return Response(response_data)

    @action(detail=True, methods=['post'])
    def complete_batch(self, request, pk=None):
        """Complete a batch"""
        batch = self.get_object()
        data = request.data
        
        if batch.status not in ['in_process', 'quality_check']:
            return Response(
                {'error': 'Batch must be in process or quality check to complete'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        batch.status = 'completed'
        batch.actual_end_date = timezone.now()
        batch.actual_quantity_completed = data.get('actual_quantity_completed', batch.planned_quantity)
        batch.scrap_quantity = data.get('scrap_quantity', 0)
        batch.progress_percentage = 100
        batch.save()
        
        # Check if MO is fully completed
        mo = batch.mo
        total_completed = sum(b.actual_quantity_completed for b in mo.batches.filter(status='completed'))
        if total_completed >= mo.quantity:
            mo.status = 'completed'
            mo.actual_end_date = timezone.now()
            mo.save()
            
            # Create status history
            MOStatusHistory.objects.create(
                mo=mo,
                from_status='in_progress',
                to_status='completed',
                changed_by=request.user,
                notes=f'MO completed with batch: {batch.batch_id}'
            )
        
        serializer = self.get_serializer(batch)
        return Response(serializer.data)

    @action(detail=True, methods=['patch'])
    def update_progress(self, request, pk=None):
        """Update batch progress"""
        batch = self.get_object()
        data = request.data
        
        if 'progress_percentage' in data:
            progress = float(data['progress_percentage'])
            if not (0 <= progress <= 100):
                return Response(
                    {'error': 'Progress must be between 0 and 100'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            batch.progress_percentage = progress
        
        if 'actual_quantity_completed' in data:
            batch.actual_quantity_completed = data['actual_quantity_completed']
        
        if 'notes' in data:
            batch.notes = data['notes']
        
        batch.save()
        
        serializer = self.get_serializer(batch)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get batch statistics"""
        queryset = self.get_queryset()
        
        stats = {
            'total': queryset.count(),
            'created': queryset.filter(status='created').count(),
            'in_process': queryset.filter(status='in_process').count(),
            'completed': queryset.filter(status='completed').count(),
            'overdue': sum(1 for batch in queryset if batch.is_overdue)
        }
        
        return Response(stats)
    
    @action(detail=True, methods=['post'])
    def move_to_packing(self, request, pk=None):
        """
        Move batch to packing zone after all processes are completed
        """
        batch = self.get_object()
        
        if batch.status != 'completed':
            return Response(
                {'error': 'Batch must be completed before moving to packing'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create inventory transaction for movement to packing
        try:
            inv_transaction = InventoryTransactionManager.create_packing_transaction(
                batch, request.user
            )
            
            # Update batch status and notes
            batch.status = 'completed'  # Keep as completed until packing is done
            batch.notes = (batch.notes or "") + f"\nMoved to Packing Zone at {timezone.now()}"
            batch.save()
            
            serializer = self.get_serializer(batch)
            return Response({
                'message': f'Batch {batch.batch_id} moved to Packing Zone',
                'batch': serializer.data,
                'transaction_id': inv_transaction.transaction_id
            })
        except Exception as e:
            return Response(
                {'error': f'Failed to move to packing: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def move_to_fg_store(self, request, pk=None):
        """
        Move batch to FG Store (Finished Goods) after packing is completed
        This is now the final step after mandatory packing
        """
        batch = self.get_object()
        
        # Check if batch is in packing zone (mandatory step completed)
        from inventory.models import ProductLocation, Location
        try:
            packing_location = Location.objects.get(code='PACKING_ZONE')
            batch_location = ProductLocation.objects.filter(
                batch=batch,
                current_location=packing_location
            ).first()
            
            if not batch_location:
                return Response(
                    {'error': 'Batch must be in packing zone before moving to FG Store. Please complete packing first.'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Location.DoesNotExist:
            return Response(
                {'error': 'Packing zone location not found in system'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        if batch.status not in ['completed', 'packed']:
            return Response(
                {'error': 'Batch must be completed and packed before moving to FG Store'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create inventory transaction for movement to FG Store
        try:
            inv_transaction = InventoryTransactionManager.create_fg_store_transaction(
                batch, request.user
            )
            
            # Update batch status to packed and notes
            batch.status = 'packed'
            batch.notes = (batch.notes or "") + f"\nMoved to FG Store at {timezone.now()} - Ready for dispatch"
            batch.save()
            
            serializer = self.get_serializer(batch)
            return Response({
                'message': f'Batch {batch.batch_id} moved to FG Store - Ready for dispatch',
                'batch': serializer.data,
                'transaction_id': inv_transaction.transaction_id
            })
        except Exception as e:
            return Response(
                {'error': f'Failed to move to FG Store: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def current_location(self, request):
        """
        Get current location for a batch
        Query params: batch_id
        """
        batch_id = request.query_params.get('batch_id')
        if not batch_id:
            return Response(
                {'error': 'batch_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            batch = Batch.objects.get(id=batch_id)
            location_info = InventoryTransactionManager.get_batch_current_location(batch)
            
            return Response({
                'batch_id': batch.batch_id,
                'mo_id': batch.mo.mo_id,
                'location': location_info
            })
        except Batch.DoesNotExist:
            return Response(
                {'error': 'Batch not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )


class OutsourcingRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Outsourcing Requests
    Supervisors can create/edit their own requests, managers/PH can view all
    """
    permission_classes = [IsManagerOrSupervisor]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'vendor', 'created_by']
    search_fields = ['request_id', 'vendor__name', 'vendor_contact_person']
    ordering_fields = ['created_at', 'expected_return_date', 'date_sent']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Filter queryset based on user role"""
        queryset = OutsourcingRequest.objects.select_related(
            'vendor', 'created_by', 'collected_by'
        ).prefetch_related('items')
        
        # Check user role
        user_role = self._get_user_role(self.request.user)
        
        # Supervisors can only see their own requests
        if user_role == 'supervisor':
            queryset = queryset.filter(created_by=self.request.user)
        
        # Managers and Production Heads can see all requests
        return queryset
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return OutsourcingRequestListSerializer
        return OutsourcingRequestDetailSerializer
    
    def _get_user_role(self, user):
        """Get user role with caching"""
        from django.core.cache import cache
        cache_key = f'user_role_{user.id}'
        user_role = cache.get(cache_key)
        
        if not user_role:
            active_role = user.user_roles.filter(is_active=True).select_related('role').first()
            user_role = active_role.role.name if active_role else None
            cache.set(cache_key, user_role, 300)
        
        return user_role
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """Send outsourcing request - creates OUT inventory transactions"""
        try:
            outsourcing_request = self.get_object()
        except OutsourcingRequest.DoesNotExist:
            return Response(
                {'error': 'Outsourcing request not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Validate request can be sent
        if outsourcing_request.status != 'draft':
            return Response(
                {'error': 'Only draft requests can be sent'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not outsourcing_request.items.exists():
            return Response(
                {'error': 'Request must have at least one item to send'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = OutsourcingRequestSendSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        validated_data = serializer.validated_data
        
        # Update request status and details
        outsourcing_request.status = 'sent'
        outsourcing_request.date_sent = validated_data['date_sent']
        outsourcing_request.vendor_contact_person = validated_data.get('vendor_contact_person', '')
        outsourcing_request.save()
        
        # Create OUT inventory transactions for each item
        try:
            from inventory.models import InventoryTransaction, Location
            from django.db import transaction
            
            with transaction.atomic():
                # Get default outgoing location (or create one if needed)
                outgoing_location = Location.objects.filter(
                    location_type='dispatch'
                ).first()
                
                if not outgoing_location:
                    # Create a default dispatch location if none exists
                    outgoing_location = Location.objects.create(
                        location_name='Dispatch Area',
                        location_type='dispatch',
                        is_active=True
                    )
                
                for item in outsourcing_request.items.all():
                    # Validate item has qty or kg
                    if not item.qty and not item.kg:
                        raise ValueError(f"Item {item.id} must have qty or kg")
                    
                    # Create OUT transaction
                    transaction_id = f"OUT-{outsourcing_request.request_id}-{item.id}"
                    
                    InventoryTransaction.objects.create(
                        transaction_id=transaction_id,
                        transaction_type='outward',
                        product_id=None,  # We don't have product FK, using product_code string
                        quantity=item.qty or item.kg or 0,
                        transaction_datetime=timezone.now(),
                        created_by=request.user,
                        reference_type='outsourcing',
                        reference_id=str(outsourcing_request.id),
                        notes=f"Outsourcing request {outsourcing_request.request_id} - {item.mo_number} - {item.product_code}",
                        location_from=outgoing_location
                    )
            
            return Response({
                'message': 'Request sent successfully',
                'status': 'sent',
                'date_sent': outsourcing_request.date_sent
            })
            
        except Exception as e:
            # Rollback request status if transaction creation fails
            outsourcing_request.status = 'draft'
            outsourcing_request.date_sent = None
            outsourcing_request.save()
            
            return Response(
                {'error': f'Failed to create inventory transactions: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def return_items(self, request, pk=None):
        """Mark outsourcing request as returned - creates IN inventory transactions"""
        try:
            outsourcing_request = self.get_object()
        except OutsourcingRequest.DoesNotExist:
            return Response(
                {'error': 'Outsourcing request not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Validate request can be returned
        if outsourcing_request.status != 'sent':
            return Response(
                {'error': 'Only sent requests can be marked as returned'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = OutsourcingRequestReturnSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        validated_data = serializer.validated_data
        
        try:
            from inventory.models import InventoryTransaction, Location
            from django.db import transaction
            
            with transaction.atomic():
                # Get default incoming location (or create one if needed)
                incoming_location = Location.objects.filter(
                    location_type='fg_store'
                ).first()
                
                if not incoming_location:
                    # Create a default FG store location if none exists
                    incoming_location = Location.objects.create(
                        location_name='Finished Goods Store',
                        location_type='fg_store',
                        is_active=True
                    )
                
                # Update returned quantities for items
                for returned_item_data in validated_data['returned_items']:
                    item_id = returned_item_data['id']
                    returned_qty = returned_item_data.get('returned_qty', 0)
                    returned_kg = returned_item_data.get('returned_kg', 0)
                    
                    try:
                        item = OutsourcedItem.objects.get(id=item_id, request=outsourcing_request)
                        
                        # Validate returned quantities don't exceed sent quantities
                        if item.qty and returned_qty > item.qty:
                            raise ValueError(f"Returned qty ({returned_qty}) cannot exceed sent qty ({item.qty}) for item {item.id}")
                        if item.kg and returned_kg > item.kg:
                            raise ValueError(f"Returned kg ({returned_kg}) cannot exceed sent kg ({item.kg}) for item {item.id}")
                        
                        # Update item returned quantities
                        item.returned_qty = returned_qty
                        item.returned_kg = returned_kg
                        item.save()
                        
                        # Create IN transaction
                        transaction_id = f"IN-{outsourcing_request.request_id}-{item.id}"
                        
                        InventoryTransaction.objects.create(
                            transaction_id=transaction_id,
                            transaction_type='inward',
                            product_id=None,  # We don't have product FK, using product_code string
                            quantity=returned_qty or returned_kg or 0,
                            transaction_datetime=timezone.now(),
                            created_by=request.user,
                            reference_type='outsourcing',
                            reference_id=str(outsourcing_request.id),
                            notes=f"Outsourcing return {outsourcing_request.request_id} - {item.mo_number} - {item.product_code}",
                            location_to=incoming_location
                        )
                        
                    except OutsourcedItem.DoesNotExist:
                        raise ValueError(f"Item {item_id} not found in this request")
                
                # Update request status
                outsourcing_request.status = 'returned'
                outsourcing_request.collection_date = validated_data['collection_date']
                outsourcing_request.collected_by_id = validated_data['collected_by_id']
                outsourcing_request.save()
            
            return Response({
                'message': 'Items returned successfully',
                'status': 'returned',
                'collection_date': outsourcing_request.collection_date
            })
            
        except Exception as e:
            return Response(
                {'error': f'Failed to process return: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Close outsourcing request"""
        try:
            outsourcing_request = self.get_object()
        except OutsourcingRequest.DoesNotExist:
            return Response(
                {'error': 'Outsourcing request not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Validate request can be closed
        if outsourcing_request.status != 'returned':
            return Response(
                {'error': 'Only returned requests can be closed'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        outsourcing_request.status = 'closed'
        outsourcing_request.save()
        
        return Response({
            'message': 'Request closed successfully',
            'status': 'closed'
        })
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get outsourcing summary statistics"""
        queryset = self.get_queryset()
        
        # Calculate summary stats
        total_requests = queryset.count()
        pending_returns = queryset.filter(status='sent').count()
        overdue_returns = queryset.filter(
            status='sent',
            expected_return_date__lt=timezone.now().date()
        ).count()
        
        # Recent requests (last 30 days)
        recent_requests = queryset.filter(
            created_at__gte=timezone.now() - timezone.timedelta(days=30)
        ).count()
        
        return Response({
            'total_requests': total_requests,
            'pending_returns': pending_returns,
            'overdue_returns': overdue_returns,
            'recent_requests': recent_requests
        })


# Enhanced Workflow API Views

from .services.workflow import ManufacturingWorkflowService
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ValidationError


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_mo_workflow(request):
    """
    Create MO and initialize approval workflow
    """
    try:
        mo_id = request.data.get('mo_id')
        if not mo_id:
            return Response({
                'success': False,
                'error': 'MO ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        workflow = ManufacturingWorkflowService.create_mo_workflow(mo_id, request.user)
        
        return Response({
            'success': True,
            'message': 'MO workflow created successfully',
            'workflow_id': workflow.id,
            'status': workflow.status
        }, status=status.HTTP_201_CREATED)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_mo(request):
    """
    Manager approves MO
    """
    try:
        mo_id = request.data.get('mo_id')
        approval_notes = request.data.get('approval_notes', '')
        
        if not mo_id:
            return Response({
                'success': False,
                'error': 'MO ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        workflow = ManufacturingWorkflowService.approve_mo(mo_id, request.user, approval_notes)
        
        return Response({
            'success': True,
            'message': 'MO approved successfully',
            'workflow_id': workflow.id,
            'status': workflow.status
        }, status=status.HTTP_200_OK)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def allocate_rm_to_mo(request):
    """
    RM Store allocates raw materials to MO
    """
    try:
        mo_id = request.data.get('mo_id')
        allocation_notes = request.data.get('allocation_notes', '')
        
        if not mo_id:
            return Response({
                'success': False,
                'error': 'MO ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        workflow = ManufacturingWorkflowService.allocate_rm_to_mo(mo_id, request.user, allocation_notes)
        
        return Response({
            'success': True,
            'message': 'RM allocated successfully',
            'workflow_id': workflow.id,
            'status': workflow.status
        }, status=status.HTTP_200_OK)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def assign_process_to_operator(request):
    """
    Production Head assigns process to operator
    """
    try:
        mo_process_execution_id = request.data.get('mo_process_execution_id')
        operator_id = request.data.get('operator_id')
        supervisor_id = request.data.get('supervisor_id')
        
        if not all([mo_process_execution_id, operator_id]):
            return Response({
                'success': False,
                'error': 'MO Process Execution ID and Operator ID are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        operator = User.objects.get(id=operator_id)
        supervisor = User.objects.get(id=supervisor_id) if supervisor_id else None
        
        assignment = ManufacturingWorkflowService.assign_process_to_operator(
            mo_process_execution_id, operator, request.user, supervisor
        )
        
        return Response({
            'success': True,
            'message': 'Process assigned successfully',
            'assignment_id': assignment.id,
            'status': assignment.status
        }, status=status.HTTP_201_CREATED)
        
    except User.DoesNotExist:
        return Response({
            'success': False,
            'error': 'User not found'
        }, status=status.HTTP_400_BAD_REQUEST)
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reassign_process(request):
    """
    Production Head reassigns process to different operator
    """
    try:
        assignment_id = request.data.get('assignment_id')
        new_operator_id = request.data.get('new_operator_id')
        reassignment_reason = request.data.get('reassignment_reason', '')
        
        if not all([assignment_id, new_operator_id]):
            return Response({
                'success': False,
                'error': 'Assignment ID and New Operator ID are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        new_operator = User.objects.get(id=new_operator_id)
        
        assignment = ManufacturingWorkflowService.reassign_process(
            assignment_id, new_operator, request.user, reassignment_reason
        )
        
        return Response({
            'success': True,
            'message': 'Process reassigned successfully',
            'assignment_id': assignment.id,
            'status': assignment.status
        }, status=status.HTTP_200_OK)
        
    except User.DoesNotExist:
        return Response({
            'success': False,
            'error': 'User not found'
        }, status=status.HTTP_400_BAD_REQUEST)
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_available_heat_numbers_for_mo(request, mo_id):
    """
    Get available heat numbers for an MO's material
    """
    try:
        mo = ManufacturingOrder.objects.select_related('product_code__material').get(id=mo_id)
        material = mo.product_code.material
        
        if not material:
            return Response({
                'success': False,
                'error': 'MO product has no material assigned'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get available heat numbers for this material
        heat_numbers = HeatNumber.objects.filter(
            raw_material=material,
            is_available=True
        ).select_related('grm_receipt', 'raw_material').order_by('-created_at')
        
        # Serialize heat number data with coil information
        heat_numbers_data = []
        for heat in heat_numbers:
            available_qty = heat.get_available_quantity_kg()
            if available_qty > 0:  # Only show heat numbers with available quantity
                # Parse items JSONField to get coil/sheet information
                coils = []
                if heat.items:
                    for item in heat.items:
                        coils.append({
                            'number': item.get('number', ''),
                            'weight': float(item.get('weight', 0))
                        })
                
                heat_numbers_data.append({
                    'id': heat.id,
                    'heat_number': heat.heat_number,
                    'grm_number': heat.grm_receipt.grm_number if heat.grm_receipt else None,
                    'total_weight_kg': float(heat.total_weight_kg or 0),
                    'consumed_quantity_kg': float(heat.consumed_quantity_kg),
                    'available_quantity_kg': float(available_qty),
                    'coils_received': heat.coils_received,
                    'sheets_received': heat.sheets_received,
                    'coils': coils,
                    'created_at': heat.created_at.isoformat()
                })
        
        return Response({
            'success': True,
            'heat_numbers': heat_numbers_data,
            'material': {
                'id': material.id,
                'material_code': material.material_code,
                'material_name': material.material_name,
                'material_type': material.material_type
            }
        })
        
    except ManufacturingOrder.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Manufacturing Order not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def allocate_batch_to_process(request):
    """
    RM Store allocates batch to specific process
    """
    try:
        batch_id = request.data.get('batch_id')
        process_id = request.data.get('process_id')
        operator_id = request.data.get('operator_id')
        heat_number_ids = request.data.get('heat_number_ids', [])
        
        if not all([batch_id, process_id, operator_id]):
            return Response({
                'success': False,
                'error': 'Batch ID, Process ID, and Operator ID are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        operator = User.objects.get(id=operator_id)
        heat_numbers = HeatNumber.objects.filter(id__in=heat_number_ids) if heat_number_ids else None
        
        allocation = ManufacturingWorkflowService.allocate_batch_to_process(
            batch_id, process_id, operator, request.user, heat_numbers
        )
        
        return Response({
            'success': True,
            'message': 'Batch allocated successfully',
            'allocation_id': allocation.id,
            'status': allocation.status
        }, status=status.HTTP_201_CREATED)
        
    except User.DoesNotExist:
        return Response({
            'success': False,
            'error': 'User not found'
        }, status=status.HTTP_400_BAD_REQUEST)
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def receive_batch_by_operator(request):
    """
    Operator receives batch and starts process
    """
    try:
        allocation_id = request.data.get('allocation_id')
        location = request.data.get('location', '')
        
        if not allocation_id:
            return Response({
                'success': False,
                'error': 'Allocation ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        allocation = ManufacturingWorkflowService.receive_batch_by_operator(
            allocation_id, request.user, location
        )
        
        return Response({
            'success': True,
            'message': 'Batch received successfully',
            'allocation_id': allocation.id,
            'status': allocation.status
        }, status=status.HTTP_200_OK)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_process(request):
    """
    Operator completes process
    """
    try:
        allocation_id = request.data.get('allocation_id')
        completion_notes = request.data.get('completion_notes', '')
        quantity_processed = request.data.get('quantity_processed')
        
        if not allocation_id:
            return Response({
                'success': False,
                'error': 'Allocation ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        allocation = ManufacturingWorkflowService.complete_process(
            allocation_id, request.user, completion_notes, quantity_processed
        )
        
        return Response({
            'success': True,
            'message': 'Process completed successfully',
            'allocation_id': allocation.id,
            'status': allocation.status
        }, status=status.HTTP_200_OK)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_finished_goods(request):
    """
    Quality check for finished goods
    """
    try:
        batch_id = request.data.get('batch_id')
        quality_notes = request.data.get('quality_notes', '')
        passed = request.data.get('passed', True)
        
        if not batch_id:
            return Response({
                'success': False,
                'error': 'Batch ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        fg_verification = ManufacturingWorkflowService.verify_finished_goods(
            batch_id, request.user, quality_notes, passed
        )
        
        return Response({
            'success': True,
            'message': 'FG verification completed successfully',
            'verification_id': fg_verification.id,
            'status': fg_verification.status
        }, status=status.HTTP_200_OK)
        
    except ValidationError as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Raw Material Allocation API Views

class RawMaterialAllocationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for managing raw material allocations
    
    Provides endpoints for:
    - Viewing RM allocations
    - Checking RM availability
    - Swapping RM allocations between MOs
    - Getting allocation history
    """
    queryset = RawMaterialAllocation.objects.all().select_related(
        'mo', 'raw_material', 'swapped_to_mo', 'allocated_by', 'locked_by', 'swapped_by'
    ).prefetch_related('history')
    serializer_class = RawMaterialAllocationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['mo', 'raw_material', 'status', 'can_be_swapped']
    search_fields = ['mo__mo_id', 'raw_material__material_code', 'raw_material__material_name']
    ordering_fields = ['allocated_at', 'locked_at', 'swapped_at']
    ordering = ['-allocated_at']
    
    @action(detail=False, methods=['get'])
    def by_mo(self, request):
        """Get all RM allocations for a specific MO"""
        mo_id = request.query_params.get('mo_id')
        if not mo_id:
            return Response(
                {'error': 'mo_id query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            mo = ManufacturingOrder.objects.get(id=mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'Manufacturing Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get all allocations for this MO (including reserved, locked, swapped, released)
        allocations = self.queryset.filter(mo=mo)
        # Use minimal serializer for production-head MO detail page to reduce payload
        from .serializers import RawMaterialAllocationMinimalSerializer
        serializer = RawMaterialAllocationMinimalSerializer(allocations, many=True)
        
        # DEBUG: Log allocation details
        logger.info(f"[DEBUG] by_mo API - MO {mo.mo_id} - Total allocations: {allocations.count()}")
        for alloc in allocations:
            logger.info(f"[DEBUG]   - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg}kg, Material: {alloc.raw_material.material_code}")
        
        # Get allocation summary
        from manufacturing.services.rm_allocation import RMAllocationService
        summary = RMAllocationService.get_allocation_summary_for_mo(mo)
        
        # Calculate allocation status breakdown
        allocation_statuses = {
            'reserved': allocations.filter(status='reserved').count(),
            'locked': allocations.filter(status='locked').count(),
            'swapped': allocations.filter(status='swapped').count(),
            'released': allocations.filter(status='released').count(),
        }
        
        # Check if all required RM is fully reserved (production ready)
        total_reserved = sum(
            float(alloc.allocated_quantity_kg) 
            for alloc in allocations.filter(status='reserved')
        )
        total_locked = sum(
            float(alloc.allocated_quantity_kg) 
            for alloc in allocations.filter(status='locked')
        )
        total_reserved_locked = total_reserved + total_locked
        required_kg = float(mo.rm_required_kg) if mo.rm_required_kg else 0
        is_fully_reserved = total_reserved >= required_kg if required_kg > 0 else False
        
        logger.info(f"[DEBUG] by_mo API - MO {mo.mo_id} - Reserved: {total_reserved}kg, Locked: {total_locked}kg, Required: {required_kg}kg, Fully Reserved: {is_fully_reserved}")
        
        return Response({
            'mo_id': mo.mo_id,
            'mo_priority': mo.priority,
            'mo_status': mo.status,
            'required_rm_kg': required_kg,
            'allocations': serializer.data,
            'allocation_count': len(serializer.data),
            'allocation_statuses': allocation_statuses,
            'is_fully_reserved': is_fully_reserved,
            'total_reserved_kg': total_reserved,
            'total_locked_kg': total_locked,
            'total_reserved_locked_kg': total_reserved_locked,
            'summary': summary
        })
    
    @action(detail=False, methods=['post'])
    def check_availability(self, request):
        """Check RM availability for an MO"""
        serializer = RMAllocationCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        mo_id = serializer.validated_data['mo_id']
        mo = ManufacturingOrder.objects.get(id=mo_id)
        
        from manufacturing.services.rm_allocation import RMAllocationService
        availability = RMAllocationService.check_rm_availability_for_mo(mo)
        
        return Response(availability)
    
    @action(detail=False, methods=['get'])
    def swappable_allocations(self, request):
        """Find RM allocations that can be swapped to a target MO"""
        target_mo_id = request.query_params.get('target_mo_id')
        if not target_mo_id:
            return Response(
                {'error': 'target_mo_id query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            target_mo = ManufacturingOrder.objects.get(id=target_mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'Target Manufacturing Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        from manufacturing.services.rm_allocation import RMAllocationService
        swappable = RMAllocationService.find_swappable_allocations(target_mo)
        serializer = self.get_serializer(swappable, many=True)
        
        return Response({
            'target_mo_id': target_mo.mo_id,
            'target_mo_priority': target_mo.priority,
            'swappable_allocations': serializer.data,
            'total_swappable_quantity_kg': sum(alloc.allocated_quantity_kg for alloc in swappable)
        })
    
    @action(detail=True, methods=['post'])
    def swap(self, request, pk=None):
        """Swap this allocation to another MO"""
        allocation = self.get_object()
        serializer = RMAllocationSwapSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        target_mo_id = serializer.validated_data['target_mo_id']
        reason = serializer.validated_data.get('reason', '')
        
        try:
            target_mo = ManufacturingOrder.objects.get(id=target_mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'Target Manufacturing Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        success, message = allocation.swap_to_mo(target_mo, request.user, reason)
        
        if success:
            # Create history record
            RMAllocationHistory.objects.create(
                allocation=allocation,
                action='swapped',
                from_mo=allocation.mo,
                to_mo=target_mo,
                quantity_kg=allocation.allocated_quantity_kg,
                performed_by=request.user,
                reason=reason or f"Manual swap to {target_mo.mo_id}"
            )
            
            return Response({
                'success': True,
                'message': message,
                'allocation': self.get_serializer(allocation).data
            })
        else:
            return Response(
                {'error': message},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['post'])
    def auto_swap(self, request):
        """Automatically swap RM allocations from lower priority MOs to target MO"""
        target_mo_id = request.data.get('target_mo_id')
        if not target_mo_id:
            return Response(
                {'error': 'target_mo_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            target_mo = ManufacturingOrder.objects.get(id=target_mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'Target Manufacturing Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        from manufacturing.services.rm_allocation import RMAllocationService
        result = RMAllocationService.auto_swap_allocations(target_mo, request.user)
        
        if result['success']:
            return Response(result)
        else:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        """Lock an allocation (when MO is approved)"""
        allocation = self.get_object()
        
        success = allocation.lock_allocation(request.user)
        
        if success:
            # Create history record
            RMAllocationHistory.objects.create(
                allocation=allocation,
                action='locked',
                from_mo=None,
                to_mo=allocation.mo,
                quantity_kg=allocation.allocated_quantity_kg,
                performed_by=request.user,
                reason=f"MO {allocation.mo.mo_id} approved"
            )
            
            return Response({
                'success': True,
                'message': f'Allocation locked for MO {allocation.mo.mo_id}',
                'allocation': self.get_serializer(allocation).data
            })
        else:
            return Response(
                {'error': 'Allocation is already locked'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        """Release an allocation back to stock"""
        allocation = self.get_object()
        reason = request.data.get('reason', '')
        
        success = allocation.release_allocation()
        
        if success:
            # Create history record
            RMAllocationHistory.objects.create(
                allocation=allocation,
                action='released',
                from_mo=allocation.mo,
                to_mo=None,
                quantity_kg=allocation.allocated_quantity_kg,
                performed_by=request.user,
                reason=reason or f"MO {allocation.mo.mo_id} cancelled"
            )
            
            return Response({
                'success': True,
                'message': f'Allocation released for MO {allocation.mo.mo_id}',
                'allocation': self.get_serializer(allocation).data
            })
        else:
            return Response(
                {'error': 'Failed to release allocation'},
                status=status.HTTP_400_BAD_REQUEST
            )
