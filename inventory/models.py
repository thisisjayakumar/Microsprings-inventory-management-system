from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from utils.enums import (
    MaterialTypeChoices, FinishingChoices, LocationTypeChoices,
    TransactionTypeChoices, ReferenceTypeChoices, GRMStatusChoices,
    HandoverStatusChoices, HandoverIssueTypeChoices, RMReturnDispositionChoices,
    RMReturnReasonChoices
)

User = get_user_model()

class RawMaterial(models.Model):
    
    
    material_code = models.CharField(max_length=50, unique=True, help_text="Unique identifier for the raw material")
    material_name = models.CharField(max_length=100, help_text="Complete material name from CSV")
    material_type = models.CharField(max_length=20, choices=MaterialTypeChoices.choices)
    grade = models.CharField(max_length=50)
    finishing = models.CharField(max_length=20, choices=FinishingChoices.choices, null=True, blank=True)
    
    # Conditional fields based on material type
    wire_diameter_mm = models.DecimalField(
        max_digits=8, 
        decimal_places=3, 
        null=True, 
        blank=True,
        help_text="Required if material type is Coil"
    )
    weight_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        null=True, 
        blank=True,
        help_text="Weight in KG for Coil type"
    )
    thickness_mm = models.DecimalField(
        max_digits=8, 
        decimal_places=3, 
        null=True, 
        blank=True,
        help_text="Required if material type is Sheet"
    )
    
    # Sheet dimensions (for sheet type only)
    length_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Length in mm (for sheet type materials)"
    )
    breadth_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Breadth in mm (for sheet type materials)"
    )
    quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of sheets (for sheet type materials)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['material_name', 'grade']

    def __str__(self):
        specs = []
        if self.material_type == 'coil':
            if self.wire_diameter_mm:
                specs.append(f"⌀{self.wire_diameter_mm}mm")
            if self.weight_kg:
                specs.append(f"{self.weight_kg}kg")
        elif self.material_type == 'sheet':
            if self.thickness_mm:
                specs.append(f"t{self.thickness_mm}mm")
        
        spec_str = f" ({', '.join(specs)})" if specs else ""
        material_display = self.material_name if self.material_name else "Unknown Material"
        type_display = self.get_material_type_display() if self.material_type else "Unknown Type"
        grade_str = f" {self.grade}" if self.grade else ""
        
        return f"{self.material_code} - {material_display}{grade_str} - {type_display}{spec_str}"

    def clean(self):
        errors = {}

        if self.material_type == 'coil':
            if not self.wire_diameter_mm:
                errors['wire_diameter_mm'] = "Wire diameter is required for Coil type materials"
 
            if self.thickness_mm:
                errors['thickness_mm'] = "Thickness should not be set for Coil type materials"
                
        elif self.material_type == 'sheet':
            if not self.thickness_mm:
                errors['thickness_mm'] = "Thickness is required for Sheet type materials"

            if self.wire_diameter_mm:
                errors['wire_diameter_mm'] = "Wire diameter should not be set for Sheet type materials"
            
            if self.weight_kg:
                errors['weight_kg'] = "Weight should not be set for Sheet type materials (use quantity instead)"
        
        # Validate positive values for numeric fields
        if self.wire_diameter_mm is not None and self.wire_diameter_mm <= 0:
            errors['wire_diameter_mm'] = "Wire diameter must be greater than 0"
            
        if self.weight_kg is not None and self.weight_kg <= 0:
            errors['weight_kg'] = "Weight must be greater than 0"
            
        if self.thickness_mm is not None and self.thickness_mm <= 0:
            errors['thickness_mm'] = "Thickness must be greater than 0"
            
        if errors:
            raise ValidationError(errors)


class Location(models.Model):
    code = models.CharField(max_length=20, unique=True)
    location_name = models.CharField(max_length=20, choices=LocationTypeChoices.choices, default='rm_store')
    parent_location = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='sub_locations')

    class Meta:
        verbose_name = 'Location'
        verbose_name_plural = 'Locations'

    def __str__(self):
        return f"{self.location_name}"


