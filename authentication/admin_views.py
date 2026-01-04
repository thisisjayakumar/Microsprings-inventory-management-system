"""
Admin Dashboard Views
Comprehensive user and role management for administrators
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Count, Prefetch
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models.deletion import ProtectedError
from django.core.management import call_command
from django.conf import settings
import logging
import threading
import json
from pathlib import Path
from datetime import datetime

from .models import CustomUser, UserProfile, Role, UserRole, LoginSession
from .admin_serializers import (
    AdminUserListSerializer, AdminUserCreateUpdateSerializer,
    RoleCreateUpdateSerializer, AdminDashboardStatsSerializer,
    BulkUserActionSerializer, UserRoleManagementSerializer, RoleSerializer
)
from utils.enums import DepartmentChoices
from .permissions import IsAdminOrManager

logger = logging.getLogger(__name__)


class AdminUserManagementViewSet(viewsets.ModelViewSet):
    """
    Comprehensive user management for admin dashboard
    All CRUD operations in one place
    """
    permission_classes = [IsAdminOrManager]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'profile__department', 'profile__shift', 'profile__is_engaged']
    search_fields = ['first_name', 'last_name', 'email', 'profile__employee_id', 'username']
    ordering_fields = ['date_joined', 'first_name', 'last_name', 'email']
    ordering = ['-date_joined']
    
    def get_queryset(self):
        """Optimized queryset with all relations"""
        return CustomUser.objects.select_related(
            'profile'
        ).prefetch_related(
            Prefetch(
                'user_roles',
                queryset=UserRole.objects.filter(is_active=True).select_related('role', 'assigned_by')
            )
        ).annotate(
            role_count=Count('user_roles', filter=Q(user_roles__is_active=True))
        )
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return AdminUserListSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return AdminUserCreateUpdateSerializer
        return AdminUserListSerializer
    
    def perform_create(self, serializer):
        """Create user with audit trail"""
        serializer.save()
    
    def perform_update(self, serializer):
        """Update user with audit trail"""
        serializer.save()
    
    def destroy(self, request, *args, **kwargs):
        """
        Delete user with proper error handling for protected foreign keys
        """
        user = self.get_object()
        
        try:
            user_name = user.get_full_name() or user.username
            user.delete()
            return Response({
                'message': f'User {user_name} has been deleted successfully'
            }, status=status.HTTP_204_NO_CONTENT)
        
        except ProtectedError as e:
            # Parse the protected error to extract model information
            protected_objects = e.protected_objects
            model_names = set()
            
            for obj in protected_objects:
                model_name = obj._meta.verbose_name_plural or obj._meta.model_name
                model_names.add(model_name)
            
            models_list = ', '.join(model_names)
            
            return Response({
                'error': f'Cannot delete user {user_name}',
                'detail': f'This user is referenced in the following records: {models_list}',
                'suggestion': 'Consider deactivating the user instead of deleting them.',
                'action': 'deactivate',
                'protected_models': list(model_names)
            }, status=status.HTTP_400_BAD_REQUEST)
        
        except Exception as e:
            return Response({
                'error': 'Failed to delete user',
                'detail': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'])
    def bulk_action(self, request):
        """
        Perform bulk actions on multiple users
        """
        serializer = BulkUserActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        user_ids = serializer.validated_data['user_ids']
        action_type = serializer.validated_data['action']
        
        try:
            with transaction.atomic():
                users = CustomUser.objects.filter(id__in=user_ids)
                
                if action_type == 'activate':
                    users.update(is_active=True)
                    UserProfile.objects.filter(user__in=users).update(is_active=True)
                    message = f'Activated {users.count()} users'
                
                elif action_type == 'deactivate':
                    users.update(is_active=False)
                    UserProfile.objects.filter(user__in=users).update(is_active=False)
                    message = f'Deactivated {users.count()} users'
                
                elif action_type == 'assign_role':
                    role_id = serializer.validated_data['role_id']
                    role = Role.objects.get(id=role_id)
                    
                    # Deactivate existing roles
                    UserRole.objects.filter(
                        user__in=users,
                        is_active=True
                    ).update(is_active=False)
                    
                    # Create new role assignments
                    role_assignments = [
                        UserRole(user=user, role=role, assigned_by=request.user)
                        for user in users
                    ]
                    UserRole.objects.bulk_create(role_assignments)
                    message = f'Assigned role {role.get_name_display()} to {users.count()} users'
                
                elif action_type == 'change_department':
                    department = serializer.validated_data['department']
                    UserProfile.objects.filter(user__in=users).update(department=department)
                    dept_display = dict(DepartmentChoices.choices).get(department, department)
                    message = f'Changed department to {dept_display} for {users.count()} users'
                
                return Response({
                    'message': message,
                    'affected_users': users.count()
                })
        
        except Role.DoesNotExist:
            return Response({'error': 'Role not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def manage_roles(self, request, pk=None):
        """
        Manage roles for a specific user
        """
        user = self.get_object()
        serializer = UserRoleManagementSerializer(data={
            'user_id': user.id,
            **request.data
        })
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        role_ids = serializer.validated_data['role_ids']
        replace_existing = serializer.validated_data['replace_existing']
        
        try:
            with transaction.atomic():
                if replace_existing:
                    # Deactivate all existing roles
                    UserRole.objects.filter(user=user, is_active=True).update(is_active=False)
                
                # Assign new roles
                roles = Role.objects.filter(id__in=role_ids)
                for role in roles:
                    UserRole.objects.update_or_create(
                        user=user,
                        role=role,
                        defaults={
                            'is_active': True,
                            'assigned_by': request.user
                        }
                    )
                
                # Return updated user
                user_serializer = AdminUserListSerializer(user)
                return Response({
                    'message': f'Roles updated successfully for {user.full_name}',
                    'user': user_serializer.data
                })
        
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        """
        Reset user password (admin only)
        """
        user = self.get_object()
        new_password = request.data.get('new_password')
        
        if not new_password:
            return Response({'error': 'new_password is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Validate password
            validate_password(new_password, user)
            
            # Set new password
            user.set_password(new_password)
            user.save()
            
            return Response({'message': f'Password reset successfully for {user.full_name}'})
        
        except DjangoValidationError as e:
            return Response({'error': e.messages}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """
        Toggle user active status
        """
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()
        
        # Also update profile
        if hasattr(user, 'profile'):
            user.profile.is_active = user.is_active
            user.profile.save()
        
        status_text = 'activated' if user.is_active else 'deactivated'
        return Response({
            'message': f'User {user.full_name} has been {status_text}',
            'is_active': user.is_active
        })


class AdminRoleManagementViewSet(viewsets.ModelViewSet):
    """
    Full CRUD operations for roles
    """
    queryset = Role.objects.all().order_by('hierarchy_level')
    serializer_class = RoleCreateUpdateSerializer
    permission_classes = [IsAdminOrManager]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['hierarchy_level', 'name']
    ordering = ['hierarchy_level']
    
    def destroy(self, request, *args, **kwargs):
        """Prevent deletion of roles with assigned users"""
        role = self.get_object()
        
        # Check if role has active assignments
        active_assignments = UserRole.objects.filter(role=role, is_active=True).count()
        if active_assignments > 0:
            return Response({
                'error': f'Cannot delete role with {active_assignments} active user assignments. Deactivate users first.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=True, methods=['get'])
    def users(self, request, pk=None):
        """
        Get all users assigned to this role
        """
        role = self.get_object()
        users = CustomUser.objects.filter(
            user_roles__role=role,
            user_roles__is_active=True
        ).select_related('profile').prefetch_related('user_roles')
        
        serializer = AdminUserListSerializer(users, many=True)
        return Response({
            'role': RoleSerializer(role).data,
            'user_count': users.count(),
            'users': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def hierarchy(self, request):
        """
        Get role hierarchy with user counts
        """
        roles = self.get_queryset().annotate(
            active_users=Count('role_users', filter=Q(role_users__is_active=True))
        )
        
        data = [
            {
                'id': role.id,
                'name': role.name,
                'name_display': role.get_name_display(),
                'hierarchy_level': role.hierarchy_level,
                'active_users': role.active_users,
                'description': role.description
            }
            for role in roles
        ]
        
        return Response(data)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def admin_dashboard_stats(request):
    """
    Get comprehensive statistics for admin dashboard
    """
    # User statistics
    total_users = CustomUser.objects.count()
    active_users = CustomUser.objects.filter(is_active=True).count()
    inactive_users = total_users - active_users
    engaged_users = CustomUser.objects.filter(profile__is_engaged=True).count()
    available_users = CustomUser.objects.filter(
        profile__is_engaged=False,
        is_active=True
    ).count()
    
    # Users by role
    users_by_role = {}
    for role in Role.objects.all():
        count = UserRole.objects.filter(role=role, is_active=True).count()
        users_by_role[role.get_name_display()] = count
    
    # Users by department
    users_by_department = {}
    for dept_code, dept_name in DepartmentChoices.choices:
        count = UserProfile.objects.filter(department=dept_code, is_active=True).count()
        users_by_department[dept_name] = count
    
    # Role statistics
    total_roles = Role.objects.count()
    
    # Session statistics
    active_sessions = LoginSession.objects.filter(is_active=True).count()
    
    stats = {
        'total_users': total_users,
        'active_users': active_users,
        'inactive_users': inactive_users,
        'users_by_role': users_by_role,
        'users_by_department': users_by_department,
        'engaged_users': engaged_users,
        'available_users': available_users,
        'total_roles': total_roles,
        'active_sessions': active_sessions
    }
    
    serializer = AdminDashboardStatsSerializer(stats)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def users_with_multiple_roles(request):
    """
    Get users with multiple active roles
    """
    users = CustomUser.objects.annotate(
        role_count=Count('user_roles', filter=Q(user_roles__is_active=True))
    ).filter(role_count__gt=1).select_related('profile').prefetch_related('user_roles')
    
    serializer = AdminUserListSerializer(users, many=True)
    return Response({
        'count': users.count(),
        'users': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def users_without_roles(request):
    """
    Get users without any active roles
    """
    users = CustomUser.objects.annotate(
        role_count=Count('user_roles', filter=Q(user_roles__is_active=True))
    ).filter(role_count=0).select_related('profile')
    
    serializer = AdminUserListSerializer(users, many=True)
    return Response({
        'count': users.count(),
        'users': serializer.data
    })


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
def sync_user_profile_status(request):
    """
    Sync user.is_active with profile.is_active for all users
    """
    try:
        with transaction.atomic():
            # Update profiles to match user is_active status
            updated = 0
            for user in CustomUser.objects.select_related('profile'):
                if hasattr(user, 'profile'):
                    if user.profile.is_active != user.is_active:
                        user.profile.is_active = user.is_active
                        user.profile.save()
                        updated += 1
            
            return Response({
                'message': f'Synced {updated} user profiles',
                'updated_count': updated
            })
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def department_summary(request):
    """
    Get summary of users by department with role breakdown
    """
    department_data = []
    
    for dept_code, dept_name in DepartmentChoices.choices:
        users = CustomUser.objects.filter(
            profile__department=dept_code,
            is_active=True
        ).prefetch_related('user_roles__role')
        
        role_breakdown = {}
        for user in users:
            for user_role in user.user_roles.filter(is_active=True):
                role_name = user_role.role.get_name_display()
                role_breakdown[role_name] = role_breakdown.get(role_name, 0) + 1
        
        department_data.append({
            'department': dept_name,
            'department_code': dept_code,
            'total_users': users.count(),
            'engaged_users': users.filter(profile__is_engaged=True).count(),
            'available_users': users.filter(profile__is_engaged=False).count(),
            'role_breakdown': role_breakdown
        })
    
    return Response(department_data)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def role_permissions_matrix(request):
    """
    Get a matrix of all roles and their permissions
    """
    roles = Role.objects.all().order_by('hierarchy_level')
    
    matrix = [
        {
            'id': role.id,
            'name': role.get_name_display(),
            'hierarchy_level': role.hierarchy_level,
            'permissions': role.permissions or {},
            'restricted_departments': role.restricted_departments or [],
            'active_users': UserRole.objects.filter(role=role, is_active=True).count()
        }
        for role in roles
    ]
    
    return Response(matrix)


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
def reset_database(request):
    """
    Reset/truncate the entire database
    WARNING: This will delete ALL data!
    Only allowed in DEBUG mode or with explicit force flag.
    """
    # Safety check: Only allow in DEBUG mode
    if not settings.DEBUG:
        return Response(
            {
                'error': 'Database reset is only allowed in DEBUG mode!',
                'message': 'This endpoint is disabled in production for safety.'
            },
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Additional confirmation check
    confirm_text = request.data.get('confirm_text', '')
    if confirm_text != 'RESET DATABASE':
        return Response(
            {
                'error': 'Invalid confirmation text',
                'message': 'You must provide confirm_text="RESET DATABASE" to proceed.'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get options from request
        load_fixtures = request.data.get('load_fixtures', False)
        skip_migrations = request.data.get('skip_migrations', False)
        
        # Call the management command
        from io import StringIO
        from django.core.management import call_command
        
        output = StringIO()
        command_kwargs = {
            'force': True,  # Skip confirmation (we already checked)
            'stdout': output,
            'stderr': output
        }
        if load_fixtures:
            command_kwargs['load_fixtures'] = True
        if skip_migrations:
            command_kwargs['skip_migrations'] = True
        
        call_command('reset_database', **command_kwargs)
        
        output_text = output.getvalue()
        
        logger.warning(f'Database reset performed by user: {request.user.username} ({request.user.id})')
        
        return Response({
            'success': True,
            'message': 'Database reset completed successfully',
            'output': output_text
        })
        
    except Exception as e:
        logger.error(f'Database reset failed: {str(e)}', exc_info=True)
        return Response(
            {
                'error': 'Database reset failed',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
def backup_database(request):
    """
    Start a database backup process (async)
    Returns immediately with acknowledgement
    """
    # Safety check: Only allow in DEBUG mode
    if not settings.DEBUG:
        return Response(
            {
                'error': 'Database backup is only allowed in DEBUG mode!',
                'message': 'This endpoint is disabled in production for safety.'
            },
            status=status.HTTP_403_FORBIDDEN
        )
    
    try:
        backup_format = request.data.get('format', 'sql')  # 'sql' or 'json'
        
        # Get backup directory
        backup_dir = Path(settings.BASE_DIR) / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Create status file
        status_file = backup_dir / 'backup_status.json'
        status = {
            'status': 'in_progress',
            'started_at': datetime.now().isoformat(),
            'format': backup_format,
            'initiated_by': request.user.username,
            'backup_filename': None,
            'backup_path': None,
            'file_size_mb': None,
            'error': None
        }
        
        with open(status_file, 'w') as f:
            json.dump(status, f, indent=2)
        
        # Run backup in background thread
        def run_backup():
            try:
                from io import StringIO
                output = StringIO()
                
                # Call command with proper argument names
                # Use dictionary unpacking to pass 'format' (Python keyword) as argument
                cmd_kwargs = {
                    'format': backup_format,  # This is the --format argument
                    'output_dir': str(backup_dir),
                    'stdout': output,
                    'stderr': output,
                    'verbosity': 1
                }
                call_command('backup_database', **cmd_kwargs)
                
                # Read metadata from the backup command output
                # The command creates a .meta.json file
                # Find the most recent backup metadata
                backup_files = sorted(
                    backup_dir.glob(f'*.meta.json'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                
                # Find the most recent backup file (not just metadata)
                # Look for .sql files created in the last minute
                recent_backup_files = sorted(
                    backup_dir.glob('*.sql'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                
                if recent_backup_files:
                    # Get the most recent backup file
                    latest_backup = recent_backup_files[0]
                    meta_file = backup_dir / f'{latest_backup.name}.meta.json'
                    
                    if meta_file.exists():
                        with open(meta_file, 'r') as f:
                            metadata = json.load(f)
                            status['status'] = 'completed'
                            status['backup_filename'] = metadata.get('backup_filename')
                            status['backup_path'] = metadata.get('backup_path')
                            status['file_size_mb'] = metadata.get('file_size_mb')
                            status['completed_at'] = datetime.now().isoformat()
                    else:
                        # No metadata, create from file stats
                        stat = latest_backup.stat()
                        status['status'] = 'completed'
                        status['backup_filename'] = latest_backup.name
                        status['backup_path'] = str(latest_backup)
                        status['file_size_mb'] = round(stat.st_size / (1024 * 1024), 2)
                        status['completed_at'] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                else:
                    # No backup files found
                    status['status'] = 'completed'
                    status['completed_at'] = datetime.now().isoformat()
                    status['error'] = 'Backup completed but file not found'
                    
            except Exception as e:
                logger.error(f'Backup failed: {str(e)}', exc_info=True)
                status['status'] = 'failed'
                status['error'] = str(e)
                status['completed_at'] = datetime.now().isoformat()
            finally:
                # Update status file
                with open(status_file, 'w') as f:
                    json.dump(status, f, indent=2)
        
        # Start background thread
        thread = threading.Thread(target=run_backup, daemon=True)
        thread.start()
        
        logger.info(f'Database backup initiated by user: {request.user.username} ({request.user.id})')
        
        return Response({
            'success': True,
            'message': 'Backup process started',
            'status': 'in_progress',
            'format': backup_format
        })
        
    except Exception as e:
        logger.error(f'Backup initiation failed: {str(e)}', exc_info=True)
        return Response(
            {
                'error': 'Failed to start backup',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def backup_status(request):
    """
    Get current backup status
    """
    try:
        backup_dir = Path(settings.BASE_DIR) / 'backups'
        status_file = backup_dir / 'backup_status.json'
        
        if not status_file.exists():
            return Response({
                'status': 'no_backup',
                'message': 'No backup has been created yet'
            })
        
        with open(status_file, 'r') as f:
            status = json.load(f)
        
        return Response(status)
        
    except Exception as e:
        logger.error(f'Failed to get backup status: {str(e)}', exc_info=True)
        return Response(
            {
                'error': 'Failed to get backup status',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
def restore_database(request):
    """
    Restore database from backup
    """
    # Safety check: Only allow in DEBUG mode
    if not settings.DEBUG:
        return Response(
            {
                'error': 'Database restore is only allowed in DEBUG mode!',
                'message': 'This endpoint is disabled in production for safety.'
            },
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Additional confirmation check
    confirm_text = request.data.get('confirm_text', '')
    if confirm_text != 'RESTORE DATABASE':
        return Response(
            {
                'error': 'Invalid confirmation text',
                'message': 'You must provide confirm_text="RESTORE DATABASE" to proceed.'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        backup_path = request.data.get('backup_path')
        if not backup_path:
            return Response(
                {
                    'error': 'Missing backup_path',
                    'message': 'You must provide the backup file path.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        backup_file = Path(backup_path)
        if not backup_file.exists():
            return Response(
                {
                    'error': 'Backup file not found',
                    'message': f'Backup file does not exist: {backup_path}'
                },
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Call the management command
        from io import StringIO
        output = StringIO()
        
        call_command(
            'restore_database',
            str(backup_file),
            force=True,  # Skip confirmation (we already checked)
            stdout=output,
            stderr=output
        )
        
        output_text = output.getvalue()
        
        logger.warning(f'Database restore performed by user: {request.user.username} ({request.user.id}) from: {backup_path}')
        
        return Response({
            'success': True,
            'message': 'Database restore completed successfully',
            'output': output_text
        })
        
    except Exception as e:
        logger.error(f'Database restore failed: {str(e)}', exc_info=True)
        return Response(
            {
                'error': 'Database restore failed',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def list_backups(request):
    """
    List all available backups
    """
    try:
        backup_dir = Path(settings.BASE_DIR) / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        backups = []
        
        # Find all SQL backup files (regardless of naming pattern)
        sql_files = list(backup_dir.glob('*.sql'))
        logger.info(f'Found {len(sql_files)} SQL files in {backup_dir}')
        
        for backup_file in sql_files:
            # Skip if it's a temporary file or very small (likely incomplete)
            if backup_file.stat().st_size < 1000:  # Less than 1KB, probably incomplete
                logger.warning(f'Skipping small file (likely incomplete): {backup_file.name}')
                continue
                
            meta_file = backup_dir / f'{backup_file.name}.meta.json'
            if meta_file.exists():
                # Use metadata if available
                try:
                    with open(meta_file, 'r') as f:
                        metadata = json.load(f)
                        backups.append(metadata)
                        logger.debug(f'Added backup from metadata: {backup_file.name}')
                except Exception as e:
                    logger.warning(f'Failed to read metadata for {backup_file.name}: {e}')
                    # Fallback: create metadata from file stats
                    stat = backup_file.stat()
                    backups.append({
                        'backup_filename': backup_file.name,
                        'backup_path': str(backup_file),
                        'format': 'sql',
                        'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'file_size_bytes': stat.st_size,
                        'file_size_mb': round(stat.st_size / (1024 * 1024), 2),
                    })
            else:
                # No metadata file, create entry from file stats
                stat = backup_file.stat()
                backups.append({
                    'backup_filename': backup_file.name,
                    'backup_path': str(backup_file),
                    'format': 'sql',
                    'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    'file_size_bytes': stat.st_size,
                    'file_size_mb': round(stat.st_size / (1024 * 1024), 2),
                })
                logger.debug(f'Added backup from file stats: {backup_file.name}')
        
        # Find all JSON fixture backups (exclude metadata files and status files)
        for backup_file in backup_dir.glob('*.json'):
            # Skip metadata files and status files
            if backup_file.name.endswith('.meta.json'):
                continue
            if backup_file.name == 'backup_status.json':
                continue
            
            # JSON fixture backup
            stat = backup_file.stat()
            backups.append({
                'backup_filename': backup_file.name,
                'backup_path': str(backup_file),
                'format': 'json',
                'created_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'file_size_bytes': stat.st_size,
                'file_size_mb': round(stat.st_size / (1024 * 1024), 2),
            })
        
        # Sort by creation date (newest first)
        backups.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        logger.info(f'Found {len(backups)} backup files in {backup_dir}')
        
        return Response({
            'count': len(backups),
            'backups': backups
        })
        
    except Exception as e:
        logger.error(f'Failed to list backups: {str(e)}', exc_info=True)
        return Response(
            {
                'error': 'Failed to list backups',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

