"""
Supervisor Process Management Views
Handles stop/resume, rework, verification, and FI operations
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Sum, Count, Avg, F
from django.db import transaction
from datetime import datetime, timedelta
from decimal import Decimal

from manufacturing.models import (
    ProcessStop,
    ProcessDowntimeSummary,
    BatchProcessCompletion,
    ReworkBatch,
    FinalInspectionRework,
    BatchReceiptVerification,
    BatchReceiptLog,
    ProcessActivityLog,
    BatchTraceabilityEvent,
    Batch,
    MOProcessExecution,
    ManufacturingOrder
)
from manufacturing.serializers.supervisor_serializers import (
    ProcessStopSerializer,
    ProcessStopCreateSerializer,
    ProcessResumeSerializer,
    ProcessDowntimeSummarySerializer,
    BatchProcessCompletionSerializer,
    BatchProcessCompletionCreateSerializer,
    ReworkBatchSerializer,
    BatchReceiptVerificationSerializer,
    BatchReceiptVerifySerializer,
    BatchReceiptReportSerializer,
    BatchReceiptLogSerializer,
    FinalInspectionReworkSerializer,
    FinalInspectionReworkCreateSerializer,
    ProcessActivityLogSerializer,
    BatchTraceabilityEventSerializer
)
from processes.models import Process
from notifications.models import WorkflowNotification


class ProcessStopViewSet(viewsets.ModelViewSet):
    """
    ViewSet for process stop/resume operations
    """
    queryset = ProcessStop.objects.all()
    serializer_class = ProcessStopSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        
        # Filter based on user role
        user_roles = user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        
        # Admin, Manager, PH can see all
        if any(role in ['admin', 'manager', 'production_head'] for role in user_roles):
            return queryset
        
        # Supervisors see their own stops
        if 'supervisor' in user_roles:
            return queryset.filter(
                Q(stopped_by=user) | Q(resumed_by=user) |
                Q(process_execution__assigned_supervisor=user)
            )
        
        return queryset.none()
    
    def create(self, request):
        """Stop a process - creates ProcessStop for all active batches in the process"""
        serializer = ProcessStopCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        batches = validated_data['batches']  # List of batches to stop
        process_execution = validated_data['process_execution']
        mo = process_execution.mo
        
        # Check if process is already stopped
        if process_execution.status == 'stopped':
            # Check if there are any active (non-resumed) stops
            active_stops = ProcessStop.objects.filter(
                process_execution=process_execution,
                is_resumed=False
            ).exists()
            
            if active_stops:
                return Response(
                    {'error': 'Process is already stopped. Please resume it first before stopping again.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Check for active stops for any of these batches
        active_stops = ProcessStop.objects.filter(
            batch__in=batches,
            process_execution=process_execution,
            is_resumed=False
        )
        
        if active_stops.exists():
            stopped_batches = [stop.batch.batch_id for stop in active_stops]
            return Response(
                {'error': f'Process already has active stops for batches: {", ".join(stopped_batches)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create process stops for all batches
        created_stops = []
        with transaction.atomic():
            for batch in batches:
                process_stop = ProcessStop.objects.create(
                    batch=batch,
                    mo=mo,
                    process_execution=process_execution,
                    stopped_by=request.user,
                    stop_reason=validated_data['stop_reason'],
                    stop_reason_detail=validated_data.get('stop_reason_detail', '')
                )
                created_stops.append(process_stop)
                
                # Log activity for each batch
                ProcessActivityLog.log_process_stop(
                    process_execution=process_execution,
                    batch=batch,
                    user=request.user,
                    reason=validated_data['stop_reason'],
                    reason_detail=validated_data.get('stop_reason_detail', '')
                )
            
            # Update process execution status to stopped
            if process_execution.status != 'stopped':
                process_execution.status = 'stopped'
                process_execution.save(update_fields=['status', 'updated_at'])
            
            # Send notifications to PH and Manager (only once, using first stop)
            if created_stops:
                self._send_stop_notifications(created_stops[0])
        
        # Return the first stop (or all stops if needed)
        return Response(
            {
                'message': f'Process stopped successfully for {len(created_stops)} batch(es)',
                'process_stop': ProcessStopSerializer(created_stops[0]).data,
                'batches_stopped': [stop.batch.batch_id for stop in created_stops],
                'total_stops': len(created_stops)
            },
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        """Resume a stopped process - resumes all active stops for this process execution"""
        process_stop = self.get_object()
        
        if process_stop.is_resumed:
            return Response(
                {'error': 'This process stop is already resumed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ProcessResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        process_execution = process_stop.process_execution
        resume_notes = serializer.validated_data.get('resume_notes', '')
        
        # Find all active (non-resumed) stops for this process execution
        active_stops = ProcessStop.objects.filter(
            process_execution=process_execution,
            is_resumed=False
        )
        
        if not active_stops.exists():
            return Response(
                {'error': 'No active stops found for this process'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        resumed_stops = []
        total_downtime = 0
        
        with transaction.atomic():
            # Resume all active stops for this process
            for stop in active_stops:
                downtime_minutes = stop.resume_process(
                    resumed_by_user=request.user,
                    notes=resume_notes
                )
                resumed_stops.append(stop)
                total_downtime += downtime_minutes
                
                # Log activity for each batch
                ProcessActivityLog.log_process_resume(
                    process_execution=process_execution,
                    batch=stop.batch,
                    user=request.user,
                    downtime_minutes=downtime_minutes
                )
            
            # Update process execution status back to in_progress
            # Only if all stops are resumed
            if process_execution.status == 'stopped':
                # Check if there are any remaining active stops
                remaining_stops = ProcessStop.objects.filter(
                    process_execution=process_execution,
                    is_resumed=False
                ).exists()
                
                if not remaining_stops:
                    process_execution.status = 'in_progress'
                    process_execution.save(update_fields=['status', 'updated_at'])
            
            # Update downtime summary
            ProcessDowntimeSummary.update_summary(
                date=process_stop.stopped_at.date(),
                process=process_execution.process
            )
            
            # Send notifications (only once, using first resumed stop)
            if resumed_stops:
                self._send_resume_notifications(resumed_stops[0])
        
        return Response(
            {
                'message': f'Process resumed successfully for {len(resumed_stops)} batch(es)',
                'process_stop': ProcessStopSerializer(resumed_stops[0]).data,
                'batches_resumed': [stop.batch.batch_id for stop in resumed_stops],
                'total_resumed': len(resumed_stops),
                'total_downtime_minutes': total_downtime
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=False, methods=['get'])
    def active_stops(self, request):
        """Get all active (not resumed) stops"""
        stops = self.get_queryset().filter(is_resumed=False)
        serializer = self.get_serializer(stops, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_stops(self, request):
        """Get stops for current user's batches"""
        stops = self.get_queryset().filter(
            process_execution__assigned_supervisor=request.user,
            is_resumed=False
        )
        serializer = self.get_serializer(stops, many=True)
        return Response(serializer.data)
    
    def _send_stop_notifications(self, process_stop):
        """Send notifications when process stopped"""
        # Get PH and Manager users
        ph_users = User.objects.filter(
            user_roles__role__name='production_head',
            user_roles__is_active=True
        )
        manager_users = User.objects.filter(
            user_roles__role__name='manager',
            user_roles__is_active=True
        )
        
        message = (
            f"Process stopped by {process_stop.stopped_by.get_full_name()} - "
            f"{process_stop.get_stop_reason_display()} - "
            f"Batch: {process_stop.batch.batch_id}"
        )
        
        # Create notifications
        for user in list(ph_users) + list(manager_users):
            WorkflowNotification.objects.create(
                user=user,
                notification_type='process_stopped',
                title='Process Stopped',
                message=message,
                related_mo=process_stop.mo,
                action_url=f'/production-head/mo-detail/{process_stop.mo.id}'
            )
    
    def _send_resume_notifications(self, process_stop):
        """Send notifications when process resumed"""
        ph_users = User.objects.filter(
            user_roles__role__name='production_head',
            user_roles__is_active=True
        )
        manager_users = User.objects.filter(
            user_roles__role__name='manager',
            user_roles__is_active=True
        )
        
        message = (
            f"Process resumed by {process_stop.resumed_by.get_full_name()} - "
            f"Batch: {process_stop.batch.batch_id} - "
            f"Downtime: {process_stop.downtime_minutes} minutes"
        )
        
        for user in list(ph_users) + list(manager_users):
            WorkflowNotification.objects.create(
                user=user,
                notification_type='process_resumed',
                title='Process Resumed',
                message=message,
                related_mo=process_stop.mo,
                action_url=f'/production-head/mo-detail/{process_stop.mo.id}'
            )


class ProcessDowntimeAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for downtime analytics and reporting
    """
    queryset = ProcessDowntimeSummary.objects.all()
    serializer_class = ProcessDowntimeSummarySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
        
        # Filter by process
        process_id = self.request.query_params.get('process_id')
        if process_id:
            queryset = queryset.filter(process_id=process_id)
        
        return queryset.order_by('-date')
    
    @action(detail=False, methods=['get'])
    def by_reason(self, request):
        """Get downtime breakdown by reason"""
        queryset = self.get_queryset()
        
        summary = queryset.aggregate(
            total_machine=Sum('breakdown_machine'),
            total_power=Sum('breakdown_power'),
            total_maintenance=Sum('breakdown_maintenance'),
            total_material=Sum('breakdown_material'),
            total_quality=Sum('breakdown_quality'),
            total_others=Sum('breakdown_others'),
            total_downtime=Sum('total_downtime_minutes')
        )
        
        return Response(summary)
    
    @action(detail=False, methods=['get'])
    def trends(self, request):
        """Get downtime trends over time"""
        queryset = self.get_queryset()
        
        # Group by date and calculate totals
        trends = queryset.values('date').annotate(
            total_downtime=Sum('total_downtime_minutes'),
            total_stops=Sum('total_stops')
        ).order_by('date')
        
        return Response(list(trends))
    
    @action(detail=False, methods=['get'])
    def by_process(self, request):
        """Get downtime summary by process"""
        queryset = self.get_queryset()
        
        by_process = queryset.values(
            'process__name'
        ).annotate(
            total_downtime=Sum('total_downtime_minutes'),
            total_stops=Sum('total_stops'),
            avg_downtime_per_stop=Avg('total_downtime_minutes')
        ).order_by('-total_downtime')
        
        return Response(list(by_process))


class BatchProcessCompletionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for batch process completion with OK/Scrap/Rework
    """
    queryset = BatchProcessCompletion.objects.all()
    serializer_class = BatchProcessCompletionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by batch
        batch_id = self.request.query_params.get('batch_id')
        if batch_id:
            queryset = queryset.filter(batch_id=batch_id)
        
        # Filter by process
        process_id = self.request.query_params.get('process_id')
        if process_id:
            queryset = queryset.filter(process_execution__process_id=process_id)
        
        return queryset.order_by('-completed_at')
    
    def create(self, request):
        """Create batch completion with OK/Scrap/Rework quantities"""
        serializer = BatchProcessCompletionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        
        with transaction.atomic():
            # Get batch and process execution
            batch = Batch.objects.get(id=validated_data['batch_id'])
            process_execution = MOProcessExecution.objects.get(id=validated_data['process_execution_id'])
            
            # Create completion record
            completion = BatchProcessCompletion.objects.create(
                batch=batch,
                process_execution=process_execution,
                completed_by=request.user,
                input_quantity_kg=validated_data['input_quantity_kg'],
                ok_quantity_kg=validated_data['ok_quantity_kg'],
                scrap_quantity_kg=validated_data['scrap_quantity_kg'],
                rework_quantity_kg=validated_data['rework_quantity_kg'],
                completion_notes=validated_data.get('completion_notes', ''),
                defect_description=validated_data.get('defect_description', '')
            )
            
            # Log activity
            ProcessActivityLog.log_batch_completion(completion, request.user)
            
            # If rework quantity > 0, create rework batch
            if completion.rework_quantity_kg > 0:
                # Get currently active supervisor for this process
                from processes.models import DailySupervisorStatus
                today = timezone.now().date()
                current_shift = process_execution._get_current_shift()
                
                supervisor_status = DailySupervisorStatus.objects.filter(
                    date=today,
                    work_center=process_execution.process,
                    shift=current_shift
                ).first()
                
                # Use active supervisor from daily status, fallback to request.user
                rework_supervisor = supervisor_status.active_supervisor if supervisor_status else request.user
                
                rework_batch = ReworkBatch.objects.create(
                    original_batch=batch,
                    process_execution=process_execution,
                    completion_record=completion,
                    rework_quantity_kg=completion.rework_quantity_kg,
                    status='pending',
                    source='process_supervisor',
                    assigned_supervisor=rework_supervisor,
                    rework_cycle_number=completion.rework_cycle_number + 1,
                    defect_description=completion.defect_description
                )
                
                # Log rework creation
                ProcessActivityLog.log_rework_created(rework_batch, request.user)
            
            # Move OK quantity to next process
            if completion.ok_quantity_kg > 0:
                self._move_to_next_process(batch, process_execution, completion.ok_quantity_kg)
            
            # Add scrap to count (update batch scrap quantity)
            if completion.scrap_quantity_kg > 0:
                batch.scrap_quantity += int(completion.scrap_quantity_kg)
                batch.save()
        
        return Response(
            BatchProcessCompletionSerializer(completion).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['get'])
    def by_batch(self, request):
        """Get all completions for a specific batch"""
        batch_id = request.query_params.get('batch_id')
        if not batch_id:
            return Response(
                {'error': 'batch_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        completions = self.get_queryset().filter(batch_id=batch_id)
        serializer = self.get_serializer(completions, many=True)
        return Response(serializer.data)
    
    def _move_to_next_process(self, batch, current_process_execution, ok_quantity_kg):
        """Move OK quantity to next process"""
        next_process = current_process_execution.get_next_process_execution()
        
        if next_process:
            # Create receipt log for handover
            BatchReceiptLog.objects.create(
                batch=batch,
                mo=batch.mo,
                from_process=current_process_execution.process,
                to_process=next_process.process,
                handed_over_by=current_process_execution.assigned_supervisor,
                handed_over_at=timezone.now(),
                handed_over_quantity_kg=ok_quantity_kg,
                handover_notes=f"OK quantity from {current_process_execution.process.name}"
            )
            
            # Notify next supervisor
            if next_process.assigned_supervisor:
                WorkflowNotification.objects.create(
                    user=next_process.assigned_supervisor,
                    notification_type='batch_received',
                    title='Batch Received',
                    message=f"Batch {batch.batch_id} received from {current_process_execution.process.name} - {ok_quantity_kg} kg",
                    related_mo=batch.mo,
                    action_url=f'/supervisor/mo-detail/{batch.mo.id}'
                )


class ReworkBatchViewSet(viewsets.ModelViewSet):
    """
    ViewSet for rework batch management
    """
    queryset = ReworkBatch.objects.all()
    serializer_class = ReworkBatchSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        user = self.request.user
        user_roles = user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        
        # Admin, Manager, PH can see all
        if any(role in ['admin', 'manager', 'production_head'] for role in user_roles):
            return queryset
        
        # Supervisors see their assigned rework
        if 'supervisor' in user_roles:
            return queryset.filter(assigned_supervisor=user)
        
        return queryset.none()
    
    @action(detail=False, methods=['get'])
    def my_pending(self, request):
        """Get pending rework batches for current user"""
        rework_batches = self.get_queryset().filter(
            assigned_supervisor=request.user,
            status='pending'
        )
        serializer = self.get_serializer(rework_batches, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Start rework batch"""
        rework_batch = self.get_object()
        
        try:
            rework_batch.start_rework()
            return Response(
                ReworkBatchSerializer(rework_batch).data,
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete rework batch with OK/Scrap quantities"""
        rework_batch = self.get_object()
        
        ok_kg = request.data.get('ok_kg')
        scrap_kg = request.data.get('scrap_kg')
        
        if ok_kg is None or scrap_kg is None:
            return Response(
                {'error': 'ok_kg and scrap_kg are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            with transaction.atomic():
                completion = rework_batch.complete_rework(
                    ok_kg=Decimal(str(ok_kg)),
                    scrap_kg=Decimal(str(scrap_kg))
                )
                
                # Move OK to next process
                if ok_kg > 0:
                    next_process = rework_batch.process_execution.get_next_process_execution()
                    if next_process and next_process.assigned_supervisor:
                        WorkflowNotification.objects.create(
                            user=next_process.assigned_supervisor,
                            notification_type='rework_completed',
                            title='Rework Completed',
                            message=f"Rework batch {rework_batch.original_batch.batch_id} completed - {ok_kg} kg OK",
                            related_mo=rework_batch.original_batch.mo,
                            action_url=f'/supervisor/mo-detail/{rework_batch.original_batch.mo.id}'
                        )
            
            return Response(
                {'message': 'Rework completed successfully', 'completion': BatchProcessCompletionSerializer(completion).data},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class BatchReceiptVerificationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for batch receipt verification and reporting
    """
    queryset = BatchReceiptVerification.objects.all()
    serializer_class = BatchReceiptVerificationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        if self.request.query_params.get('on_hold') == 'true':
            queryset = queryset.filter(is_on_hold=True)
        
        if self.request.query_params.get('reported') == 'true':
            queryset = queryset.filter(action='reported')
        
        return queryset.order_by('-received_at')
    
    @action(detail=False, methods=['post'])
    def verify(self, request):
        """Verify batch receipt - OK"""
        serializer = BatchReceiptVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        
        with transaction.atomic():
            batch = Batch.objects.get(id=validated_data['batch_id'])
            process_execution = MOProcessExecution.objects.get(id=validated_data['process_execution_id'])
            
            verification = BatchReceiptVerification.objects.create(
                batch=batch,
                process_execution=process_execution,
                received_by=request.user,
                action='verified',
                expected_quantity_kg=validated_data['expected_quantity_kg']
            )
            
            # Log activity
            ProcessActivityLog.log_batch_verification(verification, request.user)
        
        return Response(
            BatchReceiptVerificationSerializer(verification).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['post'])
    def report(self, request):
        """Report batch receipt issue"""
        serializer = BatchReceiptReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        
        with transaction.atomic():
            batch = Batch.objects.get(id=validated_data['batch_id'])
            process_execution = MOProcessExecution.objects.get(id=validated_data['process_execution_id'])
            
            verification = BatchReceiptVerification.objects.create(
                batch=batch,
                process_execution=process_execution,
                received_by=request.user,
                action='reported',
                expected_quantity_kg=validated_data['expected_quantity_kg'],
                actual_quantity_kg=validated_data.get('actual_quantity_kg'),
                report_reason=validated_data['report_reason'],
                report_details=validated_data.get('report_details', '')
            )
            
            # Log activity
            ProcessActivityLog.log_batch_verification(verification, request.user)
            
            # Notify PH
            ph_users = User.objects.filter(
                user_roles__role__name='production_head',
                user_roles__is_active=True
            )
            
            for user in ph_users:
                WorkflowNotification.objects.create(
                    user=user,
                    notification_type='batch_reported',
                    title='Batch Issue Reported',
                    message=f"Batch {batch.batch_id} reported by {request.user.get_full_name()} - {verification.get_report_reason_display()}",
                    related_mo=batch.mo,
                    action_url=f'/production-head/mo-detail/{batch.mo.id}'
                )
        
        return Response(
            BatchReceiptVerificationSerializer(verification).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['get'])
    def on_hold(self, request):
        """Get all batches on hold"""
        batches_on_hold = self.get_queryset().filter(is_on_hold=True, is_resolved=False)
        serializer = self.get_serializer(batches_on_hold, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def clear_hold(self, request, pk=None):
        """PH clears hold on reported batch"""
        verification = self.get_object()
        
        notes = request.data.get('clearance_notes', '')
        
        try:
            verification.clear_hold(cleared_by_user=request.user, notes=notes)
            
            # Notify supervisor
            if verification.process_execution.assigned_supervisor:
                WorkflowNotification.objects.create(
                    user=verification.process_execution.assigned_supervisor,
                    notification_type='hold_cleared',
                    title='Hold Cleared',
                    message=f"Hold cleared for batch {verification.batch.batch_id} - ready to process",
                    related_mo=verification.batch.mo,
                    action_url=f'/supervisor/mo-detail/{verification.batch.mo.id}'
                )
            
            return Response(
                BatchReceiptVerificationSerializer(verification).data,
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """PH/Manager resolves reported issue"""
        verification = self.get_object()
        
        resolution_notes = request.data.get('resolution_notes', '')
        
        try:
            verification.resolve_issue(
                resolved_by_user=request.user,
                resolution_notes=resolution_notes
            )
            
            return Response(
                BatchReceiptVerificationSerializer(verification).data,
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class FinalInspectionReworkViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Final Inspection rework management
    """
    queryset = FinalInspectionRework.objects.all()
    serializer_class = FinalInspectionReworkSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset.order_by('-inspected_at')
    
    def create(self, request):
        """Create FI rework assignment"""
        serializer = FinalInspectionReworkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        
        with transaction.atomic():
            batch = Batch.objects.get(id=validated_data['batch_id'])
            defective_process = Process.objects.get(id=validated_data['defective_process_id'])
            
            # Find supervisor for defective process - use current active supervisor
            from processes.models import DailySupervisorStatus, WorkCenterSupervisorShift
            today = timezone.now().date()
            current_time = timezone.now().time()
            
            # Determine current shift
            shift_configs = WorkCenterSupervisorShift.objects.filter(
                work_center=defective_process,
                is_active=True
            )
            current_shift = 'shift_1'  # default
            for config in shift_configs:
                if config.shift_start_time <= current_time < config.shift_end_time:
                    current_shift = config.shift
                    break
            
            supervisor_status = DailySupervisorStatus.objects.filter(
                date=today,
                work_center=defective_process,
                shift=current_shift
            ).select_related('active_supervisor').first()
            
            if not supervisor_status:
                return Response(
                    {'error': f'No active supervisor found for {defective_process.name} in {current_shift}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create FI rework
            fi_rework = FinalInspectionRework.objects.create(
                batch=batch,
                mo=batch.mo,
                inspected_by=request.user,
                defective_process=defective_process,
                defect_description=validated_data['defect_description'],
                rework_quantity_kg=validated_data['rework_quantity_kg'],
                assigned_to_supervisor=supervisor_status.active_supervisor,
                fi_notes=validated_data.get('fi_notes', '')
            )
            
            # Log activity
            ProcessActivityLog.log_fi_rework(fi_rework, request.user)
            
            # Notify assigned supervisor
            WorkflowNotification.objects.create(
                user=fi_rework.assigned_to_supervisor,
                notification_type='fi_rework_assigned',
                title='FI Rework Assigned',
                message=f"Rework batch {batch.batch_id} assigned from Final Inspection - Defect in: {defective_process.name}",
                related_mo=batch.mo,
                action_url=f'/supervisor/mo-detail/{batch.mo.id}'
            )
            
            # Notify PH
            ph_users = User.objects.filter(
                user_roles__role__name='production_head',
                user_roles__is_active=True
            )
            for user in ph_users:
                WorkflowNotification.objects.create(
                    user=user,
                    notification_type='fi_rework_created',
                    title='FI Rework Created',
                    message=f"FI rework created for batch {batch.batch_id} - Process: {defective_process.name}",
                    related_mo=batch.mo,
                    action_url=f'/production-head/mo-detail/{batch.mo.id}'
                )
        
        return Response(
            FinalInspectionReworkSerializer(fi_rework).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['get'])
    def my_assigned(self, request):
        """Get FI reworks assigned to current user"""
        fi_reworks = self.get_queryset().filter(
            assigned_to_supervisor=request.user,
            status__in=['pending', 'in_progress']
        )
        serializer = self.get_serializer(fi_reworks, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete FI rework - supervisor action"""
        fi_rework = self.get_object()
        
        try:
            fi_rework.complete_rework(completed_by_user=request.user)
            
            # Notify FI for re-inspection
            WorkflowNotification.objects.create(
                user=fi_rework.inspected_by,
                notification_type='fi_rework_completed',
                title='FI Rework Completed',
                message=f"Rework for batch {fi_rework.batch.batch_id} completed - ready for re-inspection",
                related_mo=fi_rework.mo,
                action_url=f'/quality/final-inspection'
            )
            
            return Response(
                FinalInspectionReworkSerializer(fi_rework).data,
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def reinspect(self, request, pk=None):
        """FI re-inspection after rework"""
        fi_rework = self.get_object()
        
        passed = request.data.get('passed', False)
        notes = request.data.get('notes', '')
        
        try:
            if passed:
                fi_rework.pass_reinspection(inspector_user=request.user, notes=notes)
                
                # Notify packing zone (batch ready)
                # Implementation depends on your packing zone workflow
                
                return Response(
                    {'message': 'Batch passed re-inspection - moving to packing'},
                    status=status.HTTP_200_OK
                )
            else:
                # Failed re-inspection - create new FI rework cycle
                fi_rework.rework_cycle_count += 1
                fi_rework.status = 'pending'
                fi_rework.reinspected_at = timezone.now()
                fi_rework.reinspected_by = request.user
                fi_rework.reinspection_passed = False
                fi_rework.reinspection_notes = notes
                fi_rework.save()
                
                return Response(
                    {'message': 'Batch failed re-inspection - rework cycle continues'},
                    status=status.HTTP_200_OK
                )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'])
    def report(self, request):
        """FI rework report - which process caused most reworks"""
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        queryset = self.get_queryset()
        
        if start_date:
            queryset = queryset.filter(inspected_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(inspected_at__lte=end_date)
        
        # Group by defective process
        report = queryset.values(
            'defective_process__name'
        ).annotate(
            rework_count=Count('id'),
            total_rework_kg=Sum('rework_quantity_kg')
        ).order_by('-rework_count')
        
        return Response(list(report))


class ProcessActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for process activity logs (read-only)
    """
    queryset = ProcessActivityLog.objects.all()
    serializer_class = ProcessActivityLogSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by batch
        batch_id = self.request.query_params.get('batch_id')
        if batch_id:
            queryset = queryset.filter(batch_id=batch_id)
        
        # Filter by MO
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo_id=mo_id)
        
        # Filter by process
        process_id = self.request.query_params.get('process_id')
        if process_id:
            queryset = queryset.filter(process_id=process_id)
        
        # Filter by activity type
        activity_type = self.request.query_params.get('activity_type')
        if activity_type:
            queryset = queryset.filter(activity_type=activity_type)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(performed_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(performed_at__lte=end_date)
        
        return queryset.order_by('-performed_at')
    
    @action(detail=False, methods=['get'])
    def by_batch(self, request):
        """Get all logs for a specific batch"""
        batch_id = request.query_params.get('batch_id')
        if not batch_id:
            return Response(
                {'error': 'batch_id parameter required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logs = self.get_queryset().filter(batch_id=batch_id)
        serializer = self.get_serializer(logs, many=True)
        return Response(serializer.data)


class BatchTraceabilityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for batch traceability timeline
    """
    queryset = BatchTraceabilityEvent.objects.all()
    serializer_class = BatchTraceabilityEventSerializer
    permission_classes = [IsAuthenticated]
    
    def retrieve(self, request, pk=None):
        """Get complete traceability timeline for a batch"""
        try:
            batch = Batch.objects.get(id=pk)
        except Batch.DoesNotExist:
            return Response(
                {'error': 'Batch not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get all traceability events for this batch
        events = self.get_queryset().filter(batch=batch).order_by('timestamp')
        
        # If no events, generate from activity logs
        if not events.exists():
            activity_logs = ProcessActivityLog.objects.filter(batch=batch).order_by('performed_at')
            for log in activity_logs:
                BatchTraceabilityEvent.create_from_activity_log(log)
            
            events = self.get_queryset().filter(batch=batch).order_by('timestamp')
        
        serializer = self.get_serializer(events, many=True)
        
        return Response({
            'batch_id': batch.batch_id,
            'mo_id': batch.mo.mo_id,
            'events': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def search(self, request):
        """Search batch traceability by various filters"""
        mo_id = request.query_params.get('mo_id')
        batch_id = request.query_params.get('batch_id')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        queryset = self.get_queryset()
        
        if mo_id:
            queryset = queryset.filter(mo__mo_id__icontains=mo_id)
        if batch_id:
            queryset = queryset.filter(batch__batch_id__icontains=batch_id)
        if start_date:
            queryset = queryset.filter(timestamp__gte=start_date)
        if end_date:
            queryset = queryset.filter(timestamp__lte=end_date)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class ReworkAnalyticsViewSet(viewsets.ViewSet):
    """
    ViewSet for rework analytics and reporting
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def rate_by_process(self, request):
        """Calculate rework rate percentage by process"""
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Get all completions
        completions = BatchProcessCompletion.objects.all()
        
        if start_date:
            completions = completions.filter(completed_at__gte=start_date)
        if end_date:
            completions = completions.filter(completed_at__lte=end_date)
        
        # Group by process and calculate rates
        process_stats = completions.values(
            'process_execution__process__name'
        ).annotate(
            total_input_kg=Sum('input_quantity_kg'),
            total_ok_kg=Sum('ok_quantity_kg'),
            total_scrap_kg=Sum('scrap_quantity_kg'),
            total_rework_kg=Sum('rework_quantity_kg'),
            completion_count=Count('id')
        )
        
        # Calculate percentages
        result = []
        for stats in process_stats:
            total = float(stats['total_input_kg'])
            if total > 0:
                result.append({
                    'process_name': stats['process_execution__process__name'],
                    'ok_percentage': (float(stats['total_ok_kg']) / total) * 100,
                    'scrap_percentage': (float(stats['total_scrap_kg']) / total) * 100,
                    'rework_percentage': (float(stats['total_rework_kg']) / total) * 100,
                    'total_input_kg': stats['total_input_kg'],
                    'total_rework_kg': stats['total_rework_kg'],
                    'completion_count': stats['completion_count']
                })
        
        # Sort by rework percentage descending
        result.sort(key=lambda x: x['rework_percentage'], reverse=True)
        
        return Response(result)
    
    @action(detail=False, methods=['get'])
    def trends(self, request):
        """Monthly rework trends"""
        months = int(request.query_params.get('months', 6))
        
        start_date = timezone.now() - timedelta(days=months * 30)
        
        completions = BatchProcessCompletion.objects.filter(
            completed_at__gte=start_date
        )
        
        # Group by month
        from django.db.models.functions import TruncMonth
        
        monthly_stats = completions.annotate(
            month=TruncMonth('completed_at')
        ).values('month').annotate(
            total_input_kg=Sum('input_quantity_kg'),
            total_rework_kg=Sum('rework_quantity_kg'),
            completion_count=Count('id')
        ).order_by('month')
        
        result = []
        for stats in monthly_stats:
            total = float(stats['total_input_kg'])
            if total > 0:
                result.append({
                    'month': stats['month'].strftime('%Y-%m'),
                    'rework_percentage': (float(stats['total_rework_kg']) / total) * 100,
                    'total_rework_kg': stats['total_rework_kg'],
                    'completion_count': stats['completion_count']
                })
        
        return Response(result)
    
    @action(detail=False, methods=['get'])
    def top_processes(self, request):
        """Top processes with highest rework"""
        limit = int(request.query_params.get('limit', 10))
        
        rework_batches = ReworkBatch.objects.all()
        
        top_processes = rework_batches.values(
            'process_execution__process__name'
        ).annotate(
            rework_count=Count('id'),
            total_rework_kg=Sum('rework_quantity_kg')
        ).order_by('-rework_count')[:limit]
        
        return Response(list(top_processes))


# Import User model at the end to avoid circular import
from django.contrib.auth import get_user_model
User = get_user_model()

