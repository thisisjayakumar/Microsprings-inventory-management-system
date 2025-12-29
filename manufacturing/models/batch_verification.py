"""
Batch Receipt Verification Models
Handles verify/report mechanism when supervisors receive batches from previous process
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

User = get_user_model()


class VerificationAction(models.TextChoices):
    """Actions taken during batch receipt"""
    VERIFIED = 'verified', 'Verified - OK'
    REPORTED = 'reported', 'Reported - Issue'


class ReportReason(models.TextChoices):
    """Reasons for reporting batch issues"""
    LOW_QTY = 'low_qty', 'Low Qty Received'
    HIGH_QTY = 'high_qty', 'High Qty Received'
    DAMAGED = 'damaged', 'Damaged / Defective Parts'
    WRONG_PRODUCT = 'wrong_product', 'Wrong Product Received'
    QUALITY_ISSUE = 'quality_issue', 'Quality Issue'
    OTHERS = 'others', 'Others'


class BatchReceiptVerification(models.Model):
    """
    Track batch receipt verification/reporting by receiving supervisor
    Implements verify/report mechanism with on-hold functionality
    """
    # Relations
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='receipt_verifications'
    )
    process_execution = models.ForeignKey(
        'manufacturing.MOProcessExecution',
        on_delete=models.CASCADE,
        related_name='batch_receipts',
        help_text="Process where batch is being received"
    )
    previous_process = models.ForeignKey(
        'processes.Process',
        on_delete=models.PROTECT,
        related_name='outgoing_batch_receipts',
        null=True,
        blank=True,
        help_text="Process from which batch was received"
    )
    
    # Receipt details
    received_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='batch_receipts_verified'
    )
    received_at = models.DateTimeField(auto_now_add=True)
    
    # Action taken
    action = models.CharField(
        max_length=20,
        choices=VerificationAction.choices
    )
    
    # Quantity tracking
    expected_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Expected quantity as per system (kg)"
    )
    actual_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual quantity received (kg) - required if reported"
    )
    
    # Report details (if action = reported)
    report_reason = models.CharField(
        max_length=30,
        choices=ReportReason.choices,
        null=True,
        blank=True
    )
    report_details = models.TextField(
        blank=True,
        help_text="Additional details about the issue"
    )
    
    # On-hold management
    is_on_hold = models.BooleanField(
        default=False,
        help_text="Whether batch is currently on hold"
    )
    hold_cleared_at = models.DateTimeField(null=True, blank=True)
    hold_cleared_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='batch_holds_cleared'
    )
    clearance_notes = models.TextField(blank=True)
    
    # Resolution tracking
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='batch_reports_resolved',
        help_text="PH or Manager who resolved the issue"
    )
    resolution_notes = models.TextField(blank=True)
    
    # Notifications
    notification_sent_to_ph = models.BooleanField(default=False)
    notification_sent_to_prev_supervisor = models.BooleanField(default=False)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['batch', 'action']),
            models.Index(fields=['process_execution', '-received_at']),
            models.Index(fields=['is_on_hold', 'is_resolved']),
        ]
    
    def __str__(self):
        return f"{self.batch.batch_id} @ {self.process_execution.process.name} - {self.action}"
    
    def clean(self):
        """Validate verification data"""
        if self.action == 'reported':
            if not self.report_reason:
                raise ValidationError("report_reason is required when action is 'reported'")
            
            if self.report_reason in ['low_qty', 'high_qty'] and not self.actual_quantity_kg:
                raise ValidationError("actual_quantity_kg is required when reporting quantity issues")
        
        if self.actual_quantity_kg and self.actual_quantity_kg < 0:
            raise ValidationError("actual_quantity_kg cannot be negative")
    
    def save(self, *args, **kwargs):
        # Auto set on-hold if reported
        if self.action == 'reported' and not self.is_resolved:
            self.is_on_hold = True
        
        self.full_clean()
        super().save(*args, **kwargs)
    
    @property
    def quantity_variance_kg(self):
        """Calculate variance between expected and actual"""
        if self.actual_quantity_kg:
            return self.actual_quantity_kg - self.expected_quantity_kg
        return Decimal('0')
    
    @property
    def quantity_variance_percentage(self):
        """Calculate variance percentage"""
        if self.expected_quantity_kg > 0 and self.actual_quantity_kg:
            variance = self.actual_quantity_kg - self.expected_quantity_kg
            return float((variance / self.expected_quantity_kg) * 100)
        return 0
    
    def clear_hold(self, cleared_by_user, notes=''):
        """Clear the on-hold status - PH action"""
        if not self.is_on_hold:
            raise ValidationError("Batch is not on hold")
        
        if self.action != 'reported':
            raise ValidationError("Can only clear hold for reported batches")
        
        self.is_on_hold = False
        self.hold_cleared_at = timezone.now()
        self.hold_cleared_by = cleared_by_user
        self.clearance_notes = notes
        self.save()
        
        # Batch automatically returns to "To Process" tab
        return True
    
    def resolve_issue(self, resolved_by_user, resolution_notes=''):
        """Resolve the reported issue - PH/Manager action"""
        if self.action != 'reported':
            raise ValidationError("Can only resolve reported batches")
        
        if self.is_resolved:
            raise ValidationError("Issue already resolved")
        
        self.is_resolved = True
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by_user
        self.resolution_notes = resolution_notes
        
        # Also clear hold when resolving
        if self.is_on_hold:
            self.is_on_hold = False
            self.hold_cleared_at = timezone.now()
            self.hold_cleared_by = resolved_by_user
        
        self.save()
        
        return True


class BatchReceiptLog(models.Model):
    """
    Comprehensive log of all batch movements between processes
    Tracks every handover with verification status
    """
    # Relations
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='receipt_logs'
    )
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='batch_receipt_logs'
    )
    
    # Movement details
    from_process = models.ForeignKey(
        'processes.Process',
        on_delete=models.PROTECT,
        related_name='batch_outgoing_logs',
        null=True,
        blank=True,
        help_text="Source process (null if starting from RM Store)"
    )
    to_process = models.ForeignKey(
        'processes.Process',
        on_delete=models.PROTECT,
        related_name='batch_incoming_logs',
        help_text="Destination process"
    )
    
    # Handover
    handed_over_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='batch_handovers_sent',
        null=True,
        blank=True
    )
    handed_over_at = models.DateTimeField(null=True, blank=True)
    
    # Receipt
    received_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='batch_handovers_received',
        null=True,
        blank=True
    )
    received_at = models.DateTimeField(null=True, blank=True)
    
    # Quantity
    handed_over_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Quantity handed over (kg)"
    )
    received_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Quantity confirmed received (kg)"
    )
    
    # Verification
    verification_record = models.ForeignKey(
        BatchReceiptVerification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='receipt_logs'
    )
    
    # Rework tracking for separate batch flow
    is_rework_batch = models.BooleanField(
        default=False,
        help_text="True if this is OK quantity from a rework completion"
    )
    rework_cycle_number = models.PositiveIntegerField(
        default=0,
        help_text="Rework cycle (0 = original, 1 = R1, 2 = R2, etc.)"
    )
    parent_completion = models.ForeignKey(
        'manufacturing.BatchProcessCompletion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_receipt_logs',
        help_text="Completion record that generated this rework OK quantity"
    )
    
    # Status
    is_verified = models.BooleanField(default=False)
    has_issues = models.BooleanField(default=False)
    
    # Transit time
    transit_duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Time between handover and receipt"
    )
    
    # Notes
    handover_notes = models.TextField(blank=True)
    receipt_notes = models.TextField(blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['batch', '-created_at']),
            models.Index(fields=['from_process', 'to_process']),
            models.Index(fields=['is_verified', 'has_issues']),
            models.Index(fields=['is_rework_batch', 'rework_cycle_number']),
        ]
    
    def __str__(self):
        from_text = self.from_process.name if self.from_process else "RM Store"
        rework_indicator = f"-R{self.rework_cycle_number}" if self.is_rework_batch and self.rework_cycle_number > 0 else ""
        return f"{self.batch.batch_id}{rework_indicator}: {from_text} → {self.to_process.name}"
    
    @property
    def batch_display_id(self):
        """Get batch ID with rework indicator for display"""
        if self.is_rework_batch and self.rework_cycle_number > 0:
            return f"{self.batch.batch_id}-R{self.rework_cycle_number}"
        return self.batch.batch_id
    
    def confirm_receipt(self, verification_record):
        """Link verification record and update receipt info"""
        self.verification_record = verification_record
        self.received_by = verification_record.received_by
        self.received_at = verification_record.received_at
        self.is_verified = True
        
        if verification_record.action == 'reported':
            self.has_issues = True
            if verification_record.actual_quantity_kg:
                self.received_quantity_kg = verification_record.actual_quantity_kg
        else:
            self.received_quantity_kg = self.handed_over_quantity_kg
        
        # Calculate transit time
        if self.handed_over_at and self.received_at:
            delta = self.received_at - self.handed_over_at
            self.transit_duration_minutes = int(delta.total_seconds() / 60)
        
        self.save()
        
        return True