class InventoryTransaction(models.Model):
    transaction_id = models.CharField(max_length=50, unique=True)
    transaction_type = models.CharField(max_length=20, choices=TransactionTypeChoices.choices)
    
    # What
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='inventory_transactions')
    manufacturing_order = models.ForeignKey('manufacturing.ManufacturingOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_transactions')
    
    # Where
    location_from = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='outgoing_transactions')
    location_to = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='incoming_transactions')
    
    # How Much
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    idempotency_key = models.CharField(
        max_length=64,
        unique=False,
        null=True,
        blank=True,
        help_text="Idempotency key for safe retries. Ensures no duplicate transaction on retries."
    )

    # When & Who
    transaction_datetime = models.DateTimeField()
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_transactions')
    
    # Reference
    reference_type = models.CharField(max_length=20, choices=ReferenceTypeChoices.choices, null=True, blank=True)
    reference_id = models.CharField(max_length=50, null=True, blank=True)
    
    # Additional Info
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['transaction_datetime']),
            models.Index(fields=['product', 'location_to']),
        ]
        verbose_name = 'Inventory Transaction'
        verbose_name_plural = 'Inventory Transactions'

    def __str__(self):
        return f"{self.transaction_id} - {self.transaction_type}"


class ProductLocation(models.Model):
    """
    Track current location of products, raw materials, and batches
    """
    # What item
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE, null=True, blank=True, related_name='locations')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, null=True, blank=True, related_name='locations')
    batch = models.ForeignKey('manufacturing.Batch', on_delete=models.CASCADE, null=True, blank=True, related_name='locations')
    
    # Where
    current_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='current_items')
    
    # How much
    quantity = models.DecimalField(max_digits=10, decimal_places=3, help_text="Current quantity at this location")
    
    # Tracking
    last_moved_at = models.DateTimeField(auto_now=True)
    last_moved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Reference to the transaction that created this location record
    last_transaction = models.ForeignKey('InventoryTransaction', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        # Ensure each item can only be in one location at a time
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(product__isnull=False, raw_material__isnull=True, batch__isnull=True) |
                    models.Q(product__isnull=True, raw_material__isnull=False, batch__isnull=True) |
                    models.Q(product__isnull=True, raw_material__isnull=True, batch__isnull=False)
                ),
                name='only_one_item_type'
            )
        ]
        indexes = [
            models.Index(fields=['current_location']),
            models.Index(fields=['last_moved_at']),
        ]
        verbose_name = 'Product Location'
        verbose_name_plural = 'Product Locations'

    def __str__(self):
        item_name = ""
        if self.product:
            item_name = f"Product: {self.product.product_code}"
        elif self.raw_material:
            item_name = f"RM: {self.raw_material.material_code}"
        elif self.batch:
            item_name = f"Batch: {self.batch.batch_id}"
        
        return f"{item_name} @ {self.current_location.location_name} ({self.quantity})"

    def get_item_identifier(self):
        """Get a string identifier for the item"""
        if self.product:
            return f"product_{self.product.id}"
        elif self.raw_material:
            return f"raw_material_{self.raw_material.id}"
        elif self.batch:
            return f"batch_{self.batch.id}"
        return "unknown"


class RMStockBalance(models.Model):
    """
    Current stock levels for raw materials
    """
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, related_name='stock_balances', null=True, blank=True)
    available_quantity = models.DecimalField(max_digits=10, decimal_places=3, help_text="Available quantity in KG or pieces")
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['raw_material']
        verbose_name = 'RM Stock Balance'
        verbose_name_plural = 'RM Stock Balances'

    def __str__(self):
        return f"{self.raw_material.material_code} - {self.available_quantity}"


