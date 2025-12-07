from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from utils.enums import DepartmentChoices, ShiftChoices, RoleHierarchyChoices


class CustomUser(AbstractUser):
    """
    Custom User model extending Django's AbstractUser
    """
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=30)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    class Meta:
        db_table = 'auth_user'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class UserProfile(models.Model):
    """
    Extended user profile information for MSP-ERP
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='profile')
    employee_id = models.CharField(max_length=20, unique=True)
    designation = models.CharField(max_length=100)
    department = models.CharField(max_length=20, choices=DepartmentChoices.choices)
    shift = models.CharField(max_length=10, choices=ShiftChoices.choices, null=True, blank=True)
    date_of_joining = models.DateField()
    phone_number = models.CharField(max_length=15)
    is_active = models.BooleanField(default=True)
    is_engaged = models.BooleanField(default=False, help_text="Currently assigned to a process")
    
    # Network access control
    allowed_ip_ranges = models.JSONField(default=list, help_text="Allowed IP ranges for network restriction")
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.full_name} - {self.employee_id}"


class Role(models.Model):
    """
    Hierarchical role-based access control for MSP-ERP
    """
    name = models.CharField(max_length=50, choices=RoleHierarchyChoices.choices, unique=True)
    description = models.TextField()
    hierarchy_level = models.IntegerField(default=5, help_text="Lower number = higher authority")
    permissions = models.JSONField(default=dict, null=True, blank=True)  # Module-specific permissions
    
    # Department restrictions
    restricted_departments = models.JSONField(default=list, null=True, blank=True,help_text="Departments this role can access")
    
    class Meta:
        verbose_name = 'Role'
        verbose_name_plural = 'Roles'
        ordering = ['hierarchy_level']

    def __str__(self):
        return self.get_name_display()
    
    def can_access_department(self, department):
        """Check if role can access specific department"""
        if not self.restricted_departments:
            return True  # No restrictions
        return department in self.restricted_departments


class UserRole(models.Model):
    """
    User role assignments with audit trail
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='user_roles')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='role_users')
    assigned_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='roles_assigned')
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ['user', 'role']
        verbose_name = 'User Role'
        verbose_name_plural = 'User Roles'

    def __str__(self):
        return f"{self.user.full_name} - {self.role.name}"


class ProcessSupervisor(models.Model):
    """
    Process-specific supervisor assignments
    """
    supervisor = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='process_supervisor_assignments')
    process_names = models.JSONField(default=list, help_text="List of process names this supervisor handles")
    department = models.CharField(max_length=20, choices=DepartmentChoices.choices)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['supervisor', 'department']
        verbose_name = 'Process Supervisor'
        verbose_name_plural = 'Process Supervisors'
    
    def __str__(self):
        return f"{self.supervisor.full_name} - {self.get_department_display()}"


class OperatorEngagement(models.Model):
    """
    Track operator engagement to prevent double assignment
    """
    operator = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='current_engagement')
    batch_id = models.CharField(max_length=20, null=True, blank=True)
    process_step = models.CharField(max_length=100, null=True, blank=True)
    machine_id = models.CharField(max_length=20, null=True, blank=True)
    start_time = models.DateTimeField(auto_now_add=True)
    estimated_end_time = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = 'Operator Engagement'
        verbose_name_plural = 'Operator Engagements'
    
    def __str__(self):
        return f"{self.operator.full_name} - {self.batch_id or 'Available'}"


