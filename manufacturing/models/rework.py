"""
Rework Tracking Models
Handles OK/Scrap/Rework quantity tracking with cycle management
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

User = get_user_model()


class ReworkSource(models.TextChoices):
    """Source of rework"""
    PROCESS_SUPERVISOR = 'process_supervisor', 'Process Supervisor'
    FINAL_INSPECTION = 'final_inspection', 'Final Inspection'
    QUALITY_CHECK = 'quality_check', 'Quality Check'


class ReworkStatus(models.TextChoices):
    """Rework status"""
    PENDING = 'pending', 'Pending'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


class BatchProcessCompletion(models.Model):
    """
    Track batch process completion with OK/Scrap/Rework quantities
    Replaces simple completion with quantity split tracking
    """
    # Relations
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='process_completions'
    )
    process_execution = models.ForeignKey(
        'manufacturing.MOProcessExecution',
        on_delete=models.CASCADE,
        related_name='batch_completions'
    )
    
    # Completion details
    completed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='batch_completions'
    )
    completed_at = models.DateTimeField(auto_now_add=True)
    
    # Quantity tracking (in kg)
    input_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Input quantity received for processing (kg)"
    )
    ok_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="OK quantity moving to next process (kg)"
    )
    scrap_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Scrap quantity (kg)"
    )
    rework_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Rework quantity staying with supervisor (kg)"
    )
    
    # Rework tracking
    is_rework_cycle = models.BooleanField(
        default=False,
        help_text="Is this a rework completion?"
    )
    rework_cycle_number = models.PositiveIntegerField(
        default=0,
        help_text="Rework cycle count (0 = first time, 1 = first rework, etc.)"
    )
    parent_completion = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rework_completions',
        help_text="Original completion that generated this rework"
    )
    
    # Notes
    completion_notes = models.TextField(blank=True)
    defect_description = models.TextField(
        blank=True,
        help_text="Description of defects for rework items"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-completed_at']
        indexes = [
            models.Index(fields=['batch', '-completed_at']),
            models.Index(fields=['process_execution', '-completed_at']),
            models.Index(fields=['is_rework_cycle', 'rework_cycle_number']),
        ]
    
    def __str__(self):
        cycle_text = f" [R{self.rework_cycle_number}]" if self.is_rework_cycle else ""
        return f"{self.batch.batch_id} - {self.process_execution.process.name}{cycle_text}"
    
    def clean(self):
        """Validate quantities"""
        total = self.ok_quantity_kg + self.scrap_quantity_kg + self.rework_quantity_kg
        
        # Allow small tolerance for rounding
        tolerance = Decimal('0.01')
        if abs(total - self.input_quantity_kg) > tolerance:
            raise ValidationError(
                f"OK + Scrap + Rework ({total} kg) must equal Input ({self.input_quantity_kg} kg)"
            )
        
        if self.ok_quantity_kg < 0 or self.scrap_quantity_kg < 0 or self.rework_quantity_kg < 0:
            raise ValidationError("Quantities cannot be negative")
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    @property
    def ok_percentage(self):
        """Calculate OK percentage"""
        if self.input_quantity_kg > 0:
            return float((self.ok_quantity_kg / self.input_quantity_kg) * 100)
        return 0
    
    @property
    def scrap_percentage(self):
        """Calculate scrap percentage"""
        if self.input_quantity_kg > 0:
            return float((self.scrap_quantity_kg / self.input_quantity_kg) * 100)
        return 0
    
    @property
    def rework_percentage(self):
        """Calculate rework percentage"""
        if self.input_quantity_kg > 0:
            return float((self.rework_quantity_kg / self.input_quantity_kg) * 100)
        return 0
    
    @property
    def rework_badge(self):
        """Display badge text for rework cycles"""
        if self.rework_cycle_number > 0:
            return f"R{self.rework_cycle_number}"
        return None


class ReworkBatch(models.Model):
    """
    Track rework batches that stay with supervisor for rework
    Separate from main batch flow
    """
    # Relations
    original_batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='rework_batches'
    )
    process_execution = models.ForeignKey(
        'manufacturing.MOProcessExecution',
        on_delete=models.CASCADE,
        related_name='rework_batches'
    )
    completion_record = models.ForeignKey(
        BatchProcessCompletion,
        on_delete=models.CASCADE,
        related_name='rework_batch_records'
    )
    
    # Rework details
    rework_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Quantity pending rework (kg)"
    )
    status = models.CharField(
        max_length=20,
        choices=ReworkStatus.choices,
        default='pending'
    )
    source = models.CharField(
        max_length=30,
        choices=ReworkSource.choices,
        default='process_supervisor'
    )
    
    # Assignment
    assigned_supervisor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='rework_batches_assigned'
    )
    
    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Rework cycle
    rework_cycle_number = models.PositiveIntegerField(
        help_text="Which rework cycle this is"
    )
    
    # Defect tracking
    defect_description = models.TextField()
    defect_process = models.ForeignKey(
        'processes.Process',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Process that caused the defect (for FI rework)"
    )
    
    # Notes
    supervisor_notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['original_batch', 'status']),
            models.Index(fields=['process_execution', 'status']),
            models.Index(fields=['assigned_supervisor', 'status']),
        ]
    
    def __str__(self):
        return f"Rework R{self.rework_cycle_number} - {self.original_batch.batch_id} ({self.status})"
    
    def start_rework(self):
        """Mark rework as started"""
        if self.status != 'pending':
            raise ValidationError("Can only start pending rework")
        
        self.status = 'in_progress'
        self.started_at = timezone.now()
        self.save()
    
    def complete_rework(self, ok_kg, scrap_kg, rework_kg=Decimal('0')):
        """Complete rework and create completion record
        
        Args:
            ok_kg: OK quantity moving forward (kg)
            scrap_kg: Scrap quantity (kg)
            rework_kg: Rework quantity for next cycle (kg), defaults to 0
        
        Returns:
            tuple: (completion, updated_rework_batch or None)
            - If rework_kg > 0: returns (completion, self) - same batch stays in_progress with updated cycle
            - If rework_kg = 0: returns (completion, None) - batch marked as completed
        """
        if self.status != 'in_progress':
            raise ValidationError("Rework must be in progress to complete")
        
        # Validate quantities
        total = Decimal(str(ok_kg)) + Decimal(str(scrap_kg)) + Decimal(str(rework_kg))
        tolerance = Decimal('0.01')
        
        if abs(total - self.rework_quantity_kg) > tolerance:
            raise ValidationError(
                f"OK ({ok_kg}) + Scrap ({scrap_kg}) + Rework ({rework_kg}) must equal Rework quantity ({self.rework_quantity_kg})"
            )
        
        # Create completion record
        completion = BatchProcessCompletion.objects.create(
            batch=self.original_batch,
            process_execution=self.process_execution,
            completed_by=self.assigned_supervisor,
            input_quantity_kg=self.rework_quantity_kg,
            ok_quantity_kg=ok_kg,
            scrap_quantity_kg=scrap_kg,
            rework_quantity_kg=Decimal(str(rework_kg)),
            is_rework_cycle=True,
            rework_cycle_number=self.rework_cycle_number,
            parent_completion=self.completion_record,
            completion_notes=f"Rework cycle {self.rework_cycle_number} completed"
        )
        
        # If rework quantity > 0, update same batch for next cycle (stay in_progress)
        if Decimal(str(rework_kg)) > 0:
            # Update the same rework batch: increment cycle, update quantity, keep in_progress
            self.rework_cycle_number += 1
            self.rework_quantity_kg = Decimal(str(rework_kg))
            self.completion_record = completion  # Link to new completion record
            self.status = 'in_progress'  # Keep in_progress for next cycle
            self.started_at = timezone.now()  # Reset start time for new cycle
            self.completed_at = None  # Clear completion time
            self.save()
            
            return completion, self
        
        # If no rework quantity, mark as completed
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.save()
        
        return completion, None


class FinalInspectionRework(models.Model):
    """
    Track rework redirected by Final Inspection to specific processes
    Special handling for FI rework with process assignment
    """
    # Relations
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='fi_reworks'
    )
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='fi_reworks'
    )
    
    # FI details
    inspected_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='fi_reworks_created'
    )
    inspected_at = models.DateTimeField(auto_now_add=True)
    
    # Defect details
    defective_process = models.ForeignKey(
        'processes.Process',
        on_delete=models.PROTECT,
        help_text="Process identified as source of defect"
    )
    defect_description = models.TextField()
    rework_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Quantity sent for rework (kg)"
    )
    
    # Assignment
    assigned_to_supervisor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='fi_reworks_assigned',
        help_text="Supervisor of defective process"
    )
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=ReworkStatus.choices,
        default='pending'
    )
    rework_started_at = models.DateTimeField(null=True, blank=True)
    rework_completed_at = models.DateTimeField(null=True, blank=True)
    
    # Re-inspection
    reinspected_at = models.DateTimeField(null=True, blank=True)
    reinspected_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fi_reworks_reinspected'
    )
    reinspection_passed = models.BooleanField(null=True, blank=True)
    reinspection_notes = models.TextField(blank=True)
    
    # Cycle tracking
    rework_cycle_count = models.PositiveIntegerField(
        default=1,
        help_text="How many times this batch went through FI rework loop"
    )
    
    # Notes
    fi_notes = models.TextField(blank=True)
    supervisor_notes = models.TextField(blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-inspected_at']
        indexes = [
            models.Index(fields=['batch', 'status']),
            models.Index(fields=['defective_process', 'status']),
            models.Index(fields=['assigned_to_supervisor', 'status']),
        ]
    
    def __str__(self):
        return f"FI Rework - {self.batch.batch_id} @ {self.defective_process.name}"
    
    def complete_rework(self, completed_by_user):
        """Mark rework as completed, ready for re-inspection"""
        if self.status != 'in_progress':
            raise ValidationError("Rework must be in progress to complete")
        
        self.status = 'completed'
        self.rework_completed_at = timezone.now()
        self.save()
        
        # Batch automatically routes back to Final Inspection
        return True
    
    def pass_reinspection(self, inspector_user, notes=''):
        """FI passes the reworked batch"""
        if self.status != 'completed':
            raise ValidationError("Rework must be completed before reinspection")
        
        self.reinspected_by = inspector_user
        self.reinspected_at = timezone.now()
        self.reinspection_passed = True
        self.reinspection_notes = notes
        self.save()
        
        # Batch can now move to Packing Zone
        return True

