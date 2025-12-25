"""
Manufacturing Order Models
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
import logging

from utils.enums import (
    MOStatusChoices,
    PriorityChoices,
    ShiftChoices
)

logger = logging.getLogger(__name__)
User = get_user_model()


class ManufacturingOrder(models.Model):
    """
    Manufacturing Order (MO) - Production orders for finished goods
    Based on the Production Head Functions workflow
    """
    
    # Auto-generated fields
    mo_id = models.CharField(max_length=20, unique=True, editable=False)
    date_time = models.DateTimeField(auto_now_add=True)
    
    # Product details
    product_code = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='manufacturing_orders')
    quantity = models.PositiveIntegerField(help_text="Quantity to manufacture")
    
    # Auto-populated from product selection
    product_type = models.CharField(max_length=50, blank=True, help_text="Auto-filled: Spring/Stamping Part")
    material_name = models.CharField(max_length=100, blank=True, help_text="Auto-filled from product")
    material_type = models.CharField(max_length=20, blank=True, help_text="Auto-filled: Coil/Sheet")
    grade = models.CharField(max_length=50, blank=True, help_text="Auto-filled material grade")
    
    # Conditional fields based on material type
    wire_diameter_mm = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Auto-filled for coil materials"
    )
    thickness_mm = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text="Auto-filled for sheet materials"
    )
    finishing = models.CharField(max_length=100, blank=True, help_text="Material finishing details")
    manufacturer_brand = models.CharField(max_length=100, blank=True, help_text="Material manufacturer")
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    
    # Raw Material Requirements (calculated)
    loose_fg_stock = models.PositiveIntegerField(default=0, help_text="Available finished goods stock")
    rm_required_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0, help_text="Raw material required in kg")
    
    # Sheet-based RM Requirements
    strips_required = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Number of strips required for this MO (for press components)"
    )
    total_pieces_from_strips = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Total pieces that will be produced from strips"
    )
    excess_pieces = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Excess pieces due to strip constraints"
    )
    
    # RM Calculation Parameters
    tolerance_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=2.00,
        help_text="Tolerance percentage for RM loss during process (e.g., 2.00 for ±2%)"
    )
    scrap_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Expected scrap percentage (optional, can be predicted from previous MOs)"
    )
    scrap_rm_weight = models.PositiveIntegerField(
        default=0,
        help_text="Raw material weight sent to scrap for this MO (in grams)"
    )
    
    # RM Release/Receive Tracking
    rm_released_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        help_text="Actual RM released by RM Store Manager (in kg or sheets)"
    )
    
    # Additional RM Tracking
    additional_rm_approved_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        default=Decimal('0'),
        help_text="Additional RM approved beyond original allocation (in kg)"
    )
    last_additional_rm_approval_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When additional RM was last approved"
    )
    
    # Additional RM Added by Production Head (no approval needed)
    additional_rm_added_by_ph_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('0'),
        help_text="Additional RM added by Production Head (in kg or strips)"
    )
    
    additional_rm_added_by_ph_strips = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=0,
        help_text="Additional RM added by Production Head (in strips, for sheet materials)"
    )
    
    last_additional_rm_added_by_ph_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When additional RM was last added by Production Head"
    )
    
    additional_rm_added_by_ph_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ph_added_additional_rm_mos',
        help_text="Production Head who added the additional RM"
    )
    
    rm_completion_status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active - RM can be released'),
            ('pending_completion', 'Pending Completion - Waiting for 30min delay'),
            ('completed', 'Completed - All RM consumed and marked complete'),
        ],
        default='active',
        help_text="Status of RM allocation/release for this MO"
    )
    
    # RM Pending from PO tracking
    rm_pending_from_po = models.BooleanField(
        default=False,
        help_text="True if RM is still pending from a Purchase Order"
    )
    pending_po_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Quantity of RM pending from PO (in kg)"
    )
    
    # Assignment
    shift = models.CharField(max_length=10, choices=ShiftChoices.choices, null=True, blank=True)
    
    # Planning dates
    planned_start_date = models.DateTimeField(null=True, blank=True)
    planned_end_date = models.DateTimeField(null=True, blank=True)
    actual_start_date = models.DateTimeField(null=True, blank=True)
    actual_end_date = models.DateTimeField(null=True, blank=True)
    
    # Status & Priority
    status = models.CharField(max_length=20, choices=MOStatusChoices.choices, default='on_hold')
    priority = models.CharField(max_length=10, choices=PriorityChoices.choices, default='medium')
    priority_level = models.IntegerField(
        default=0,
        help_text="Numeric priority level for ordering (higher = more priority). 0=default"
    )
    
    # Stop/Hold tracking
    stopped_at = models.DateTimeField(null=True, blank=True, help_text="When this MO was stopped")
    stop_reason = models.TextField(blank=True, help_text="Reason for stopping this MO")
    
    # Business details
    customer_c_id = models.ForeignKey(
        'third_party.Customer', to_field='c_id', on_delete=models.PROTECT,
        null=True, blank=True, related_name='manufacturing_orders',
        help_text="Customer for this manufacturing order (references c_id)"
    )
    customer_name = models.CharField(max_length=200, blank=True, help_text="Customer name (auto-filled from customer)")
    delivery_date = models.DateField(null=True, blank=True)
    special_instructions = models.TextField(blank=True)
    
    # Workflow tracking
    submitted_at = models.DateTimeField(null=True, blank=True)
    gm_approved_at = models.DateTimeField(null=True, blank=True)
    gm_approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='gm_approved_mo_orders')
    rm_allocated_at = models.DateTimeField(null=True, blank=True)
    rm_allocated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rm_allocated_mo_orders')
    
    # Rejection tracking
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_mo_orders')
    rejection_reason = models.TextField(blank=True, help_text="Reason for rejecting this MO")
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_mo_orders')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Manufacturing Order'
        verbose_name_plural = 'Manufacturing Orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo_id']),
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.mo_id:
            today = timezone.now().strftime('%Y%m%d')
            last_mo = ManufacturingOrder.objects.filter(
                mo_id__startswith=f'MO-{today}'
            ).order_by('mo_id').last()
            
            if last_mo:
                last_sequence = int(last_mo.mo_id.split('-')[-1])
                sequence = last_sequence + 1
            else:
                sequence = 1
            
            self.mo_id = f'MO-{today}-{sequence:04d}'
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.mo_id} - {self.product_code.product_code} (Qty: {self.quantity})"
    
    def calculate_rm_requirements(self):
        """Calculate raw material requirements based on product type"""
        if not self.product_code:
            return
        
        product = self.product_code
        
        # For sheet-based press components
        if product.product_type == 'press_component' and product.material_type == 'sheet':
            if product.pcs_per_strip and product.pcs_per_strip > 0:
                strip_calc = product.calculate_strips_required(self.quantity)
                self.strips_required = strip_calc.get('strips_required', 0)
                self.total_pieces_from_strips = strip_calc.get('total_pieces_from_strips', 0)
                self.excess_pieces = strip_calc.get('excess_pieces', 0)
        
        # For coil-based products (springs)
        elif product.material_type == 'coil' and product.grams_per_product:
            total_grams = self.quantity * product.grams_per_product
            self.rm_required_kg = Decimal(str(total_grams / 1000))
            
            if self.tolerance_percentage:
                tolerance_factor = Decimal('1') + (Decimal(str(self.tolerance_percentage)) / Decimal('100'))
                self.rm_required_kg = self.rm_required_kg * tolerance_factor
    
    def stop_mo(self, stop_reason, stopped_by_user):
        """Stop this MO and release all reserved resources"""
        from django.db import transaction
        from .allocations import RMAllocationHistory
        
        if self.status in ['completed', 'cancelled', 'stopped']:
            raise ValidationError(f"Cannot stop MO with status: {self.status}")
        
        with transaction.atomic():
            released_resources = {
                'rm_allocations': [],
                'fg_reservations': [],
                'blocked_batches': []
            }
            
            # Release reserved RM allocations
            from inventory.models import RMStockBalance
            
            reserved_allocations = self.rm_allocations.filter(status='reserved')
            for allocation in reserved_allocations:
                released_resources['rm_allocations'].append({
                    'material': str(allocation.raw_material),
                    'quantity_kg': float(allocation.allocated_quantity_kg),
                    'allocation_id': allocation.id
                })
                
                stock_balance, created = RMStockBalance.objects.get_or_create(
                    raw_material=allocation.raw_material,
                    defaults={'available_quantity': Decimal('0')}
                )
                stock_balance.available_quantity += allocation.allocated_quantity_kg
                stock_balance.save()
                
                RMAllocationHistory.objects.create(
                    allocation=allocation,
                    action='released',
                    from_mo=self,
                    to_mo=None,
                    quantity_kg=allocation.allocated_quantity_kg,
                    performed_by=stopped_by_user,
                    reason=f'RM released from stopped MO {self.mo_id}. Returned to stock.'
                )
                
                try:
                    from inventory.utils import generate_transaction_id
                    transaction_id = generate_transaction_id('RM_RELEASED')
                    
                    MOTransactionHistory.objects.create(
                        mo=self,
                        transaction_type='rm_released',
                        transaction_id=transaction_id,
                        description=f'RM released from stopped MO {self.mo_id}',
                        details={
                            'material_name': allocation.raw_material.material_name,
                            'released_quantity_kg': float(allocation.allocated_quantity_kg),
                            'reason': f'MO {self.mo_id} stopped - RM returned to stock',
                            'stopped_by': stopped_by_user.get_full_name() or stopped_by_user.email,
                            'stock_returned_to': 'RM Store'
                        },
                        created_by=stopped_by_user
                    )
                except Exception as e:
                    logger.warning(f"Failed to create RM release transaction history: {str(e)}")
                
                allocation.status = 'released'
                allocation.save()
            
            # Release FG reservations
            from fg_store.models import FGStockReservation
            fg_reservations = FGStockReservation.objects.filter(mo=self, status='reserved')
            for reservation in fg_reservations:
                released_resources['fg_reservations'].append({
                    'product': str(reservation.product_code),
                    'quantity': reservation.quantity,
                    'reservation_id': reservation.id
                })
                reservation.release_reservation()
            
            # Block new batch releases
            created_batches = self.batches.filter(status='created')
            for batch in created_batches:
                batch.can_release = False
                batch.save(update_fields=['can_release'])
                released_resources['blocked_batches'].append({
                    'batch_id': batch.batch_id,
                    'quantity': batch.planned_quantity
                })
            
            # Update MO status
            self.status = 'stopped'
            self.stopped_at = timezone.now()
            self.stop_reason = stop_reason
            self.save(update_fields=['status', 'stopped_at', 'stop_reason', 'updated_at'])
            
            try:
                from inventory.utils import generate_transaction_id
                transaction_id = generate_transaction_id('MO_STOPPED')
                
                MOTransactionHistory.objects.create(
                    mo=self,
                    transaction_type='mo_stopped',
                    transaction_id=transaction_id,
                    description=f'MO {self.mo_id} stopped',
                    details={
                        'stop_reason': stop_reason,
                        'stopped_by': stopped_by_user.get_full_name() or stopped_by_user.email,
                        'stopped_at': self.stopped_at.isoformat(),
                        'released_resources': released_resources,
                        'total_rm_released_kg': sum([r['quantity_kg'] for r in released_resources['rm_allocations']]),
                        'total_fg_released': len(released_resources['fg_reservations']),
                        'blocked_batches_count': len(released_resources['blocked_batches'])
                    },
                    created_by=stopped_by_user
                )
            except Exception as e:
                logger.warning(f"Failed to create MO stop transaction history: {str(e)}")
            
            # Create audit log entry
            try:
                from notifications.models import WorkflowNotification
                WorkflowNotification.objects.create(
                    title=f"MO {self.mo_id} Stopped",
                    message=f"MO stopped by {stopped_by_user.get_full_name() or stopped_by_user.username}. Reason: {stop_reason}",
                    notification_type='mo_status_changed',
                    priority='medium',
                    recipient=stopped_by_user,
                    related_mo=self,
                    created_by=stopped_by_user
                )
            except Exception as e:
                logger.warning(f"Failed to create notification: {str(e)}")
            
            return released_resources
    
    # Additional RM Request Properties
    @property
    def total_rm_limit_kg(self):
        """Calculate total RM limit (original allocation + approved additional RM + PH added RM)"""
        base_limit = self.rm_required_kg or Decimal('0')
        additional_approved = self.additional_rm_approved_kg or Decimal('0')
        additional_ph = self.additional_rm_added_by_ph_kg or Decimal('0')
        return base_limit + additional_approved + additional_ph
    
    @property
    def total_rm_released_kg(self):
        """Calculate total RM released for this MO across all batches
        Excludes batches with quality defects"""
        from django.db.models import Sum
        total = self.batches.filter(has_quality_defect=False).aggregate(total=Sum('planned_quantity'))['total'] or 0
        return Decimal(str(total / 1000)) if total else Decimal('0')
    
    @property
    def total_rm_returned_kg(self):
        """Calculate total RM returned for this MO across all batches
        Note: Excludes quality_defect returns - they are tracked separately"""
        from manufacturing.models.rm_return import RMReturn
        from django.db.models import Sum
        # Only count non-quality_defect returns from non-quality_defect batches
        non_quality_returns = RMReturn.objects.filter(
            batch__mo=self,
            batch__has_quality_defect=False
        ).exclude(return_reason='quality_defect')
        total = non_quality_returns.aggregate(total=Sum('returned_kg'))['total'] or 0
        return Decimal(str(total)) if total else Decimal('0')
    
    @property
    def total_rm_quality_defect_kg(self):
        """Calculate total RM from quality defect batches for this MO
        This represents RM that was released but later found to have quality defects"""
        from django.db.models import Sum
        # Sum planned_quantity from all quality defect batches
        total = self.batches.filter(has_quality_defect=True).aggregate(total=Sum('planned_quantity'))['total'] or 0
        return Decimal(str(total / 1000)) if total else Decimal('0')
    
    @property
    def total_rm_used_kg(self):
        """Calculate actual RM used (Released - Returned) for this MO
        Note: Quality defect batches are excluded from both released and returned calculations"""
        return self.total_rm_released_kg - self.total_rm_returned_kg
    
    @property
    def remaining_rm_capacity_kg(self):
        """Calculate remaining RM capacity before hitting the limit
        Based on USED RM, not released RM, so returned RM can be reused"""
        return self.total_rm_limit_kg - self.total_rm_used_kg
    
    @property
    def is_rm_limit_exceeded(self):
        """Check if RM released exceeds the allocated limit"""
        return self.total_rm_released_kg >= (self.rm_required_kg or Decimal('0'))
    
    @property
    def can_create_batch(self):
        """Check if new batch can be created
        Based on USED RM, not released RM, so returned RM can be reused for new batches"""
        if self.rm_completion_status != 'active':
            return False
        
        if self.status not in ['on_hold', 'in_progress']:
            return False
        
        # Use total_rm_used_kg instead of total_rm_released_kg
        # This allows creating new batches with returned RM
        return self.total_rm_used_kg < self.total_rm_limit_kg
    
    @property
    def should_show_request_additional_rm(self):
        """Show 'Request Additional RM' button when limit exceeded and no pending request"""
        if not self.is_rm_limit_exceeded:
            return False
        
        pending_requests = self.additional_rm_requests.filter(status='pending').exists()
        return not pending_requests
    
    @property
    def should_show_mark_complete(self):
        """Show 'Mark as Complete' button when conditions are met"""
        if self.rm_completion_status == 'completed':
            return False
        
        latest_request = self.additional_rm_requests.filter(
            status='approved'
        ).order_by('-approved_at').first()
        
        if not latest_request:
            return False
        
        return latest_request.can_mark_complete
    
    def get_rm_summary(self):
        """Get comprehensive RM summary for this MO"""
        return {
            'allocated_rm_kg': float(self.rm_required_kg or 0),
            'additional_approved_kg': float(self.additional_rm_approved_kg or 0),
            'additional_rm_added_by_ph_kg': float(self.additional_rm_added_by_ph_kg or 0),
            'additional_rm_added_by_ph_strips': self.additional_rm_added_by_ph_strips or 0,
            'last_additional_rm_added_by_ph_at': self.last_additional_rm_added_by_ph_at.isoformat() if self.last_additional_rm_added_by_ph_at else None,
            'additional_rm_added_by_ph_user': self.additional_rm_added_by_ph_user.get_full_name() if self.additional_rm_added_by_ph_user else None,
            'total_limit_kg': float(self.total_rm_limit_kg),
            'total_released_kg': float(self.total_rm_released_kg),
            'remaining_capacity_kg': float(self.remaining_rm_capacity_kg),
            'is_limit_exceeded': self.is_rm_limit_exceeded,
            'can_create_batch': self.can_create_batch,
            'should_show_request_additional_rm': self.should_show_request_additional_rm,
            'should_show_mark_complete': self.should_show_mark_complete,
            'rm_completion_status': self.rm_completion_status,
        }


class MOStatusHistory(models.Model):
    """Track status changes for Manufacturing Orders"""
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'MO Status History'
        verbose_name_plural = 'MO Status Histories'
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.mo.mo_id}: {self.from_status} → {self.to_status}"


class MOTransactionHistory(models.Model):
    """Comprehensive transaction history for Manufacturing Orders"""
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name='transaction_history')
    transaction_type = models.CharField(max_length=50, help_text="Type of transaction")
    transaction_id = models.CharField(max_length=50, unique=True, help_text="Unique transaction identifier")
    description = models.TextField(help_text="Human-readable description")
    details = models.JSONField(default=dict, help_text="Detailed transaction data")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='mo_transactions')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'MO Transaction History'
        verbose_name_plural = 'MO Transaction Histories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo', 'transaction_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.mo.mo_id} - {self.transaction_type} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