class BreakType(models.Model):
    """
    Configurable break types for supervisors (lunch, general break, etc.)
    """
    name = models.CharField(max_length=100, unique=True, help_text="Break type name (e.g., Lunch Break, Tea Break)")
    description = models.TextField(blank=True, help_text="Description of this break type")
    is_active = models.BooleanField(default=True, help_text="Whether this break type is currently active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Break Type'
        verbose_name_plural = 'Break Types'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class SupervisorBreak(models.Model):
    """
    Track supervisor breaks during login sessions
    """
    login_session = models.ForeignKey('LoginSession', on_delete=models.CASCADE, related_name='breaks')
    break_type = models.ForeignKey(BreakType, on_delete=models.PROTECT, related_name='supervisor_breaks')
    break_start_time = models.DateTimeField(help_text="When the break started")
    break_end_time = models.DateTimeField(null=True, blank=True, help_text="When the break ended (null if still on break)")
    notes = models.TextField(blank=True, help_text="Optional notes about this break")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Supervisor Break'
        verbose_name_plural = 'Supervisor Breaks'
        ordering = ['-break_start_time']
    
    def __str__(self):
        status = "Active" if not self.break_end_time else "Completed"
        return f"{self.login_session.user.get_full_name()} - {self.break_type.name} ({status})"
    
    @property
    def duration_minutes(self):
        """Calculate break duration in minutes"""
        if self.break_end_time:
            return int((self.break_end_time - self.break_start_time).total_seconds() / 60)
        return None  # Still on break


class LoginSession(models.Model):
    """
    Track user login sessions for network restriction and productive hours
    """
    LOGOUT_REASON_CHOICES = [
        ('break', 'Break (Lunch/General)'),
        ('end_of_day', 'End of Day / Final Logout'),
    ]
    
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='login_sessions')
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField()
    login_time = models.DateTimeField(auto_now_add=True)
    logout_time = models.DateTimeField(null=True, blank=True)
    logout_reason = models.CharField(
        max_length=20,
        choices=LOGOUT_REASON_CHOICES,
        null=True,
        blank=True,
        help_text="Reason for logout: break or end of day"
    )
    break_type = models.ForeignKey(
        'BreakType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logout_sessions',
        help_text="Break type if logout reason is 'break'"
    )
    is_active = models.BooleanField(default=True)
    is_final_logout = models.BooleanField(
        default=False,
        help_text="True if this is the final logout for the day"
    )
    
    class Meta:
        verbose_name = 'Login Session'
        verbose_name_plural = 'Login Sessions'
        ordering = ['-login_time']
    
    def __str__(self):
        return f"{self.user.email} - {self.ip_address}"
    
    @property
    def total_break_minutes(self):
        """Calculate total break time in minutes for this session"""
        return sum(
            break_obj.duration_minutes or 0
            for break_obj in self.breaks.filter(break_end_time__isnull=False)
        )
    
    @property
    def productive_minutes(self):
        """Calculate productive time (excluding breaks) in minutes"""
        if not self.logout_time:
            return None  # Session still active
        
        total_minutes = int((self.logout_time - self.login_time).total_seconds() / 60)
        break_minutes = self.total_break_minutes
        return max(0, total_minutes - break_minutes)
    
    @property
    def non_productive_hours(self):
        """
        Calculate non-productive hours if there's a gap between sessions on the same day
        If user logged out for break and logged back in after 4+ hours, those hours are non-productive
        """
        if not self.logout_time or self.logout_reason != 'break':
            return 0
        
        # Find next login session on the same day
        next_session = LoginSession.objects.filter(
            user=self.user,
            login_time__date=self.login_time.date(),
            login_time__gt=self.logout_time
        ).order_by('login_time').first()
        
        if next_session:
            gap_hours = (next_session.login_time - self.logout_time).total_seconds() / 3600
            # If gap is more than 4 hours, consider it non-productive
            if gap_hours > 4:
                return gap_hours - 4  # Subtract 4 hours as acceptable break time
        elif self.logout_reason == 'break':
            # If they logged out for break but never logged back in on the same day
            # Check if break is still active (more than 4 hours)
            active_break = self.breaks.filter(break_end_time__isnull=True).first()
            if active_break:
                gap_hours = (timezone.now() - self.logout_time).total_seconds() / 3600
                if gap_hours > 4:
                    return gap_hours - 4
        
        return 0