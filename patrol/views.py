from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.utils import timezone
from django.db.models import Q, Count
from datetime import date, timedelta
import zipfile
import io
from django.http import HttpResponse

from .models import PatrolDuty, PatrolUpload, PatrolAlert
from .serializers import (
    PatrolDutyListSerializer,
    PatrolDutyDetailSerializer,
    PatrolDutyCreateUpdateSerializer,
    PatrolUploadSerializer,
    PatrolUploadSubmitSerializer,
    PatrolAlertSerializer,
    PatrolStatisticsSerializer,
    PatrolUserSerializer
)
from .permissions import (
    IsProductionHeadOrAdmin,
    IsManagerOrAbove,
    IsPatrolUser,
    IsPatrolUserOrManagerAbove
)
from authentication.models import CustomUser


class PatrolDutyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing patrol duties
    - Production Head: Full CRUD access
    - Manager: Read-only access
    - Patrol: Access only their own duties
    """
    queryset = PatrolDuty.objects.all().select_related('patrol_user', 'created_by')
    permission_classes = [IsManagerOrAbove | IsPatrolUser]
    
    def get_serializer_class(self):
        if self.action in ['list']:
            return PatrolDutyListSerializer
        elif self.action in ['retrieve']:
            return PatrolDutyDetailSerializer
        return PatrolDutyCreateUpdateSerializer
    
    def get_queryset(self):
        """Filter queryset based on user role"""
        user = self.request.user
        active_role = user.user_roles.filter(is_active=True).select_related('role').first()
        
        if not active_role:
            return PatrolDuty.objects.none()
        
        queryset = super().get_queryset()
        
        # Patrol users see only their own duties
        if active_role.role.name == 'patrol':
            queryset = queryset.filter(patrol_user=user)
        
        # Apply filters from query params
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        patrol_user_id = self.request.query_params.get('patrol_user')
        if patrol_user_id:
            queryset = queryset.filter(patrol_user_id=patrol_user_id)
        
        start_date = self.request.query_params.get('start_date')
        if start_date:
            queryset = queryset.filter(start_date__gte=start_date)
        
        end_date = self.request.query_params.get('end_date')
        if end_date:
            queryset = queryset.filter(end_date__lte=end_date)
        
        return queryset.order_by('-created_at')
    
    def perform_create(self, serializer):
        """Set created_by and create alert"""
        duty = serializer.save(created_by=self.request.user)
        
        # Create alert for patrol user
        PatrolAlert.objects.create(
            duty=duty,
            alert_type='duty_assigned',
            recipient=duty.patrol_user,
            message=f"New patrol duty assigned from {duty.start_date} to {duty.end_date}. "
                    f"Processes: {', '.join(duty.process_names)}. "
                    f"Frequency: Every {duty.frequency_hours} hour(s)."
        )
    
    @action(detail=False, methods=['get'])
    def active_duties(self, request):
        """Get all active duties"""
        today = date.today()
        duties = self.get_queryset().filter(
            status='active',
            start_date__lte=today,
            end_date__gte=today
        )
        serializer = PatrolDutyListSerializer(duties, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def cancel_duty(self, request, pk=None):
        """Cancel a patrol duty"""
        duty = self.get_object()
        
        if duty.status != 'active':
            return Response(
                {'error': 'Only active duties can be cancelled'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        duty.status = 'cancelled'
        duty.save()
        
        # Create alert
        PatrolAlert.objects.create(
            duty=duty,
            alert_type='duty_completed',
            recipient=duty.patrol_user,
            message=f"Your patrol duty has been cancelled by {request.user.full_name}."
        )
        
        serializer = PatrolDutyDetailSerializer(duty)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def patrol_users(self, request):
        """Get list of patrol users for dropdown"""
        patrol_users = CustomUser.objects.filter(
            user_roles__role__name='patrol',
            user_roles__is_active=True,
            is_active=True
        ).distinct()
        serializer = PatrolUserSerializer(patrol_users, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def process_list(self, request):
        """Get list of available processes"""
        from processes.models import WorkCenterMaster
        
        processes = WorkCenterMaster.objects.filter(
            is_active=True
        ).select_related('work_center').values_list('work_center__name', flat=True).distinct()
        
        return Response({'processes': list(processes)})


class PatrolUploadViewSet(viewsets.ModelViewSet):
    """
    ViewSet for patrol uploads
    - Patrol users can upload/view their own
    - Manager/Production Head can view all
    """
    queryset = PatrolUpload.objects.all().select_related('duty', 'duty__patrol_user')
    serializer_class = PatrolUploadSerializer
    permission_classes = [IsPatrolUserOrManagerAbove]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_queryset(self):
        """Filter based on user role and query params"""
        user = self.request.user
        active_role = user.user_roles.filter(is_active=True).select_related('role').first()
        
        if not active_role:
            return PatrolUpload.objects.none()
        
        queryset = super().get_queryset()
        
        # Patrol users see only their own uploads
        if active_role.role.name == 'patrol':
            queryset = queryset.filter(duty__patrol_user=user)
        
        # Apply filters
        duty_id = self.request.query_params.get('duty_id')
        if duty_id:
            queryset = queryset.filter(duty_id=duty_id)
        
        process_name = self.request.query_params.get('process')
        if process_name:
            queryset = queryset.filter(process_name=process_name)
        
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        date_filter = self.request.query_params.get('date')
        if date_filter:
            queryset = queryset.filter(scheduled_date=date_filter)
        else:
            # Default to today's uploads
            queryset = queryset.filter(scheduled_date=date.today())
        
        return queryset.order_by('scheduled_date', 'scheduled_time')
    
    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def submit_upload(self, request, pk=None):
        """Submit QC image upload"""
        upload = self.get_object()
        
        # Validate upload
        serializer = PatrolUploadSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check upload window
        if not upload.is_upload_window_open:
            return Response(
                {'error': 'Upload window is not open. You can only upload 15 minutes before to 30 minutes after the scheduled time.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if locked
        if upload.is_locked:
            return Response(
                {'error': 'This upload is locked and cannot be modified.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check reupload permission
        if upload.first_upload_time and not upload.can_reupload:
            return Response(
                {'error': 'Reupload window has expired (10 minutes from first upload).'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Submit upload
        success = upload.submit_upload(
            image=serializer.validated_data['qc_image'],
            remarks=serializer.validated_data.get('patrol_remarks', '')
        )
        
        if success:
            response_serializer = PatrolUploadSerializer(upload, context={'request': request})
            return Response(response_serializer.data)
        else:
            return Response(
                {'error': 'Failed to submit upload'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['delete'])
    def delete_upload(self, request, pk=None):
        """Delete uploaded image (only within reupload window)"""
        upload = self.get_object()
        
        if not upload.can_reupload:
            return Response(
                {'error': 'Cannot delete upload. Reupload window has expired.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Delete the image but keep the record
        if upload.qc_image:
            upload.qc_image.delete()
        
        upload.qc_image = None
        upload.patrol_remarks = ''
        upload.status = 'pending'
        upload.save()
        
        serializer = PatrolUploadSerializer(upload, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_today_uploads(self, request):
        """Get today's uploads for the logged-in patrol user"""
        today = date.today()
        uploads = self.get_queryset().filter(
            scheduled_date=today,
            duty__status='active'
        )
        serializer = PatrolUploadSerializer(uploads, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def download_uploads(self, request):
        """Download patrol uploads as ZIP file"""
        # Filters
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        process = request.query_params.get('process')
        duty_id = request.query_params.get('duty_id')
        
        uploads = self.get_queryset().filter(status__in=['submitted', 'reuploaded'])
        
        if start_date:
            uploads = uploads.filter(scheduled_date__gte=start_date)
        if end_date:
            uploads = uploads.filter(scheduled_date__lte=end_date)
        if process:
            uploads = uploads.filter(process_name=process)
        if duty_id:
            uploads = uploads.filter(duty_id=duty_id)
        
        # Create ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for upload in uploads:
                if upload.qc_image:
                    # Generate filename
                    filename = f"{upload.process_name}_{upload.scheduled_date}_{upload.scheduled_time.strftime('%H%M')}_{upload.id}.jpg"
                    
                    # Add image to ZIP
                    zip_file.writestr(filename, upload.qc_image.read())
        
        # Prepare response
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="patrol_uploads_{date.today()}.zip"'
        
        return response


class PatrolAlertViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for patrol alerts (read-only with mark as read action)
    """
    queryset = PatrolAlert.objects.all().select_related('duty', 'recipient')
    serializer_class = PatrolAlertSerializer
    permission_classes = [IsPatrolUserOrManagerAbove]
    
    def get_queryset(self):
        """Filter alerts for current user or all for managers"""
        user = self.request.user
        active_role = user.user_roles.filter(is_active=True).select_related('role').first()
        
        if not active_role:
            return PatrolAlert.objects.none()
        
        queryset = super().get_queryset()
        
        # Patrol users and Production Head see only their own alerts
        if active_role.role.name in ['patrol', 'production_head']:
            queryset = queryset.filter(recipient=user)
        
        # Apply filters
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            queryset = queryset.filter(is_read=is_read.lower() == 'true')
        
        alert_type = self.request.query_params.get('type')
        if alert_type:
            queryset = queryset.filter(alert_type=alert_type)
        
        return queryset.order_by('-created_at')
    
    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, pk=None):
        """Mark alert as read"""
        alert = self.get_object()
        alert.is_read = True
        alert.save()
        serializer = PatrolAlertSerializer(alert)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def mark_action_taken(self, request, pk=None):
        """Mark alert action as taken"""
        alert = self.get_object()
        alert.is_action_taken = True
        alert.save()
        serializer = PatrolAlertSerializer(alert)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """Get count of unread alerts"""
        count = self.get_queryset().filter(is_read=False).count()
        return Response({'unread_count': count})


class PatrolDashboardViewSet(viewsets.ViewSet):
    """
    Dashboard statistics for patrol management
    """
    permission_classes = [IsManagerOrAbove]
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get patrol statistics"""
        today = date.today()
        
        # Active duties
        active_duties = PatrolDuty.objects.filter(
            status='active',
            start_date__lte=today,
            end_date__gte=today
        ).count()
        
        # Today's uploads
        today_uploads = PatrolUpload.objects.filter(scheduled_date=today)
        total_uploads = today_uploads.count()
        submitted = today_uploads.filter(status__in=['submitted', 'reuploaded']).count()
        missed = today_uploads.filter(status='missed').count()
        pending = today_uploads.filter(status='pending').count()
        
        # Calculate compliance rate
        compliance_rate = (submitted / total_uploads * 100) if total_uploads > 0 else 0
        
        data = {
            'active_duties': active_duties,
            'total_uploads_today': total_uploads,
            'submitted_today': submitted,
            'missed_today': missed,
            'pending_today': pending,
            'compliance_rate': round(compliance_rate, 2)
        }
        
        serializer = PatrolStatisticsSerializer(data)
        return Response(serializer.data)

