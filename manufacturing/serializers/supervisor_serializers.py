"""
Serializers for Supervisor Process Management Features
Handles stop/resume, rework, verification, and FI operations
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model

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
    MOProcessExecution
)
from processes.models import Process

User = get_user_model()


# ============================================
# Process Stop/Resume Serializers
# ============================================

class ProcessStopSerializer(serializers.ModelSerializer):
    """Serializer for ProcessStop with nested details"""
    stopped_by_name = serializers.CharField(source='stopped_by.get_full_name', read_only=True)
    resumed_by_name = serializers.CharField(source='resumed_by.get_full_name', read_only=True)
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    process_name = serializers.CharField(source='process_execution.process.name', read_only=True)
    stop_duration_display = serializers.CharField(read_only=True)
    current_downtime_minutes = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = ProcessStop
        fields = [
            'id', 'batch', 'batch_id', 'mo', 'mo_id', 'process_execution', 
            'process_name', 'stopped_by', 'stopped_by_name', 'stop_reason',
            'stop_reason_detail', 'stopped_at', 'is_resumed', 'resumed_by',
            'resumed_by_name', 'resumed_at', 'resume_notes', 'downtime_minutes',
            'current_downtime_minutes', 'stop_duration_display',
            'notification_sent_to_ph', 'notification_sent_to_manager',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'stopped_at', 'is_resumed', 'resumed_at', 'downtime_minutes',
            'created_at', 'updated_at'
        ]


class ProcessStopCreateSerializer(serializers.Serializer):
    """Serializer for creating process stop"""
    batch_id = serializers.IntegerField(required=False, allow_null=True)
    process_execution_id = serializers.IntegerField()
    stop_reason = serializers.ChoiceField(choices=ProcessStop._meta.get_field('stop_reason').choices)
    stop_reason_detail = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        # Validate process execution first
        process_execution_id = data.get('process_execution_id')
        if process_execution_id is None:
            raise serializers.ValidationError({'process_execution_id': 'process_execution_id is required'})
        
        try:
            process_execution = MOProcessExecution.objects.select_related('mo', 'process').get(id=process_execution_id)
            data['process_execution'] = process_execution
        except MOProcessExecution.DoesNotExist:
            raise serializers.ValidationError({
                'process_execution_id': f'Process execution with id {process_execution_id} not found. Please verify it exists.'
            })
        except (ValueError, TypeError) as e:
            raise serializers.ValidationError({
                'process_execution_id': f'Invalid process_execution_id format: {process_execution_id}. Expected an integer.'
            })
        
        # Get all active batches for this process's MO
        # Active batches are those that are allocated but not cancelled or completed
        # This includes: created, in_process, in_progress (not yet completed)
        mo = process_execution.mo
        active_batches = mo.batches.exclude(status__in=['cancelled', 'completed', 'returned_to_rm'])
        
        # If batch_id is provided, validate it exists
        batch_id = data.get('batch_id')
        if batch_id is not None:
            try:
                # Get the batch - no need to validate MO relationship
                # Because all batches for an MO can be stopped via any process execution for that MO
                batch = Batch.objects.get(id=batch_id)
                
                # Validate batch is in active_batches (belongs to the same MO)
                if batch not in active_batches:
                    raise serializers.ValidationError({
                        'batch_id': f'Batch {batch_id} (Batch ID: {batch.batch_id}) does not belong to MO {mo.mo_id}. This process execution is for MO {mo.mo_id}.'
                    })
                
                # Use the specified batch
                data['batches'] = [batch]
            except Batch.DoesNotExist:
                # If batch doesn't exist, fall back to stopping all active batches
                # This handles cases where batch was deleted or invalid ID was provided
                if not active_batches.exists():
                    raise serializers.ValidationError({
                        'batch_id': f'Batch with id {batch_id} not found, and no active batches found for MO {mo.mo_id}.'
                    })
                # Use all active batches instead
                data['batches'] = list(active_batches)
                # Optionally add a warning (for logging)
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f'Batch {batch_id} not found for process_execution {process_execution_id}. Stopping all active batches for MO {mo.mo_id} instead.')
            except (ValueError, TypeError) as e:
                raise serializers.ValidationError({
                    'batch_id': f'Invalid batch_id format: {batch_id}. Expected an integer.'
                })
        else:
            # No batch_id provided - stop process for all active batches
            if not active_batches.exists():
                raise serializers.ValidationError({
                    'batch_id': f'No active batches found for MO {mo.mo_id}. Cannot stop process without active batches.'
                })
            data['batches'] = list(active_batches)
        
        return data
        
        # Check if already stopped
        if process_execution.status == 'stopped':
            raise serializers.ValidationError('Process is already stopped')
        
        # Check for active stop
        active_stop = ProcessStop.objects.filter(
            batch=batch,
            process_execution=process_execution,
            is_resumed=False
        ).first()
        
        if active_stop:
            raise serializers.ValidationError('Process already has an active stop')
        
        return data


class ProcessResumeSerializer(serializers.Serializer):
    """Serializer for resuming process"""
    process_stop_id = serializers.IntegerField()
    resume_notes = serializers.CharField(required=False, allow_blank=True)


class ProcessDowntimeSummarySerializer(serializers.ModelSerializer):
    """Serializer for downtime summaries"""
    process_name = serializers.CharField(source='process.name', read_only=True)
    
    class Meta:
        model = ProcessDowntimeSummary
        fields = [
            'id', 'date', 'process', 'process_name', 'total_stops',
            'total_downtime_minutes', 'breakdown_machine', 'breakdown_power',
            'breakdown_maintenance', 'breakdown_material', 'breakdown_quality',
            'breakdown_others', 'last_updated'
        ]
        read_only_fields = ['id', 'last_updated']


# ============================================
# Rework Management Serializers
# ============================================

class BatchProcessCompletionSerializer(serializers.ModelSerializer):
    """Serializer for batch process completion with quantities"""
    completed_by_name = serializers.CharField(source='completed_by.get_full_name', read_only=True)
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    process_name = serializers.CharField(source='process_execution.process.name', read_only=True)
    ok_percentage = serializers.FloatField(read_only=True)
    scrap_percentage = serializers.FloatField(read_only=True)
    rework_percentage = serializers.FloatField(read_only=True)
    rework_badge = serializers.CharField(read_only=True)
    
    class Meta:
        model = BatchProcessCompletion
        fields = [
            'id', 'batch', 'batch_id', 'process_execution', 'process_name',
            'completed_by', 'completed_by_name', 'completed_at',
            'input_quantity_kg', 'ok_quantity_kg', 'scrap_quantity_kg',
            'rework_quantity_kg', 'is_rework_cycle', 'rework_cycle_number',
            'parent_completion', 'completion_notes', 'defect_description',
            'ok_percentage', 'scrap_percentage', 'rework_percentage',
            'rework_badge', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'completed_at', 'ok_percentage', 'scrap_percentage',
            'rework_percentage', 'rework_badge', 'created_at', 'updated_at'
        ]


class BatchProcessCompletionCreateSerializer(serializers.Serializer):
    """Serializer for creating batch completion with OK/Scrap/Rework"""
    batch_id = serializers.IntegerField()
    process_execution_id = serializers.IntegerField()
    input_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    ok_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    scrap_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    rework_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    completion_notes = serializers.CharField(required=False, allow_blank=True)
    defect_description = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        from decimal import Decimal
        
        # Validate quantities are non-negative
        for field in ['input_quantity_kg', 'ok_quantity_kg', 'scrap_quantity_kg', 'rework_quantity_kg']:
            if data[field] < 0:
                raise serializers.ValidationError({field: 'Cannot be negative'})
        
        # Validate total matches input
        total = data['ok_quantity_kg'] + data['scrap_quantity_kg'] + data['rework_quantity_kg']
        tolerance = Decimal('0.01')
        
        if abs(total - data['input_quantity_kg']) > tolerance:
            raise serializers.ValidationError(
                f"OK + Scrap + Rework ({total} kg) must equal Input ({data['input_quantity_kg']} kg)"
            )
        
        return data


class ReworkBatchSerializer(serializers.ModelSerializer):
    """Serializer for rework batches"""
    original_batch_id = serializers.CharField(source='original_batch.batch_id', read_only=True)
    process_name = serializers.CharField(source='process_execution.process.name', read_only=True)
    assigned_supervisor_name = serializers.CharField(source='assigned_supervisor.get_full_name', read_only=True)
    defect_process_name = serializers.CharField(source='defect_process.name', read_only=True, allow_null=True)
    
    class Meta:
        model = ReworkBatch
        fields = [
            'id', 'original_batch', 'original_batch_id', 'process_execution',
            'process_name', 'completion_record', 'rework_quantity_kg',
            'status', 'source', 'assigned_supervisor', 'assigned_supervisor_name',
            'created_at', 'started_at', 'completed_at', 'rework_cycle_number',
            'defect_description', 'defect_process', 'defect_process_name',
            'supervisor_notes'
        ]
        read_only_fields = ['id', 'created_at', 'started_at', 'completed_at']


# ============================================
# Batch Receipt Verification Serializers
# ============================================

class BatchReceiptVerificationSerializer(serializers.ModelSerializer):
    """Serializer for batch receipt verification"""
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True)
    hold_cleared_by_name = serializers.CharField(source='hold_cleared_by.get_full_name', read_only=True, allow_null=True)
    resolved_by_name = serializers.CharField(source='resolved_by.get_full_name', read_only=True, allow_null=True)
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    process_name = serializers.CharField(source='process_execution.process.name', read_only=True)
    previous_process_name = serializers.CharField(source='previous_process.name', read_only=True, allow_null=True)
    quantity_variance_kg = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    quantity_variance_percentage = serializers.FloatField(read_only=True)
    
    class Meta:
        model = BatchReceiptVerification
        fields = [
            'id', 'batch', 'batch_id', 'process_execution', 'process_name',
            'previous_process', 'previous_process_name', 'received_by',
            'received_by_name', 'received_at', 'action', 'expected_quantity_kg',
            'actual_quantity_kg', 'report_reason', 'report_details',
            'is_on_hold', 'hold_cleared_at', 'hold_cleared_by',
            'hold_cleared_by_name', 'clearance_notes', 'is_resolved',
            'resolved_at', 'resolved_by', 'resolved_by_name', 'resolution_notes',
            'quantity_variance_kg', 'quantity_variance_percentage',
            'notification_sent_to_ph', 'notification_sent_to_prev_supervisor',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'received_at', 'is_on_hold', 'hold_cleared_at',
            'is_resolved', 'resolved_at', 'created_at', 'updated_at'
        ]


class BatchReceiptVerifySerializer(serializers.Serializer):
    """Serializer for verifying batch receipt - OK"""
    batch_id = serializers.IntegerField()
    process_execution_id = serializers.IntegerField()
    expected_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)


class BatchReceiptReportSerializer(serializers.Serializer):
    """Serializer for reporting batch receipt issues"""
    batch_id = serializers.IntegerField()
    process_execution_id = serializers.IntegerField()
    expected_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    actual_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    report_reason = serializers.ChoiceField(choices=BatchReceiptVerification._meta.get_field('report_reason').choices)
    report_details = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        # If reason is low/high qty, actual_quantity_kg is required
        if data['report_reason'] in ['low_qty', 'high_qty']:
            if not data.get('actual_quantity_kg'):
                raise serializers.ValidationError({
                    'actual_quantity_kg': 'Required when reporting quantity issues'
                })
        
        return data


class BatchReceiptLogSerializer(serializers.ModelSerializer):
    """Serializer for batch receipt logs"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    from_process_name = serializers.CharField(source='from_process.name', read_only=True, allow_null=True)
    to_process_name = serializers.CharField(source='to_process.name', read_only=True)
    handed_over_by_name = serializers.CharField(source='handed_over_by.get_full_name', read_only=True, allow_null=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = BatchReceiptLog
        fields = [
            'id', 'batch', 'batch_id', 'mo', 'from_process', 'from_process_name',
            'to_process', 'to_process_name', 'handed_over_by', 'handed_over_by_name',
            'handed_over_at', 'received_by', 'received_by_name', 'received_at',
            'handed_over_quantity_kg', 'received_quantity_kg', 'verification_record',
            'is_verified', 'has_issues', 'transit_duration_minutes',
            'handover_notes', 'receipt_notes', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'is_verified', 'has_issues', 'transit_duration_minutes',
            'created_at', 'updated_at'
        ]


# ============================================
# Final Inspection Rework Serializers
# ============================================

class FinalInspectionReworkSerializer(serializers.ModelSerializer):
    """Serializer for FI rework assignments"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    inspected_by_name = serializers.CharField(source='inspected_by.get_full_name', read_only=True)
    defective_process_name = serializers.CharField(source='defective_process.name', read_only=True)
    assigned_to_supervisor_name = serializers.CharField(source='assigned_to_supervisor.get_full_name', read_only=True)
    reinspected_by_name = serializers.CharField(source='reinspected_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = FinalInspectionRework
        fields = [
            'id', 'batch', 'batch_id', 'mo', 'mo_id', 'inspected_by',
            'inspected_by_name', 'inspected_at', 'defective_process',
            'defective_process_name', 'defect_description',
            'rework_quantity_kg', 'assigned_to_supervisor',
            'assigned_to_supervisor_name', 'status', 'rework_started_at',
            'rework_completed_at', 'reinspected_at', 'reinspected_by',
            'reinspected_by_name', 'reinspection_passed', 'reinspection_notes',
            'rework_cycle_count', 'fi_notes', 'supervisor_notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'inspected_at', 'status', 'rework_started_at',
            'rework_completed_at', 'reinspected_at', 'created_at', 'updated_at'
        ]


class FinalInspectionReworkCreateSerializer(serializers.Serializer):
    """Serializer for creating FI rework assignment"""
    batch_id = serializers.IntegerField()
    defective_process_id = serializers.IntegerField()
    defect_description = serializers.CharField()
    rework_quantity_kg = serializers.DecimalField(max_digits=10, decimal_places=2)
    fi_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_rework_quantity_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError("Rework quantity must be greater than 0")
        return value


# ============================================
# Activity Log Serializers
# ============================================

class ProcessActivityLogSerializer(serializers.ModelSerializer):
    """Serializer for process activity logs"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True, allow_null=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    process_name = serializers.CharField(source='process.name', read_only=True, allow_null=True)
    performed_by_name = serializers.CharField(source='performed_by.get_full_name', read_only=True)
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    
    class Meta:
        model = ProcessActivityLog
        fields = [
            'id', 'batch', 'batch_id', 'mo', 'mo_id', 'process',
            'process_name', 'process_execution', 'activity_type',
            'activity_type_display', 'performed_by', 'performed_by_name',
            'performed_at', 'ok_quantity_kg', 'scrap_quantity_kg',
            'rework_quantity_kg', 'reason', 'remarks', 'metadata',
            'created_at'
        ]
        read_only_fields = ['id', 'performed_at', 'created_at']


class BatchTraceabilityEventSerializer(serializers.ModelSerializer):
    """Serializer for batch traceability timeline"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    
    class Meta:
        model = BatchTraceabilityEvent
        fields = [
            'id', 'batch', 'batch_id', 'mo', 'mo_id', 'event_type',
            'event_description', 'timestamp', 'process_name',
            'supervisor_name', 'ok_kg', 'scrap_kg', 'rework_kg',
            'rework_cycle', 'is_on_hold', 'metadata'
        ]
        read_only_fields = ['id']

