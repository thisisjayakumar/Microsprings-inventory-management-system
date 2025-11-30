from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
import logging
from .models import (
    ManufacturingOrder, PurchaseOrder, MOStatusHistory, POStatusHistory,
    MOTransactionHistory, POTransactionHistory,
    MOProcessExecution, MOProcessStepExecution, MOProcessAlert, Batch,
    OutsourcingRequest, OutsourcedItem, RawMaterialAllocation, RMAllocationHistory
)
from products.models import Product
from inventory.models import RawMaterial
from third_party.models import Vendor

User = get_user_model()
logger = logging.getLogger(__name__)


class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user serializer for nested relationships"""
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name']
        read_only_fields = fields


class ProductBasicSerializer(serializers.ModelSerializer):
    """Basic product serializer for nested relationships"""
    material_type_display = serializers.CharField(read_only=True)
    product_type_display = serializers.CharField(source='get_product_type_display', read_only=True)
    material_name = serializers.CharField(read_only=True)
    customer_name = serializers.CharField(source='customer_c_id.name', read_only=True)
    customer_id = serializers.CharField(source='customer_c_id.c_id', read_only=True)
    grade = serializers.CharField(read_only=True)
    wire_diameter_mm = serializers.DecimalField(max_digits=8, decimal_places=3, read_only=True)
    thickness_mm = serializers.DecimalField(max_digits=8, decimal_places=3, read_only=True)
    finishing = serializers.CharField(read_only=True)
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3, read_only=True)
    material_type = serializers.CharField(read_only=True)
    
    class Meta:
        model = Product
        fields = [
            'id', 'product_code', 'product_type', 'product_type_display', 
            'material_type', 'material_type_display', 'material_name', 'grade', 
            'wire_diameter_mm', 'thickness_mm', 'finishing', 'weight_kg', 
            'customer_name', 'customer_id', 'grams_per_product', 'length_mm', 'breadth_mm',
            'whole_sheet_length_mm', 'whole_sheet_breadth_mm', 'strip_length_mm', 
            'strip_breadth_mm', 'strips_per_sheet', 'pcs_per_strip'
        ]
        read_only_fields = fields


class RawMaterialBasicSerializer(serializers.ModelSerializer):
    """Basic raw material serializer for nested relationships"""
    material_name_display = serializers.CharField(source='material_name', read_only=True)
    material_type_display = serializers.CharField(source='get_material_type_display', read_only=True)
    available_quantity = serializers.SerializerMethodField()
    
    class Meta:
        model = RawMaterial
        fields = [
            'id', 'material_code', 'material_name', 'material_name_display',
            'material_type', 'material_type_display', 'grade', 'wire_diameter_mm',
            'weight_kg', 'thickness_mm', 'finishing', 'available_quantity',
            'length_mm', 'breadth_mm', 'quantity'
        ]
        read_only_fields = fields
    
    def get_available_quantity(self, obj):
        """Get available quantity from RMStockBalance"""
        try:
            from inventory.models import RMStockBalance
            stock_balance = RMStockBalance.objects.get(raw_material=obj)
            return float(stock_balance.available_quantity)
        except RMStockBalance.DoesNotExist:
            return 0.0


class VendorBasicSerializer(serializers.ModelSerializer):
    """Basic vendor serializer for nested relationships"""
    vendor_type_display = serializers.CharField(source='get_vendor_type_display', read_only=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'name', 'vendor_type', 'vendor_type_display', 'gst_no',
            'address', 'contact_no', 'email', 'contact_person', 'is_active'
        ]
        read_only_fields = fields


class MOTransactionHistorySerializer(serializers.ModelSerializer):
    """Serializer for MO transaction history"""
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = MOTransactionHistory
        fields = [
            'id', 'transaction_type', 'transaction_id', 'description', 
            'details', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class POTransactionHistorySerializer(serializers.ModelSerializer):
    """Serializer for PO transaction history"""
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = POTransactionHistory
        fields = [
            'id', 'transaction_type', 'transaction_id', 'description', 
            'details', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class MOStatusHistorySerializer(serializers.ModelSerializer):
    """Serializer for MO status history"""
    changed_by = UserBasicSerializer(read_only=True)
    
    class Meta:
        model = MOStatusHistory
        fields = ['id', 'from_status', 'to_status', 'changed_by', 'changed_at', 'notes']
        read_only_fields = fields


class POStatusHistorySerializer(serializers.ModelSerializer):
    """Serializer for PO status history"""
    changed_by = UserBasicSerializer(read_only=True)
    
    class Meta:
        model = POStatusHistory
        fields = ['id', 'from_status', 'to_status', 'changed_by', 'changed_at', 'notes']
        read_only_fields = fields


class BatchListSerializer(serializers.ModelSerializer):
    """Optimized serializer for Batch list view"""
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    product_code_display = serializers.CharField(source='product_code.product_code', read_only=True)
    assigned_operator_name = serializers.CharField(source='assigned_operator.get_full_name', read_only=True)
    assigned_supervisor_name = serializers.CharField(source='assigned_supervisor.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    completion_percentage = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    remaining_quantity = serializers.ReadOnlyField()

    class Meta:
        model = Batch
        fields = [
            'id', 'batch_id', 'mo', 'mo_id', 'product_code', 'product_code_display',
            'planned_quantity', 'actual_quantity_started', 'actual_quantity_completed',
            'scrap_quantity', 'scrap_rm_weight', 'status', 'status_display', 'progress_percentage',
            'completion_percentage', 'remaining_quantity', 'assigned_operator',
            'assigned_operator_name', 'assigned_supervisor', 'assigned_supervisor_name',
            'planned_start_date', 'planned_end_date', 'actual_start_date', 'actual_end_date',
            'is_overdue', 'created_at', 'updated_at'
        ]
        read_only_fields = ['batch_id', 'created_at', 'updated_at']


class BatchMinimalSerializer(serializers.ModelSerializer):
    """Highly optimized minimal serializer for batches in production-head MO detail page"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Batch
        # Highly optimized: Only include fields used in production-head MO detail page
        # REMOVED UNUSED FIELDS: mo, mo_id, product_code, product_code_display, actual_quantity_started,
        # scrap_rm_weight, progress_percentage, completion_percentage, remaining_quantity,
        # assigned_operator, assigned_operator_name, assigned_supervisor, assigned_supervisor_name,
        # planned_start_date, planned_end_date, actual_start_date, actual_end_date,
        # is_overdue, created_at, updated_at
        # NOTE: 'notes' is included to check for batch verification status
        fields = [
            'id', 'batch_id', 'planned_quantity', 'actual_quantity_completed',
            'scrap_quantity', 'status', 'status_display', 'notes'
        ]


