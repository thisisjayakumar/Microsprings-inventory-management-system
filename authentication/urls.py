from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from . import views, admin_views

app_name = 'authentication'

# Create router for ViewSets
router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='user')
router.register(r'roles', views.RoleViewSet, basename='role')
router.register(r'process-supervisors', views.ProcessSupervisorViewSet, basename='process-supervisor')
router.register(r'operator-engagements', views.OperatorEngagementViewSet, basename='operator-engagement')
router.register(r'login-sessions', views.LoginSessionViewSet, basename='login-session')

# Admin dashboard router
admin_router = DefaultRouter()
admin_router.register(r'users', admin_views.AdminUserManagementViewSet, basename='admin-users')
admin_router.register(r'roles', admin_views.AdminRoleManagementViewSet, basename='admin-roles')

urlpatterns = [
    # Authentication endpoints
    path('register/', views.register_user, name='register'),
    path('login/', views.CustomTokenObtainPairView.as_view(), name='login'),
    path('logout/', views.logout_user, name='logout'),
    path('break-types/', views.get_break_types, name='get_break_types'),
    path('resume-after-break/', views.resume_after_break, name='resume_after_break'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # User profile endpoints
    path('profile/', views.user_profile, name='user_profile'),
    path('change-password/', views.change_password, name='change_password'),
    path('permissions/', views.user_permissions, name='user_permissions'),
    
    # Bulk operations
    path('bulk/role-assignment/', views.bulk_role_assignment, name='bulk_role_assignment'),
    
    # Dashboard and stats
    path('dashboard/stats/', views.dashboard_stats, name='dashboard_stats'),
    
    # Network restrictions
    path('ip-restrictions/update/', views.update_ip_restrictions, name='update_ip_restrictions'),
    
    # Health check
    path('health/', views.health_check, name='health_check'),
    
    # Admin Dashboard endpoints
    path('admin/', include(admin_router.urls)),
    path('admin/dashboard/stats/', admin_views.admin_dashboard_stats, name='admin_dashboard_stats'),
    path('admin/users/multiple-roles/', admin_views.users_with_multiple_roles, name='users_multiple_roles'),
    path('admin/users/without-roles/', admin_views.users_without_roles, name='users_without_roles'),
    path('admin/sync-profiles/', admin_views.sync_user_profile_status, name='sync_user_profiles'),
    path('admin/department-summary/', admin_views.department_summary, name='department_summary'),
    path('admin/role-permissions-matrix/', admin_views.role_permissions_matrix, name='role_permissions_matrix'),
    
    # Include router URLs
    path('', include(router.urls)),
]

# Additional URL patterns for specific endpoints
urlpatterns += [
    # User management specific endpoints
    path('users/available-operators/', views.UserViewSet.as_view({'get': 'available_operators'}), name='available_operators'),
    path('users/supervisors-by-department/', views.UserViewSet.as_view({'get': 'supervisors_by_department'}), name='supervisors_by_department'),
    path('users/<int:pk>/assign-role/', views.UserViewSet.as_view({'post': 'assign_role'}), name='assign_role'),
    path('users/<int:pk>/engage-operator/', views.UserViewSet.as_view({'post': 'engage_operator'}), name='engage_operator'),
    path('users/<int:pk>/release-operator/', views.UserViewSet.as_view({'post': 'release_operator'}), name='release_operator'),
    
    # Role management
    path('roles/hierarchy/', views.RoleViewSet.as_view({'get': 'hierarchy'}), name='role_hierarchy'),
]