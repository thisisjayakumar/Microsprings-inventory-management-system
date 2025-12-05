from rest_framework import viewsets, status, filters, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Prefetch, Sum
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal

from products.models import Product
from .models import (
    RMStockBalance, RawMaterial, InventoryTransaction,
    GRMReceipt, HeatNumber, RMStockBalanceHeat, InventoryTransactionHeat,
    HandoverIssue, RMReturn
)
from .serializers import (
    ProductListSerializer, ProductCreateUpdateSerializer,
    RMStockBalanceSerializer, RMStockBalanceUpdateSerializer,
    ProductStockDashboardSerializer, RawMaterialBasicSerializer,
    InventoryTransactionSerializer, GRMReceiptSerializer, GRMReceiptCreateSerializer,
    GRMReceiptListSerializer, HeatNumberSerializer, RMStockBalanceHeatSerializer,
    InventoryTransactionHeatSerializer, HeatNumberHandoverSerializer,
    HandoverIssueSerializer, HandoverIssueCreateSerializer, HandoverVerifySerializer,
    RMReturnSerializer, RMReturnCreateSerializer, RMReturnDispositionSerializer,
    MaterialHeatNumbersResponseSerializer, DeleteCoilsRequestSerializer,
    DeleteCoilsResponseSerializer
)
from .transaction_manager import InventoryTransactionManager
from authentication.models import UserRole, Role

User = get_user_model()


class IsRMStoreUser(permissions.BasePermission):
    """
    Custom permission to only allow RM Store users to access inventory management
    """
    
    def has_permission(self, request, view):
        import logging
        logger = logging.getLogger(__name__)
        
        if not request.user or not request.user.is_authenticated:
            logger.warning(f"IsRMStoreUser: User not authenticated - User: {request.user}")
            return False
        
        # Check if user has rm_store role
        try:
            user_roles = UserRole.objects.filter(
                user=request.user, 
                is_active=True,
                role__name='rm_store'
            ).exists()
            
            logger.info(f"IsRMStoreUser check - User: {request.user.username}, Has rm_store role: {user_roles}")
            return user_roles
        except Exception as e:
            logger.error(f"IsRMStoreUser: Exception checking roles - {str(e)}")
            return False


class IsRMStoreOrProductionHead(permissions.BasePermission):
    """
    Custom permission to allow RM Store users and Production Head to access inventory transactions
    """
    
    def has_permission(self, request, view):
        import logging
        logger = logging.getLogger(__name__)
        
        if not request.user or not request.user.is_authenticated:
            logger.warning(f"IsRMStoreOrProductionHead: User not authenticated")
            return False
        
        # Check if user has rm_store or production_head role
        try:
            user_roles = UserRole.objects.filter(
                user=request.user, 
                is_active=True,
                role__name__in=['rm_store', 'production_head']
            ).exists()
            
            return user_roles
        except Exception as e:
            logger.error(f"IsRMStoreOrProductionHead: Exception checking roles - {str(e)}")
            return False