class GRNReceipt(models.Model):
    """
    Goods Receipt Note (GRN) - Represents a truck delivery/consignment
    Each GRN can contain multiple raw materials with different heat numbers
    """
    # Auto-generated GRN number
    grn_number = models.CharField(max_length=20, unique=True, editable=False)
    
    # Related Purchase Orders - Multiple POs can be in one GRN
    purchase_orders = models.ManyToManyField(
        'manufacturing.PurchaseOrder', 
        related_name='grn_receipts',
        help_text="Purchase Orders included in this GRN"
    )
    
    # Delivery Information
    truck_number = models.CharField(max_length=20, blank=True, help_text="Truck/Van number")
    driver_name = models.CharField(max_length=100, blank=True)
    
    # Receipt Details
    receipt_date = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='grn_receipts')
    
    # Status and Tracking
    status = models.CharField(max_length=20, choices=GRMStatusChoices.choices, default='pending')
    total_items_received = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Total items/weight received (in kg for weight-based, count for count-based)"
    )
    total_items_expected = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Total items/weight expected (in kg for weight-based, count for count-based)"
    )
    
    # Additional Information
    quality_check_passed = models.BooleanField(default=False)
    quality_check_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='quality_checked_grns'
    )
    quality_check_date = models.DateTimeField(null=True, blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'inventory_grnreciept'  # Use actual table name in database
        ordering = ['-receipt_date']
        verbose_name = 'GRN Receipt'
        verbose_name_plural = 'GRN Receipts'
        indexes = [
            models.Index(fields=['grn_number']),
            models.Index(fields=['receipt_date']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        po_ids = ', '.join([po.po_id for po in self.purchase_orders.all()[:3]])
        if self.purchase_orders.count() > 3:
            po_ids += f' (+{self.purchase_orders.count() - 3} more)'
        return f"GRN-{self.grn_number} - {po_ids}"
    
    def save(self, *args, **kwargs):
        if not self.grn_number:
            # Generate GRN number: GRN-YYYYMMDD-XXXX
            from django.utils import timezone
            date_str = timezone.now().strftime('%Y%m%d')
            last_grn = GRNReceipt.objects.filter(
                grn_number__startswith=f'GRN-{date_str}'
            ).order_by('-grn_number').first()
            
            if last_grn:
                last_num = int(last_grn.grn_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.grn_number = f'GRN-{date_str}-{new_num:04d}'
        
        super().save(*args, **kwargs)
    
    def calculate_completion_status(self):
        """Calculate completion status based on received items"""
        if self.total_items_expected == 0:
            return 'pending'
        
        if self.total_items_received == 0:
            return 'pending'
        elif self.total_items_received < self.total_items_expected:
            return 'partial'
        else:
            return 'completed'


class HeatNumber(models.Model):
    """
    Heat Number - Represents a specific batch/lot of raw material within a GRN
    Each heat number contains specific quantities and specifications
    """
    # Heat number identifier
    heat_number = models.CharField(max_length=50, help_text="Heat number from supplier")
    
    # Related GRN and Raw Material
    grn_receipt = models.ForeignKey(
        GRNReceipt, 
        on_delete=models.CASCADE, 
        related_name='heat_numbers',
        db_column='grn_receipt_id'  # Map to existing database column
    )
    raw_material = models.ForeignKey(
        RawMaterial, 
        on_delete=models.PROTECT, 
        related_name='heat_numbers'
    )
    
    # Quantities
    coils_received = models.PositiveIntegerField(
        default=0, 
        help_text="Number of coils received for this heat number"
    )
    total_weight_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Total weight in KG for this heat number"
    )
    
    # For sheet materials
    sheets_received = models.PositiveIntegerField(
        default=0, 
        help_text="Number of sheets received (for sheet materials)"
    )
    
    # Quality Information
    test_certificate_date = models.DateField(null=True, blank=True)
    
    # Individual items (coils/sheets) details
    items = models.JSONField(
        default=list,
        blank=True,
        help_text="List of individual coil/sheet items with number and weight"
    )
    
    # Status
    is_available = models.BooleanField(default=True, help_text="Is this heat number still available for use")
    consumed_quantity_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=0,
        help_text="Quantity consumed from this heat number"
    )
    
    # Handover Verification Status
    handover_status = models.CharField(
        max_length=20,
        choices=HandoverStatusChoices.choices,
        default='pending_handover',
        help_text="Status of handover verification to Coiling department"
    )
    verified_at = models.DateTimeField(null=True, blank=True, help_text="When the handover was verified")
    verified_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='verified_heat_numbers',
        help_text="User who verified the handover"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['heat_number', 'grn_receipt', 'raw_material']
        ordering = ['-created_at']
        verbose_name = 'Heat Number'
        verbose_name_plural = 'Heat Numbers'
        indexes = [
            models.Index(fields=['heat_number']),
            models.Index(fields=['grn_receipt', 'raw_material']),
            models.Index(fields=['is_available']),
        ]
    
    def __str__(self):
        return f"{self.heat_number} - {self.raw_material.material_code} ({self.total_weight_kg}kg)"
    
    def get_available_quantity_kg(self):
        """Get remaining available quantity in KG"""
        return self.total_weight_kg - self.consumed_quantity_kg
    
    def get_available_coils(self):
        """Get remaining available coils"""
        consumed_coils = int((self.consumed_quantity_kg / self.total_weight_kg) * self.coils_received) if self.total_weight_kg > 0 else 0
        return max(0, self.coils_received - consumed_coils)
    
    def update_stock_balance(self):
        """Update the stock balance for this heat number's raw material"""
        try:
            stock_balance, created = RMStockBalanceHeat.objects.get_or_create(
                raw_material=self.raw_material,
                defaults={
                    'total_available_quantity_kg': 0,
                    'total_coils_available': 0,
                    'total_sheets_available': 0,
                    'active_heat_numbers_count': 0
                }
            )
            stock_balance.update_from_heat_numbers()
        except Exception as e:
            print(f"Error updating stock balance: {e}")
    
    def clean(self):
        """Validate heat number data"""
        print(f"DEBUG: HeatNumber model clean() called")
        print(f"DEBUG: raw_material: {self.raw_material}")
        print(f"DEBUG: coils_received: {self.coils_received}")
        print(f"DEBUG: sheets_received: {self.sheets_received}")
        print(f"DEBUG: total_weight_kg: {self.total_weight_kg}")
        
        # Skip validation if raw_material is not set (during creation)
        if not self.raw_material:
            print(f"DEBUG: raw_material not set, skipping validation")
            return
            
        errors = {}
        
        # Validate quantities based on material type
        if self.raw_material.material_type == 'coil':
            if self.coils_received == 0:
                errors['coils_received'] = "Number of coils must be specified for coil materials"
            if self.sheets_received > 0:
                errors['sheets_received'] = "Sheets should not be specified for coil materials"
        elif self.raw_material.material_type == 'sheet':
            if self.sheets_received == 0:
                errors['sheets_received'] = "Number of sheets must be specified for sheet materials"
            if self.coils_received > 0:
                errors['coils_received'] = "Coils should not be specified for sheet materials"
        
        # Validate weight
        if self.total_weight_kg and self.total_weight_kg <= 0:
            errors['total_weight_kg'] = "Total weight must be greater than 0"
        
        if errors:
            print(f"DEBUG: Model validation errors: {errors}")
            raise ValidationError(errors)
        
        print(f"DEBUG: Model validation passed")


class RMStockBalanceHeat(models.Model):
    """
    Enhanced stock balance tracking with Heat number traceability
    Replaces the simple RMStockBalance model
    """
    raw_material = models.ForeignKey(
        RawMaterial, 
        on_delete=models.CASCADE, 
        related_name='heat_stock_balances'
    )
    
    # Aggregate quantities across all heat numbers
    total_available_quantity_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=0,
        help_text="Total available quantity across all heat numbers"
    )
    total_coils_available = models.PositiveIntegerField(
        default=0,
        help_text="Total coils available across all heat numbers"
    )
    total_sheets_available = models.PositiveIntegerField(
        default=0,
        help_text="Total sheets available across all heat numbers"
    )
    
    # Heat number count
    active_heat_numbers_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of active heat numbers with available stock"
    )
    
    # Last updated
    last_updated = models.DateTimeField(auto_now=True)
    last_transaction = models.ForeignKey(
        InventoryTransaction, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    class Meta:
        unique_together = ['raw_material']
        verbose_name = 'RM Stock Balance (Heat Tracked)'
        verbose_name_plural = 'RM Stock Balances (Heat Tracked)'
    
    def __str__(self):
        return f"{self.raw_material.material_code} - {self.total_available_quantity_kg}kg"
    
    def update_from_heat_numbers(self):
        """Update stock balance from all active heat numbers"""
        heat_numbers = HeatNumber.objects.filter(
            raw_material=self.raw_material,
            is_available=True
        )
        
        self.total_available_quantity_kg = sum(
            heat.get_available_quantity_kg() for heat in heat_numbers
        )
        self.total_coils_available = sum(
            heat.get_available_coils() for heat in heat_numbers
        )
        self.total_sheets_available = sum(
            heat.sheets_received for heat in heat_numbers if heat.raw_material.material_type == 'sheet'
        )
        self.active_heat_numbers_count = heat_numbers.count()
        
        self.save()


class InventoryTransactionHeat(models.Model):
    """
    Enhanced inventory transaction with Heat number tracking
    Links transactions to specific heat numbers for full traceability
    """
    # Link to existing transaction
    inventory_transaction = models.OneToOneField(
        InventoryTransaction,
        on_delete=models.CASCADE,
        related_name='heat_transaction'
    )
    
    # Heat number details
    heat_number = models.ForeignKey(
        HeatNumber,
        on_delete=models.PROTECT,
        related_name='transactions',
        help_text="Specific heat number involved in this transaction"
    )
    
    # Transaction quantities
    quantity_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3,
        help_text="Quantity in KG for this transaction"
    )
    coils_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of coils involved (for coil materials)"
    )
    sheets_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of sheets involved (for sheet materials)"
    )
    
    # Traceability
    grn_number = models.CharField(
        max_length=20,
        help_text="GRN number for traceability"
    )
    
    class Meta:
        verbose_name = 'Inventory Transaction Heat'
        verbose_name_plural = 'Inventory Transactions Heat'
        indexes = [
            models.Index(fields=['heat_number']),
            models.Index(fields=['grn_number']),
        ]
    
    def __str__(self):
        return f"{self.inventory_transaction.transaction_id} - {self.heat_number.heat_number}"


