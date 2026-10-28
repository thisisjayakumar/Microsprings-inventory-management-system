from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid
from utils.enums import (
    DispatchBatchStatusChoices, DispatchTransactionStatusChoices,
    FGStockAlertTypeChoices, SeverityChoices, DispatchOrderStatusChoices,
    FGReservationTypeChoices
)

User = get_user_model()


class DispatchBatch(models.Model):
    """
    Dispatch Batch - Represents finished goods batches ready for dispatch
    Links to manufacturing batches and tracks dispatch quantities
    """
    
    # Auto-generated unique identifier
    batch_id = models.CharField(max_length=30, unique=True, editable=False)
    
    # Relationships
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder', 
        on_delete=models.CASCADE, 
        related_name='dispatch_batches',
        help_text="Parent Manufacturing Order"
    )
    
    # Link to production batch
    production_batch = models.ForeignKey(
        'manufacturing.Batch',
        on_delete=models.CASCADE,
        related_name='dispatch_batch',
        help_text="Production batch this dispatch batch is based on"
    )
    
    # Product details (for easy access)
    product_code = models.ForeignKey(
        'products.Product',
        on_delete=models.PROTECT,
        related_name='dispatch_batches',
        help_text="Product being dispatched"
    )
    
    # Quantities
    quantity_produced = models.PositiveIntegerField(
        help_text="Total quantity produced in this batch"
    )
    quantity_packed = models.PositiveIntegerField(
        default=0,
        help_text="Quantity packed and ready for dispatch"
    )
    quantity_dispatched = models.PositiveIntegerField(
        default=0,
        help_text="Quantity already dispatched"
    )
    loose_stock = models.PositiveIntegerField(
        default=0,
        help_text="Individual loose items (not in packs)"
    )
    
    # Status and tracking
    status = models.CharField(
        max_length=20, 
        choices=DispatchBatchStatusChoices.choices, 
        default='pending_dispatch'
    )
    
    # Location and packing details
    location_in_store = models.CharField(
        max_length=100,
        blank=True,
        help_text="Physical location in FG store"
    )
    packing_date = models.DateTimeField(
        null=True, blank=True,
        help_text="Date when batch was packed"
    )
    packing_supervisor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='packed_batches',
        help_text="Supervisor who packed this batch"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_dispatch_batches'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Dispatch Batch'
        verbose_name_plural = 'Dispatch Batches'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo', 'status']),
            models.Index(fields=['product_code', 'status']),
            models.Index(fields=['batch_id']),
            models.Index(fields=['status']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate batch_id if not set
        if not self.batch_id:
            # Format: DISPATCH-BATCH-MO_ID-SEQUENCE
            # Example: DISPATCH-BATCH-MO-20250927-0001-001
            existing_batches = DispatchBatch.objects.filter(
                mo=self.mo
            ).count()
            sequence = existing_batches + 1
            self.batch_id = f"DISPATCH-BATCH-{self.mo.mo_id}-{sequence:03d}"
        
        # Validate product_code matches MO product
        if self.mo and self.product_code:
            if self.mo.product_code != self.product_code:
                raise ValueError(
                    f"Dispatch batch product_code ({self.product_code}) must match "
                    f"MO product_code ({self.mo.product_code})"
                )
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.batch_id} - {self.product_code.product_code} (Qty: {self.quantity_available})"
    
    @property
    def quantity_available(self):
        """Calculate available quantity for dispatch"""
        return self.quantity_packed - self.quantity_dispatched
    
    @property
    def dispatch_percentage(self):
        """Calculate dispatch completion percentage"""
        if self.quantity_packed and self.quantity_packed > 0:
            return (self.quantity_dispatched / self.quantity_packed) * 100
        return 0
    
    def can_dispatch(self, quantity):
        """Check if specified quantity can be dispatched"""
        return quantity <= self.quantity_available and quantity > 0
    
    def update_status(self):
        """Update batch status based on dispatch quantities"""
        if self.quantity_dispatched == 0:
            self.status = 'pending_dispatch'
        elif self.quantity_dispatched >= self.quantity_packed:
            self.status = 'fully_dispatched'
        else:
            self.status = 'partially_dispatched'
        self.save(update_fields=['status', 'updated_at'])


