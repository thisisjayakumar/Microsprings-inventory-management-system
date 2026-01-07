"""
Batch Models
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from utils.enums import BatchStatusChoices

User = get_user_model()


class Batch(models.Model):
    """
    Production Batch - Breaks down Manufacturing Orders into manageable production units
    
    Key Concept: 1 MO can have multiple Batches until total batch quantities fulfill MO target quantity
    """
    # Auto-generated unique identifier
    # Format: BATCH-MO-YYYYMMDD-XXXX-XXX or BATCH-MO-YYYYMMDD-XXXX-XXX-RN for rework
    batch_id = models.CharField(max_length=35, unique=True, editable=False)
    
    # Relationships
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='batches',
        help_text="Parent Manufacturing Order"
    )
    product_code = models.ForeignKey(
        'products.Product',
        on_delete=models.PROTECT,
        related_name='batches',
        help_text="Product being manufactured in this batch (should match MO product)"
    )
    
    # Quantities
    planned_quantity = models.PositiveIntegerField(help_text="Planned quantity for this batch")
    actual_quantity_started = models.PositiveIntegerField(default=0, help_text="Actual quantity that started production")
    actual_quantity_completed = models.PositiveIntegerField(default=0, help_text="Actual quantity completed successfully")
    scrap_quantity = models.PositiveIntegerField(default=0, help_text="Quantity scrapped during production")
    scrap_rm_weight = models.PositiveIntegerField(default=0, help_text="Raw material weight sent to scrap (in grams)")
    
    # Timing
    planned_start_date = models.DateTimeField(null=True, blank=True, help_text="Planned start date for this batch")
    planned_end_date = models.DateTimeField(null=True, blank=True, help_text="Planned completion date for this batch")
    actual_start_date = models.DateTimeField(null=True, blank=True, help_text="Actual start date")
    actual_end_date = models.DateTimeField(null=True, blank=True, help_text="Actual completion date")
    
    # Status and Progress
    status = models.CharField(max_length=20, choices=BatchStatusChoices.choices, default='created')
    progress_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Overall completion percentage")
    
    # Process tracking
    current_process_step = models.ForeignKey(
        'processes.ProcessStep',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="Current process step being executed"
    )
    
    # Assignment
    assigned_operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_batches',
        help_text="Primary operator assigned to this batch"
    )
    assigned_supervisor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supervised_batches',
        help_text="Supervisor overseeing this batch"
    )
    
    # Metrics
    total_processing_time_minutes = models.PositiveIntegerField(default=0, help_text="Total time spent in processing")
    
    # Additional tracking
    notes = models.TextField(blank=True, help_text="Any special notes or instructions for this batch")
    
    # RM Release Tracking
    released_coils = models.JSONField(
        default=list,
        blank=True,
        help_text="List of coils released for this batch with heat numbers and weights"
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when batch was released (RM allocated)"
    )
    rm_returned_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=0,
        help_text="Total RM returned from this batch (in kg)"
    )
    
    # MO Stop/Release control
    can_release = models.BooleanField(
        default=True,
        help_text="Whether this batch can be released for production. Set to False when MO is stopped."
    )
    
    # Quality defect tracking
    has_quality_defect = models.BooleanField(
        default=False,
        help_text="True if any coil in this batch was returned as quality defect. This batch will be excluded from RM calculations."
    )
    
    # Rework batch tracking
    is_rework_batch = models.BooleanField(
        default=False,
        help_text="True if this is a rework batch created from a rework cycle. Rework batches don't require RM allocation and should only be visible to supervisors."
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_batches')
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Production Batch'
        verbose_name_plural = 'Production Batches'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo', 'status']),
            models.Index(fields=['product_code', 'status']),
            models.Index(fields=['batch_id']),
            models.Index(fields=['is_rework_batch', 'status']),
        ]
    
    def save(self, *args, **kwargs):
        if not self.batch_id:
            # Check if this is a rework batch (has rework_cycle_number in kwargs or as attribute)
            rework_cycle = kwargs.pop('rework_cycle_number', None) or getattr(self, '_rework_cycle_number', None)
            
            if rework_cycle and rework_cycle > 0:
                # Mark as rework batch
                self.is_rework_batch = True
                
                # For rework batches, use the original batch ID with rework cycle suffix
                original_batch_id = kwargs.pop('original_batch_id', None) or getattr(self, '_original_batch_id', None)
                if original_batch_id:
                    # IMPORTANT: Extract base batch ID by removing any existing R suffix
                    # This prevents double R1-R1 issue when creating rework from a rework batch
                    import re
                    # Remove any existing -R{N} suffix from original_batch_id
                    base_batch_id = re.sub(r'-R\d+$', '', original_batch_id)
                    
                    # Append rework cycle (e.g., "-R1")
                    proposed_batch_id = f"{base_batch_id}-R{rework_cycle}"
                    
                    # Check if this batch_id already exists (shouldn't happen, but be safe)
                    if Batch.objects.filter(batch_id=proposed_batch_id).exists():
                        # If it exists, append a counter
                        counter = 1
                        while Batch.objects.filter(batch_id=f"{proposed_batch_id}-{counter}").exists():
                            counter += 1
                        self.batch_id = f"{proposed_batch_id}-{counter}"
                    else:
                        self.batch_id = proposed_batch_id
                else:
                    # Fallback: generate based on MO
                    existing_batches = Batch.objects.filter(mo=self.mo).count()
                    sequence = existing_batches + 1
                    self.batch_id = f"BATCH-{self.mo.mo_id}-{sequence:03d}-R{rework_cycle}"
            else:
                # Regular batch creation - ensure is_rework_batch is False
                if not hasattr(self, 'is_rework_batch') or self.is_rework_batch is None:
                    self.is_rework_batch = False
                existing_batches = Batch.objects.filter(mo=self.mo).count()
                sequence = existing_batches + 1
                self.batch_id = f"BATCH-{self.mo.mo_id}-{sequence:03d}"
        
        # Validate product_code matches MO product
        if self.mo and self.product_code:
            if self.mo.product_code != self.product_code:
                raise ValueError(
                    f"Batch product_code ({self.product_code}) must match "
                    f"MO product_code ({self.mo.product_code})"
                )
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.batch_id} - {self.product_code.product_code} (Qty: {self.planned_quantity})"
    
    @property
    def completion_percentage(self):
        """Calculate completion percentage based on actual vs planned quantity"""
        if self.planned_quantity and self.planned_quantity > 0 and self.actual_quantity_completed is not None:
            return (self.actual_quantity_completed / self.planned_quantity) * 100
        return 0
    
    @property
    def rm_released_kg(self):
        """Calculate total RM released for this batch in kg
        Returns 0 if batch has quality defect (excluded from calculations)"""
        if self.has_quality_defect:
            return 0
        # planned_quantity is stored in grams, convert to kg
        return self.planned_quantity / 1000 if self.planned_quantity else 0
    
    @property
    def rm_used_kg(self):
        """Calculate actual RM used (Released - Returned)
        Returns 0 if batch has quality defect (excluded from calculations)
        Note: Quality defect batches are completely excluded from RM calculations"""
        if self.has_quality_defect:
            return 0
        
        released = self.planned_quantity / 1000 if self.planned_quantity else 0
        # Only count non-quality_defect returns
        from manufacturing.models.rm_return import RMReturn
        non_quality_returns = RMReturn.objects.filter(
            batch=self
        ).exclude(return_reason='quality_defect')
        returned = sum(float(r.returned_kg) for r in non_quality_returns) if non_quality_returns.exists() else 0
        return released - returned