class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Product management - accessible by RM Store users
    """
    permission_classes = [IsAuthenticated, IsRMStoreUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['product_type', 'spring_type']
    search_fields = ['product_code', 'internal_product_code', 'material__material_name']
    ordering_fields = ['product_code', 'internal_product_code', 'created_at']
    ordering = ['internal_product_code']
    
    def get_queryset(self):
        """Optimized queryset with prefetch_related for material stock balances"""
        return Product.objects.select_related('material', 'customer_c_id', 'created_by').prefetch_related(
            Prefetch('material__stock_balances', queryset=RMStockBalance.objects.all())
        )
    
    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateUpdateSerializer
        return ProductListSerializer
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Get products with stock information for RM Store dashboard"""
        products = self.get_queryset()
        serializer = ProductStockDashboardSerializer(products, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dropdown(self, request):
        """Get products dropdown for selection"""
        products = Product.objects.all().order_by('internal_product_code')
        data = [
            {
                'id': product.id,
                'internal_product_code': product.internal_product_code,
                'product_code': product.product_code,
                'display_name': f"{product.internal_product_code} - {product.product_code}"
            }
            for product in products
        ]
        return Response(data)


class RMStockBalanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for RMStockBalance management - accessible by RM Store users
    """
    permission_classes = [IsAuthenticated, IsRMStoreUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['raw_material__material_code', 'raw_material__material_name']
    ordering_fields = ['available_quantity', 'last_updated']
    ordering = ['-last_updated']
    
    def get_queryset(self):
        """Optimized queryset with select_related"""
        return RMStockBalance.objects.select_related('raw_material')
    
    def get_serializer_class(self):
        """Use RMStockBalanceSerializer for all operations"""
        return RMStockBalanceSerializer
    
    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        """
        Bulk update stock balances using material_code
        Expected payload: [{"material_code": "RM001", "available_quantity": 100}, ...]
        """
        serializer = RMStockBalanceUpdateSerializer(data=request.data, many=True)
        if serializer.is_valid():
            updated_records = []
            
            with transaction.atomic():
                for item in serializer.validated_data:
                    raw_material = RawMaterial.objects.only("id").get(
                        material_code=item['material_code']
                    )
                    
                    # Use update_or_create for upsert behavior
                    stock_balance, created = RMStockBalance.objects.update_or_create(
                        raw_material=raw_material,
                        defaults={'available_quantity': item['available_quantity']}
                    )
                    
                    updated_records.append({
                        'material_code': item['material_code'],
                        'available_quantity': stock_balance.available_quantity,
                        'created': created,
                        'last_updated': stock_balance.last_updated
                    })
            
            return Response({
                'message': f'Successfully updated {len(updated_records)} stock records',
                'updated_records': updated_records
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'])
    def update_by_material_code(self, request):
        """
        Update single stock balance using material_code
        Expected payload: {"material_code": "RM001", "available_quantity": 100}
        """
        serializer = RMStockBalanceUpdateSerializer(data=request.data)
        if serializer.is_valid():
            raw_material = RawMaterial.objects.only("id").get(
                material_code=serializer.validated_data['material_code']
            )
            
            # Use update_or_create for upsert behavior
            stock_balance, created = RMStockBalance.objects.update_or_create(
                raw_material=raw_material,
                defaults={'available_quantity': serializer.validated_data['available_quantity']}
            )
            
            return Response({
                'message': 'Stock balance updated successfully',
                'material_code': serializer.validated_data['material_code'],
                'available_quantity': stock_balance.available_quantity,
                'created': created,
                'last_updated': stock_balance.last_updated
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RawMaterialViewSet(viewsets.ModelViewSet):
    """
    ViewSet for RawMaterial - READ for all authenticated users, CREATE/UPDATE/DELETE for RM Store users
    """
    queryset = RawMaterial.objects.all()
    pagination_class = None
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['material_type', 'material_name']
    search_fields = ['material_code', 'material_name', 'grade']
    ordering_fields = ['material_code', 'material_name']
    ordering = ['material_code']
    
    def get_permissions(self):
        """Allow read for all authenticated users, but create/update/delete only for RM Store users"""
        if self.action in ['list', 'retrieve', 'dropdown']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsRMStoreUser()]
    
    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action in ['create', 'update', 'partial_update']:
            from .serializers import RawMaterialCreateSerializer
            return RawMaterialCreateSerializer
        from .serializers import RawMaterialBasicSerializer
        return RawMaterialBasicSerializer
    
    @action(detail=False, methods=['get'])
    def dropdown(self, request):
        """Get raw materials dropdown for product creation"""
        materials = self.get_queryset()
        data = [
            {
                'id': material.id,
                'material_code': material.material_code,
                'material_name': material.material_name,
                'material_type': material.material_type,
                'display_name': f"{material.material_code} - {material.material_name}"
            }
            for material in materials
        ]
        return Response(data)


class InventoryTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing inventory transactions
    Accessible by RM Store users and Production Head
    """
    permission_classes = [IsAuthenticated, IsRMStoreOrProductionHead]
    serializer_class = InventoryTransactionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['transaction_type', 'reference_type']
    search_fields = ['transaction_id', 'product__product_code', 'manufacturing_order__mo_id']
    ordering_fields = ['transaction_datetime', 'created_at']
    ordering = ['-transaction_datetime']

    def get_queryset(self):
        queryset = InventoryTransaction.objects.select_related(
            'product', 'manufacturing_order', 'location_from', 'location_to', 'created_by'
        )

        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(transaction_datetime__gte=start_date)
        if end_date:
            queryset = queryset.filter(transaction_datetime__lte=end_date)

        return queryset


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint for inventory service
    """
    return Response({'status': 'healthy', 'message': 'Inventory service is running'})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsRMStoreUser])
def rm_store_dashboard_stats(request):
    """
    Get dashboard statistics for RM Store users - Raw Materials focused
    """
    try:
        total_raw_materials = RawMaterial.objects.count()
        materials_with_stock = RMStockBalance.objects.filter(available_quantity__gt=0).count()
        materials_out_of_stock = RMStockBalance.objects.filter(available_quantity=0).count()
        materials_no_stock_record = total_raw_materials - RMStockBalance.objects.count()
        
        # Material type breakdown
        total_coils = RawMaterial.objects.filter(material_type='coil').count()
        total_sheets = RawMaterial.objects.filter(material_type='sheet').count()
        
        return Response({
            'total_raw_materials': total_raw_materials,
            'materials_with_stock': materials_with_stock,
            'materials_out_of_stock': materials_out_of_stock,
            'materials_no_stock_record': materials_no_stock_record,
            'total_stock_records': RMStockBalance.objects.count(),
            'total_coils': total_coils,
            'total_sheets': total_sheets
        })
    except Exception as e:
        return Response(
            {'error': 'Failed to fetch dashboard stats', 'detail': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([AllowAny])  # Temporarily allow any access for testing
def test_grm_api(request):
    """Test endpoint to check if GRM API is working"""
    try:
        # Simple test without complex queries
        return Response({
            'message': 'GRM API is working',
            'status': 'success',
            'timestamp': timezone.now().isoformat(),
            'user': request.user.username if request.user.is_authenticated else 'anonymous'
        })
    except Exception as e:
        return Response({
            'error': 'GRM API test failed',
            'detail': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([AllowAny])  # Temporarily allow any access for testing
def test_grm_models(request):
    """Test endpoint to check if GRM models are accessible"""
    try:
        grm_count = GRMReceipt.objects.count()
        heat_count = HeatNumber.objects.count()
        return Response({
            'message': 'GRM Models are accessible',
            'grm_receipts_count': grm_count,
            'heat_numbers_count': heat_count,
            'status': 'success'
        })
    except Exception as e:
        return Response({
            'error': 'GRM Models test failed',
            'detail': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# GRM and Heat Number ViewSets

class GRMReceiptViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing GRM Receipts
    """
    permission_classes = [IsAuthenticated, IsRMStoreUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'quality_check_passed', 'received_by']
    search_fields = ['grm_number', 'truck_number', 'driver_name', 'purchase_order__po_id']
    ordering_fields = ['receipt_date', 'created_at', 'grm_number']
    ordering = ['-receipt_date']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return GRMReceiptCreateSerializer
        elif self.action == 'list':
            return GRMReceiptListSerializer
        return GRMReceiptSerializer
    
    def get_queryset(self):
        return GRMReceipt.objects.select_related(
            'purchase_order', 'purchase_order__vendor_name', 'received_by', 'quality_check_by'
        ).prefetch_related('heat_numbers', 'heat_numbers__raw_material')
    
    @action(detail=True, methods=['post'])
    def complete_receipt(self, request, pk=None):
        """Mark GRM receipt as completed"""
        grm_receipt = self.get_object()
        
        if grm_receipt.status == 'completed':
            return Response({'error': 'GRM receipt is already completed'}, 
                          status=status.HTTP_400_BAD_REQUEST)
        
        # Validate that GRM's total weight equals received quantity (with tolerance for rounding)
        from decimal import Decimal
        total_weight_from_heat_numbers = Decimal('0')
        for heat_number in grm_receipt.heat_numbers.all():
            weight = heat_number.total_weight_kg or Decimal('0')
            total_weight_from_heat_numbers += Decimal(str(weight))
        
        received_qty = Decimal(str(grm_receipt.purchase_order.quantity_received or 0))
        
        # Allow 0.01 kg tolerance for rounding differences
        difference = abs(received_qty - total_weight_from_heat_numbers)
        if difference > Decimal('0.01'):
            return Response({
                'error': f'GRM total weight ({float(total_weight_from_heat_numbers):.2f} kg) does not match received quantity ({float(received_qty):.2f} kg)'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        grm_receipt.status = 'completed'
        grm_receipt.save()
        
        # Update PO status to completed
        po = grm_receipt.purchase_order
        po.status = 'rm_completed'
        po.save()
        
        # When PO is completed, update related MO to clear RM pending status
        if po.related_mo:
            mo = po.related_mo
            mo.rm_pending_from_po = False
            mo.pending_po_quantity_kg = None
            mo.save()
            
            # Create transaction history for MO
            try:
                from manufacturing.core_models import MOTransactionHistory
                from inventory.utils import generate_transaction_id
                transaction_id = generate_transaction_id('MO_RM_PO_COMPLETED')
                MOTransactionHistory.objects.create(
                    mo=mo,
                    transaction_type='rm_po_completed',
                    transaction_id=transaction_id,
                    description=f'RM from PO {po.po_id} is now available for MO {mo.mo_id}',
                    details={
                        'po_id': po.po_id,
                        'po_quantity_received': float(po.quantity_received) if po.quantity_received else None,
                        'mo_id': mo.mo_id,
                    },
                    created_by=request.user
                )
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to create MO transaction history for PO completion: {str(e)}")
        
        # Update stock balances for all heat numbers
        for heat_number in grm_receipt.heat_numbers.all():
            heat_number.update_stock_balance()
        
        # Create inventory transactions for GRM completion
        try:
            InventoryTransactionManager.create_grm_completion_transaction(
                grm_receipt, request.user
            )
        except Exception as e:
            print(f"Error creating GRM completion transaction: {e}")
            # Don't fail the completion if transaction creation fails
        
        serializer = self.get_serializer(grm_receipt)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def quality_check(self, request, pk=None):
        """Perform quality check on GRM receipt"""
        grm_receipt = self.get_object()
        
        quality_passed = request.data.get('quality_check_passed', False)
        grm_receipt.quality_check_passed = quality_passed
        grm_receipt.quality_check_by = request.user
        grm_receipt.quality_check_date = timezone.now()
        grm_receipt.save()
        
        serializer = self.get_serializer(grm_receipt)
        return Response(serializer.data)


class HeatNumberViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Heat Numbers
    """
    permission_classes = [IsAuthenticated, IsRMStoreUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_available', 'raw_material__material_type', 'grm_receipt__status']
    search_fields = ['heat_number', 'raw_material__material_code', 'grm_receipt__grm_number']
    ordering_fields = ['created_at', 'heat_number', 'total_weight_kg']
    ordering = ['-created_at']
    
    def get_queryset(self):
        return HeatNumber.objects.select_related(
            'grm_receipt', 'raw_material'
        )
    
    @action(detail=True, methods=['post'])
    def consume_quantity(self, request, pk=None):
        """Consume quantity from heat number"""
        heat_number = self.get_object()
        
        quantity_kg = request.data.get('quantity_kg', 0)
        if quantity_kg <= 0:
            return Response({'error': 'Quantity must be greater than 0'}, 
                          status=status.HTTP_400_BAD_REQUEST)
        
        available_quantity = heat_number.get_available_quantity_kg()
        if quantity_kg > available_quantity:
            return Response({'error': 'Insufficient quantity available'}, 
                          status=status.HTTP_400_BAD_REQUEST)
        
        heat_number.consumed_quantity_kg += quantity_kg
        heat_number.save()
        
        # Update stock balance
        heat_number.update_stock_balance()
        
        serializer = self.get_serializer(heat_number)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def update_location(self, request, pk=None):
        """Update storage location for heat number"""
        heat_number = self.get_object()
        
        storage_location = request.data.get('storage_location', '')
        rack_number = request.data.get('rack_number', '')
        shelf_number = request.data.get('shelf_number', '')
        
        heat_number.storage_location = storage_location
        heat_number.rack_number = rack_number
        heat_number.shelf_number = shelf_number
        heat_number.save()
        
        serializer = self.get_serializer(heat_number)
        return Response(serializer.data)


class RMStockBalanceHeatViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Heat-tracked stock balances
    """
    permission_classes = [IsAuthenticated, IsRMStoreUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['raw_material__material_type']
    search_fields = ['raw_material__material_code', 'raw_material__material_name']
    ordering_fields = ['last_updated', 'total_available_quantity_kg']
    ordering = ['-last_updated']
    
    def get_queryset(self):
        return RMStockBalanceHeat.objects.select_related('raw_material')
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics for heat-tracked stock"""
        try:
            total_materials = RMStockBalanceHeat.objects.count()
            materials_with_stock = RMStockBalanceHeat.objects.filter(
                total_available_quantity_kg__gt=0
            ).count()
            materials_out_of_stock = RMStockBalanceHeat.objects.filter(
                total_available_quantity_kg=0
            ).count()
            
            # Total quantities
            total_kg = RMStockBalanceHeat.objects.aggregate(
                total=Sum('total_available_quantity_kg')
            )['total'] or 0
            
            total_coils = RMStockBalanceHeat.objects.aggregate(
                total=Sum('total_coils_available')
            )['total'] or 0
            
            total_sheets = RMStockBalanceHeat.objects.aggregate(
                total=Sum('total_sheets_available')
            )['total'] or 0
            
            # Heat numbers count
            total_heat_numbers = HeatNumber.objects.filter(is_available=True).count()
            
            return Response({
                'total_materials': total_materials,
                'materials_with_stock': materials_with_stock,
                'materials_out_of_stock': materials_out_of_stock,
                'total_kg': float(total_kg),
                'total_coils': total_coils,
                'total_sheets': total_sheets,
                'total_heat_numbers': total_heat_numbers
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def refresh_balance(self, request, pk=None):
        """Refresh stock balance from heat numbers"""
        stock_balance = self.get_object()
        stock_balance.update_from_heat_numbers()
        
        serializer = self.get_serializer(stock_balance)
        return Response(serializer.data)


class InventoryTransactionHeatViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Heat-tracked inventory transactions
    Accessible by RM Store users and Production Head
    """
    permission_classes = [IsAuthenticated, IsRMStoreOrProductionHead]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['grm_number', 'heat_number__raw_material__material_type']
    search_fields = ['grm_number', 'heat_number__heat_number', 'inventory_transaction__transaction_id']
    ordering_fields = ['inventory_transaction__transaction_datetime']
    ordering = ['-inventory_transaction__transaction_datetime']
    
    def get_queryset(self):
        return InventoryTransactionHeat.objects.select_related(
            'inventory_transaction', 'heat_number', 'heat_number__raw_material',
            'heat_number__grm_receipt'
        )


class IsCoilingSupervisor(permissions.BasePermission):
    """
    Custom permission to only allow Coiling Supervisors to access handover verification
    """
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check if user has supervisor role and is assigned to coiling department
        try:
            user_roles = UserRole.objects.filter(
                user=request.user, 
                is_active=True,
                role__name='supervisor'
            ).exists()
            
            if user_roles:
                # Check if user is specifically assigned to coiling department
                from authentication.models import ProcessSupervisor
                coiling_supervisor = ProcessSupervisor.objects.filter(
                    supervisor=request.user,
                    department='coiling',
                    is_active=True
                ).exists()
                return coiling_supervisor
            
            return False
        except:
            return False


@api_view(['GET'])
@permission_classes([IsCoilingSupervisor])
def pending_handover_list(request):
    """
    Get list of heat numbers pending handover verification for Coiling department
    """
    try:
        # Get heat numbers with pending handover status
        heat_numbers = HeatNumber.objects.filter(
            handover_status='pending_handover',
            is_available=True
        ).select_related(
            'raw_material', 'grm_receipt', 'grm_receipt__purchase_order'
        ).order_by('-created_at')
        
        serializer = HeatNumberHandoverSerializer(heat_numbers, many=True)
        
        return Response({
            'success': True,
            'data': serializer.data,
            'count': heat_numbers.count()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsCoilingSupervisor])
def verify_handover(request):
    """
    Verify handover of raw material coil to Coiling department
    """
    try:
        serializer = HandoverVerifySerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            heat_number = serializer.save()
            
            return Response({
                'success': True,
                'message': f'Handover verified successfully for heat number {heat_number.heat_number}',
                'data': HeatNumberHandoverSerializer(heat_number).data
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsCoilingSupervisor])
def report_handover_issue(request):
    """
    Report an issue with raw material handover
    """
    try:
        serializer = HandoverIssueCreateSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            issue = serializer.save()
            
            return Response({
                'success': True,
                'message': f'Issue reported successfully for heat number {issue.heat_number.heat_number}',
                'data': HandoverIssueSerializer(issue).data
            }, status=status.HTTP_201_CREATED)
        else:
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsCoilingSupervisor])
def handover_issues_list(request):
    """
    Get list of reported handover issues
    """
    try:
        issues = HandoverIssue.objects.filter(
            is_resolved=False
        ).select_related(
            'heat_number', 'heat_number__raw_material', 'reported_by'
        ).order_by('-reported_at')
        
        serializer = HandoverIssueSerializer(issues, many=True)
        
        return Response({
            'success': True,
            'data': serializer.data,
            'count': issues.count()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# RM Return ViewSet

class RMReturnViewSet(viewsets.ModelViewSet):
    """
    ViewSet for RM Returns
    - Supervisors can create returns
    - RM Store users can view and process returns (set disposition)
    """
    queryset = RMReturn.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['disposition', 'returned_from_location', 'batch', 'manufacturing_order']
    search_fields = ['return_id', 'raw_material__material_code', 'batch__batch_id', 
                     'manufacturing_order__mo_id', 'return_reason']
    ordering_fields = ['returned_at', 'disposed_at', 'quantity_kg']
    ordering = ['-returned_at']
    
    def get_permissions(self):
        """
        - Supervisors can create (POST)
        - RM Store users can list, retrieve, and update disposition
        """
        if self.action == 'create':
            # Any authenticated supervisor can create returns
            return [IsAuthenticated()]
        else:
            # RM Store users can view and process
            return [IsAuthenticated(), IsRMStoreUser()]
    
    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action == 'create':
            return RMReturnCreateSerializer
        elif self.action == 'process_disposition':
            return RMReturnDispositionSerializer
        return RMReturnSerializer
    
    def get_queryset(self):
        """Optimized queryset with select_related"""
        return RMReturn.objects.select_related(
            'raw_material', 'heat_number', 'batch', 'manufacturing_order',
            'returned_from_location', 'returned_by', 'disposed_by', 'return_transaction'
        ).order_by('-returned_at')
    
    @action(detail=True, methods=['post'], url_path='process-disposition')
    def process_disposition(self, request, pk=None):
        """
        Process RM return disposition by RM Store
        
        Business Logic:
        1. Received Wrong RM (return_reason='received_wrong_rm'):
           - RM Store selects "Return to RM" (material can be used for another MO)
           - Enters actual received kg (may differ from supervisor's quantity)
           - Stock is adjusted by adding received_kg back to RM stock
        
        2. Enough Qty Reached (return_reason='enough_qty_reached'):
           - Supervisor returns excess RM after MO quantity is achieved
           - RM Store selects "Return to RM"
           - Enters actual received kg
           - Stock is adjusted by adding received_kg back to RM stock
        
        3. Defect in Quality (return_reason='defect_in_quality'):
           - RM is defective and cannot be reused
           - RM Store selects "Return to Vendor"
           - Enters actual received kg
           - This quantity is fully subtracted from RM stock (sent back to vendor)
           - This batch is NOT counted as part of allocated RM for the MO
        
        Only RM Store users can perform this action
        """
        rm_return = self.get_object()
        
        # Check if already processed
        if rm_return.disposition != 'pending':
            return Response({
                'success': False,
                'error': f'This return has already been processed with disposition: {rm_return.get_disposition_display()}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = self.get_serializer(rm_return, data=request.data, partial=True)
        
        if serializer.is_valid():
            updated_return = serializer.save()
            
            # Create a summary message based on return reason and disposition
            if updated_return.disposition == 'return_to_rm':
                message = f'{updated_return.received_kg}kg added back to RM stock'
            else:  # return_to_vendor
                message = f'{updated_return.received_kg}kg deducted from stock (returning to vendor)'
            
            return Response({
                'success': True,
                'message': f'Return processed successfully. {message}',
                'data': RMReturnSerializer(updated_return).data
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'], url_path='pending')
    def pending_returns(self, request):
        """
        Get all pending RM returns (disposition = pending)
        """
        pending = self.get_queryset().filter(disposition='pending')
        
        serializer = self.get_serializer(pending, many=True)
        
        return Response({
            'success': True,
            'data': serializer.data,
            'count': pending.count()
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='by-batch')
    def by_batch(self, request):
        """
        Get RM returns for a specific batch
        """
        batch_id = request.query_params.get('batch_id')
        
        if not batch_id:
            return Response({
                'success': False,
                'error': 'batch_id parameter is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        returns = self.get_queryset().filter(batch__batch_id=batch_id)
        serializer = self.get_serializer(returns, many=True)
        
        return Response({
            'success': True,
            'data': serializer.data,
            'count': returns.count()
        }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_material_heat_numbers_coils(request):
    """
    Get all heat numbers and coils for a given material code
    
    Query Parameters:
    - material_code (required): The material code to query
    
    Returns:
    - material_code: The material code
    - material_name: The material name
    - heat_numbers: List of heat numbers with their coils
        - id: Heat number ID
        - heat_number: Heat number string
        - grm_number: GRM receipt number
        - total_weight_kg: Total weight in kg
        - coils_received: Number of coils received
        - available_quantity_kg: Available quantity in kg
        - coils: List of coils with number and weight
    """
    material_code = request.query_params.get('material_code')
    
    if not material_code:
        return Response({
            'error': 'material_code query parameter is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        raw_material = RawMaterial.objects.get(material_code=material_code)
    except RawMaterial.DoesNotExist:
        return Response({
            'error': f'Material code "{material_code}" does not exist'
        }, status=status.HTTP_404_NOT_FOUND)
    
    # Get all heat numbers for this material
    heat_numbers = HeatNumber.objects.filter(
        raw_material=raw_material,
        is_available=True
    ).select_related('grm_receipt').order_by('-created_at')
    
    # Serialize the response
    serializer = MaterialHeatNumbersResponseSerializer({
        'material_code': raw_material.material_code,
        'material_name': raw_material.material_name,
        'heat_numbers': heat_numbers
    })
    
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRMStoreUser])
def delete_coils_by_heat_numbers(request):
    """
    Delete all coils from specified heat numbers for a material code
    
    Request Body:
    {
        "material_code": "MAT-001",
        "heat_numbers": ["HEAT-001", "HEAT-002"]
    }
    
    This will:
    1. Find all heat numbers matching the material_code and heat_numbers list
    2. Delete all coils (clear the items JSONField) from those heat numbers
    3. Update coils_received to 0
    4. Update total_weight_kg to 0
    5. Update RM stock balance
    6. Mark heat numbers as unavailable (is_available=False)
    
    Returns:
    - message: Success message
    - material_code: The material code
    - deleted_heat_numbers: List of heat numbers that had coils deleted
    - deleted_coils_count: Total number of coils deleted
    - updated_heat_numbers: List of heat numbers that were updated
    """
    serializer = DeleteCoilsRequestSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'error': 'Invalid request data',
            'details': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    material_code = serializer.validated_data['material_code']
    heat_number_list = serializer.validated_data['heat_numbers']
    
    try:
        raw_material = RawMaterial.objects.get(material_code=material_code)
    except RawMaterial.DoesNotExist:
        return Response({
            'error': f'Material code "{material_code}" does not exist'
        }, status=status.HTTP_404_NOT_FOUND)
    
    # Find heat numbers matching the criteria
    heat_numbers = HeatNumber.objects.filter(
        raw_material=raw_material,
        heat_number__in=heat_number_list,
        is_available=True
    )
    
    if not heat_numbers.exists():
        return Response({
            'error': f'No active heat numbers found for material "{material_code}" with the provided heat numbers'
        }, status=status.HTTP_404_NOT_FOUND)
    
    deleted_heat_numbers = []
    deleted_coils_count = 0
    updated_heat_numbers = []
    
    with transaction.atomic():
        for heat in heat_numbers:
            # Count coils before deletion
            coils_before = len(heat.items) if heat.items else 0
            deleted_coils_count += coils_before
            
            # Clear coils (items JSONField)
            heat.items = []
            heat.coils_received = 0
            heat.total_weight_kg = Decimal('0')
            heat.is_available = False
            heat.save()
            
            deleted_heat_numbers.append(heat.heat_number)
            updated_heat_numbers.append(heat.heat_number)
        
        # Update stock balance for the raw material (both RMStockBalanceHeat and RMStockBalance)
        try:
            stock_balance_heat = RMStockBalanceHeat.objects.get(raw_material=raw_material)
            stock_balance_heat.update_from_heat_numbers()
        except RMStockBalanceHeat.DoesNotExist:
            # If stock balance doesn't exist, create it
            RMStockBalanceHeat.objects.create(raw_material=raw_material)
        
        # Also update the simple RMStockBalance if it exists
        try:
            stock_balance = RMStockBalance.objects.get(raw_material=raw_material)
            # Calculate total available quantity from heat numbers
            total_available = sum(
                heat.get_available_quantity_kg() 
                for heat in HeatNumber.objects.filter(
                    raw_material=raw_material,
                    is_available=True
                )
            )
            stock_balance.available_quantity = total_available
            stock_balance.save()
        except RMStockBalance.DoesNotExist:
            # Create RMStockBalance if it doesn't exist
            total_available = sum(
                heat.get_available_quantity_kg() 
                for heat in HeatNumber.objects.filter(
                    raw_material=raw_material,
                    is_available=True
                )
            )
            RMStockBalance.objects.create(
                raw_material=raw_material,
                available_quantity=total_available
            )
    
    response_serializer = DeleteCoilsResponseSerializer({
        'message': f'Successfully deleted {deleted_coils_count} coils from {len(deleted_heat_numbers)} heat number(s)',
        'material_code': material_code,
        'deleted_heat_numbers': deleted_heat_numbers,
        'deleted_coils_count': deleted_coils_count,
        'updated_heat_numbers': updated_heat_numbers
    })
    
    return Response(response_serializer.data, status=status.HTTP_200_OK)