class ManufacturingOrderListSerializer(serializers.ModelSerializer):
    """Optimized serializer for MO list view"""
    product_code = ProductBasicSerializer(read_only=True)
    # NOTE: assigned_rm_store removed - all RM store users see all MOs
    # NOTE: assigned_supervisor removed - supervisor tracking moved to work center level
    created_by = UserBasicSerializer(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    batches = BatchListSerializer(many=True, read_only=True)
    material_type = serializers.CharField(source='product_code.material_type', read_only=True)
    material_name = serializers.CharField(source='product_code.material.material_name', read_only=True)
    remaining_rm = serializers.SerializerMethodField()
    rm_unit = serializers.SerializerMethodField()
    can_create_batch = serializers.SerializerMethodField()
    
    def get_remaining_rm(self, obj):
        """Calculate remaining RM for batch creation"""
        product = obj.product_code
        if not product:
            return None
        
        total_rm_required = Decimal('0')
        cumulative_rm_released = Decimal('0')
        
        try:
            if product.material_type == 'coil' and product.grams_per_product:
                # For coil-based products - calculate in kg
                total_grams = obj.quantity * product.grams_per_product
                base_rm_kg = Decimal(str(total_grams / 1000))
                tolerance = obj.tolerance_percentage or Decimal('2.00')
                tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
                total_rm_required = base_rm_kg * tolerance_factor
                
                # Calculate cumulative RM from all non-cancelled batches
                for batch in obj.batches.exclude(status='cancelled'):
                    batch_rm_base_kg = Decimal(str(batch.planned_quantity / 1000))
                    batch_rm = batch_rm_base_kg * tolerance_factor
                    cumulative_rm_released += batch_rm
                    
            elif product.material_type == 'sheet' and product.pcs_per_strip:
                # For sheet-based products - calculate in strips
                strips_calc = product.calculate_strips_required(obj.quantity)
                total_rm_required = Decimal(str(strips_calc.get('strips_required', 0)))
                
                # Calculate cumulative RM from all non-cancelled batches
                for batch in obj.batches.exclude(status='cancelled'):
                    batch_strips = Decimal(str(batch.planned_quantity or 0))
                    cumulative_rm_released += batch_strips
            
            # Calculate remaining
            remaining = float(total_rm_required - cumulative_rm_released)
            return max(0, remaining)  # Never return negative
            
        except Exception as e:
            logger.error(f"Error calculating remaining RM for MO {obj.mo_id}: {str(e)}")
            return None
    
    def get_rm_unit(self, obj):
        """Get RM unit based on material type"""
        product = obj.product_code
        if not product:
            return 'kg'
        return 'kg' if product.material_type == 'coil' else 'strips'
    
    def get_can_create_batch(self, obj):
        """Check if more batches can be created"""
        remaining = self.get_remaining_rm(obj)
        if remaining is None:
            return True  # Default to allowing batch creation if calculation fails
        
        product = obj.product_code
        if not product:
            return True
        
        # Threshold: 0.05 kg for coil, 1 strip for sheet
        threshold = 0.05 if product.material_type == 'coil' else 1
        return remaining > threshold
    
    class Meta:
        model = ManufacturingOrder
        fields = [
            'id', 'mo_id', 'date_time', 'product_code', 'quantity', 'status', 
            'status_display', 'priority', 'priority_display', 'shift', 'shift_display',
            'planned_start_date', 'planned_end_date',
            'delivery_date', 'created_by', 'created_at', 'strips_required', 
            'total_pieces_from_strips', 'excess_pieces', 'tolerance_percentage',
            'material_type', 'material_name', 'batches', 'remaining_rm', 'rm_unit', 'can_create_batch'
        ]
        read_only_fields = ['mo_id', 'date_time']


class ManufacturingOrderDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for MO create/update/detail view"""
    product_code = ProductBasicSerializer(read_only=True)
    # NOTE: assigned_rm_store removed - all RM store users see all MOs
    # NOTE: assigned_supervisor removed - supervisor tracking moved to work center level
    created_by = UserBasicSerializer(read_only=True)
    gm_approved_by = UserBasicSerializer(read_only=True)
    rm_allocated_by = UserBasicSerializer(read_only=True)
    status_history = MOStatusHistorySerializer(many=True, read_only=True)
    transaction_history = MOTransactionHistorySerializer(many=True, read_only=True)
    rm_returns = serializers.SerializerMethodField()
    
    # Customer fields
    from third_party.serializers import CustomerListSerializer
    customer = CustomerListSerializer(read_only=True)
    
    # Display fields
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    
    # Write-only fields for creation
    product_code_id = serializers.IntegerField(write_only=True)
    # NOTE: assigned_rm_store_id removed - all RM store users see all MOs
    # NOTE: assigned_supervisor_id removed - supervisor tracking moved to work center level
    customer_id = serializers.IntegerField(write_only=True, required=False)
    
    # Explicitly define date fields to handle empty strings
    planned_start_date = serializers.DateTimeField(required=False, allow_null=True)
    planned_end_date = serializers.DateTimeField(required=False, allow_null=True)
    
    def to_internal_value(self, data):
        """Convert empty strings to None for datetime fields before validation"""
        # Make a mutable copy of the data
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        
        # Convert empty strings to None for datetime fields
        if 'planned_start_date' in data and data['planned_start_date'] == '':
            data['planned_start_date'] = None
        if 'planned_end_date' in data and data['planned_end_date'] == '':
            data['planned_end_date'] = None
        
        return super().to_internal_value(data)
    
    class Meta:
        model = ManufacturingOrder
        fields = [
            'id', 'mo_id', 'date_time', 'product_code', 'product_code_id', 'quantity',
            'product_type', 'material_name', 'material_type', 'grade', 'wire_diameter_mm',
            'thickness_mm', 'finishing', 'manufacturer_brand', 'weight_kg',
            'loose_fg_stock', 'rm_required_kg', 'tolerance_percentage', 'scrap_percentage', 
            'rm_released_kg', 'strips_required', 'total_pieces_from_strips', 'excess_pieces',
            'shift', 'shift_display', 'planned_start_date', 'planned_end_date',
            'actual_start_date', 'actual_end_date', 'status', 'status_display',
            'priority', 'priority_display', 'customer', 'customer_id', 'customer_name', 
            'delivery_date', 'special_instructions', 'submitted_at', 'gm_approved_at', 
            'gm_approved_by', 'rm_allocated_at', 'rm_allocated_by', 'created_at', 
            'created_by', 'updated_at', 'status_history', 'transaction_history', 'rm_returns'
        ]
        read_only_fields = [
            'mo_id', 'date_time', 'product_type', 'material_name', 'material_type',
            'grade', 'wire_diameter_mm', 'thickness_mm', 'finishing', 'manufacturer_brand',
            'weight_kg', 'submitted_at', 'gm_approved_at', 'gm_approved_by',
            'rm_allocated_at', 'rm_allocated_by', 'created_at', 'updated_at'
        ]

    def create(self, validated_data):
        """Create MO with auto-population of product details"""
        product_code_id = validated_data.pop('product_code_id')
        # NOTE: assigned_rm_store_id removed - all RM store users see all MOs
        # NOTE: assigned_supervisor_id removed - supervisor tracking moved to work center level
        customer_id = validated_data.pop('customer_id', None)
        
        try:
            # Try to get product by ID first (numeric)
            if str(product_code_id).isdigit():
                product = Product.objects.select_related('customer_c_id', 'material').get(id=product_code_id)
            else:
                # If ID is not numeric, treat it as product_code (string)
                product = Product.objects.select_related('customer_c_id', 'material').get(product_code=product_code_id)
        except Product.DoesNotExist:
            # If product doesn't exist, we need to create it or handle it differently
            from processes.models import BOM
            bom_item = BOM.objects.filter(product_code=product_code_id, is_active=True).first()
            if not bom_item:
                raise serializers.ValidationError("Invalid product reference - not found in Product table or BOM")
            
            # Create a new Product record from BOM data
            # First, try to get the material from BOM
            material = bom_item.material
            if not material:
                raise serializers.ValidationError("BOM item has no associated material")
            
            product = Product.objects.create(
                product_code=product_code_id,
                product_type='spring' if bom_item.type == 'spring' else 'stamping_part',
                material=material,
                created_by=self.context['request'].user
            )
        
        # NOTE: RM store assignment removed - all RM store users see all MOs
        # NOTE: Supervisor handling removed - supervisor tracking moved to work center level
        
        # Handle optional customer
        customer = None
        if customer_id:
            try:
                from third_party.models import Customer
                customer = Customer.objects.get(id=customer_id)
            except Customer.DoesNotExist:
                raise serializers.ValidationError("Invalid customer reference")
        
        # Auto-populate product details
        validated_data.update({
            'product_code': product,
            # NOTE: assigned_rm_store removed - all RM store users see all MOs
            # NOTE: assigned_supervisor removed - supervisor tracking moved to work center level
            'customer_c_id': customer,
            'customer_name': customer.name if customer else validated_data.get('customer_name', ''),
            'product_type': product.get_product_type_display() if product.product_type else '',
            'material_name': product.material_name or '',
            'material_type': product.material_type or '',
            'grade': product.grade or '',
            'wire_diameter_mm': product.wire_diameter_mm,
            'thickness_mm': product.thickness_mm,
            'finishing': product.finishing or '',
            'manufacturer_brand': '',  # Not available in new structure
            'weight_kg': product.weight_kg,
            'created_by': self.context['request'].user
        })
        
        # Create the MO instance
        mo = super().create(validated_data)
        
        # Calculate RM requirements (including sheet calculations)
        mo.calculate_rm_requirements()
        mo.save()
        
        # Create MO creation transaction history
        try:
            from inventory.transaction_manager import InventoryTransactionManager
            from inventory.models import RMStockBalanceHeat
            from inventory.utils import generate_transaction_id
            from django.utils import timezone
            
            # Get RM stock levels before allocation
            raw_material = mo.product_code.material
            stock_before = RMStockBalanceHeat.objects.filter(
                raw_material=raw_material
            ).first()
            
            stock_before_quantity = stock_before.total_available_quantity_kg if stock_before else Decimal('0')
            
            # Create MO creation transaction history
            transaction_id = generate_transaction_id('MO_CREATED')
            
            # Create a comprehensive transaction history entry
            from manufacturing.models import MOTransactionHistory
            MOTransactionHistory.objects.create(
                mo=mo,
                transaction_type='mo_created',
                transaction_id=transaction_id,
                description=f'MO {mo.mo_id} created for {mo.product_code.product_code}',
                details={
                    'quantity': mo.quantity,
                    'rm_required_kg': float(mo.rm_required_kg),
                    'stock_before_allocation': float(stock_before_quantity),
                    'product_code': mo.product_code.product_code,
                    'material_name': raw_material.material_name,
                    'created_by': self.context['request'].user.get_full_name() or self.context['request'].user.email
                },
                created_by=self.context['request'].user
            )
            
        except Exception as e:
            logger.warning(f"Failed to create MO creation transaction history: {str(e)}")
        
        # NOTE: RM allocation is NOT done during MO creation
        # RM will be reserved when production starts (via start_production action)
        logger.info(f"MO {mo.mo_id} created. RM will be reserved when production starts.")
        
        # Automatically create MO workflow and send notifications to managers
        try:
            from manufacturing.workflow_service import ManufacturingWorkflowService
            workflow = ManufacturingWorkflowService.create_mo_workflow(mo.id, self.context['request'].user)
            logger.info(f"MO workflow created for MO {mo.mo_id}")
        except Exception as e:
            logger.error(f"Failed to create MO workflow for MO {mo.mo_id}: {str(e)}")
            # Don't fail MO creation if workflow creation fails
        
        return mo

    def update(self, instance, validated_data):
        """Update MO with status change tracking"""
        old_status = instance.status
        new_status = validated_data.get('status', old_status)
        
        # Handle product change
        if 'product_code_id' in validated_data:
            product_code_id = validated_data.pop('product_code_id')
            try:
                product = Product.objects.select_related('customer_c_id', 'material').get(id=product_code_id)
                validated_data['product_code'] = product
                # Re-populate product details if product changed
                validated_data.update({
                    'product_type': product.get_product_type_display() if product.product_type else '',
                    'material_name': product.material_name or '',
                    'material_type': product.material_type or '',
                    'grade': product.grade or '',
                    'wire_diameter_mm': product.wire_diameter_mm,
                    'thickness_mm': product.thickness_mm,
                    'finishing': product.finishing or '',
                    'manufacturer_brand': '',  # Not available in new structure
                    'weight_kg': product.weight_kg,
                })
            except Product.DoesNotExist:
                raise serializers.ValidationError("Invalid product reference")
        
        # NOTE: RM store assignment removed - all RM store users see all MOs
        # NOTE: Supervisor handling removed - supervisor tracking moved to work center level
        
        instance = super().update(instance, validated_data)
        
        # Recalculate RM requirements if quantity or product changed
        if 'quantity' in validated_data or 'product_code' in validated_data:
            instance.calculate_rm_requirements()
            instance.save()
        
        # Create status history if status changed
        if old_status != new_status:
            MOStatusHistory.objects.create(
                mo=instance,
                from_status=old_status,
                to_status=new_status,
                changed_by=self.context['request'].user,
                notes=f"Status changed via API"
            )
            
            # Create comprehensive transaction history for status changes
            try:
                from inventory.utils import generate_transaction_id
                transaction_id = generate_transaction_id('MO_STATUS_CHANGED')
                
                MOTransactionHistory.objects.create(
                    mo=instance,
                    transaction_type='status_changed',
                    transaction_id=transaction_id,
                    description=f'MO {instance.mo_id} status changed from {old_status} to {new_status}',
                    details={
                        'from_status': old_status,
                        'to_status': new_status,
                        'changed_by': self.context['request'].user.get_full_name() or self.context['request'].user.email,
                        'changed_at': timezone.now().isoformat(),
                        'mo_id': instance.mo_id,
                        'product_code': instance.product_code.product_code if instance.product_code else None
                    },
                    created_by=self.context['request'].user
                )
            except Exception as e:
                logger.warning(f"Failed to create status change transaction history: {str(e)}")
        
        return instance
    
    def get_rm_returns(self, obj):
        """Get all RM returns for this MO"""
        from inventory.serializers import RMReturnSerializer
        from inventory.models import RMReturn
        
        rm_returns = RMReturn.objects.filter(
            manufacturing_order=obj
        ).select_related(
            'raw_material', 'heat_number', 'batch', 'returned_from_location',
            'returned_by', 'disposed_by'
        ).order_by('-returned_at')
        
        return RMReturnSerializer(rm_returns, many=True).data


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    """Optimized serializer for PO list view"""
    rm_code = RawMaterialBasicSerializer(read_only=True)
    vendor_name = VendorBasicSerializer(read_only=True)
    created_by = UserBasicSerializer(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    material_type_display = serializers.CharField(source='get_material_type_display', read_only=True)
    
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'po_id', 'date_time', 'rm_code', 'vendor_name', 'quantity_ordered', 'quantity_received',
            'unit_price', 'total_amount', 'status', 'status_display', 'material_type',
            'material_type_display', 'expected_date', 'created_by', 'created_at'
        ]
        read_only_fields = ['po_id', 'date_time', 'total_amount']


# Process Execution Serializers
class MOProcessStepExecutionSerializer(serializers.ModelSerializer):
    """Serializer for process step execution tracking"""
    process_step_name = serializers.CharField(source='process_step.step_name', read_only=True)
    process_step_code = serializers.CharField(source='process_step.step_code', read_only=True)
    process_step_full_path = serializers.CharField(source='process_step.full_path', read_only=True)
    operator_name = serializers.CharField(source='operator.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    quality_status_display = serializers.CharField(source='get_quality_status_display', read_only=True)
    duration_minutes = serializers.ReadOnlyField()
    efficiency_percentage = serializers.ReadOnlyField()
    
    class Meta:
        model = MOProcessStepExecution
        fields = [
            'id', 'process_step', 'process_step_name', 'process_step_code', 
            'process_step_full_path', 'status', 'status_display', 
            'quality_status', 'quality_status_display', 'actual_start_time', 
            'actual_end_time', 'quantity_processed', 'quantity_passed', 
            'quantity_failed', 'scrap_percentage', 'operator', 'operator_name',
            'operator_notes', 'quality_notes', 'duration_minutes', 
            'efficiency_percentage', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


# Shared batch counts calculation method
def _get_batch_counts_for_process(obj):
    """Calculate batch counts by status for a specific process execution (shared logic)"""
    batches = Batch.objects.filter(
        mo=obj.mo
    ).exclude(status='cancelled')
    
    # Check each batch's status for this specific process
    process_key = f"PROCESS_{obj.id}_STATUS"
    pending_count = 0
    in_progress_count = 0
    completed_count = 0
    failed_count = 0
    
    for batch in batches:
        batch_notes = batch.notes or ""
        
        # Check if this batch has a status for this process
        if f"{process_key}:" in batch_notes:
            # Extract status from notes: PROCESS_{id}_STATUS:status;
            import re
            pattern = f"{re.escape(process_key)}:([^;]+);"
            match = re.search(pattern, batch_notes)
            if match:
                batch_process_status = match.group(1).strip()
                if batch_process_status == 'in_progress':
                    in_progress_count += 1
                elif batch_process_status == 'completed':
                    completed_count += 1
                elif batch_process_status == 'failed':
                    failed_count += 1
                else:
                    pending_count += 1
            else:
                # Has the key but no valid status, treat as pending
                pending_count += 1
        else:
            # No process-specific status in notes - batch hasn't explicitly started this process
            pending_count += 1
    
    return {
        'pending': pending_count,
        'in_progress': in_progress_count,
        'completed': completed_count,
        'failed': failed_count,
        'total': batches.count()
    }


class MOProcessExecutionListSerializer(serializers.ModelSerializer):
    """Serializer for process execution list view - extends minimal with additional fields"""
    # Inherit base fields from minimal serializer pattern
    process_name = serializers.CharField(source='process.name', read_only=True)
    process_code = serializers.IntegerField(source='process.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    assigned_operator_name = serializers.SerializerMethodField()
    assigned_supervisor_name = serializers.SerializerMethodField()
    duration_minutes = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    step_count = serializers.SerializerMethodField()
    completed_steps = serializers.SerializerMethodField()
    batch_counts = serializers.SerializerMethodField()
    
    class Meta:
        model = MOProcessExecution
        fields = [
            'id', 'process', 'process_name', 'process_code', 'status', 
            'status_display', 'sequence_order', 'planned_start_time', 
            'planned_end_time', 'actual_start_time', 'actual_end_time',
            'assigned_operator', 'assigned_operator_name', 'assigned_supervisor', 'assigned_supervisor_name',
            'progress_percentage', 'duration_minutes', 'is_overdue', 'step_count', 'completed_steps',
            'batch_counts', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_assigned_operator_name(self, obj):
        return obj.assigned_operator.get_full_name() if obj.assigned_operator else None
    
    def get_assigned_supervisor_name(self, obj):
        return obj.assigned_supervisor.get_full_name() if obj.assigned_supervisor else None
    
    def get_step_count(self, obj):
        return obj.step_executions.count()
    
    def get_completed_steps(self, obj):
        return obj.step_executions.filter(status='completed').count()
    
    def get_batch_counts(self, obj):
        """Get batch counts by status for this specific process execution"""
        return _get_batch_counts_for_process(obj)


class MOProcessExecutionDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for process execution with step details"""
    process_name = serializers.CharField(source='process.name', read_only=True)
    process_code = serializers.IntegerField(source='process.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    assigned_operator_name = serializers.CharField(source='assigned_supervisor.get_full_name', read_only=True)
    assigned_supervisor_name = serializers.CharField(source='assigned_supervisor.get_full_name', read_only=True)
    duration_minutes = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    step_executions = MOProcessStepExecutionSerializer(many=True, read_only=True)
    alerts = serializers.SerializerMethodField()
    batch_counts = serializers.SerializerMethodField()
    
    class Meta:
        model = MOProcessExecution
        fields = [
            'id', 'process', 'process_name', 'process_code', 'status', 
            'status_display', 'sequence_order', 'planned_start_time', 
            'planned_end_time', 'actual_start_time', 'actual_end_time',
            'assigned_operator', 'assigned_operator_name', 'assigned_supervisor', 'assigned_supervisor_name',
            'progress_percentage', 'notes', 'duration_minutes', 'is_overdue', 'step_executions',
            'alerts', 'batch_counts', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_alerts(self, obj):
        from .serializers import MOProcessAlertSerializer
        return MOProcessAlertSerializer(
            obj.alerts.filter(is_resolved=False), many=True
        ).data
    
    def get_batch_counts(self, obj):
        """Get batch counts by status for this specific process execution"""
        return _get_batch_counts_for_process(obj)


class MOProcessAlertSerializer(serializers.ModelSerializer):
    """Serializer for process alerts"""
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    resolved_by_name = serializers.CharField(source='resolved_by.get_full_name', read_only=True)

    class Meta:
        model = MOProcessAlert
        fields = [
            'id', 'alert_type', 'alert_type_display', 'severity', 'severity_display',
            'title', 'description', 'is_resolved', 'resolved_at', 'resolved_by',
            'resolved_by_name', 'resolution_notes', 'created_at', 'created_by',
            'created_by_name'
        ]
        read_only_fields = ['created_at']


class MOProcessAlertMinimalSerializer(serializers.ModelSerializer):
    """Highly optimized minimal serializer for process alerts in production-head MO detail page"""
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = MOProcessAlert
        # Highly optimized: Only include fields used in production-head MO detail page
        # REMOVED UNUSED FIELDS: alert_type, alert_type_display, is_resolved, resolved_at,
        # resolved_by, resolved_by_name, resolution_notes, created_by
        fields = [
            'id', 'severity', 'severity_display', 'title', 'description',
            'created_at', 'created_by_name'
        ]


class MOProcessExecutionMinimalSerializer(serializers.ModelSerializer):
    """Highly optimized minimal serializer for process executions in production-head MO detail page"""
    process_name = serializers.CharField(source='process.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    assigned_supervisor_name = serializers.SerializerMethodField()
    batch_counts = serializers.SerializerMethodField()

    class Meta:
        model = MOProcessExecution
        # Highly optimized: Only include fields used in production-head MO detail page
        # REMOVED UNUSED FIELDS: process, process_code, actual_start_time, actual_end_time,
        # assigned_operator, assigned_operator_name, progress_percentage, duration_minutes,
        # is_overdue, step_count, created_at, updated_at
        fields = [
            'id', 'process_name', 'status', 'status_display', 'sequence_order',
            'assigned_supervisor', 'assigned_supervisor_name', 'batch_counts'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def get_assigned_operator_name(self, obj):
        return obj.assigned_operator.get_full_name() if obj.assigned_operator else None
    
    def get_assigned_supervisor_name(self, obj):
        return obj.assigned_supervisor.get_full_name() if obj.assigned_supervisor else None
    
    def get_step_count(self, obj):
        return obj.step_executions.count()
    
    def get_batch_counts(self, obj):
        """Get batch counts by status for this specific process execution"""
        return _get_batch_counts_for_process(obj)


class ManufacturingOrderWithProcessesSerializer(serializers.ModelSerializer):
    """Highly optimized MO serializer for production-head MO detail page - only essential fields"""
    product_code_display = serializers.CharField(source='product_code.product_code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    process_executions = MOProcessExecutionMinimalSerializer(many=True, read_only=True)
    overall_progress = serializers.SerializerMethodField()

    class Meta:
        model = ManufacturingOrder
        # Highly optimized: Only include fields used in production-head MO detail page
        # REMOVED UNUSED FIELDS: date_time, product_code, product_code_value, product_type, material_type,
        # wire_diameter_mm, thickness_mm, planned_start_date, planned_end_date, actual_start_date,
        # actual_end_date, active_process (replaced with overall_progress), rejected_by, rejection_reason
        fields = [
            'id', 'mo_id', 'product_code_display', 'quantity', 'material_name', 'grade',
            'shift', 'shift_display', 'status', 'status_display', 'priority', 'priority_display',
            'delivery_date', 'special_instructions', 'process_executions',
            'overall_progress', 'rejected_at'
        ]
        read_only_fields = ['mo_id', 'created_at', 'updated_at']
    
    def get_overall_progress(self, obj):
        """Calculate overall progress across all processes"""
        executions = obj.process_executions.all()
        if not executions:
            return 0
        
        total_progress = 0
        valid_processes = 0
        
        for exec in executions:
            progress = exec.progress_percentage
            if progress is not None and not (isinstance(progress, float) and (progress != progress or progress < 0)):  # Check for NaN and negative
                total_progress += float(progress)
                valid_processes += 1
        
        if valid_processes == 0:
            return 0
        
        return round(total_progress / valid_processes, 2)
    
    def get_active_process(self, obj):
        """Get currently active process"""
        active_exec = obj.process_executions.filter(status='in_progress').first()
        if active_exec:
            progress = active_exec.progress_percentage
            # Handle NaN values
            if progress is None or (isinstance(progress, float) and progress != progress):
                progress = 0
                
            return {
                'id': active_exec.id,
                'process_name': active_exec.process.name,
                'progress_percentage': progress,
                'assigned_operator': active_exec.assigned_operator.get_full_name() if active_exec.assigned_operator else None
            }
        return None


class PurchaseOrderDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for PO create/update/detail view"""
    rm_code = RawMaterialBasicSerializer(read_only=True)
    vendor_name = VendorBasicSerializer(read_only=True)
    created_by = UserBasicSerializer(read_only=True)
    approved_by = UserBasicSerializer(read_only=True)
    cancelled_by = UserBasicSerializer(read_only=True)
    status_history = POStatusHistorySerializer(many=True, read_only=True)
    transaction_history = POTransactionHistorySerializer(many=True, read_only=True)
    
    # Display fields
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    material_type_display = serializers.CharField(source='get_material_type_display', read_only=True)
    
    # Write-only fields for creation
    rm_code_id = serializers.IntegerField(write_only=True)
    vendor_name_id = serializers.IntegerField(write_only=True)
    
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'po_id', 'date_time', 'rm_code', 'rm_code_id', 'material_type',
            'material_type_display', 'material_auto', 'grade_auto', 'wire_diameter_mm_auto',
            'thickness_mm_auto', 'finishing_auto', 'manufacturer_brand_auto', 'kg_auto',
            'sheet_roll_auto', 'qty_sheets_auto', 'vendor_name', 'vendor_name_id',
            'vendor_address_auto', 'gst_no_auto', 'mob_no_auto', 'expected_date',
            'quantity_ordered', 'quantity_received', 'unit_price', 'total_amount', 'status', 'status_display',
            'submitted_at', 'approved_at', 'approved_by', 'cancelled_at',
            'cancelled_by', 'cancellation_reason',
            'terms_conditions', 'notes', 'created_at', 'created_by', 'updated_at',
            'status_history', 'transaction_history'
        ]
        read_only_fields = [
            'po_id', 'date_time', 'material_type', 'material_auto', 'grade_auto', 'finishing_auto',
            'wire_diameter_mm_auto', 'thickness_mm_auto', 'kg_auto', 'sheet_roll_auto', 'qty_sheets_auto',
            'vendor_address_auto', 'gst_no_auto', 'mob_no_auto', 'total_amount',
            'submitted_at', 'approved_at', 'approved_by', 'cancelled_at',
            'cancelled_by', 'created_at', 'updated_at'
        ]

    def create(self, validated_data):
        """Create PO with auto-population of material and vendor details"""
        rm_code_id = validated_data.pop('rm_code_id')
        vendor_name_id = validated_data.pop('vendor_name_id')
        
        try:
            rm_code = RawMaterial.objects.get(id=rm_code_id)
            vendor = Vendor.objects.get(id=vendor_name_id)
        except (RawMaterial.DoesNotExist, Vendor.DoesNotExist) as e:
            raise serializers.ValidationError(f"Invalid reference: {str(e)}")
        
        # Auto-populate material details
        validated_data.update({
            'rm_code': rm_code,
            'material_type': getattr(rm_code, 'material_type', ''),
            'material_auto': getattr(rm_code, 'material_name', ''),
            'grade_auto': getattr(rm_code, 'grade', ''),
            'finishing_auto': getattr(rm_code, 'finishing', '') or '',
            'wire_diameter_mm_auto': getattr(rm_code, 'wire_diameter_mm', None),
            'thickness_mm_auto': getattr(rm_code, 'thickness_mm', None),
            'kg_auto': getattr(rm_code, 'weight_per_unit_kg', None),
        })
        
        # Auto-populate vendor details
        validated_data.update({
            'vendor_name': vendor,
            'vendor_address_auto': getattr(vendor, 'address', ''),
            'gst_no_auto': getattr(vendor, 'gst_no', ''),
            'mob_no_auto': getattr(vendor, 'contact_no', ''),
            'created_by': self.context['request'].user
        })
        
        # Create the PO instance
        po = super().create(validated_data)
        
        # Create PO creation transaction history
        try:
            from inventory.utils import generate_transaction_id
            
            # Create PO creation transaction history
            transaction_id = generate_transaction_id('PO_CREATED')
            
            POTransactionHistory.objects.create(
                po=po,
                transaction_type='po_created',
                transaction_id=transaction_id,
                description=f'PO {po.po_id} created for {po.rm_code.material_name}',
                details={
                    'quantity_ordered': po.quantity_ordered,
                    'unit_price': float(po.unit_price),
                    'total_amount': float(po.total_amount),
                    'material_name': po.rm_code.material_name,
                    'material_code': po.rm_code.material_code,
                    'vendor_name': po.vendor_name.name,
                    'vendor_address': po.vendor_address_auto,
                    'expected_date': po.expected_date.isoformat() if po.expected_date else None,
                    'created_by': self.context['request'].user.get_full_name() or self.context['request'].user.email
                },
                created_by=self.context['request'].user
            )
            
        except Exception as e:
            logger.warning(f"Failed to create PO creation transaction history: {str(e)}")
        
        return po

    def update(self, instance, validated_data):
        """Update PO with status change tracking"""
        old_status = instance.status
        new_status = validated_data.get('status', old_status)
        
        # Handle rm_code change
        if 'rm_code_id' in validated_data:
            rm_code_id = validated_data.pop('rm_code_id')
            try:
                rm_code = RawMaterial.objects.get(id=rm_code_id)
                validated_data['rm_code'] = rm_code
            except RawMaterial.DoesNotExist:
                raise serializers.ValidationError("Invalid raw material reference")
        
        # Handle vendor change
        if 'vendor_name_id' in validated_data:
            vendor_id = validated_data.pop('vendor_name_id')
            try:
                vendor = Vendor.objects.get(id=vendor_id)
                validated_data['vendor_name'] = vendor
            except Vendor.DoesNotExist:
                raise serializers.ValidationError("Invalid vendor reference")
        
        instance = super().update(instance, validated_data)
        
        # Create status history if status changed
        if old_status != new_status:
            POStatusHistory.objects.create(
                po=instance,
                from_status=old_status,
                to_status=new_status,
                changed_by=self.context['request'].user,
                notes=f"Status changed via API"
            )
        
        return instance


# Utility serializers for dropdown/select options
class ProductDropdownSerializer(serializers.ModelSerializer):
    """Serializer for product dropdown options"""
    
    class Meta:
        model = Product
        fields = ['id', 'product_code']


class RawMaterialDropdownSerializer(serializers.ModelSerializer):
    """Serializer for raw material dropdown options"""
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = RawMaterial
        fields = ['id', 'material_name', 'material_type', 'grade', 'display_name']
    
    def get_display_name(self, obj):
        return str(obj)  # Uses the __str__ method from the model


class VendorDropdownSerializer(serializers.ModelSerializer):
    """Serializer for vendor dropdown options"""
    class Meta:
        model = Vendor
        fields = ['id', 'name', 'vendor_type', 'is_active']


class UserDropdownSerializer(serializers.ModelSerializer):
    """Serializer for user dropdown options"""
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'display_name']
    
    def get_display_name(self, obj):
        if obj.first_name and obj.last_name:
            return f"{obj.first_name} {obj.last_name}"
        return obj.email


class BatchDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for Batch create/update/detail view"""
    mo_details = ManufacturingOrderListSerializer(source='mo', read_only=True)
    product_details = ProductBasicSerializer(source='product_code', read_only=True)
    assigned_operator = UserBasicSerializer(read_only=True)
    assigned_supervisor = UserBasicSerializer(read_only=True)
    created_by = UserBasicSerializer(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    completion_percentage = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    remaining_quantity = serializers.ReadOnlyField()
    
    # Write-only fields for creation
    mo_id = serializers.IntegerField(write_only=True)
    assigned_operator_id = serializers.IntegerField(write_only=True, required=False)
    assigned_supervisor_id = serializers.IntegerField(write_only=True, required=False)
    
    class Meta:
        model = Batch
        fields = [
            'id', 'batch_id', 'mo', 'mo_id', 'mo_details', 'product_code', 'product_details',
            'planned_quantity', 'actual_quantity_started', 'actual_quantity_completed',
            'scrap_quantity', 'scrap_rm_weight', 'planned_start_date', 'planned_end_date', 
            'actual_start_date', 'actual_end_date', 'status', 'status_display',
            'progress_percentage', 'current_process_step', 'assigned_operator',
            'assigned_operator_id', 'assigned_supervisor', 'assigned_supervisor_id',
            'total_processing_time_minutes', 'notes', 'completion_percentage',
            'is_overdue', 'remaining_quantity', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['batch_id', 'mo', 'product_code', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        """Create batch with RM release calculation"""
        logger.info(f"Creating batch with validated_data: {validated_data}")
        
        mo_id = validated_data.pop('mo_id')
        assigned_operator_id = validated_data.pop('assigned_operator_id', None)
        assigned_supervisor_id = validated_data.pop('assigned_supervisor_id', None)
        
        try:
            mo = ManufacturingOrder.objects.select_related('product_code', 'product_code__material').get(id=mo_id)
        except ManufacturingOrder.DoesNotExist:
            raise serializers.ValidationError("Manufacturing Order not found")
        
        # Validate MO status - should be on_hold or in_progress
        if mo.status not in ['on_hold', 'in_progress']:
            raise serializers.ValidationError(
                f"Cannot create batch for MO in {mo.status} status. MO must be in On Hold or In Progress status."
            )
        
        # Handle operator assignment
        operator = None
        if assigned_operator_id:
            try:
                operator = User.objects.get(id=assigned_operator_id)
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid operator reference")
        
        # Handle supervisor assignment
        supervisor = None
        if assigned_supervisor_id:
            try:
                supervisor = User.objects.get(id=assigned_supervisor_id)
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid supervisor reference")
        
        # Calculate RM to release for this batch
        # NOTE: planned_quantity now stores RM in grams (integer) directly from user input
        # User enters RM amount in kg, frontend converts to grams and sends as integer
        
        product = mo.product_code
        batch_quantity_grams = validated_data.get('planned_quantity')
        
        # Convert grams to kg for logging/tracking
        rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
        
        # Apply tolerance to calculate final RM
        tolerance = mo.tolerance_percentage or Decimal('2.00')
        tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
        rm_final_kg = rm_base_kg * tolerance_factor
        
        logger.info(f"Batch RM allocation: Base={rm_base_kg}kg, Tolerance={tolerance}%, Final={rm_final_kg}kg")
        
        # Don't add rm_released_kg to validated_data - Batch model doesn't have this field
        # The RM tracking is done via planned_quantity (in grams)
        
        # Create the batch
        batch = Batch.objects.create(
            mo=mo,
            product_code=mo.product_code,
            assigned_operator=operator,
            assigned_supervisor=supervisor,
            created_by=self.context['request'].user,
            **validated_data
        )
        
        # Update MO status to in_progress if this is the first batch
        if mo.batches.count() == 1:  # This is the first batch
            mo.status = 'in_progress'
            mo.actual_start_date = timezone.now()
            mo.save()
            
            # Create status history
            from .models import MOStatusHistory
            MOStatusHistory.objects.create(
                mo=mo,
                from_status='on_hold',
                to_status='in_progress',
                changed_by=self.context['request'].user,
                notes=f'First batch created: {batch.batch_id}'
            )
            
            # TODO: Create notification for supervisor if assigned
            # NOTE: Supervisor notifications now handled at process execution level
            # since supervisors are no longer assigned at MO level
            # Note: Notification system can be implemented later using Alert model
        
        # Recalculate process progress for all process executions when a new batch is created
        # This ensures that if a process was marked as completed, adding a new batch will update the progress
        # Progress should decrease when a new batch is added (e.g., 1/1=100% → 1/2=50%)
        try:
            process_executions = mo.process_executions.all()
            mo_batches = mo.batches.exclude(status='cancelled')
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
                    execution.progress_percentage = progress_percentage
                    
                    # If process was completed but new batch added, revert to in_progress
                    if execution.status == 'completed' and completed_batches < total_batches:
                        execution.status = 'in_progress'
                        execution.actual_end_time = None
                        logger.info(
                            f"Process {execution.id} ({execution.process.name}) reverted from completed to in_progress "
                            f"because new batch was added. Progress: {completed_batches}/{total_batches} = {progress_percentage}%"
                        )
                    else:
                        logger.info(
                            f"Updated process {execution.id} ({execution.process.name}) progress: "
                            f"{completed_batches}/{total_batches} = {progress_percentage}%"
                        )
                    
                    execution.save(update_fields=['progress_percentage', 'status', 'actual_end_time'])
        except Exception as e:
            logger.error(f"Error updating process progress after batch creation: {e}", exc_info=True)
            # Don't fail batch creation if progress update fails
        
        return batch
    
    def update(self, instance, validated_data):
        """Update batch"""
        # Handle operator change
        if 'assigned_operator_id' in validated_data:
            operator_id = validated_data.pop('assigned_operator_id')
            try:
                operator = User.objects.get(id=operator_id) if operator_id else None
                instance.assigned_operator = operator
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid operator reference")
        
        # Handle supervisor change
        if 'assigned_supervisor_id' in validated_data:
            supervisor_id = validated_data.pop('assigned_supervisor_id')
            try:
                supervisor = User.objects.get(id=supervisor_id) if supervisor_id else None
                instance.assigned_supervisor = supervisor
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid supervisor reference")
        
        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        return instance


# Outsourcing Serializers
class OutsourcedItemSerializer(serializers.ModelSerializer):
    """Serializer for outsourced items"""
    
    class Meta:
        model = OutsourcedItem
        fields = [
            'id', 'mo_number', 'product_code', 'qty', 'kg', 
            'returned_qty', 'returned_kg', 'notes'
        ]
    
    def validate(self, data):
        """Validate that at least qty or kg is provided"""
        if not data.get('qty') and not data.get('kg'):
            raise serializers.ValidationError("Either quantity or weight must be provided")
        return data


class OutsourcedItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating outsourced items"""
    
    class Meta:
        model = OutsourcedItem
        fields = ['mo_number', 'product_code', 'qty', 'kg', 'notes']


class OutsourcingRequestListSerializer(serializers.ModelSerializer):
    """Optimized serializer for outsourcing request list view"""
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    collected_by_name = serializers.CharField(source='collected_by.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_overdue = serializers.ReadOnlyField()
    total_items = serializers.ReadOnlyField()
    total_qty = serializers.ReadOnlyField()
    total_kg = serializers.ReadOnlyField()
    
    class Meta:
        model = OutsourcingRequest
        fields = [
            'id', 'request_id', 'vendor', 'vendor_name', 'date_sent', 
            'expected_return_date', 'status', 'status_display', 'created_by',
            'created_by_name', 'collected_by', 'collected_by_name', 
            'collection_date', 'vendor_contact_person', 'is_overdue',
            'total_items', 'total_qty', 'total_kg', 'created_at', 'updated_at'
        ]
        read_only_fields = ['request_id', 'created_at', 'updated_at']


class OutsourcingRequestDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for outsourcing request create/update/detail view"""
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    collected_by_name = serializers.CharField(source='collected_by.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_overdue = serializers.ReadOnlyField()
    total_items = serializers.ReadOnlyField()
    total_qty = serializers.ReadOnlyField()
    total_kg = serializers.ReadOnlyField()
    items = OutsourcedItemSerializer(many=True, read_only=True)
    
    # Write-only fields for creation
    vendor_id = serializers.IntegerField(write_only=True)
    collected_by_id = serializers.IntegerField(write_only=True, required=False)
    items_data = OutsourcedItemCreateSerializer(many=True, write_only=True, required=False)
    
    class Meta:
        model = OutsourcingRequest
        fields = [
            'id', 'request_id', 'vendor', 'vendor_id', 'vendor_name', 
            'date_sent', 'expected_return_date', 'status', 'status_display',
            'created_by', 'created_by_name', 'collected_by', 'collected_by_id',
            'collected_by_name', 'collection_date', 'vendor_contact_person',
            'notes', 'is_overdue', 'total_items', 'total_qty', 'total_kg',
            'items', 'items_data', 'created_at', 'updated_at'
        ]
        read_only_fields = ['request_id', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        """Create outsourcing request with items"""
        vendor_id = validated_data.pop('vendor_id')
        collected_by_id = validated_data.pop('collected_by_id', None)
        items_data = validated_data.pop('items_data', [])
        
        try:
            vendor = Vendor.objects.get(id=vendor_id)
        except Vendor.DoesNotExist:
            raise serializers.ValidationError("Invalid vendor reference")
        
        # Handle collected_by user
        collected_by = None
        if collected_by_id:
            try:
                collected_by = User.objects.get(id=collected_by_id)
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid collected_by user reference")
        
        # Create the request
        request = OutsourcingRequest.objects.create(
            vendor=vendor,
            collected_by=collected_by,
            created_by=self.context['request'].user,
            **validated_data
        )
        
        # Create items
        for item_data in items_data:
            OutsourcedItem.objects.create(request=request, **item_data)
        
        return request
    
    def update(self, instance, validated_data):
        """Update outsourcing request"""
        # Handle vendor change
        if 'vendor_id' in validated_data:
            vendor_id = validated_data.pop('vendor_id')
            try:
                vendor = Vendor.objects.get(id=vendor_id)
                instance.vendor = vendor
            except Vendor.DoesNotExist:
                raise serializers.ValidationError("Invalid vendor reference")
        
        # Handle collected_by change
        if 'collected_by_id' in validated_data:
            collected_by_id = validated_data.pop('collected_by_id')
            try:
                collected_by = User.objects.get(id=collected_by_id) if collected_by_id else None
                instance.collected_by = collected_by
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid collected_by user reference")
        
        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        return instance


class OutsourcingRequestSendSerializer(serializers.Serializer):
    """Serializer for sending outsourcing request"""
    date_sent = serializers.DateField()
    vendor_contact_person = serializers.CharField(max_length=100, required=False, allow_blank=True)
    
    def validate_date_sent(self, value):
        """Validate date_sent is not in the future"""
        if value > timezone.now().date():
            raise serializers.ValidationError("Date sent cannot be in the future")
        return value


class OutsourcingRequestReturnSerializer(serializers.Serializer):
    """Serializer for marking outsourcing request as returned"""
    collection_date = serializers.DateField()
    collected_by_id = serializers.IntegerField()
    returned_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of returned items with returned_qty and returned_kg"
    )
    
    def validate_collection_date(self, value):
        """Validate collection_date is not in the future"""
        if value > timezone.now().date():
            raise serializers.ValidationError("Collection date cannot be in the future")
        return value
    
    def validate_collected_by_id(self, value):
        """Validate collected_by user exists"""
        try:
            User.objects.get(id=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("Invalid collected_by user reference")
        return value
    
    def validate_returned_items(self, value):
        """Validate returned items data"""
        if not value:
            raise serializers.ValidationError("At least one returned item must be provided")
        
        for item in value:
            if 'id' not in item:
                raise serializers.ValidationError("Each returned item must have an id")
            if 'returned_qty' not in item and 'returned_kg' not in item:
                raise serializers.ValidationError("Each returned item must have returned_qty or returned_kg")
        
        return value


# Raw Material Allocation Serializers

class RMAllocationHistorySerializer(serializers.ModelSerializer):
    """Serializer for RM allocation history"""
    performed_by = UserBasicSerializer(read_only=True)
    from_mo_id = serializers.CharField(source='from_mo.mo_id', read_only=True)
    to_mo_id = serializers.CharField(source='to_mo.mo_id', read_only=True)
    
    class Meta:
        model = RMAllocationHistory
        fields = [
            'id', 'action', 'from_mo', 'from_mo_id', 'to_mo', 'to_mo_id',
            'quantity_kg', 'performed_by', 'performed_at', 'reason'
        ]
        read_only_fields = fields


class RawMaterialAllocationSerializer(serializers.ModelSerializer):
    """Serializer for raw material allocations"""
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    mo_priority = serializers.CharField(source='mo.priority', read_only=True)
    mo_status = serializers.CharField(source='mo.status', read_only=True)
    raw_material_code = serializers.CharField(source='raw_material.material_code', read_only=True)
    raw_material_name = serializers.CharField(source='raw_material.material_name', read_only=True)
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    allocated_by_name = serializers.CharField(source='allocated_by.get_full_name', read_only=True)
    locked_by_name = serializers.CharField(source='locked_by.get_full_name', read_only=True)
    swapped_to_mo_id = serializers.CharField(source='swapped_to_mo.mo_id', read_only=True)
    history = RMAllocationHistorySerializer(many=True, read_only=True)

    class Meta:
        model = RawMaterialAllocation
        fields = [
            'id', 'mo', 'mo_id', 'mo_priority', 'mo_status',
            'raw_material', 'raw_material_code', 'raw_material_name', 'raw_material_details',
            'allocated_quantity_kg', 'status', 'can_be_swapped',
            'swapped_to_mo', 'swapped_to_mo_id', 'swapped_at', 'swapped_by', 'swap_reason',
            'locked_at', 'locked_by', 'locked_by_name',
            'allocated_at', 'allocated_by', 'allocated_by_name',
            'notes', 'history'
        ]
        read_only_fields = [
            'id', 'mo_id', 'mo_priority', 'mo_status', 'raw_material_code', 'raw_material_name',
            'raw_material_details', 'status', 'swapped_at', 'swapped_by', 'locked_at', 'locked_by',
            'allocated_at', 'allocated_by', 'allocated_by_name', 'locked_by_name',
            'swapped_to_mo_id', 'history'
        ]


class RawMaterialAllocationMinimalSerializer(serializers.ModelSerializer):
    """Highly optimized minimal serializer for RM allocations in production-head MO detail page"""
    raw_material_name = serializers.CharField(source='raw_material.material_name', read_only=True)
    allocated_by_name = serializers.CharField(source='allocated_by.get_full_name', read_only=True)

    class Meta:
        model = RawMaterialAllocation
        # Highly optimized: Only include fields used in production-head MO detail page
        # REMOVED UNUSED FIELDS: mo, mo_id, mo_priority, mo_status, raw_material, raw_material_code,
        # raw_material_details, can_be_swapped, swapped_to_mo, swapped_to_mo_id, swapped_at,
        # swapped_by, swap_reason, locked_at, locked_by, locked_by_name, notes, history
        fields = [
            'raw_material_name', 'allocated_quantity_kg', 'status',
            'allocated_at', 'allocated_by_name'
        ]


class RMAllocationSwapSerializer(serializers.Serializer):
    """Serializer for swapping RM allocation to another MO"""
    target_mo_id = serializers.IntegerField(help_text="ID of the MO to swap allocation to")
    reason = serializers.CharField(
        required=False, 
        allow_blank=True,
        help_text="Reason for swapping"
    )
    
    def validate_target_mo_id(self, value):
        """Validate target MO exists"""
        try:
            ManufacturingOrder.objects.get(id=value)
        except ManufacturingOrder.DoesNotExist:
            raise serializers.ValidationError("Target MO not found")
        return value


class RMAllocationCheckSerializer(serializers.Serializer):
    """Serializer for checking RM availability for MO"""
    mo_id = serializers.IntegerField(help_text="Manufacturing Order ID")
    
    def validate_mo_id(self, value):
        """Validate MO exists"""
        try:
            ManufacturingOrder.objects.get(id=value)
        except ManufacturingOrder.DoesNotExist:
            raise serializers.ValidationError("Manufacturing Order not found")
        return value


class FGReservationSerializer(serializers.Serializer):
    """Serializer for FG Stock Reservations"""
    reservation_id = serializers.CharField(read_only=True)
    product = serializers.SerializerMethodField()
    quantity = serializers.IntegerField()
    reservation_type = serializers.CharField()
    status = serializers.CharField()
    reserved_at = serializers.DateTimeField(read_only=True)
    
    def get_product(self, obj):
        return {
            'product_code': obj.product_code.product_code,
            'product_name': str(obj.product_code)
        }


class MOResourceStatusSerializer(serializers.Serializer):
    """Serializer for MO resource status summary"""
    reserved_rm = serializers.SerializerMethodField()
    allocated_rm = serializers.SerializerMethodField()
    reserved_fg = serializers.SerializerMethodField()
    in_progress_batches = serializers.SerializerMethodField()
    pending_batches = serializers.SerializerMethodField()
    
    def get_reserved_rm(self, mo):
        """Get reserved RM allocations"""
        allocations = mo.rm_allocations.filter(status='reserved')
        return [{
            'material': str(allocation.raw_material),
            'material_code': allocation.raw_material.material_code,
            'quantity_kg': float(allocation.allocated_quantity_kg),
            'status': allocation.status,
            'allocation_id': allocation.id
        } for allocation in allocations]
    
    def get_allocated_rm(self, mo):
        """Get locked/allocated RM"""
        allocations = mo.rm_allocations.filter(status='locked')
        return [{
            'material': str(allocation.raw_material),
            'material_code': allocation.raw_material.material_code,
            'quantity_kg': float(allocation.allocated_quantity_kg),
            'status': allocation.status,
            'allocation_id': allocation.id
        } for allocation in allocations]
    
    def get_reserved_fg(self, mo):
        """Get reserved FG stock"""
        from fg_store.models import FGStockReservation
        reservations = FGStockReservation.objects.filter(mo=mo, status='reserved')
        return [{
            'product': str(reservation.product_code),
            'product_code': reservation.product_code.product_code,
            'quantity': reservation.quantity,
            'reservation_type': reservation.reservation_type,
            'reservation_id': reservation.reservation_id
        } for reservation in reservations]
    
    def get_in_progress_batches(self, mo):
        """Get batches currently in production"""
        batches = mo.batches.filter(status__in=['in_process', 'quality_check'])
        return [{
            'batch_id': batch.batch_id,
            'status': batch.status,
            'planned_quantity': batch.planned_quantity,
            'actual_quantity_completed': batch.actual_quantity_completed
        } for batch in batches]
    
    def get_pending_batches(self, mo):
        """Get batches not yet started"""
        batches = mo.batches.filter(status='created')
        return [{
            'batch_id': batch.batch_id,
            'status': batch.status,
            'planned_quantity': batch.planned_quantity,
            'can_release': batch.can_release
        } for batch in batches]


class MOStopSerializer(serializers.Serializer):
    """Serializer for stopping an MO"""
    stop_reason = serializers.CharField(
        required=True,
        help_text="Reason for stopping this MO"
    )
    
    def validate_stop_reason(self, value):
        if not value or len(value.strip()) < 10:
            raise serializers.ValidationError(
                "Stop reason must be at least 10 characters long"
            )
        return value.strip()


class MOPriorityQueueSerializer(serializers.ModelSerializer):
    """Serializer for MO priority queue"""
    product_code_display = serializers.CharField(source='product_code.product_code', read_only=True)
    customer_name = serializers.CharField(source='customer_c_id.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    
    reserved_rm_count = serializers.SerializerMethodField()
    allocated_rm_count = serializers.SerializerMethodField()
    reserved_fg_count = serializers.SerializerMethodField()
    can_be_stopped = serializers.SerializerMethodField()
    
    class Meta:
        model = ManufacturingOrder
        fields = [
            'id', 'mo_id', 'product_code_display', 'customer_name',
            'quantity', 'status', 'status_display', 'priority', 'priority_display',
            'priority_level', 'planned_start_date', 'planned_end_date',
            'reserved_rm_count', 'allocated_rm_count', 'reserved_fg_count',
            'can_be_stopped', 'created_at'
        ]
        read_only_fields = fields
    
    def get_reserved_rm_count(self, obj):
        return obj.rm_allocations.filter(status='reserved').count()
    
    def get_allocated_rm_count(self, obj):
        return obj.rm_allocations.filter(status='locked').count()
    
    def get_reserved_fg_count(self, obj):
        from fg_store.models import FGStockReservation
        return FGStockReservation.objects.filter(mo=obj, status='reserved').count()
    
    def get_can_be_stopped(self, obj):
        return obj.status in ['on_hold', 'rm_allocated', 'in_progress']
