from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid

User = get_user_model()

class GRNReceipt(models.Model):
    """
    Goods Received Note (GRN) - Represents a truck delivery/consignment
    Each GRN can contain multiple raw materials with different heat numbers
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Receipt'),
        ('partial', 'Partially Received'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ]
    
    # Auto-generated GRN number
    grn_number = models.CharField(max_length=20, unique=True, editable=False)
    
    # Related Purchase Order
    purchase_order = models.ForeignKey(
        'manufacturing.PurchaseOrder', 
        on_delete=models.PROTECT, 
        related_name='grn_receipts',
        help_text="Purchase Order this GRN is for"
    )
    
    # Delivery Information
    truck_number = models.CharField(max_length=20, blank=True, help_text="Truck/Van number")
    driver_name = models.CharField(max_length=100, blank=True)
    driver_contact = models.CharField(max_length=15, blank=True)
    
    # Receipt Details
    receipt_date = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='grn_receipts')
    
    # Status and Tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_items_received = models.PositiveIntegerField(default=0)
    total_items_expected = models.PositiveIntegerField(default=0)
    
    # Additional Information
    notes = models.TextField(blank=True)
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
        ordering = ['-receipt_date']
        verbose_name = 'GRN Receipt'
        verbose_name_plural = 'GRN Receipts'
        indexes = [
            models.Index(fields=['grn_number']),
            models.Index(fields=['receipt_date']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"GRN-{self.grn_number} - {self.purchase_order.po_id}"
    
    def save(self, *args, **kwargs):
        if not self.grn_number:
            # Generate GRN number: GRN-YYYYMMDD-XXXX
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
        related_name='heat_numbers'
    )
    raw_material = models.ForeignKey(
        'inventory.RawMaterial', 
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
        help_text="Total weight in KG for this heat number"
    )
    
    # For sheet materials
    sheets_received = models.PositiveIntegerField(
        default=0, 
        help_text="Number of sheets received (for sheet materials)"
    )
    
    # Quality Information
    quality_certificate_number = models.CharField(max_length=100, blank=True)
    # NOTE: test_certificate_date - Reserved for future use. 
    # Currently not exposed via API or used in frontend.
    test_certificate_date = models.DateField(null=True, blank=True)
    
    # Physical Location in Store
    storage_location = models.CharField(max_length=100, blank=True, help_text="Physical location in RM store")
    rack_number = models.CharField(max_length=50, blank=True)
    shelf_number = models.CharField(max_length=50, blank=True)
    
    # Status
    is_available = models.BooleanField(default=True, help_text="Is this heat number still available for use")
    consumed_quantity_kg = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=0,
        help_text="Quantity consumed from this heat number"
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
    
    def clean(self):
        """Validate heat number data"""
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
        if self.total_weight_kg <= 0:
            errors['total_weight_kg'] = "Total weight must be greater than 0"
        
        if errors:
            raise ValidationError(errors)


class RMStockBalanceHeat(models.Model):
    """
    Enhanced stock balance tracking with Heat number traceability
    Replaces the simple RMStockBalance model
    """
    raw_material = models.ForeignKey(
        'inventory.RawMaterial', 
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
        'inventory.InventoryTransaction', 
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
        'inventory.InventoryTransaction',
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
