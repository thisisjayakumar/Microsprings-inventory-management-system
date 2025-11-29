"""
MO Supervisor Configuration Models
Per-MO supervisor overrides and shift configurations
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

User = get_user_model()


class MOShiftConfiguration(models.Model):
    """
    Defines which shifts an MO should run in
    PH can configure shift 1, shift 2, shift 3 for a specific MO
    """
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='shift_configurations',
        help_text="MO for this shift configuration"
    )
    shift = models.CharField(
        max_length=10,
        choices=[('shift_1', 'Shift 1'), ('shift_2', 'Shift 2'), ('shift_3', 'Shift 3')],
        help_text="Shift identifier"
    )
    
    # Shift timing
    shift_start_time = models.TimeField(help_text="Shift start time (e.g., 09:00)")
    shift_end_time = models.TimeField(help_text="Shift end time (e.g., 17:00)")
    
    # Status
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this shift is currently active for this MO"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_mo_shifts'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'MO Shift Configuration'
        verbose_name_plural = 'MO Shift Configurations'
        ordering = ['mo', 'shift']
        unique_together = [['mo', 'shift']]
        indexes = [
            models.Index(fields=['mo', 'shift', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.mo.mo_id} - {self.shift}"


class MOSupervisorOverride(models.Model):
    """
    Per-MO supervisor overrides for specific processes and shifts
    PH can override the default supervisors for a specific MO + process + shift
    This takes precedence over global WorkCenterSupervisorShift defaults
    """
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='supervisor_overrides',
        help_text="MO for this supervisor override"
    )
    process = models.ForeignKey(
        'processes.Process',
        on_delete=models.CASCADE,
        related_name='mo_supervisor_overrides',
        help_text="Process (work center) for this override"
    )
    shift = models.CharField(
        max_length=10,
        choices=[('shift_1', 'Shift 1'), ('shift_2', 'Shift 2'), ('shift_3', 'Shift 3')],
        help_text="Shift for this override"
    )
    
    # Overridden supervisor assignments
    primary_supervisor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='mo_primary_overrides',
        help_text="Primary supervisor for this MO + process + shift"
    )
    backup_supervisor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='mo_backup_overrides',
        help_text="Backup supervisor for this MO + process + shift"
    )
    
    # Override reason/notes
    override_reason = models.TextField(
        blank=True,
        help_text="Reason for overriding the default supervisors"
    )
    
    # Status
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this override is currently active"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_mo_overrides'
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_mo_overrides'
    )
    
    class Meta:
        verbose_name = 'MO Supervisor Override'
        verbose_name_plural = 'MO Supervisor Overrides'
        ordering = ['mo', 'process', 'shift']
        unique_together = [['mo', 'process', 'shift']]
        indexes = [
            models.Index(fields=['mo', 'process', 'shift', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.mo.mo_id} - {self.process.name} - {self.shift} - {self.primary_supervisor.get_full_name()}"
    
    def clean(self):
        """Validate that primary and backup supervisors are different"""
        if self.primary_supervisor == self.backup_supervisor:
            raise ValidationError("Primary and backup supervisors must be different users")
        
        # Validate that both users have supervisor role
        if not self.primary_supervisor.user_roles.filter(role__name='supervisor', is_active=True).exists():
            raise ValidationError(f"{self.primary_supervisor.get_full_name()} is not assigned as a supervisor")
        
        if not self.backup_supervisor.user_roles.filter(role__name='supervisor', is_active=True).exists():
            raise ValidationError(f"{self.backup_supervisor.get_full_name()} is not assigned as a supervisor")


class SupervisorChangeLog(models.Model):
    """
    Tracks all supervisor changes for process executions
    Provides complete timestamp audit trail
    """
    mo_process_execution = models.ForeignKey(
        'manufacturing.MOProcessExecution',
        on_delete=models.CASCADE,
        related_name='supervisor_changes',
        help_text="Process execution for this supervisor change"
    )
    
    # Change details
    from_supervisor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supervisor_changes_from',
        help_text="Previous supervisor (null if first assignment)"
    )
    to_supervisor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='supervisor_changes_to',
        help_text="New supervisor"
    )
    
    # Change metadata
    change_reason = models.CharField(
        max_length=50,
        choices=[
            ('initial_assignment', 'Initial Assignment'),
            ('attendance_absence', 'Attendance - Primary Absent'),
            ('mid_process_change', 'Mid-Process Change by PH'),
            ('shift_change', 'Shift Change'),
            ('manual_override', 'Manual Override'),
            ('both_unavailable', 'Both Primary and Backup Unavailable'),
        ],
        help_text="Reason for supervisor change"
    )
    change_notes = models.TextField(blank=True)
    
    # Shift context
    shift = models.CharField(
        max_length=10,
        choices=[('shift_1', 'Shift 1'), ('shift_2', 'Shift 2'), ('shift_3', 'Shift 3')],
        null=True,
        blank=True,
        help_text="Shift when this change occurred"
    )
    
    # Timestamp
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='initiated_supervisor_changes',
        help_text="User who initiated this change (null for automatic changes)"
    )
    
    # Process state when changed
    process_status_at_change = models.CharField(
        max_length=20,
        blank=True,
        help_text="Status of process execution when supervisor changed"
    )
    
    class Meta:
        verbose_name = 'Supervisor Change Log'
        verbose_name_plural = 'Supervisor Change Logs'
        ordering = ['-changed_at']
        indexes = [
            models.Index(fields=['mo_process_execution', 'changed_at']),
            models.Index(fields=['to_supervisor', 'changed_at']),
            models.Index(fields=['change_reason']),
        ]
    
    def __str__(self):
        from_name = self.from_supervisor.get_full_name() if self.from_supervisor else 'Unassigned'
        to_name = self.to_supervisor.get_full_name()
        return f"{self.mo_process_execution.mo.mo_id} - {from_name} → {to_name}"

