"""
RM Return ViewSet
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q
import logging

from manufacturing.models.rm_return import RMReturn
from manufacturing.serializers.rm_return_serializers import (
    RMReturnSerializer,
    RMReturnCreateSerializer
)

logger = logging.getLogger(__name__)


class RMReturnViewSet(viewsets.ModelViewSet):
    """
    ViewSet for RM Returns
    Handles all RM return operations from batches
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RMReturnSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter, filters.SearchFilter]
    filterset_fields = ['return_reason', 'is_processed', 'batch__mo']
    search_fields = ['return_id', 'batch__batch_id', 'mo__mo_id', 'heat_number', 'coil_number']
    ordering_fields = ['created_at', 'returned_kg']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Get RM returns with related data"""
        return RMReturn.objects.select_related(
            'batch',
            'mo',
            'created_by'
        ).all()
    
    def get_serializer_class(self):
        """Use different serializers for create vs list/retrieve"""
        if self.action == 'create':
            return RMReturnCreateSerializer
        return RMReturnSerializer
    
    def create(self, request, *args, **kwargs):
        """Create RM return"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            rm_return = serializer.save()
            
            # Return full details using the list serializer
            output_serializer = RMReturnSerializer(rm_return)
            
            return Response({
                'success': True,
                'message': f'RM return processed successfully. Return ID: {rm_return.return_id}',
                'data': output_serializer.data
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error creating RM return: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def by_batch(self, request):
        """Get all returns for a specific batch"""
        batch_id = request.query_params.get('batch_id')
        
        if not batch_id:
            return Response({
                'success': False,
                'error': 'batch_id parameter is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        returns = self.get_queryset().filter(batch_id=batch_id)
        serializer = self.get_serializer(returns, many=True)
        
        return Response({
            'success': True,
            'count': returns.count(),
            'results': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def by_mo(self, request):
        """Get all returns for a specific MO"""
        mo_id = request.query_params.get('mo_id')
        
        if not mo_id:
            return Response({
                'success': False,
                'error': 'mo_id parameter is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        returns = self.get_queryset().filter(mo_id=mo_id)
        serializer = self.get_serializer(returns, many=True)
        
        return Response({
            'success': True,
            'count': returns.count(),
            'results': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get summary statistics for RM returns"""
        queryset = self.get_queryset()
        
        total_returns = queryset.count()
        total_kg = sum(float(r.returned_kg) for r in queryset)
        
        by_reason = {}
        for reason_value, reason_label in [
            ('left_over', 'Left Over'),
            ('quality_defect', 'Quality Defect'),
            ('wrong_rm', 'Wrong RM')
        ]:
            reason_returns = queryset.filter(return_reason=reason_value)
            by_reason[reason_value] = {
                'label': reason_label,
                'count': reason_returns.count(),
                'total_kg': sum(float(r.returned_kg) for r in reason_returns)
            }
        
        return Response({
            'success': True,
            'summary': {
                'total_returns': total_returns,
                'total_kg': round(total_kg, 3),
                'by_reason': by_reason
            }
        })

