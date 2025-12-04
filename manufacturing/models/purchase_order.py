"""
Purchase Order Models
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

from utils.enums import POStatusChoices, MaterialTypeChoices

User = get_user_model()


class PurchaseOrder(models.Model):
    """
    Purchase Order (PO) - Orders for raw materials from vendors
    Based on the Production Head Functions workflow
    """
    # Auto-generated fields
    po_id = models.CharField(max_length=20, unique=True, editable=False)
    date_time = models.DateTimeField(auto_now_add=True)
    
    # Material selection
    rm_code = models.ForeignKey(
        'inventory.RawMaterial', on_delete=models.PROTECT,
        related_name='purchase_orders',
        help_text="Select from dropdown - auto fills other details"
    )
    material_type = models.CharField(max_length=20, choices=MaterialTypeChoices.choices, blank=True)
    
    # Auto-populated fields based on material selection
    material_auto = models.CharField(max_length=100, blank=True, help_text="Auto-filled")
    grade_auto = models.CharField(max_length=50, blank=True, help_text="Auto-filled")
    wire_diameter_mm_auto = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Auto-filled for coil"
    )
    finishing_auto = models.CharField(max_length=100, blank=True, null=True, help_text="Auto-filled finishing")
    manufacturer_brand_auto = models.CharField(max_length=100, blank=True, null=True, help_text="Auto-filled manufacturer")
    kg_auto = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, help_text="Auto-filled weight")
    thickness_mm_auto = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Auto-filled for sheet"
    )
    sheet_roll_auto = models.CharField(max_length=50, blank=True, null=True, help_text="Auto-filled sheet/roll info")
    qty_sheets_auto = models.PositiveIntegerField(null=True, blank=True, help_text="Auto-filled number of sheets")
    
    # Vendor details
    vendor_name = models.ForeignKey(
        'third_party.Vendor', on_delete=models.PROTECT,
        related_name='purchase_orders',
        help_text="Only show vendors who have this material"
    )
    vendor_address_auto = models.TextField(blank=True, null=True, help_text="Auto-filled from vendor")
    gst_no_auto = models.CharField(max_length=15, blank=True, null=True, help_text="Auto-filled from vendor")
    mob_no_auto = models.CharField(max_length=17, blank=True, null=True, help_text="Auto-filled from vendor")
    
    # Order details
    expected_date = models.DateField(help_text="Expected delivery date")
    quantity_ordered = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Quantity to order (in kg for coil, sheets for sheet material)"
    )
    quantity_received = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Actual quantity received (set when GRM is created)"
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=20, choices=POStatusChoices.choices, default='po_initiated')
    
    # Workflow tracking
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_po_orders')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_po_orders')
    cancellation_reason = models.TextField(blank=True, help_text="Reason for cancellation")
    
    # Relationship to Manufacturing Order (when PO is created for a specific MO)
    related_mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='related_purchase_orders',
        help_text="Manufacturing Order this PO is created for (when stock is insufficient)"
    )
    
    # Additional details
    terms_conditions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_po_orders')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Purchase Order'
        verbose_name_plural = 'Purchase Orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['po_id']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.po_id:
            today = timezone.now().strftime('%Y%m%d')
            last_po = PurchaseOrder.objects.filter(
                po_id__startswith=f'PO-{today}'
            ).order_by('po_id').last()
            
            if last_po:
                last_sequence = int(last_po.po_id.split('-')[-1])
                sequence = last_sequence + 1
            else:
                sequence = 1
            
            self.po_id = f'PO-{today}-{sequence:04d}'
        
        # Auto-populate fields based on rm_code selection
        if self.rm_code:
            self.material_type = self.rm_code.material_type
            self.material_auto = self.rm_code.material_name
            self.grade_auto = self.rm_code.grade
            
            if self.rm_code.material_type == 'coil':
                self.wire_diameter_mm_auto = self.rm_code.wire_diameter_mm
                self.kg_auto = self.rm_code.weight_kg
            elif self.rm_code.material_type == 'sheet':
                self.thickness_mm_auto = self.rm_code.thickness_mm
                self.qty_sheets_auto = self.rm_code.quantity
        
        # Auto-populate vendor details
        if self.vendor_name:
            self.vendor_address_auto = self.vendor_name.address
            self.gst_no_auto = self.vendor_name.gst_no
            self.mob_no_auto = self.vendor_name.contact_no
        
        # Calculate total amount
        if self.quantity_ordered and self.unit_price:
            self.total_amount = self.quantity_ordered * self.unit_price
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.po_id} - {self.vendor_name.name} (Qty: {self.quantity_ordered})"
    
    def get_cancelled_by_role(self):
        """Get the role of the user who cancelled this PO"""
        if not self.cancelled_by:
            return None
        
        # Get the primary role of the user who cancelled
        active_role = self.cancelled_by.user_roles.filter(is_active=True).select_related('role').first()
        if active_role:
            return active_role.role.name
        return None
    
    def get_cancellation_display(self):
        """Get human-readable cancellation info"""
        if not self.cancelled_by:
            return None
        
        role = self.get_cancelled_by_role()
        if role:
            # Map role names to display text
            role_display_map = {
                'manager': 'Manager',
                'production_head': 'Production Head',
                'admin': 'Admin',
                'gm': 'General Manager',
            }
            role_display = role_display_map.get(role, role.replace('_', ' ').title())
            return f"Cancelled by {role_display}"
        
        # Fallback to user name if role not found
        return f"Cancelled by {self.cancelled_by.get_full_name() or self.cancelled_by.email}"


class POStatusHistory(models.Model):
    """Track status changes for Purchase Orders"""
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'PO Status History'
        verbose_name_plural = 'PO Status Histories'
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.po.po_id}: {self.from_status} → {self.to_status}"


class POTransactionHistory(models.Model):
    """Comprehensive transaction history for Purchase Orders"""
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='transaction_history')
    transaction_type = models.CharField(max_length=50, help_text="Type of transaction")
    transaction_id = models.CharField(max_length=50, unique=True, help_text="Unique transaction identifier")
    description = models.TextField(help_text="Human-readable description")
    details = models.JSONField(default=dict, help_text="Detailed transaction data")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='po_transactions')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'PO Transaction History'
        verbose_name_plural = 'PO Transaction Histories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['po', 'transaction_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.po.po_id} - {self.transaction_type} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

