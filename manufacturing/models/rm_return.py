"""
RM Return Models - Track all RM returns from batches
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class RMReturnReasonChoices(models.TextChoices):
    LEFT_OVER = 'left_over', 'Left Over'
    QUALITY_DEFECT = 'quality_defect', 'Quality Defect'
    WRONG_RM = 'wrong_rm', 'Wrong RM'


class RMReturn(models.Model):
    """
    Track RM returns from batches with different return reasons
    """
    # Auto-generated identifier
    return_id = models.CharField(max_length=30, unique=True, editable=False)
    
    # Relationships
    batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='batch_rm_returns',
        help_text="Batch from which RM is being returned"
    )
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='mo_rm_returns',
        help_text="Manufacturing Order"
    )
    
    # Return details
    return_reason = models.CharField(
        max_length=20,
        choices=RMReturnReasonChoices.choices,
        help_text="Reason for RM return"
    )
    returned_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity returned in kg"
    )
    
    # Coil/Heat tracking
    heat_number = models.CharField(
        max_length=50,
        help_text="Heat number of the returned RM"
    )
    coil_number = models.CharField(
        max_length=50,
        help_text="Coil number of the returned RM"
    )
    
    # Additional details
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about the return"
    )
    
    # Status tracking
    is_processed = models.BooleanField(
        default=False,
        help_text="Whether the return has been processed (stock updated)"
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the return was processed"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_rm_returns',
        help_text="RM Store user who processed the return"
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'RM Return'
        verbose_name_plural = 'RM Returns'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['batch', 'return_reason']),
            models.Index(fields=['mo', 'return_reason']),
            models.Index(fields=['heat_number', 'coil_number']),
            models.Index(fields=['created_at']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate return_id if not set
        if not self.return_id:
            # Format: RMR-BATCH_ID-SEQUENCE
            # Example: RMR-BATCH-MO-20250927-0001-001-001
            existing_returns = RMReturn.objects.filter(
                batch=self.batch
            ).count()
            sequence = existing_returns + 1
            self.return_id = f"RMR-{self.batch.batch_id}-{sequence:03d}"
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.return_id} - {self.get_return_reason_display()} ({self.returned_kg}kg)"