class HandoverIssue(models.Model):
    """
    Track issues reported during raw material handover verification
    """
    # Related heat number (equivalent to ChildBatch)
    heat_number = models.ForeignKey(
        HeatNumber,
        on_delete=models.CASCADE,
        related_name='handover_issues',
        help_text="Heat number for which issue was reported"
    )
    
    # Issue details
    issue_type = models.CharField(
        max_length=20,
        choices=HandoverIssueTypeChoices.choices,
        help_text="Type of issue reported"
    )
    actual_weight = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Actual weight found (if different from expected)"
    )
    remarks = models.TextField(
        blank=True,
        help_text="Additional remarks about the issue"
    )
    
    # Reporting details
    reported_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='reported_handover_issues',
        help_text="User who reported the issue"
    )
    reported_at = models.DateTimeField(auto_now_add=True)
    
    # Resolution tracking
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_handover_issues',
        help_text="User who resolved the issue"
    )
    resolution_notes = models.TextField(
        blank=True,
        help_text="Notes about how the issue was resolved"
    )
    
    class Meta:
        verbose_name = 'Handover Issue'
        verbose_name_plural = 'Handover Issues'
        ordering = ['-reported_at']
        indexes = [
            models.Index(fields=['heat_number']),
            models.Index(fields=['reported_at']),
            models.Index(fields=['is_resolved']),
        ]
    
    def __str__(self):
        return f"Issue #{self.id} - {self.heat_number.heat_number} ({self.get_issue_type_display()})"


