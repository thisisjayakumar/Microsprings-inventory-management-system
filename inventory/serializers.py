from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from products.models import Product
from .models import (
    RMStockBalance, RawMaterial, InventoryTransaction, Location,
    GRMReceipt, HeatNumber, RMStockBalanceHeat, InventoryTransactionHeat,
    HandoverIssue, RMReturn
)


class HeatNumberDetailSerializer(serializers.ModelSerializer):
    """Serializer for heat number details with coil/sheet information"""
    grm_number = serializers.SerializerMethodField()
    coils_received = serializers.IntegerField(read_only=True)
    sheets_received = serializers.IntegerField(read_only=True)
    total_weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3, read_only=True)
    available_quantity_kg = serializers.SerializerMethodField()
    available_coils = serializers.SerializerMethodField()
    available_sheets = serializers.SerializerMethodField()
    items = serializers.JSONField(read_only=True)
    
    class Meta:
        model = HeatNumber
        fields = [
            'id', 'heat_number', 'grm_number', 'coils_received', 'sheets_received',
            'total_weight_kg', 'available_quantity_kg', 'available_coils', 'available_sheets',
            'items', 'is_available', 'created_at'
        ]
        read_only_fields = fields
    
    def get_grm_number(self, obj):
        """Safely get GRM number, handling missing grm_receipt relationship"""
        try:
            # Try to access grm_receipt if it exists
            if hasattr(obj, 'grm_receipt') and obj.grm_receipt:
                return obj.grm_receipt.grm_number
            # Try to get it via the foreign key ID if available
            if hasattr(obj, 'grm_receipt_id') and obj.grm_receipt_id:
                from .models import GRMReceipt
                try:
                    grm = GRMReceipt.objects.get(id=obj.grm_receipt_id)
                    return grm.grm_number
                except GRMReceipt.DoesNotExist:
                    return None
        except Exception:
            pass
        return None
    
    def get_available_quantity_kg(self, obj):
        """Get remaining available quantity in KG"""
        return float(obj.get_available_quantity_kg()) if obj.total_weight_kg else 0.0
    
    def get_available_coils(self, obj):
        """Get remaining available coils"""
        return obj.get_available_coils() if obj.raw_material.material_type == 'coil' else None
    
    def get_available_sheets(self, obj):
        """Get remaining available sheets"""
        return obj.sheets_received if obj.raw_material.material_type == 'sheet' and obj.is_available else None


class RawMaterialBasicSerializer(serializers.ModelSerializer):
    """Optimized raw material serializer - returns only fields shown in frontend"""
    material_name_display = serializers.CharField(source='material_name', read_only=True)
    material_type_display = serializers.CharField(source='get_material_type_display', read_only=True)
    finishing_display = serializers.CharField(source='get_finishing_display', read_only=True)
    available_quantity = serializers.SerializerMethodField()
    heat_numbers = serializers.SerializerMethodField()
    
    class Meta:
        model = RawMaterial
        fields = [
            'id', 'material_code', 'material_name_display',
            'material_type_display', 'grade', 'wire_diameter_mm',
            'finishing_display', 'available_quantity', 'heat_numbers'
        ]
        read_only_fields = fields
    
    def get_available_quantity(self, obj):
        """Get available quantity from RMStockBalance"""
        stock_balance = RMStockBalance.objects.filter(raw_material=obj).first()
        if stock_balance:
            return float(stock_balance.available_quantity)
        return 0.0
    
    def get_heat_numbers(self, obj):
        """Get heat numbers with coil/sheet details for this raw material"""
        try:
            # Query HeatNumber directly using raw_material_id, excluding grm_receipt_id
            # Use .only() to only select columns that exist, avoiding grm_receipt_id
            heat_numbers = HeatNumber.objects.filter(
                raw_material_id=obj.id,
                is_available=True
            ).only(
                'id', 'heat_number', 'raw_material_id',
                'coils_received', 'sheets_received', 'total_weight_kg',
                'items', 'is_available', 'consumed_quantity_kg', 'created_at'
            ).order_by('-created_at')
            
            if not heat_numbers.exists():
                return "NIL"
            
            serializer = HeatNumberDetailSerializer(heat_numbers, many=True)
            return serializer.data
        except Exception as e:
            # If querying heat_numbers fails due to missing database column,
            # return empty list or "NIL" instead of crashing
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not fetch heat_numbers for raw material {obj.id}: {str(e)}")
            return "NIL"


class RawMaterialCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating raw materials"""
    
    class Meta:
        model = RawMaterial
        fields = [
            'material_code', 'material_name', 'material_type', 'grade', 'finishing',
            'wire_diameter_mm', 'weight_kg', 'thickness_mm', 
            'length_mm', 'breadth_mm', 'quantity'
        ]
    
    def validate_material_code(self, value):
        """Validate material code uniqueness"""
        if RawMaterial.objects.filter(material_code=value).exists():
            raise serializers.ValidationError("Material code already exists.")
        return value
    
    def validate(self, data):
        """Validate raw material data based on material type"""
        material_type = data.get('material_type')
        wire_diameter_mm = data.get('wire_diameter_mm')
        thickness_mm = data.get('thickness_mm')
        weight_kg = data.get('weight_kg')
        quantity = data.get('quantity')
        
        errors = {}
        
        if material_type == 'coil':
            if not wire_diameter_mm:
                errors['wire_diameter_mm'] = "Wire diameter is required for Coil type materials"
            if thickness_mm:
                errors['thickness_mm'] = "Thickness should not be set for Coil type materials"
        elif material_type == 'sheet':
            if not thickness_mm:
                errors['thickness_mm'] = "Thickness is required for Sheet type materials"
            if wire_diameter_mm:
                errors['wire_diameter_mm'] = "Wire diameter should not be set for Sheet type materials"
            if weight_kg:
                errors['weight_kg'] = "Weight should not be set for Sheet type materials (use quantity instead)"
        
        # Validate positive values
        if wire_diameter_mm is not None and wire_diameter_mm <= 0:
            errors['wire_diameter_mm'] = "Wire diameter must be greater than 0"
        
        if weight_kg is not None and weight_kg <= 0:
            errors['weight_kg'] = "Weight must be greater than 0"
        
        if thickness_mm is not None and thickness_mm <= 0:
            errors['thickness_mm'] = "Thickness must be greater than 0"
        
        if errors:
            raise serializers.ValidationError(errors)
        
        return data


class ProductListSerializer(serializers.ModelSerializer):
    """Product serializer for list view with stock balance"""
    material_details = RawMaterialBasicSerializer(source='material', read_only=True)
    product_type_display = serializers.CharField(source='get_product_type_display', read_only=True)
    spring_type_display = serializers.CharField(source='get_spring_type_display', read_only=True)
    customer_name = serializers.CharField(source='customer_c_id.name', read_only=True)
    customer_id = serializers.CharField(source='customer_c_id.c_id', read_only=True)
    stock_balance = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'internal_product_code', 'product_code', 'product_type', 
            'product_type_display', 'spring_type', 'spring_type_display',
            'material_details', 'customer_name', 'customer_id', 'stock_balance', 
            'grams_per_product', 'length_mm', 'breadth_mm',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_stock_balance(self, obj):
        """Get stock balance for the product"""
        try:
            stock_balance = obj.stock_balances.first()
            if stock_balance:
                return {
                    'available_quantity': stock_balance.available_quantity,
                    'last_updated': stock_balance.last_updated
                }
            return {
                'available_quantity': 0,
                'last_updated': None
            }
        except:
            return {
                'available_quantity': 0,
                'last_updated': None
            }


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """Product serializer for create/update operations"""
    
    class Meta:
        model = Product
        fields = [
            'id', 'internal_product_code', 'product_code', 'product_type', 
            'spring_type', 'material', 'customer_c_id', 'grams_per_product',
            'length_mm', 'breadth_mm'
        ]
        read_only_fields = ['id']
    
    def validate_internal_product_code(self, value):
        """Validate internal product code uniqueness"""
        if value:
            instance = getattr(self, 'instance', None)
            if instance:
                # Update case - exclude current instance
                if Product.objects.filter(internal_product_code=value).exclude(id=instance.id).exists():
                    raise serializers.ValidationError("Internal product code already exists.")
            else:
                # Create case
                if Product.objects.filter(internal_product_code=value).exists():
                    raise serializers.ValidationError("Internal product code already exists.")
        return value
    
    def validate_product_code(self, value):
        """Validate product code uniqueness"""
        instance = getattr(self, 'instance', None)
        if instance:
            # Update case - exclude current instance
            if Product.objects.filter(product_code=value).exclude(id=instance.id).exists():
                raise serializers.ValidationError("Product code already exists.")
        else:
            # Create case
            if Product.objects.filter(product_code=value).exists():
                raise serializers.ValidationError("Product code already exists.")
        return value
    
    def create(self, validated_data):
        """Create product with audit trail"""
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class RMStockBalanceSerializer(serializers.ModelSerializer):
    """RMStockBalance serializer for CRUD operations"""
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    material_code = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = RMStockBalance
        fields = [
            'id', 'raw_material', 'raw_material_details', 'material_code',
            'available_quantity', 'last_updated'
        ]
        read_only_fields = ['id', 'last_updated']
    
    def validate_available_quantity(self, value):
        """Validate available quantity is not negative"""
        if value < 0:
            raise serializers.ValidationError("Available quantity cannot be negative.")
        return value
    
    def create(self, validated_data):
        """Create or update stock balance using update_or_create"""
        material_code = validated_data.pop('material_code', None)
        
        if material_code:
            try:
                raw_material = RawMaterial.objects.only("id").get(material_code=material_code)
                validated_data['raw_material'] = raw_material
            except RawMaterial.DoesNotExist:
                raise serializers.ValidationError({"material_code": "Raw material with this code does not exist."})
        
        raw_material = validated_data['raw_material']
        available_quantity = validated_data['available_quantity']
        
        # Use update_or_create for upsert behavior
        stock_balance, created = RMStockBalance.objects.update_or_create(
            raw_material=raw_material,
            defaults={'available_quantity': available_quantity}
        )
        
        return stock_balance
    
    def update(self, instance, validated_data):
        """Update stock balance"""
        material_code = validated_data.pop('material_code', None)
        
        if material_code:
            try:
                raw_material = RawMaterial.objects.only("id").get(material_code=material_code)
                validated_data['raw_material'] = raw_material
            except RawMaterial.DoesNotExist:
                raise serializers.ValidationError({"material_code": "Raw material with this code does not exist."})
        
        return super().update(instance, validated_data)


class RMStockBalanceUpdateSerializer(serializers.Serializer):
    """Serializer for bulk stock balance updates using material_code"""
    material_code = serializers.CharField(max_length=120)
    available_quantity = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0)
    
    def validate_material_code(self, value):
        """Validate that raw material exists"""
        try:
            RawMaterial.objects.only("id").get(material_code=value)
        except RawMaterial.DoesNotExist:
            raise serializers.ValidationError("Raw material with this code does not exist.")
        return value


class ProductStockDashboardSerializer(serializers.ModelSerializer):
    """Combined serializer for RM Store dashboard showing products with stock"""
    material = RawMaterialBasicSerializer(read_only=True)
    material_name = serializers.CharField(read_only=True)
    material_type_display = serializers.CharField(read_only=True)
    product_type_display = serializers.CharField(source='get_product_type_display', read_only=True)
    spring_type_display = serializers.CharField(source='get_spring_type_display', read_only=True)
    customer_name = serializers.CharField(source='customer_c_id.name', read_only=True)
    customer_id = serializers.CharField(source='customer_c_id.c_id', read_only=True)
    stock_info = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'internal_product_code', 'product_code', 'product_type',
            'product_type_display', 'spring_type', 'spring_type_display',
            'material', 'material_name', 'material_type_display', 'customer_name', 
            'customer_id', 'stock_info'
        ]
    
    def get_stock_info(self, obj):
        """Get comprehensive stock information from material's stock balance"""
        try:
            # Access stock balance through the material relationship
            if obj.material and hasattr(obj.material, 'stock_balances'):
                stock_balance = obj.material.stock_balances.first()
                if stock_balance:
                    return {
                        'available_quantity': stock_balance.available_quantity,
                        'last_updated': stock_balance.last_updated,
                        'stock_status': 'in_stock' if stock_balance.available_quantity > 0 else 'out_of_stock'
                    }
            return {
                'available_quantity': 0,
                'last_updated': None,
                'stock_status': 'no_stock_record'
            }
        except Exception as e:
            return {
                'available_quantity': 0,
                'last_updated': None,
                'stock_status': 'error'
            }


