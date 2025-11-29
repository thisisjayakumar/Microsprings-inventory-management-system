"""
Supervisor Configuration API Views
Handles supervisor shift management, MO-specific overrides, and reporting
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count, Sum, F
from django.db import transaction
from datetime import datetime, timedelta

from manufacturing.models import (
    MOShiftConfiguration,
    MOSupervisorOverride,
    SupervisorChangeLog,
    MOProcessExecution
)
from processes.models import (
    WorkCenterSupervisorShift,
    DailySupervisorStatus,
    SupervisorActivityLog,
    Process
)
from manufacturing.serializers import (
    WorkCenterSupervisorShiftSerializer,
    WorkCenterSupervisorShiftCreateSerializer,
    DailySupervisorStatusSerializer,
    MOShiftConfigurationSerializer,
    MOShiftConfigurationCreateSerializer,
    MOSupervisorOverrideSerializer,
    MOSupervisorOverrideCreateSerializer,
    SupervisorChangeLogSerializer,
    SupervisorAssignmentReportSerializer
)


class WorkCenterSupervisorShiftViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing work center supervisor shift assignments (global defaults)
    PH sets primary + backup supervisors for each process for each shift
    """
    queryset = WorkCenterSupervisorShift.objects.all()
    serializer_class = WorkCenterSupervisorShiftSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'work_center', 'primary_supervisor', 'backup_supervisor',
            'created_by', 'updated_by'
        )
        
        # Filter by work center
        work_center_id = self.request.query_params.get('work_center_id')
        if work_center_id:
            queryset = queryset.filter(work_center_id=work_center_id)
        
        # Filter by shift
        shift = self.request.query_params.get('shift')
        if shift:
            queryset = queryset.filter(shift=shift)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset.order_by('work_center__name', 'shift')
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return WorkCenterSupervisorShiftCreateSerializer
        return WorkCenterSupervisorShiftSerializer
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
    
    @action(detail=False, methods=['get'])
    def by_process(self, request):
        """Get all shift configurations for a specific process"""
        work_center_id = request.query_params.get('work_center_id')
        if not work_center_id:
            return Response(
                {'error': 'work_center_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        configs = self.get_queryset().filter(work_center_id=work_center_id)
        serializer = self.get_serializer(configs, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get summary of all work center shift configurations"""
        work_centers = Process.objects.filter(is_active=True)
        summary = []
        
        for wc in work_centers:
            shifts = WorkCenterSupervisorShift.objects.filter(
                work_center=wc,
                is_active=True
            ).select_related('primary_supervisor', 'backup_supervisor')
            
            shift_data = []
            for shift_config in shifts:
                shift_data.append({
                    'id': shift_config.id,
                    'shift': shift_config.shift,
                    'shift_display': shift_config.get_shift_display(),
                    'shift_start_time': shift_config.shift_start_time,
                    'shift_end_time': shift_config.shift_end_time,
                    'primary_supervisor': {
                        'id': shift_config.primary_supervisor.id,
                        'name': shift_config.primary_supervisor.get_full_name()
                    },
                    'backup_supervisor': {
                        'id': shift_config.backup_supervisor.id,
                        'name': shift_config.backup_supervisor.get_full_name()
                    }
                })
            
            summary.append({
                'work_center_id': wc.id,
                'work_center_name': wc.name,
                'shifts': shift_data
            })
        
        return Response(summary)


class DailySupervisorStatusViewSet(viewsets.ModelViewSet):
    """
    ViewSet for daily supervisor status (attendance-based assignments)
    """
    queryset = DailySupervisorStatus.objects.all()
    serializer_class = DailySupervisorStatusSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'work_center', 'default_supervisor', 'active_supervisor',
            'manually_updated_by'
        )
        
        # Filter by date
        date = self.request.query_params.get('date')
        if date:
            queryset = queryset.filter(date=date)
        else:
            # Default to today
            queryset = queryset.filter(date=timezone.now().date())
        
        # Filter by work center
        work_center_id = self.request.query_params.get('work_center_id')
        if work_center_id:
            queryset = queryset.filter(work_center_id=work_center_id)
        
        # Filter by shift
        shift = self.request.query_params.get('shift')
        if shift:
            queryset = queryset.filter(shift=shift)
        
        return queryset.order_by('work_center__name', 'shift')
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's supervisor status for all work centers"""
        today = timezone.now().date()
        statuses = self.get_queryset().filter(date=today)
        serializer = self.get_serializer(statuses, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def manual_override(self, request, pk=None):
        """
        Manually override active supervisor (e.g., mid-shift change by PH)
        """
        daily_status = self.get_object()
        
        new_supervisor_id = request.data.get('new_supervisor_id')
        reason = request.data.get('reason', '')
        
        if not new_supervisor_id:
            return Response(
                {'error': 'new_supervisor_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        try:
            new_supervisor = User.objects.get(id=new_supervisor_id)
        except User.DoesNotExist:
            return Response(
                {'error': 'Supervisor not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        with transaction.atomic():
            old_supervisor = daily_status.active_supervisor
            daily_status.active_supervisor = new_supervisor
            daily_status.manually_updated = True
            daily_status.manually_updated_by = request.user
            daily_status.manually_updated_at = timezone.now()
            daily_status.manual_update_reason = reason
            daily_status.save()
            
            # Update all in-progress process executions for this work center today
            process_executions = MOProcessExecution.objects.filter(
                process=daily_status.work_center,
                actual_start_time__date=daily_status.date,
                status='in_progress'
            )
            
            for pe in process_executions:
                pe.assign_supervisor_manually(
                    new_supervisor=new_supervisor,
                    changed_by_user=request.user,
                    notes=f'Manual override from daily status: {reason}'
                )
        
        return Response({
            'message': 'Supervisor manually overridden',
            'old_supervisor': old_supervisor.get_full_name(),
            'new_supervisor': new_supervisor.get_full_name(),
            'updated_process_executions': process_executions.count()
        })


class MOSupervisorConfigViewSet(viewsets.ViewSet):
    """
    ViewSet for MO-specific supervisor configurations
    Combines shift configuration and supervisor overrides
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def for_mo(self, request):
        """Get complete supervisor configuration for an MO"""
        mo_id = request.query_params.get('mo_id')
        if not mo_id:
            return Response(
                {'error': 'mo_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from manufacturing.models import ManufacturingOrder
        try:
            mo = ManufacturingOrder.objects.get(id=mo_id)
        except ManufacturingOrder.DoesNotExist:
            return Response(
                {'error': 'MO not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get shift configurations
        shift_configs = MOShiftConfiguration.objects.filter(
            mo=mo,
            is_active=True
        )
        
        # Get supervisor overrides
        supervisor_overrides = MOSupervisorOverride.objects.filter(
            mo=mo,
            is_active=True
        ).select_related('process', 'primary_supervisor', 'backup_supervisor')
        
        return Response({
            'mo_id': mo.mo_id,
            'shift_configurations': MOShiftConfigurationSerializer(shift_configs, many=True).data,
            'supervisor_overrides': MOSupervisorOverrideSerializer(supervisor_overrides, many=True).data
        })
    
    @action(detail=False, methods=['post'])
    def add_shift_to_mo(self, request):
        """Add a shift configuration to an MO"""
        serializer = MOShiftConfigurationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        shift_config = serializer.save(created_by=request.user)
        
        return Response(
            MOShiftConfigurationSerializer(shift_config).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['post'])
    def add_supervisor_override(self, request):
        """Add a supervisor override for MO + process + shift"""
        serializer = MOSupervisorOverrideCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        override = serializer.save(created_by=request.user)
        
        return Response(
            MOSupervisorOverrideSerializer(override).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['put'])
    def update_supervisor_override(self, request):
        """Update an existing supervisor override"""
        override_id = request.data.get('override_id')
        if not override_id:
            return Response(
                {'error': 'override_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            override = MOSupervisorOverride.objects.get(id=override_id)
        except MOSupervisorOverride.DoesNotExist:
            return Response(
                {'error': 'Override not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = MOSupervisorOverrideCreateSerializer(
            override,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        
        return Response(MOSupervisorOverrideSerializer(override).data)
    
    @action(detail=False, methods=['delete'])
    def remove_supervisor_override(self, request):
        """Remove a supervisor override"""
        override_id = request.query_params.get('override_id')
        if not override_id:
            return Response(
                {'error': 'override_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            override = MOSupervisorOverride.objects.get(id=override_id)
            override.is_active = False
            override.save()
            return Response({'message': 'Override removed successfully'})
        except MOSupervisorOverride.DoesNotExist:
            return Response(
                {'error': 'Override not found'},
                status=status.HTTP_404_NOT_FOUND
            )


class SupervisorChangeLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for supervisor change logs (read-only)
    Provides audit trail of all supervisor changes
    """
    queryset = SupervisorChangeLog.objects.all()
    serializer_class = SupervisorChangeLogSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'mo_process_execution__mo',
            'mo_process_execution__process',
            'from_supervisor',
            'to_supervisor',
            'changed_by'
        )
        
        # Filter by MO
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo_process_execution__mo_id=mo_id)
        
        # Filter by process
        process_id = self.request.query_params.get('process_id')
        if process_id:
            queryset = queryset.filter(mo_process_execution__process_id=process_id)
        
        # Filter by supervisor
        supervisor_id = self.request.query_params.get('supervisor_id')
        if supervisor_id:
            queryset = queryset.filter(
                Q(from_supervisor_id=supervisor_id) | Q(to_supervisor_id=supervisor_id)
            )
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(changed_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(changed_at__lte=end_date)
        
        return queryset.order_by('-changed_at')
    
    @action(detail=False, methods=['get'])
    def for_mo(self, request):
        """Get all supervisor changes for a specific MO"""
        mo_id = request.query_params.get('mo_id')
        if not mo_id:
            return Response(
                {'error': 'mo_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        changes = self.get_queryset().filter(mo_process_execution__mo__mo_id=mo_id)
        serializer = self.get_serializer(changes, many=True)
        return Response(serializer.data)


class SupervisorReportViewSet(viewsets.ViewSet):
    """
    ViewSet for supervisor assignment reports and analytics
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def assignment_report(self, request):
        """
        Comprehensive report: Which supervisors worked on which MOs in which processes with timestamps
        """
        mo_id = request.query_params.get('mo_id')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        work_center_id = request.query_params.get('work_center_id')
        supervisor_id = request.query_params.get('supervisor_id')
        
        # Get process executions
        executions = MOProcessExecution.objects.select_related(
            'mo', 'process', 'assigned_supervisor'
        )
        
        if mo_id:
            executions = executions.filter(mo__mo_id=mo_id)
        if start_date:
            executions = executions.filter(actual_start_time__gte=start_date)
        if end_date:
            executions = executions.filter(actual_start_time__lte=end_date)
        if work_center_id:
            executions = executions.filter(process_id=work_center_id)
        if supervisor_id:
            executions = executions.filter(assigned_supervisor_id=supervisor_id)
        
        # Build report
        report = []
        for execution in executions:
            # Determine shift
            current_shift = execution._get_current_shift() if hasattr(execution, '_get_current_shift') else 'shift_1'
            
            # Get activity log
            activity_log = None
            if execution.actual_start_time:
                activity_log = SupervisorActivityLog.objects.filter(
                    date=execution.actual_start_time.date(),
                    work_center=execution.process,
                    active_supervisor=execution.assigned_supervisor
                ).first()
            
            report.append({
                'mo_id': execution.mo.mo_id,
                'process_name': execution.process.name,
                'supervisor_id': execution.assigned_supervisor.id if execution.assigned_supervisor else None,
                'supervisor_name': execution.assigned_supervisor.get_full_name() if execution.assigned_supervisor else 'Unassigned',
                'status': execution.status,
                'shift': current_shift,
                'start_time': execution.actual_start_time,
                'end_time': execution.actual_end_time,
                'duration_minutes': execution.duration_minutes,
                'date': execution.actual_start_time.date() if execution.actual_start_time else None,
                'total_mos_handled': activity_log.mos_handled if activity_log else 0,
                'total_operations': activity_log.total_operations if activity_log else 0
            })
        
        serializer = SupervisorAssignmentReportSerializer(report, many=True)
        return Response({
            'count': len(report),
            'results': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def supervisor_workload(self, request):
        """Get workload summary by supervisor"""
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        activity_logs = SupervisorActivityLog.objects.select_related('active_supervisor', 'work_center')
        
        if start_date:
            activity_logs = activity_logs.filter(date__gte=start_date)
        if end_date:
            activity_logs = activity_logs.filter(date__lte=end_date)
        
        # Group by supervisor
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        supervisors = User.objects.filter(
            user_roles__role__name='supervisor',
            user_roles__is_active=True
        ).distinct()
        
        workload = []
        for supervisor in supervisors:
            logs = activity_logs.filter(active_supervisor=supervisor)
            
            total_mos = logs.aggregate(Sum('mos_handled'))['mos_handled__sum'] or 0
            total_operations = logs.aggregate(Sum('total_operations'))['total_operations__sum'] or 0
            total_completed = logs.aggregate(Sum('operations_completed'))['operations_completed__sum'] or 0
            total_time = logs.aggregate(Sum('total_processing_time_minutes'))['total_processing_time_minutes__sum'] or 0
            
            workload.append({
                'supervisor_id': supervisor.id,
                'supervisor_name': supervisor.get_full_name(),
                'total_mos_handled': total_mos,
                'total_operations': total_operations,
                'operations_completed': total_completed,
                'total_processing_time_minutes': total_time,
                'work_centers': list(logs.values_list('work_center__name', flat=True).distinct())
            })
        
        return Response(workload)
    
    @action(detail=False, methods=['get'])
    def shift_summary(self, request):
        """Get summary of which shifts are running for which MOs"""
        date = request.query_params.get('date', timezone.now().date())
        
        statuses = DailySupervisorStatus.objects.filter(
            date=date
        ).select_related('work_center', 'active_supervisor')
        
        summary_by_shift = {}
        for status in statuses:
            shift = status.shift
            if shift not in summary_by_shift:
                summary_by_shift[shift] = []
            
            summary_by_shift[shift].append({
                'work_center': status.work_center.name,
                'active_supervisor': status.active_supervisor.get_full_name(),
                'is_present': status.is_present
            })
        
        return Response(summary_by_shift)

