from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from .models import (
    CustomUser, UserProfile, Role, UserRole, 
    ProcessSupervisor, OperatorEngagement, LoginSession,
    BreakType, SupervisorBreak
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'
    fields = [
        'employee_id', 'designation', 'department', 'shift',
        'date_of_joining', 'phone_number', 'is_active', 'is_engaged',
        'allowed_ip_ranges'
    ]


class UserRoleInline(admin.TabularInline):
    model = UserRole
    fk_name = 'user'
    extra = 0
    fields = ['role', 'assigned_by', 'assigned_at', 'is_active']
    readonly_fields = ['assigned_at']


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    inlines = [UserProfileInline, UserRoleInline]
    list_display = [
        'email', 'full_name', 'get_employee_id', 'get_department', 
        'get_primary_role', 'is_active', 'date_joined'
    ]
    list_filter = [
        'is_active', 'is_staff', 'profile__department', 
        'profile__shift', 'profile__is_engaged'
    ]
    search_fields = ['email', 'first_name', 'last_name', 'profile__employee_id']
    ordering = ['-date_joined']
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email', 'phone_number')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('MSP-ERP Info', {
            'fields': ('get_employee_id', 'get_department', 'get_primary_role'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['date_joined', 'last_login', 'get_employee_id', 'get_department', 'get_primary_role']
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    
    def get_employee_id(self, obj):
        return obj.profile.employee_id if hasattr(obj, 'profile') else '-'
    get_employee_id.short_description = 'Employee ID'
    
    def get_department(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.get_department_display()
        return '-'
    get_department.short_description = 'Department'
    
    def get_primary_role(self, obj):
        active_role = obj.user_roles.filter(is_active=True).first()
        if active_role:
            return active_role.role.get_name_display()
        return '-'
    get_primary_role.short_description = 'Role'


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['get_name_display', 'hierarchy_level', 'description']
    list_filter = ['name', 'hierarchy_level']
    search_fields = ['name', 'description']
    ordering = ['hierarchy_level']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'hierarchy_level')
        }),
        ('Permissions', {
            'fields': ('permissions', 'restricted_departments'),
            'classes': ('collapse',)
        }),
    )


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'role', 'assigned_by', 'assigned_at', 'is_active'
    ]
    list_filter = ['role', 'is_active', 'assigned_at']
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    date_hierarchy = 'assigned_at'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'user', 'role', 'assigned_by'
        )


@admin.register(ProcessSupervisor)
class ProcessSupervisorAdmin(admin.ModelAdmin):
    list_display = [
        'supervisor', 'get_department_display', 'get_process_names', 'is_active'
    ]
    list_filter = ['department', 'is_active']
    search_fields = ['supervisor__email', 'supervisor__first_name', 'supervisor__last_name']
    
    def get_department_display(self, obj):
        return obj.get_department_display()
    get_department_display.short_description = 'Department'
    
    def get_process_names(self, obj):
        return ', '.join(obj.process_names[:3]) + ('...' if len(obj.process_names) > 3 else '')
    get_process_names.short_description = 'Processes'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('supervisor')


@admin.register(OperatorEngagement)
class OperatorEngagementAdmin(admin.ModelAdmin):
    list_display = [
        'operator', 'batch_id', 'process_step', 'machine_id', 
        'start_time', 'estimated_end_time', 'get_duration'
    ]
    list_filter = ['process_step', 'start_time']
    search_fields = [
        'operator__email', 'operator__first_name', 'operator__last_name',
        'batch_id', 'machine_id'
    ]
    date_hierarchy = 'start_time'
    readonly_fields = ['start_time']
    
    def get_duration(self, obj):
        if obj.start_time and obj.estimated_end_time:
            duration = obj.estimated_end_time - obj.start_time
            hours = duration.total_seconds() / 3600
            return f"{hours:.1f} hours"
        return '-'
    get_duration.short_description = 'Duration'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('operator')


@admin.register(LoginSession)
class LoginSessionAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'ip_address', 'login_time', 'logout_time', 
        'get_duration', 'is_active_display'
    ]
    list_filter = ['is_active', 'login_time', 'logout_time']
    search_fields = ['user__email', 'ip_address']
    date_hierarchy = 'login_time'
    readonly_fields = ['login_time', 'logout_time']
    
    def get_duration(self, obj):
        if obj.logout_time:
            duration = obj.logout_time - obj.login_time
            hours = duration.total_seconds() / 3600
            return f"{hours:.1f} hours"
        elif obj.is_active:
            from django.utils import timezone
            duration = timezone.now() - obj.login_time
            hours = duration.total_seconds() / 3600
            return f"{hours:.1f} hours (active)"
        return '-'
    get_duration.short_description = 'Duration'
    
    def is_active_display(self, obj):
        if obj.is_active:
            return format_html('<span style="color: green;">●</span> Active')
        else:
            return format_html('<span style="color: red;">●</span> Inactive')
    is_active_display.short_description = 'Status'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


@admin.register(BreakType)
class BreakTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']
    
    def get_queryset(self, request):
        return super().get_queryset(request)


@admin.register(SupervisorBreak)
class SupervisorBreakAdmin(admin.ModelAdmin):
    list_display = ['login_session', 'break_type', 'break_start_time', 'break_end_time', 'get_duration', 'is_active']
    list_filter = ['break_type', 'break_start_time', 'break_end_time']
    search_fields = ['login_session__user__email', 'login_session__user__first_name', 'login_session__user__last_name']
    readonly_fields = ['created_at']
    date_hierarchy = 'break_start_time'
    
    def get_duration(self, obj):
        if obj.break_end_time:
            duration = obj.break_end_time - obj.break_start_time
            minutes = int(duration.total_seconds() / 60)
            return f"{minutes} minutes"
        return "Active"
    get_duration.short_description = 'Duration'
    
    def is_active(self, obj):
        return obj.break_end_time is None
    is_active.boolean = True
    is_active.short_description = 'Active'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('login_session', 'login_session__user', 'break_type')


# Customize admin site
admin.site.site_header = 'MSP-ERP Administration'
admin.site.site_title = 'MSP-ERP Admin'
admin.site.index_title = 'Microsprings Inventory Management System'