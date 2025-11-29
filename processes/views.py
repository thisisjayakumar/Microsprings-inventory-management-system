from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count, Sum
from datetime import datetime, timedelta
import logging

from .models import (
    Process, WorkCenterMaster, DailySupervisorStatus, SupervisorActivityLog
)
from .serializers import (
    ProcessBasicSerializer,
    WorkCenterMasterListSerializer, WorkCenterMasterDetailSerializer,
    DailySupervisorStatusSerializer, DailySupervisorStatusUpdateSerializer,
    SupervisorActivityLogSerializer
)
from authentication.permissions import IsAdminOrManager, IsManagerOrAbove

logger = logging.getLogger(__name__)


class ProcessViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing processes (read-only)
    """
    permission_classes = [IsAuthenticated]
    queryset = Process.objects.filter(is_active=True).order_by('name')
    serializer_class = ProcessBasicSerializer


class WorkCenterMasterViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Work Center Master management
    Admin, Managers, and Production Heads can CRUD work center masters
    """
    permission_classes = [IsAuthenticated, IsManagerOrAbove]
    queryset = WorkCenterMaster.objects.all().select_related(
        'work_center', 'default_supervisor', 'backup_supervisor',
        'created_by', 'updated_by'
    )
    
    def get_serializer_class(self):
        if self.action in ['list']:
            return WorkCenterMasterListSerializer
        return WorkCenterMasterDetailSerializer
    
    @action(detail=False, methods=['get'])
    def available_work_centers(self, request):
        """Get list of processes that don't have work center master yet"""
        existing_wc_ids = WorkCenterMaster.objects.values_list('work_center_id', flat=True)
        available_processes = Process.objects.filter(
            is_active=True
        ).exclude(
            id__in=existing_wc_ids
        )
        serializer = ProcessBasicSerializer(available_processes, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def supervisors(self, request):
        """Get list of users with supervisor role"""
        from authentication.models import CustomUser, UserRole
        supervisors = CustomUser.objects.filter(
            user_roles__role__name='supervisor',
            user_roles__is_active=True,
            is_active=True
        ).distinct()
        
        from manufacturing.serializers import UserBasicSerializer
        serializer = UserBasicSerializer(supervisors, many=True)
        return Response(serializer.data)


class DailySupervisorStatusViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Daily Supervisor Status
    Read-only with manual update capability for admins
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DailySupervisorStatusSerializer
    
    def get_queryset(self):
        queryset = DailySupervisorStatus.objects.all().select_related(
            'work_center', 'default_supervisor', 'active_supervisor',
            'manually_updated_by'
        )
        
        # Filter by date if provided
        date_param = self.request.query_params.get('date')
        if date_param:
            try:
                filter_date = datetime.strptime(date_param, '%Y-%m-%d').date()
                queryset = queryset.filter(date=filter_date)
            except ValueError:
                pass
        else:
            # Default to today
            queryset = queryset.filter(date=timezone.now().date())
        
        # Filter by work center if provided
        work_center_id = self.request.query_params.get('work_center_id')
        if work_center_id:
            queryset = queryset.filter(work_center_id=work_center_id)
        
        return queryset.order_by('work_center__name')
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsManagerOrAbove])
    def manual_update(self, request, pk=None):
        """
        Manually update active supervisor for a work center
        Only admins, managers, and production heads can do this
        """
        status = self.get_object()
        serializer = DailySupervisorStatusUpdateSerializer(
            status,
            data=request.data,
            context={'request': request}
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': 'Supervisor updated successfully',
                'status': DailySupervisorStatusSerializer(status).data
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def today_dashboard(self, request):
        """
        Get today's supervisor status dashboard
        Returns summary with color-coded status
        """
        today = timezone.now().date()
        statuses = DailySupervisorStatus.objects.filter(
            date=today
        ).select_related(
            'work_center', 'default_supervisor', 'active_supervisor'
        ).order_by('work_center__name')
        
        # Count present vs backup
        present_count = statuses.filter(is_present=True).count()
        backup_count = statuses.filter(is_present=False).count()
        
        serializer = self.get_serializer(statuses, many=True)
        
        return Response({
            'date': today,
            'total_work_centers': statuses.count(),
            'default_supervisors_present': present_count,
            'backup_supervisors_active': backup_count,
            'statuses': serializer.data
        })
    
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsManagerOrAbove])
    def run_attendance_check(self, request):
        """
        Manually trigger the attendance check command
        Useful for testing or manual runs
        """
        from django.core.management import call_command
        from io import StringIO
        
        date_param = request.data.get('date')  # Optional: YYYY-MM-DD
        force = request.data.get('force', False)
        
        # Capture command output
        out = StringIO()
        
        try:
            if date_param:
                call_command('check_supervisor_attendance', f'--date={date_param}', stdout=out)
            else:
                call_command('check_supervisor_attendance', stdout=out)
            
            output = out.getvalue()
            
            return Response({
                'message': 'Attendance check completed successfully',
                'output': output
            })
        except Exception as e:
            logger.error(f'Error running attendance check: {str(e)}', exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SupervisorActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Supervisor Activity Logs
    Read-only view of supervisor activities
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SupervisorActivityLogSerializer
    
    def get_queryset(self):
        queryset = SupervisorActivityLog.objects.all().select_related(
            'work_center', 'active_supervisor'
        )
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                queryset = queryset.filter(date__gte=start)
            except ValueError:
                pass
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                queryset = queryset.filter(date__lte=end)
            except ValueError:
                pass
        
        # Filter by work center if provided
        work_center_id = self.request.query_params.get('work_center_id')
        if work_center_id:
            queryset = queryset.filter(work_center_id=work_center_id)
        
        # Filter by supervisor if provided
        supervisor_id = self.request.query_params.get('supervisor_id')
        if supervisor_id:
            queryset = queryset.filter(active_supervisor_id=supervisor_id)
        
        return queryset.order_by('-date', 'work_center__name')
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Get summary statistics for supervisor activities
        """
        # Get date range
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=30)  # Last 30 days
        
        # Allow custom date range
        start_param = request.query_params.get('start_date')
        end_param = request.query_params.get('end_date')
        
        if start_param:
            try:
                start_date = datetime.strptime(start_param, '%Y-%m-%d').date()
            except ValueError:
                pass
        
        if end_param:
            try:
                end_date = datetime.strptime(end_param, '%Y-%m-%d').date()
            except ValueError:
                pass
        
        logs = SupervisorActivityLog.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        )
        
        # Aggregate by supervisor
        supervisor_summary = logs.values(
            'active_supervisor__id',
            'active_supervisor__first_name',
            'active_supervisor__last_name'
        ).annotate(
            total_days=Count('id'),
            total_mos=Sum('mos_handled'),
            total_operations=Sum('total_operations'),
            total_completed=Sum('operations_completed'),
            total_time=Sum('total_processing_time_minutes')
        )
        
        # Aggregate by work center
        work_center_summary = logs.values(
            'work_center__id',
            'work_center__name'
        ).annotate(
            total_mos=Sum('mos_handled'),
            total_operations=Sum('total_operations'),
            total_completed=Sum('operations_completed')
        )
        
        return Response({
            'date_range': {
                'start': start_date,
                'end': end_date
            },
            'supervisor_summary': list(supervisor_summary),
            'work_center_summary': list(work_center_summary)
        })
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's activity logs"""
        today = timezone.now().date()
        logs = self.get_queryset().filter(date=today)
        serializer = self.get_serializer(logs, many=True)
        
        return Response({
            'date': today,
            'logs': serializer.data
        })
