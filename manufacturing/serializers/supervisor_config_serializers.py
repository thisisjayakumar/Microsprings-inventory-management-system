"""
Serializers for Supervisor Configuration Models
"""
from rest_framework import serializers
from manufacturing.models import (
    MOShiftConfiguration,
    MOSupervisorOverride,
    SupervisorChangeLog
)
from processes.models import (
    WorkCenterSupervisorShift,
    DailySupervisorStatus
)


class WorkCenterSupervisorShiftSerializer(serializers.ModelSerializer):
    """Serializer for work center supervisor shift assignments"""
    work_center_name = serializers.CharField(source='work_center.name', read_only=True)
    primary_supervisor_name = serializers.CharField(source='primary_supervisor.get_full_name', read_only=True)
    backup_supervisor_name = serializers.CharField(source='backup_supervisor.get_full_name', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    
    class Meta:
        model = WorkCenterSupervisorShift
        fields = [
            'id', 'work_center', 'work_center_name', 'shift', 'shift_display',
            'shift_start_time', 'shift_end_time',
            'primary_supervisor', 'primary_supervisor_name',
            'backup_supervisor', 'backup_supervisor_name',
            'check_in_deadline', 'is_active',
            'created_at', 'created_by', 'updated_at', 'updated_by'
        ]
        read_only_fields = ['created_at', 'created_by', 'updated_at', 'updated_by']


class WorkCenterSupervisorShiftCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating work center supervisor shift assignments"""
    
    class Meta:
        model = WorkCenterSupervisorShift
        fields = [
            'work_center', 'shift', 'shift_start_time', 'shift_end_time',
            'primary_supervisor', 'backup_supervisor', 'check_in_deadline', 'is_active'
        ]
    
    def validate(self, data):
        """Validate supervisor assignments"""
        if data['primary_supervisor'] == data['backup_supervisor']:
            raise serializers.ValidationError(
                "Primary and backup supervisors must be different users"
            )
        return data


class DailySupervisorStatusSerializer(serializers.ModelSerializer):
    """Serializer for daily supervisor status"""
    work_center_name = serializers.CharField(source='work_center.name', read_only=True)
    default_supervisor_name = serializers.CharField(source='default_supervisor.get_full_name', read_only=True)
    active_supervisor_name = serializers.CharField(source='active_supervisor.get_full_name', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    status_color = serializers.CharField(read_only=True)
    
    class Meta:
        model = DailySupervisorStatus
        fields = [
            'id', 'date', 'work_center', 'work_center_name', 'shift', 'shift_display',
            'default_supervisor', 'default_supervisor_name',
            'active_supervisor', 'active_supervisor_name',
            'is_present', 'login_time', 'check_in_deadline',
            'manually_updated', 'manually_updated_by', 'manually_updated_at',
            'manual_update_reason', 'status_color',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at', 'status_color']


class MOShiftConfigurationSerializer(serializers.ModelSerializer):
    """Serializer for MO shift configuration"""
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    
    class Meta:
        model = MOShiftConfiguration
        fields = [
            'id', 'mo', 'mo_id', 'shift', 'shift_display',
            'shift_start_time', 'shift_end_time', 'is_active',
            'created_at', 'created_by', 'updated_at'
        ]
        read_only_fields = ['created_at', 'created_by', 'updated_at']


class MOShiftConfigurationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating MO shift configuration"""
    
    class Meta:
        model = MOShiftConfiguration
        fields = ['mo', 'shift', 'shift_start_time', 'shift_end_time', 'is_active']


class MOSupervisorOverrideSerializer(serializers.ModelSerializer):
    """Serializer for MO supervisor override"""
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    process_name = serializers.CharField(source='process.name', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    primary_supervisor_name = serializers.CharField(source='primary_supervisor.get_full_name', read_only=True)
    backup_supervisor_name = serializers.CharField(source='backup_supervisor.get_full_name', read_only=True)
    
    class Meta:
        model = MOSupervisorOverride
        fields = [
            'id', 'mo', 'mo_id', 'process', 'process_name', 'shift', 'shift_display',
            'primary_supervisor', 'primary_supervisor_name',
            'backup_supervisor', 'backup_supervisor_name',
            'override_reason', 'is_active',
            'created_at', 'created_by', 'updated_at', 'updated_by'
        ]
        read_only_fields = ['created_at', 'created_by', 'updated_at', 'updated_by']


class MOSupervisorOverrideCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating MO supervisor override"""
    
    class Meta:
        model = MOSupervisorOverride
        fields = [
            'mo', 'process', 'shift',
            'primary_supervisor', 'backup_supervisor',
            'override_reason', 'is_active'
        ]
    
    def validate(self, data):
        """Validate supervisor assignments"""
        if data['primary_supervisor'] == data['backup_supervisor']:
            raise serializers.ValidationError(
                "Primary and backup supervisors must be different users"
            )
        return data


class SupervisorChangeLogSerializer(serializers.ModelSerializer):
    """Serializer for supervisor change log"""
    mo_id = serializers.CharField(source='mo_process_execution.mo.mo_id', read_only=True)
    process_name = serializers.CharField(source='mo_process_execution.process.name', read_only=True)
    from_supervisor_name = serializers.SerializerMethodField()
    to_supervisor_name = serializers.CharField(source='to_supervisor.get_full_name', read_only=True)
    changed_by_name = serializers.SerializerMethodField()
    change_reason_display = serializers.CharField(source='get_change_reason_display', read_only=True)
    shift_display = serializers.CharField(source='get_shift_display', read_only=True)
    
    class Meta:
        model = SupervisorChangeLog
        fields = [
            'id', 'mo_process_execution', 'mo_id', 'process_name',
            'from_supervisor', 'from_supervisor_name',
            'to_supervisor', 'to_supervisor_name',
            'change_reason', 'change_reason_display', 'change_notes',
            'shift', 'shift_display', 'changed_at', 'changed_by', 'changed_by_name',
            'process_status_at_change'
        ]
        read_only_fields = ['changed_at']
    
    def get_from_supervisor_name(self, obj):
        if obj.from_supervisor:
            return obj.from_supervisor.get_full_name()
        return 'Unassigned'
    
    def get_changed_by_name(self, obj):
        if obj.changed_by:
            return obj.changed_by.get_full_name()
        return 'System'


class SupervisorAssignmentReportSerializer(serializers.Serializer):
    """Serializer for supervisor assignment reports"""
    mo_id = serializers.CharField()
    process_name = serializers.CharField()
    supervisor_id = serializers.IntegerField(allow_null=True)
    supervisor_name = serializers.CharField()
    status = serializers.CharField()
    shift = serializers.CharField()
    start_time = serializers.DateTimeField(allow_null=True)
    end_time = serializers.DateTimeField(allow_null=True)
    duration_minutes = serializers.IntegerField(allow_null=True)
    date = serializers.DateField(allow_null=True)
    total_mos_handled = serializers.IntegerField()
    total_operations = serializers.IntegerField()

