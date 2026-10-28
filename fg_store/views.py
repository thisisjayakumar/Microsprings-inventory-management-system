from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Q, Count, F
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction
from datetime import datetime, timedelta

from .models import DispatchBatch, DispatchTransaction, FGStockAlert, DispatchOrder
from .serializers import (
    DispatchBatchSerializer, DispatchBatchCreateSerializer,
    DispatchTransactionSerializer, DispatchTransactionCreateSerializer,
    DispatchTransactionConfirmSerializer, FGStockAlertSerializer,
    DispatchOrderSerializer, FGStockLevelSerializer,
    MOPendingDispatchSerializer, DispatchTransactionLogSerializer,
    DispatchValidationSerializer
)
from manufacturing.models import ManufacturingOrder, Batch
from products.models import Product
from third_party.models import Customer


class IsFGStoreUser(permissions.BasePermission):
    """Permission class for FG Store users"""
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check if user has FG Store role
        user_roles = request.user.user_roles.filter(is_active=True).values_list('role__name', flat=True)
        return any(role in ['fg_store', 'admin', 'manager', 'production_head'] for role in user_roles)


class DispatchBatchViewSet(viewsets.ModelViewSet):
    """ViewSet for Dispatch Batch operations"""
    permission_classes = [IsFGStoreUser]
    queryset = DispatchBatch.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DispatchBatchCreateSerializer
        return DispatchBatchSerializer
    
    def get_queryset(self):
        queryset = DispatchBatch.objects.select_related(
            'mo', 'mo__customer_c_id', 'mo__product_code',
            'product_code', 'packing_supervisor'
        ).order_by('-created_at')
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by product
        product_code = self.request.query_params.get('product_code')
        if product_code:
            queryset = queryset.filter(product_code__product_code__icontains=product_code)
        
        # Filter by customer
        customer_id = self.request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(mo__customer_c_id__c_id=customer_id)
        
        # Filter by MO ID
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo__mo_id__icontains=mo_id)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update batch status"""
        batch = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in [choice[0] for choice in DispatchBatch.STATUS_CHOICES]:
            return Response(
                {'error': 'Invalid status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        batch.status = new_status
        batch.save()
        
        serializer = self.get_serializer(batch)
        return Response(serializer.data)


class DispatchTransactionViewSet(viewsets.ModelViewSet):
    """ViewSet for Dispatch Transaction operations"""
    permission_classes = [IsFGStoreUser]
    queryset = DispatchTransaction.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DispatchTransactionCreateSerializer
        elif self.action == 'confirm':
            return DispatchTransactionConfirmSerializer
        return DispatchTransactionSerializer

    def create(self, request, *args, **kwargs):
        """Create a dispatch transaction and return the full representation."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        output = DispatchTransactionSerializer(
            serializer.instance, context=self.get_serializer_context()
        )
        return Response(output.data, status=status.HTTP_201_CREATED)
    
    def get_queryset(self):
        queryset = DispatchTransaction.objects.select_related(
            'mo', 'mo__product_code', 'mo__customer_c_id',
            'dispatch_batch', 'customer_c_id', 'supervisor_id'
        ).order_by('-dispatch_date')
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(dispatch_date__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(dispatch_date__date__lte=date_to)
        
        # Filter by MO ID
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo__mo_id__icontains=mo_id)
        
        # Filter by batch ID
        batch_id = self.request.query_params.get('batch_id')
        if batch_id:
            queryset = queryset.filter(dispatch_batch__batch_id__icontains=batch_id)
        
        # Filter by supervisor
        supervisor_id = self.request.query_params.get('supervisor_id')
        if supervisor_id:
            queryset = queryset.filter(supervisor_id=supervisor_id)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Confirm dispatch transaction"""
        transaction_obj = self.get_object()
        serializer = self.get_serializer(data=request.data)
        
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    transaction_obj.confirm_dispatch(request.user)
                    
                    # Update notes if provided
                    if 'notes' in serializer.validated_data:
                        transaction_obj.notes = serializer.validated_data['notes']
                        transaction_obj.save()
                    
                    response_serializer = DispatchTransactionSerializer(transaction_obj)
                    return Response(response_serializer.data)
                    
            except Exception as e:
                return Response(
                    {'error': str(e)}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FGStockAlertViewSet(viewsets.ModelViewSet):
    """ViewSet for FG Stock Alert operations"""
    permission_classes = [IsFGStoreUser]
    serializer_class = FGStockAlertSerializer
    queryset = FGStockAlert.objects.all()
    
    def get_queryset(self):
        queryset = FGStockAlert.objects.select_related(
            'product_code', 'created_by'
        ).order_by('product_code', 'alert_type')
        
        # Filter by product
        product_code = self.request.query_params.get('product_code')
        if product_code:
            queryset = queryset.filter(product_code__product_code__icontains=product_code)
        
        # Filter by alert type
        alert_type = self.request.query_params.get('alert_type')
        if alert_type:
            queryset = queryset.filter(alert_type=alert_type)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset


class DispatchOrderViewSet(viewsets.ModelViewSet):
    """ViewSet for Dispatch Order operations"""
    permission_classes = [IsFGStoreUser]
    serializer_class = DispatchOrderSerializer
    queryset = DispatchOrder.objects.all()
    
    def get_queryset(self):
        queryset = DispatchOrder.objects.select_related(
            'mo', 'mo__product_code', 'mo__customer_c_id',
            'customer_c_id', 'created_by'
        ).order_by('-created_at')
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by customer
        customer_id = self.request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(customer_c_id__c_id=customer_id)
        
        # Filter by MO ID
        mo_id = self.request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo__mo_id__icontains=mo_id)
        
        return queryset


class FGStoreDashboardViewSet(viewsets.ViewSet):
    """ViewSet for FG Store Dashboard data"""
    permission_classes = [IsFGStoreUser]
    
    @action(detail=False, methods=['get'])
    def stock_levels(self, request):
        """Get FG Stock Level data"""
        queryset = DispatchBatch.objects.select_related(
            'mo', 'mo__customer_c_id', 'mo__product_code',
            'product_code', 'packing_supervisor'
        ).filter(
            status__in=['pending_dispatch', 'partially_dispatched']
        )
        
        # Apply filters
        product_code = request.query_params.get('product_code')
        if product_code:
            queryset = queryset.filter(product_code__product_code__icontains=product_code)
        
        customer_id = request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(mo__customer_c_id__c_id=customer_id)
        
        batch_id = request.query_params.get('batch_id')
        if batch_id:
            queryset = queryset.filter(batch_id__icontains=batch_id)
        
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Serialize data
        data = []
        for batch in queryset:
            data.append({
                'batch_id': batch.batch_id,
                'mo_id': batch.mo.mo_id,
                'product_code': batch.product_code.product_code,
                'product_name': batch.product_code.product_code,
                'quantity_in_stock': batch.quantity_available,
                'loose_stock': batch.loose_stock,
                'unit': 'pieces',  # Assuming pieces as unit
                'location': batch.location_in_store or 'FG Store',
                'customer_name': batch.mo.customer_c_id.name if batch.mo.customer_c_id else '',
                'delivery_date': batch.mo.delivery_date,
                'status': batch.status,
                'packing_date': batch.packing_date
            })
        
        serializer = FGStockLevelSerializer(data, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def pending_dispatch_mos(self, request):
        """Get MO List (Pending Dispatch) data"""
        # Get MOs that are completed but not fully dispatched
        queryset = ManufacturingOrder.objects.select_related(
            'customer_c_id', 'product_code'
        ).filter(
            status='completed'
        ).annotate(
            total_packed=Sum('dispatch_batches__quantity_packed'),
            total_dispatched=Sum('dispatch_batches__quantity_dispatched')
        ).filter(
            total_packed__gt=0  # Only MOs with packed batches
        )
        
        # Apply filters
        customer_id = request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(customer_c_id__c_id=customer_id)
        
        priority = request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        delivery_date = request.query_params.get('delivery_date')
        if delivery_date:
            queryset = queryset.filter(delivery_date=delivery_date)
        
        # Serialize data
        data = []
        for mo in queryset:
            total_packed = mo.total_packed or 0
            total_dispatched = mo.total_dispatched or 0
            pending = total_packed - total_dispatched
            dispatch_percentage = (total_dispatched / total_packed * 100) if total_packed > 0 else 0
            
            data.append({
                'mo_id': mo.mo_id,
                'customer_name': mo.customer_c_id.name if mo.customer_c_id else '',
                'customer_c_id': mo.customer_c_id.c_id if mo.customer_c_id else '',
                'product_code': mo.product_code.product_code,
                'product_name': mo.product_code.product_code,
                'quantity_ordered': mo.quantity,
                'quantity_packed': total_packed,
                'quantity_dispatched': total_dispatched,
                'quantity_pending': pending,
                'delivery_date': mo.delivery_date,
                'priority': mo.priority,
                'status': 'pending_dispatch' if pending > 0 else 'fully_dispatched',
                'dispatch_percentage': dispatch_percentage
            })
        
        serializer = MOPendingDispatchSerializer(data, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def transactions_log(self, request):
        """Get Transactions Log data"""
        queryset = DispatchTransaction.objects.select_related(
            'mo', 'mo__customer_c_id', 'mo__product_code',
            'dispatch_batch', 'customer_c_id', 'supervisor_id', 'created_by'
        ).order_by('-dispatch_date')
        
        # Apply filters
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(dispatch_date__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(dispatch_date__date__lte=date_to)
        
        mo_id = request.query_params.get('mo_id')
        if mo_id:
            queryset = queryset.filter(mo__mo_id__icontains=mo_id)
        
        batch_id = request.query_params.get('batch_id')
        if batch_id:
            queryset = queryset.filter(dispatch_batch__batch_id__icontains=batch_id)
        
        supervisor_id = request.query_params.get('supervisor_id')
        if supervisor_id:
            queryset = queryset.filter(supervisor_id=supervisor_id)
        
        # Serialize data
        data = []
        for txn in queryset:
            data.append({
                'transaction_id': txn.transaction_id,
                'mo_id': txn.mo.mo_id,
                'batch_id': txn.dispatch_batch.batch_id,
                'transaction_type': 'dispatch',
                'quantity': txn.quantity_dispatched,
                'timestamp': txn.dispatch_date,
                'user_id': txn.created_by.id if txn.created_by else None,
                'user_name': txn.created_by.full_name if txn.created_by else '',
                'supervisor_id': txn.supervisor_id.id if txn.supervisor_id else None,
                'supervisor_name': txn.supervisor_id.full_name if txn.supervisor_id else '',
                'customer_name': txn.customer_c_id.name if txn.customer_c_id else '',
                'product_name': txn.mo.product_code.product_code,
                'status': txn.status,
                'notes': txn.notes
            })
        
        serializer = DispatchTransactionLogSerializer(data, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def validate_dispatch(self, request):
        """Validate dispatch operation"""
        batch_id = request.query_params.get('batch_id')
        quantity_to_dispatch = request.query_params.get('quantity_to_dispatch')
        
        if not batch_id or not quantity_to_dispatch:
            return Response(
                {'error': 'batch_id and quantity_to_dispatch are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            batch = DispatchBatch.objects.get(batch_id=batch_id)
            quantity = int(quantity_to_dispatch)
            
            is_valid = batch.can_dispatch(quantity)
            warnings = []
            errors = []
            
            if not is_valid:
                if quantity > batch.quantity_available:
                    errors.append(f"Requested quantity ({quantity}) exceeds available quantity ({batch.quantity_available})")
                if quantity <= 0:
                    errors.append("Quantity must be greater than 0")
            
            if batch.status == 'fully_dispatched':
                warnings.append("This batch is already fully dispatched")
            
            if batch.mo.delivery_date and batch.mo.delivery_date < timezone.now().date():
                warnings.append("Delivery date has passed")
            
            data = {
                'is_valid': is_valid,
                'available_qty': batch.quantity_available,
                'warnings': warnings,
                'errors': errors
            }
            
            serializer = DispatchValidationSerializer(data)
            return Response(serializer.data)
            
        except DispatchBatch.DoesNotExist:
            return Response(
                {'error': 'Dispatch batch not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except ValueError:
            return Response(
                {'error': 'Invalid quantity format'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'])
    def loose_fg_stock(self, request):
        """Get loose FG stock for a specific product"""
        product_code = request.query_params.get('product_code')
        
        if not product_code:
            return Response(
                {'error': 'product_code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Get product
            product = Product.objects.get(product_code=product_code)
            
            # Calculate total loose stock for this product
            loose_stock_data = DispatchBatch.objects.filter(
                product_code=product,
                status__in=['pending_dispatch', 'partially_dispatched']
            ).aggregate(
                total_loose_stock=Sum('loose_stock'),
                total_quantity_available=Sum(F('quantity_packed') - F('quantity_dispatched'))
            )
            
            total_loose = loose_stock_data['total_loose_stock'] or 0
            total_available = loose_stock_data['total_quantity_available'] or 0
            
            # Get batches with loose stock
            batches_with_loose_stock = DispatchBatch.objects.filter(
                product_code=product,
                loose_stock__gt=0,
                status__in=['pending_dispatch', 'partially_dispatched']
            ).select_related('mo', 'mo__customer_c_id').values(
                'batch_id',
                'loose_stock',
                'location_in_store',
                'packing_date',
                'mo__mo_id',
                'mo__customer_c_id__name'
            )[:10]  # Limit to 10 most recent
            
            data = {
                'product_code': product_code,
                'total_loose_stock': total_loose,
                'total_available_stock': total_available,
                'batches': list(batches_with_loose_stock)
            }
            
            return Response(data)
            
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found'},
                status=status.HTTP_404_NOT_FOUND
            )