class LocationBasicSerializer(serializers.ModelSerializer):
    """Minimal serializer for inventory locations"""

    class Meta:
        model = Location
        fields = ['id', 'location_name', 'parent_location']


class InventoryTransactionSerializer(serializers.ModelSerializer):
    """Serializer for inventory transactions"""
    product_display = serializers.SerializerMethodField()
    raw_material_display = serializers.SerializerMethodField()
    mo_display = serializers.CharField(source='manufacturing_order.mo_id', read_only=True)
    location_from_details = LocationBasicSerializer(source='location_from', read_only=True)
    location_to_details = LocationBasicSerializer(source='location_to', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = [
            'id', 'transaction_id', 'transaction_type', 'product', 'product_display',
            'raw_material_display', 'manufacturing_order', 'mo_display',
            'location_from', 'location_from_details',
            'location_to', 'location_to_details', 'quantity', 'unit_cost',
            'total_value', 'transaction_datetime', 'created_at', 'created_by', 'created_by_name',
            'reference_type', 'reference_id', 'notes',
        ]
        read_only_fields = fields

    def get_product_display(self, obj):
        if obj.product:
            if hasattr(obj.product, 'internal_product_code') and obj.product.internal_product_code:
                return f"{obj.product.internal_product_code} - {obj.product.product_code}"
            return getattr(obj.product, 'product_code', None)
        return None
    
    def get_raw_material_display(self, obj):
        if obj.product and hasattr(obj.product, 'material') and obj.product.material:
            return f"{obj.product.material.material_code} - {obj.product.material.material_name}"
        return None


# GRM and Heat Number Serializers

class HeatNumberSerializer(serializers.ModelSerializer):
    """Serializer for Heat Number model"""
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    available_quantity_kg = serializers.SerializerMethodField()
    available_coils = serializers.SerializerMethodField()
    grm_number = serializers.CharField(source='grm_receipt.grm_number', read_only=True)
    
    class Meta:
        model = HeatNumber
        fields = [
            'id', 'heat_number', 'grm_receipt', 'raw_material', 'raw_material_details',
            'coils_received', 'total_weight_kg', 'sheets_received', 'test_certificate_date',
            'items', 'is_available', 'consumed_quantity_kg', 'available_quantity_kg', 'available_coils',
            'grm_number', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'grm_receipt', 'is_available', 'consumed_quantity_kg', 'created_at', 'updated_at', 'available_quantity_kg', 'available_coils']
    
    def get_available_quantity_kg(self, obj):
        """Get remaining available quantity in KG"""
        return float(obj.get_available_quantity_kg())
    
    def get_available_coils(self, obj):
        """Get remaining available coils"""
        return obj.get_available_coils()
    
    def validate(self, data):
        """Validate heat number data"""
        raw_material = data.get('raw_material')
        coils_received = data.get('coils_received', 0)
        sheets_received = data.get('sheets_received', 0)
        total_weight_kg = data.get('total_weight_kg')
        
        # Validate total weight
        if total_weight_kg is None or total_weight_kg <= 0:
            raise serializers.ValidationError({
                'total_weight_kg': 'Total weight must be greater than 0'
            })
        
        # Check if raw material is provided and validate quantities based on material type
        if raw_material:
            if raw_material.material_type == 'coil':
                if coils_received <= 0:
                    raise serializers.ValidationError({
                        'coils_received': 'Number of coils must be specified for coil materials'
                    })
                if sheets_received > 0:
                    raise serializers.ValidationError({
                        'sheets_received': 'Sheets should not be specified for coil materials'
                    })
            elif raw_material.material_type == 'sheet':
                if sheets_received <= 0:
                    raise serializers.ValidationError({
                        'sheets_received': 'Number of sheets must be specified for sheet materials'
                    })
                if coils_received > 0:
                    raise serializers.ValidationError({
                        'coils_received': 'Coils should not be specified for sheet materials'
                    })
        
        return data


class GRMReceiptSerializer(serializers.ModelSerializer):
    """Serializer for GRM Receipt model"""
    purchase_order_details = serializers.SerializerMethodField()
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True)
    quality_check_by_name = serializers.CharField(source='quality_check_by.get_full_name', read_only=True)
    heat_numbers = HeatNumberSerializer(many=True, read_only=True)
    completion_percentage = serializers.SerializerMethodField()
    
    class Meta:
        model = GRMReceipt
        fields = [
            'id', 'grm_number', 'purchase_order', 'purchase_order_details',
            'truck_number', 'driver_name', 'receipt_date',
            'received_by', 'received_by_name', 'status', 'total_items_received',
            'total_items_expected', 'quality_check_passed',
            'quality_check_by', 'quality_check_by_name', 'quality_check_date',
            'heat_numbers', 'completion_percentage', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'grm_number', 'created_at', 'updated_at', 'completion_percentage']
    
    def get_purchase_order_details(self, obj):
        """Get purchase order details"""
        if obj.purchase_order:
            return {
                'po_id': obj.purchase_order.po_id,
                'vendor_name': obj.purchase_order.vendor_name.name if obj.purchase_order.vendor_name else None,
                'expected_date': obj.purchase_order.expected_date,
                'quantity_ordered': obj.purchase_order.quantity_ordered,
                'total_amount': obj.purchase_order.total_amount
            }
        return None
    
    def get_completion_percentage(self, obj):
        """Calculate completion percentage"""
        if obj.total_items_expected == 0:
            return 0
        return (obj.total_items_received / obj.total_items_expected) * 100


