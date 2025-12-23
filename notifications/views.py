from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone

from .models import WorkflowNotification
from .serializers import WorkflowNotificationSerializer


class WorkflowNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for managing workflow notifications
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowNotificationSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['notification_type', 'is_read', 'action_required', 'priority']
    ordering_fields = ['created_at', 'priority']
    ordering = ['-created_at']

    def get_queryset(self):
        """Get workflow notifications for current user"""
        return WorkflowNotification.objects.filter(
            recipient=self.request.user
        ).select_related(
            'related_mo', 'related_batch', 'related_process_assignment', 'created_by'
        )

    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, pk=None):
        """Mark notification as read"""
        notification = self.get_object()
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save()
        
        return Response({'message': 'marked as read'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def mark_action_taken(self, request, pk=None):
        """Mark that action has been taken on notification"""
        notification = self.get_object()
        notification.action_taken = True
        notification.action_taken_at = timezone.now()
        notification.save()
        
        serializer = self.get_serializer(notification)
        return Response(serializer.data)