class DispatchTransaction(models.Model):
    """
    Dispatch Transaction - Records all dispatch operations
    Provides complete audit trail for FG dispatch operations
    """
    # Auto-generated transaction ID
    transaction_id = models.CharField(max_length=30, unique=True, editable=False)
    
    # Relationships
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.PROTECT,
        related_name='dispatch_transactions',
        help_text="Manufacturing Order being dispatched"
    )
    dispatch_batch = models.ForeignKey(
        DispatchBatch,
        on_delete=models.PROTECT,
        related_name='dispatch_transactions',
        help_text="Dispatch batch being dispatched"
    )
    customer_c_id = models.ForeignKey(
        'third_party.Customer',
        to_field='c_id',
        on_delete=models.PROTECT,
        related_name='dispatch_transactions',
        help_text="Customer receiving the dispatch"
    )
    
    # Dispatch details
    quantity_dispatched = models.PositiveIntegerField(
        help_text="Quantity dispatched in this transaction"
    )
    dispatch_date = models.DateTimeField(
        auto_now_add=True,
        help_text="Date and time of dispatch"
    )
    supervisor_id = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='supervised_dispatches',
        help_text="Supervisor who confirmed the dispatch"
    )
    
    # Status and tracking
    status = models.CharField(
        max_length=20,
        choices=DispatchTransactionStatusChoices.choices,
        default='pending_confirmation'
    )
    
    # Additional information
    notes = models.TextField(
        blank=True,
        help_text="Additional notes or special instructions"
    )
    delivery_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Delivery reference number or tracking ID"
    )
    
    # Confirmation details
    confirmed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When dispatch was confirmed"
    )
    received_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When customer received the dispatch"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_dispatch_transactions'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Dispatch Transaction'
        verbose_name_plural = 'Dispatch Transactions'
        ordering = ['-dispatch_date']
        indexes = [
            models.Index(fields=['mo', 'status']),
            models.Index(fields=['dispatch_batch', 'status']),
            models.Index(fields=['customer_c_id', 'dispatch_date']),
            models.Index(fields=['supervisor_id', 'dispatch_date']),
            models.Index(fields=['transaction_id']),
            models.Index(fields=['dispatch_date']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate transaction_id if not set
        if not self.transaction_id:
            # Format: DISPATCH-TXN-YYYYMMDD-XXXX
            today = timezone.now().strftime('%Y%m%d')
            last_txn = DispatchTransaction.objects.filter(
                transaction_id__startswith=f'DISPATCH-TXN-{today}'
            ).order_by('transaction_id').last()
            
            if last_txn:
                last_sequence = int(last_txn.transaction_id.split('-')[-1])
                sequence = last_sequence + 1
            else:
                sequence = 1
            
            self.transaction_id = f'DISPATCH-TXN-{today}-{sequence:04d}'
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.transaction_id} - {self.mo.mo_id} (Qty: {self.quantity_dispatched})"
    
    def confirm_dispatch(self, confirmed_by_user):
        """Confirm the dispatch transaction"""
        if self.status != 'pending_confirmation':
            raise ValidationError("Only pending dispatches can be confirmed")
        
        self.status = 'confirmed'
        self.confirmed_at = timezone.now()
        self.supervisor_id = confirmed_by_user
        self.save()
        
        # Update dispatch batch quantities
        self.dispatch_batch.quantity_dispatched += self.quantity_dispatched
        self.dispatch_batch.save(update_fields=['quantity_dispatched', 'updated_at'])
        self.dispatch_batch.update_status()
        
        # Update MO status if all batches are dispatched
        self._update_mo_status()
    
    def _update_mo_status(self):
        """Update MO status based on dispatch completion"""
        mo = self.mo
        
        # Check if all dispatch batches for this MO are fully dispatched
        all_batches_dispatched = DispatchBatch.objects.filter(
            mo=mo,
            status__in=['fully_dispatched']
        ).count()
        
        total_batches = DispatchBatch.objects.filter(mo=mo).count()
        
        if total_batches > 0 and all_batches_dispatched == total_batches:
            mo.status = 'dispatched'
            mo.save(update_fields=['status', 'updated_at'])


class FGStockAlert(models.Model):
    """
    FG Stock Alert - Proactive notifications for stock levels
    """
    # Alert details
    product_code = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='stock_alerts',
        help_text="Product for this alert"
    )
    alert_type = models.CharField(
        max_length=20,
        choices=FGStockAlertTypeChoices.choices,
        help_text="Type of alert"
    )
    severity = models.CharField(
        max_length=10,
        choices=SeverityChoices.choices,
        default='medium'
    )
    
    # Alert conditions
    min_stock_level = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Minimum stock level threshold"
    )
    max_stock_level = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum stock level threshold"
    )
    expiry_days_threshold = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Days before expiry to trigger alert"
    )
    
    # Alert status
    is_active = models.BooleanField(
        default=True,
        help_text="Is this alert rule active"
    )
    last_triggered = models.DateTimeField(
        null=True, blank=True,
        help_text="When this alert was last triggered"
    )
    last_alerted = models.DateTimeField(
        null=True, blank=True,
        help_text="When notification was last sent"
    )
    
    # Additional info
    description = models.TextField(
        blank=True,
        help_text="Description of this alert rule"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_stock_alerts'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'FG Stock Alert'
        verbose_name_plural = 'FG Stock Alerts'
        ordering = ['product_code', 'alert_type']
        indexes = [
            models.Index(fields=['product_code', 'is_active']),
            models.Index(fields=['alert_type', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.product_code.product_code} - {self.get_alert_type_display()}"
    
    def check_and_trigger(self):
        """Check if alert conditions are met and trigger if needed"""
        if not self.is_active:
            return False
        
        # Get current stock level for this product
        current_stock = self._get_current_stock_level()
        
        triggered = False
        
        if self.alert_type == 'low_stock' and self.min_stock_level:
            if current_stock <= self.min_stock_level:
                triggered = True
        
        elif self.alert_type == 'overstock' and self.max_stock_level:
            if current_stock >= self.max_stock_level:
                triggered = True
        
        elif self.alert_type == 'expiring' and self.expiry_days_threshold:
            # Check for batches expiring within threshold
            from django.utils import timezone
            from datetime import timedelta
            
            expiry_date = timezone.now().date() + timedelta(days=self.expiry_days_threshold)
            expiring_batches = DispatchBatch.objects.filter(
                product_code=self.product_code,
                status='pending_dispatch',
                packing_date__lte=expiry_date
            ).exists()
            
            if expiring_batches:
                triggered = True
        
        if triggered:
            self.last_triggered = timezone.now()
            self.save(update_fields=['last_triggered'])
        
        return triggered
    
    def _get_current_stock_level(self):
        """Get current stock level for this product"""
        total_stock = DispatchBatch.objects.filter(
            product_code=self.product_code,
            status__in=['pending_dispatch', 'partially_dispatched']
        ).aggregate(
            total=models.Sum('quantity_available')
        )['total'] or 0
        
        return total_stock


class DispatchOrder(models.Model):
    """
    Dispatch Order - Groups multiple dispatch transactions for a single MO
    Provides a higher-level view of dispatch operations
    """
    # Auto-generated order ID
    order_id = models.CharField(max_length=30, unique=True, editable=False)
    
    # Relationships
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.PROTECT,
        related_name='fg_dispatch_orders',
        help_text="Manufacturing Order for this dispatch"
    )
    customer_c_id = models.ForeignKey(
        'third_party.Customer',
        to_field='c_id',
        on_delete=models.PROTECT,
        related_name='dispatch_orders',
        help_text="Customer for this dispatch"
    )
    
    # Dispatch details
    total_quantity_ordered = models.PositiveIntegerField(
        help_text="Total quantity ordered in MO"
    )
    total_quantity_dispatched = models.PositiveIntegerField(
        default=0,
        help_text="Total quantity dispatched so far"
    )
    dispatch_date = models.DateTimeField(
        null=True, blank=True,
        help_text="Planned dispatch date"
    )
    
    # Status and tracking
    status = models.CharField(
        max_length=20,
        choices=DispatchOrderStatusChoices.choices,
        default='draft'
    )
    
    # Additional information
    special_instructions = models.TextField(
        blank=True,
        help_text="Special instructions for dispatch"
    )
    delivery_address = models.TextField(
        blank=True,
        help_text="Delivery address (if different from customer default)"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_dispatch_orders'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Dispatch Order'
        verbose_name_plural = 'Dispatch Orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo', 'status']),
            models.Index(fields=['customer_c_id', 'dispatch_date']),
            models.Index(fields=['order_id']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate order_id if not set
        if not self.order_id:
            # Format: DISPATCH-ORD-YYYYMMDD-XXXX
            today = timezone.now().strftime('%Y%m%d')
            last_order = DispatchOrder.objects.filter(
                order_id__startswith=f'DISPATCH-ORD-{today}'
            ).order_by('order_id').last()
            
            if last_order:
                last_sequence = int(last_order.order_id.split('-')[-1])
                sequence = last_sequence + 1
            else:
                sequence = 1
            
            self.order_id = f'DISPATCH-ORD-{today}-{sequence:04d}'
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.order_id} - {self.mo.mo_id} ({self.get_status_display()})"
    
    @property
    def dispatch_percentage(self):
        """Calculate dispatch completion percentage"""
        if self.total_quantity_ordered and self.total_quantity_ordered > 0:
            return (self.total_quantity_dispatched / self.total_quantity_ordered) * 100
        return 0
    
    @property
    def remaining_quantity(self):
        """Calculate remaining quantity to dispatch"""
        return self.total_quantity_ordered - self.total_quantity_dispatched
    
    def update_status(self):
        """Update order status based on dispatch progress"""
        if self.total_quantity_dispatched == 0:
            self.status = 'draft'
        elif self.total_quantity_dispatched >= self.total_quantity_ordered:
            self.status = 'fully_dispatched'
        elif self.total_quantity_dispatched > 0:
            self.status = 'partially_dispatched'
        
        self.save(update_fields=['status', 'updated_at'])


class FGStockReservation(models.Model):
    """
    FG Stock Reservation - Track reservations of finished goods for MOs
    Used for priority-based resource allocation
    """
    # Auto-generated reservation ID
    reservation_id = models.CharField(max_length=30, unique=True, editable=False)
    
    # Relationships
    mo = models.ForeignKey(
        'manufacturing.ManufacturingOrder',
        on_delete=models.CASCADE,
        related_name='fg_reservations',
        help_text="Manufacturing Order this reservation is for"
    )
    product_code = models.ForeignKey(
        'products.Product',
        on_delete=models.PROTECT,
        related_name='fg_reservations',
        help_text="Product being reserved"
    )
    
    # Reservation details
    quantity = models.PositiveIntegerField(
        help_text="Quantity of FG reserved"
    )
    reservation_type = models.CharField(
        max_length=20,
        choices=FGReservationTypeChoices.choices,
        default='mo_reserve',
        help_text="Type of reservation"
    )
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=[('reserved', 'Reserved'), ('allocated', 'Allocated'), ('released', 'Released')],
        default='reserved',
        help_text="Reservation status"
    )
    
    # Timestamps
    reserved_at = models.DateTimeField(auto_now_add=True)
    reserved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fg_reservations_created'
    )
    allocated_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    
    # Additional information
    notes = models.TextField(blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'FG Stock Reservation'
        verbose_name_plural = 'FG Stock Reservations'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['mo', 'status']),
            models.Index(fields=['product_code', 'status']),
            models.Index(fields=['reservation_id']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate reservation_id if not set
        if not self.reservation_id:
            # Format: FG-RES-YYYYMMDD-XXXX
            today = timezone.now().strftime('%Y%m%d')
            last_res = FGStockReservation.objects.filter(
                reservation_id__startswith=f'FG-RES-{today}'
            ).order_by('reservation_id').last()
            
            if last_res:
                last_sequence = int(last_res.reservation_id.split('-')[-1])
                sequence = last_sequence + 1
            else:
                sequence = 1
            
            self.reservation_id = f'FG-RES-{today}-{sequence:04d}'
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.reservation_id} - {self.mo.mo_id} ({self.quantity} units)"
    
    def release_reservation(self):
        """Release this reservation back to available stock"""
        if self.status == 'released':
            raise ValidationError("Reservation already released")
        
        self.status = 'released'
        self.released_at = timezone.now()
        self.save(update_fields=['status', 'released_at', 'updated_at'])
    
    def allocate_reservation(self):
        """Mark this reservation as allocated (actually used)"""
        if self.status != 'reserved':
            raise ValidationError("Can only allocate reserved reservations")
        
        self.status = 'allocated'
        self.allocated_at = timezone.now()
        self.save(update_fields=['status', 'allocated_at', 'updated_at'])