class RMReturn(models.Model):
    """
    Track raw materials returned from production processes back to RM Store
    Supervisors can return faulty batches, and RM Store decides disposition
    """
    # Auto-generated return ID
    return_id = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        help_text="Auto-generated return ID"
    )
    
    # What was returned
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name='returns',
        help_text="Raw material being returned"
    )
    heat_number = models.ForeignKey(
        HeatNumber,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='returns',
        help_text="Heat number of the returned material (if tracked)"
    )
    
    # Related batch and MO
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='rm_returns',
        help_text="Batch that returned this material"
    )
    manufacturing_order = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='rm_returns',
        help_text="Manufacturing Order associated with this return"
    )
    
    # Location/Process where material was returned from
    returned_from_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='rm_returns_from',
        help_text="Location/process from which material was returned"
    )
    
    # Quantity and details
    quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity returned by Supervisor in KG"
    )
    
    # Return reason (from Supervisor)
    return_reason = models.CharField(
        max_length=30,
        choices=RMReturnReasonChoices.choices,
        help_text="Reason for returning the raw material"
    )
    
    # Actual quantity received by RM Store (may differ from quantity_kg)
    received_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Actual quantity received by RM Store when processing"
    )
    
    # Who returned it (supervisor)
    returned_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='rm_returns_created',
        help_text="Supervisor who returned the material"
    )
    returned_at = models.DateTimeField(auto_now_add=True)
    
    # Disposition by RM Store
    disposition = models.CharField(
        max_length=20,
        choices=RMReturnDispositionChoices.choices,
        default='pending',
        help_text="Action taken by RM Store"
    )
    disposition_notes = models.TextField(
        blank=True,
        help_text="Notes about the disposition decision"
    )
    disposed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rm_returns_disposed',
        help_text="RM Store user who processed the return"
    )
    disposed_at = models.DateTimeField(null=True, blank=True)
    
    # Inventory transaction tracking
    return_transaction = models.ForeignKey(
        InventoryTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rm_return_records',
        help_text="Inventory transaction created for this return"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'RM Return'
        verbose_name_plural = 'RM Returns'
        ordering = ['-returned_at']
        indexes = [
            models.Index(fields=['batch']),
            models.Index(fields=['manufacturing_order']),
            models.Index(fields=['disposition']),
            models.Index(fields=['returned_at']),
            models.Index(fields=['return_id']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate return_id if not set
        if not self.return_id:
            from django.utils import timezone
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            # Format: RMR-YYYYMMDDHHMMSS-BATCHID
            self.return_id = f"RMR-{timestamp}-{self.batch.batch_id}"
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.return_id} - {self.raw_material.material_code} ({self.quantity_kg}kg) - {self.get_disposition_display()}"