class GRMReceiptListSerializer(serializers.ModelSerializer):
    """Simplified serializer for GRM Receipt list view"""
    po_id = serializers.CharField(source='purchase_order.po_id', read_only=True)
    vendor_name = serializers.CharField(source='purchase_order.vendor_name.name', read_only=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    quantity_ordered = serializers.DecimalField(
        source='purchase_order.quantity_ordered',
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    quantity_received = serializers.DecimalField(
        source='purchase_order.quantity_received',
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    
    class Meta:
        model = GRMReceipt
        fields = [
            'id', 'grm_number', 'po_id', 'vendor_name', 'truck_number',
            'driver_name', 'receipt_date', 'received_by_name', 'status',
            'status_display', 'quantity_ordered', 'quantity_received',
            'quality_check_passed', 'created_at'
        ]


class GRMReceiptCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating GRM Receipt"""
    heat_numbers_data = HeatNumberSerializer(many=True, write_only=True)
    
    class Meta:
        model = GRMReceipt
        fields = [
            'purchase_order', 'truck_number', 'driver_name', 'heat_numbers_data'
        ]
    
    def validate(self, data):
        """Validate GRM receipt data"""
        purchase_order = data.get('purchase_order')
        heat_numbers_data = data.get('heat_numbers_data', [])
        
        # Check if active GRM already exists for this PO (exclude cancelled)
        if purchase_order and GRMReceipt.objects.filter(
            purchase_order=purchase_order
        ).exclude(status='cancelled').exists():
            raise serializers.ValidationError({
                'purchase_order': 'An active GRM receipt already exists for this Purchase Order'
            })
        
        # Validate heat numbers data
        if not heat_numbers_data:
            raise serializers.ValidationError({
                'heat_numbers_data': 'At least one heat number is required'
            })
        
        # Calculate total weight from heat numbers
        total_weight = Decimal('0')
        for heat_data in heat_numbers_data:
            weight = heat_data.get('total_weight_kg', 0)
            if weight:
                total_weight += Decimal(str(weight))
        
        # Set the calculated quantity_received
        data['quantity_received'] = total_weight
        
        return data
    
    def create(self, validated_data):
        """Create GRM receipt with heat numbers"""
        from decimal import Decimal
        from inventory.models import RMStockBalance
        
        heat_numbers_data = validated_data.pop('heat_numbers_data', [])
        quantity_received = validated_data.pop('quantity_received', None)
        
        # Set received_by from context
        validated_data['received_by'] = self.context['request'].user
        
        # Create GRM receipt
        grm_receipt = GRMReceipt.objects.create(**validated_data)
        
        # Update PO with received quantity
        if quantity_received and grm_receipt.purchase_order:
            grm_receipt.purchase_order.quantity_received = quantity_received
            grm_receipt.purchase_order.save()
        
        # Create heat numbers and update stock balances
        for i, heat_data in enumerate(heat_numbers_data):
            try:
                heat_number = HeatNumber.objects.create(grm_receipt=grm_receipt, **heat_data)
                
                # Update RMStockBalance available_quantity for this raw material
                raw_material = heat_data.get('raw_material')
                total_weight = heat_data.get('total_weight_kg', 0)
                
                if raw_material and total_weight:
                    stock_balance, created = RMStockBalance.objects.get_or_create(
                        raw_material=raw_material,
                        defaults={'available_quantity': Decimal('0')}
                    )
                    # Add the weight to available quantity
                    stock_balance.available_quantity += Decimal(str(total_weight))
                    stock_balance.save()
                    
                # Update heat-tracked stock balance
                heat_number.update_stock_balance()
            except Exception as e:
                raise e
        
        # Update completion status
        grm_receipt.total_items_received = len(heat_numbers_data)
        grm_receipt.total_items_expected = quantity_received or 0
        grm_receipt.save()
        
        return grm_receipt


class RMStockBalanceHeatSerializer(serializers.ModelSerializer):
    """Enhanced stock balance serializer with heat number tracking"""
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    heat_numbers_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = RMStockBalanceHeat
        fields = [
            'id', 'raw_material', 'raw_material_details', 'total_available_quantity_kg',
            'total_coils_available', 'total_sheets_available', 'active_heat_numbers_count',
            'heat_numbers_summary', 'last_updated'
        ]
        read_only_fields = fields
    
    def get_heat_numbers_summary(self, obj):
        """Get summary of heat numbers for this raw material"""
        heat_numbers = HeatNumber.objects.filter(
            raw_material=obj.raw_material,
            is_available=True
        ).order_by('-created_at')[:5]  # Get latest 5 heat numbers
        
        return [
            {
                'heat_number': heat.heat_number,
                'grm_number': heat.grm_receipt.grm_number,
                'available_quantity_kg': float(heat.get_available_quantity_kg()),
                'coils_available': heat.get_available_coils(),
                'created_at': heat.created_at
            }
            for heat in heat_numbers
        ]


class InventoryTransactionHeatSerializer(serializers.ModelSerializer):
    """Serializer for heat-tracked inventory transactions"""
    heat_number_details = HeatNumberSerializer(source='heat_number', read_only=True)
    transaction_details = InventoryTransactionSerializer(source='inventory_transaction', read_only=True)
    
    class Meta:
        model = InventoryTransactionHeat
        fields = [
            'id', 'inventory_transaction', 'transaction_details', 'heat_number',
            'heat_number_details', 'quantity_kg', 'coils_count', 'sheets_count',
            'grm_number'
        ]
        read_only_fields = ['id']


class GRMReceiptListSerializer(serializers.ModelSerializer):
    """Simplified serializer for GRM receipt list view"""
    purchase_order_po_id = serializers.CharField(source='purchase_order.po_id', read_only=True)
    vendor_name = serializers.CharField(source='purchase_order.vendor_name.name', read_only=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True)
    heat_numbers_count = serializers.SerializerMethodField()
    
    class Meta:
        model = GRMReceipt
        fields = [
            'id', 'grm_number', 'purchase_order_po_id', 'vendor_name',
            'truck_number', 'driver_name', 'status', 'total_items_received',
            'total_items_expected', 'received_by_name', 'receipt_date',
            'heat_numbers_count', 'quality_check_passed'
        ]
    
    def get_heat_numbers_count(self, obj):
        """Get count of heat numbers in this GRM"""
        return obj.heat_numbers.count()


# Handover Verification Serializers

class HeatNumberHandoverSerializer(serializers.ModelSerializer):
    """Serializer for heat numbers pending handover verification"""
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    grm_number = serializers.CharField(source='grm_receipt.grm_number', read_only=True)
    handover_status_display = serializers.CharField(source='get_handover_status_display', read_only=True)
    verified_by_name = serializers.CharField(source='verified_by.get_full_name', read_only=True)
    
    class Meta:
        model = HeatNumber
        fields = [
            'id', 'heat_number', 'grm_number', 'raw_material', 'raw_material_details',
            'total_weight_kg', 'coils_received', 'sheets_received', 'handover_status',
            'handover_status_display', 'verified_at', 'verified_by', 'verified_by_name',
            'created_at'
        ]
        read_only_fields = fields


class HandoverIssueSerializer(serializers.ModelSerializer):
    """Serializer for handover issues"""
    heat_number_details = HeatNumberHandoverSerializer(source='heat_number', read_only=True)
    issue_type_display = serializers.CharField(source='get_issue_type_display', read_only=True)
    reported_by_name = serializers.CharField(source='reported_by.get_full_name', read_only=True)
    resolved_by_name = serializers.CharField(source='resolved_by.get_full_name', read_only=True)
    
    class Meta:
        model = HandoverIssue
        fields = [
            'id', 'heat_number', 'heat_number_details', 'issue_type', 'issue_type_display',
            'actual_weight', 'remarks', 'reported_by', 'reported_by_name', 'reported_at',
            'is_resolved', 'resolved_at', 'resolved_by', 'resolved_by_name', 'resolution_notes'
        ]
        read_only_fields = ['id', 'reported_at']


class HandoverIssueCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating handover issues"""
    
    class Meta:
        model = HandoverIssue
        fields = ['heat_number', 'issue_type', 'actual_weight', 'remarks']
    
    def create(self, validated_data):
        """Create handover issue and update heat number status"""
        validated_data['reported_by'] = self.context['request'].user
        
        # Create the issue
        issue = HandoverIssue.objects.create(**validated_data)
        
        # Update heat number status to issue_reported
        heat_number = issue.heat_number
        heat_number.handover_status = 'issue_reported'
        heat_number.save()
        
        return issue


class HandoverVerifySerializer(serializers.Serializer):
    """Serializer for verifying handover"""
    heat_number_id = serializers.IntegerField()
    
    def validate_heat_number_id(self, value):
        """Validate that heat number exists and is pending handover"""
        try:
            heat_number = HeatNumber.objects.get(id=value)
            if heat_number.handover_status != 'pending_handover':
                raise serializers.ValidationError(
                    f"Heat number {heat_number.heat_number} is not pending handover verification"
                )
            return value
        except HeatNumber.DoesNotExist:
            raise serializers.ValidationError("Heat number does not exist")
    
    def create(self, validated_data):
        """Verify handover and update heat number status"""
        heat_number_id = validated_data['heat_number_id']
        user = self.context['request'].user
        
        heat_number = HeatNumber.objects.get(id=heat_number_id)
        heat_number.handover_status = 'verified'
        heat_number.verified_at = timezone.now()
        heat_number.verified_by = user
        heat_number.save()
        
        return heat_number


# RM Return Serializers

class RMReturnSerializer(serializers.ModelSerializer):
    """Serializer for RM Return listing and details"""
    raw_material_details = RawMaterialBasicSerializer(source='raw_material', read_only=True)
    heat_number_display = serializers.CharField(source='heat_number.heat_number', read_only=True)
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='manufacturing_order.mo_id', read_only=True)
    returned_from_location_display = serializers.CharField(source='returned_from_location.get_location_name_display', read_only=True)
    disposition_display = serializers.CharField(source='get_disposition_display', read_only=True)
    return_reason_display = serializers.CharField(source='get_return_reason_display', read_only=True)
    returned_by_name = serializers.CharField(source='returned_by.get_full_name', read_only=True)
    disposed_by_name = serializers.CharField(source='disposed_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = RMReturn
        fields = [
            'id', 'return_id', 'raw_material', 'raw_material_details', 'heat_number', 'heat_number_display',
            'batch', 'batch_id', 'manufacturing_order', 'mo_id', 'returned_from_location', 
            'returned_from_location_display', 'quantity_kg', 'received_kg', 'return_reason', 
            'return_reason_display', 'returned_by', 'returned_by_name', 'returned_at', 
            'disposition', 'disposition_display', 'disposition_notes', 'disposed_by', 
            'disposed_by_name', 'disposed_at', 'return_transaction', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'return_id', 'returned_at', 'created_at', 'updated_at',
            'raw_material_details', 'heat_number_display', 'batch_id', 'mo_id',
            'returned_from_location_display', 'disposition_display', 'return_reason_display',
            'returned_by_name', 'disposed_by_name'
        ]


class RMReturnCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating RM returns by supervisors"""
    
    class Meta:
        model = RMReturn
        fields = [
            'raw_material', 'heat_number', 'batch', 'manufacturing_order',
            'returned_from_location', 'quantity_kg', 'return_reason'
        ]
        extra_kwargs = {
            'raw_material': { 'required': False },
            'returned_from_location': { 'required': False, 'allow_null': True },
        }
    
    def validate_quantity_kg(self, value):
        """Validate quantity is positive"""
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than 0")
        return value
    
    def create(self, validated_data):
        """Create RM return and create inventory transaction"""
        # Resolve returned_from_location from code if provided in payload
        returned_from_location = validated_data.get('returned_from_location')
        if not returned_from_location:
            # Accept a helper field from request: returned_from_location_code
            location_code = None
            try:
                # Prefer initial_data to access non-model fields
                location_code = (self.initial_data.get('returned_from_location_code') or '').strip()
            except Exception:
                location_code = None
            if location_code:
                # Resolve by code or by location_name (case-insensitive)
                loc = (
                    Location.objects.filter(code__iexact=location_code).first()
                    or Location.objects.filter(location_name__iexact=location_code).first()
                )
                if not loc:
                    raise serializers.ValidationError({
                        'returned_from_location_code': f"Invalid location code: {location_code}"
                    })
                validated_data['returned_from_location'] = loc
            else:
                raise serializers.ValidationError({
                    'returned_from_location': 'This field is required (or provide returned_from_location_code)'
                })

        # Set the returned_by to the current user
        validated_data['returned_by'] = self.context['request'].user
        
        # Derive raw_material from heat_number if not provided
        if not validated_data.get('raw_material'):
            heat = validated_data.get('heat_number')
            if heat and getattr(heat, 'raw_material_id', None):
                validated_data['raw_material'] = heat.raw_material
            else:
                raise serializers.ValidationError({
                    'raw_material': 'Unable to derive raw material. Provide heat_number or raw_material.'
                })
        
        # Create the RM return
        rm_return = RMReturn.objects.create(**validated_data)
        
        # Mark the batch as returned to RM store
        batch = validated_data['batch']
        batch.status = 'returned_to_rm'
        batch.save(update_fields=['status'])
        
        # Create inventory transaction for the return
        from .transaction_manager import InventoryTransactionManager
        transaction_manager = InventoryTransactionManager()
        
        # Create return transaction (from process back to RM Store)
        try:
            # Get RM Store location
            rm_store_location = Location.objects.get(location_name='rm_store')
            
            # Create transaction ID
            transaction_id = f"TXN-RETURN-{rm_return.return_id}"
            
            # Create the transaction
            inventory_transaction = transaction_manager.create_transaction(
                transaction_type='return',
                product_id=validated_data['raw_material'].id,  # Raw material as product
                quantity=validated_data['quantity_kg'],
                transaction_datetime=timezone.now(),
                created_by=self.context['request'].user,
                location_from=validated_data['returned_from_location'],
                location_to=rm_store_location,
                manufacturing_order=validated_data['manufacturing_order'],
                reference_type='process',
                reference_id=validated_data['batch'].batch_id,
                notes=f"RM Return: {validated_data['return_reason']}",
                transaction_id=transaction_id
            )
            
            # Link transaction to return
            rm_return.return_transaction = inventory_transaction
            rm_return.save()
            
        except Exception as e:
            # If transaction creation fails, we should still keep the return record
            # but log the error
            print(f"Warning: Failed to create inventory transaction for return {rm_return.return_id}: {str(e)}")
        
        return rm_return


class RMReturnDispositionSerializer(serializers.Serializer):
    """Serializer for processing RM return disposition by RM Store"""
    disposition = serializers.ChoiceField(choices=['return_to_rm', 'return_to_vendor'])
    received_kg = serializers.DecimalField(max_digits=10, decimal_places=3, required=True)
    disposition_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_received_kg(self, value):
        """Validate received quantity is positive"""
        if value <= 0:
            raise serializers.ValidationError("Received quantity must be greater than 0")
        return value
    
    def validate(self, data):
        """Validate that disposition is not pending"""
        if data.get('disposition') == 'pending':
            raise serializers.ValidationError("Disposition cannot be set to pending")
        return data
    
    def update(self, instance, validated_data):
        """Update RM return with disposition and adjust stock accordingly"""
        from decimal import Decimal
        from django.db import transaction
        
        disposition = validated_data['disposition']
        received_kg = validated_data['received_kg']
        
        with transaction.atomic():
            # Update the RM return record
            instance.disposition = disposition
            instance.received_kg = received_kg
            instance.disposition_notes = validated_data.get('disposition_notes', '')
            instance.disposed_by = self.context['request'].user
            instance.disposed_at = timezone.now()
            instance.save()
            
            # Adjust RM stock based on disposition
            if disposition == 'return_to_rm':
                # Add back to RM stock
                stock_balance, created = RMStockBalance.objects.get_or_create(
                    raw_material=instance.raw_material,
                    defaults={'available_quantity': Decimal('0')}
                )
                stock_balance.available_quantity += received_kg
                stock_balance.save()
                
                # If heat number tracking is used, update heat stock balance
                if instance.heat_number:
                    try:
                        heat_stock_balance, _ = RMStockBalanceHeat.objects.get_or_create(
                            raw_material=instance.raw_material,
                            defaults={
                                'total_available_quantity_kg': Decimal('0'),
                                'total_coils_available': 0,
                                'total_sheets_available': 0,
                                'active_heat_numbers_count': 0
                            }
                        )
                        heat_stock_balance.total_available_quantity_kg += received_kg
                        heat_stock_balance.save()
                        
                        # Update heat number consumed quantity (reverse it)
                        heat = instance.heat_number
                        heat.consumed_quantity_kg = max(Decimal('0'), heat.consumed_quantity_kg - received_kg)
                        heat.save()
                    except Exception as e:
                        # Log but don't fail the transaction
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"Failed to update heat stock balance: {e}")
                
            elif disposition == 'return_to_vendor':
                # For defective material being returned to vendor
                # Deduct from RM stock (material is unusable and will be replaced by vendor)
                stock_balance, created = RMStockBalance.objects.get_or_create(
                    raw_material=instance.raw_material,
                    defaults={'available_quantity': Decimal('0')}
                )
                # Ensure we don't go negative
                stock_balance.available_quantity = max(Decimal('0'), stock_balance.available_quantity - received_kg)
                stock_balance.save()
                
                # If heat number tracking is used, update heat stock balance
                if instance.heat_number:
                    try:
                        heat_stock_balance, _ = RMStockBalanceHeat.objects.get_or_create(
                            raw_material=instance.raw_material,
                            defaults={
                                'total_available_quantity_kg': Decimal('0'),
                                'total_coils_available': 0,
                                'total_sheets_available': 0,
                                'active_heat_numbers_count': 0
                            }
                        )
                        heat_stock_balance.total_available_quantity_kg = max(
                            Decimal('0'), 
                            heat_stock_balance.total_available_quantity_kg - received_kg
                        )
                        heat_stock_balance.save()
                        
                        # Update heat number consumed quantity (increase it since material is defective)
                        heat = instance.heat_number
                        heat.consumed_quantity_kg += received_kg
                        heat.save()
                    except Exception as e:
                        # Log but don't fail the transaction
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"Failed to update heat stock balance for vendor return: {e}")
        
        return instance


# Serializers for Material Heat Numbers and Coils APIs

class CoilSerializer(serializers.Serializer):
    """Serializer for coil information"""
    number = serializers.CharField(help_text="Coil number")
    weight = serializers.DecimalField(max_digits=10, decimal_places=3, help_text="Weight in kg")


class HeatNumberWithCoilsSerializer(serializers.Serializer):
    """Serializer for heat number with its coils"""
    id = serializers.IntegerField()
    heat_number = serializers.CharField()
    grm_number = serializers.CharField(source='grm_receipt.grm_number')
    total_weight_kg = serializers.DecimalField(max_digits=10, decimal_places=3)
    coils_received = serializers.IntegerField()
    available_quantity_kg = serializers.SerializerMethodField()
    coils = serializers.SerializerMethodField()
    
    def get_available_quantity_kg(self, obj):
        """Get remaining available quantity in KG"""
        return float(obj.get_available_quantity_kg())
    
    def get_coils(self, obj):
        """Get all coils for this heat number"""
        coils = []
        if obj.items:
            for item in obj.items:
                coils.append({
                    'number': item.get('number', ''),
                    'weight': float(item.get('weight', 0)) if item.get('weight') else 0
                })
        return coils


class MaterialHeatNumbersResponseSerializer(serializers.Serializer):
    """Serializer for material heat numbers API response"""
    material_code = serializers.CharField()
    material_name = serializers.CharField()
    heat_numbers = HeatNumberWithCoilsSerializer(many=True)


class DeleteCoilsRequestSerializer(serializers.Serializer):
    """Serializer for delete coils API request"""
    material_code = serializers.CharField(help_text="Material code")
    heat_numbers = serializers.ListField(
        child=serializers.CharField(),
        help_text="List of heat numbers to delete coils from"
    )
    
    def validate_material_code(self, value):
        """Validate that material code exists"""
        try:
            RawMaterial.objects.get(material_code=value)
        except RawMaterial.DoesNotExist:
            raise serializers.ValidationError(f"Material code '{value}' does not exist")
        return value
    
    def validate_heat_numbers(self, value):
        """Validate that heat numbers list is not empty"""
        if not value:
            raise serializers.ValidationError("At least one heat number must be provided")
        return value


class DeleteCoilsResponseSerializer(serializers.Serializer):
    """Serializer for delete coils API response"""
    message = serializers.CharField()
    material_code = serializers.CharField()
    deleted_heat_numbers = serializers.ListField(child=serializers.CharField())
    deleted_coils_count = serializers.IntegerField()
    updated_heat_numbers = serializers.ListField(child=serializers.CharField())
