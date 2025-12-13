from django.db import models
from django.utils.translation import gettext_lazy as _


# ============================================================================
# AUTHENTICATION & USER CHOICES
# ============================================================================

class DepartmentChoices(models.TextChoices):
    RM_STORE = 'rm_store', _('RM Store')
    COILING = 'coiling', _('Coiling')
    TEMPERING = 'tempering', _('Tempering')
    PLATING = 'plating', _('Plating')
    PACKING = 'packing', _('Packing')
    FG_STORE = 'fg_store', _('FG Store')
    QUALITY = 'quality', _('Quality Control')
    MAINTENANCE = 'maintenance', _('Maintenance')
    ADMIN = 'admin', _('Administration')


class ShiftChoices(models.TextChoices):
    SHIFT_I = 'I', _('9AM-5PM (Shift I)')
    SHIFT_II = 'II', _('5PM-2AM (Shift II)')
    SHIFT_III = 'III', _('2AM-9AM (Shift III)')


class RoleHierarchyChoices(models.TextChoices):
    ADMIN = 'admin', _('Admin')
    MANAGER = 'manager', _('Manager')
    PRODUCTION_HEAD = 'production_head', _('Production Head')
    SUPERVISOR = 'supervisor', _('Supervisor')
    RM_STORE = 'rm_store', _('RM Store')
    FG_STORE = 'fg_store', _('FG Store')
    PATROL = 'patrol', _('Patrol')


# ============================================================================
# RAW MATERIAL & INVENTORY CHOICES
# ============================================================================

class MaterialTypeChoices(models.TextChoices):
    COIL = 'coil', _('Coil')
    SHEET = 'sheet', _('Sheet')


class FinishingChoices(models.TextChoices):
    SOAP_COATED = 'soap_coated', _('Soap Coated')
    BRIGHT = 'bright', _('BRIGHT')


class LocationTypeChoices(models.TextChoices):
    RM_STORE = 'rm_store', _('Raw Material Store')
    COILING = 'coiling', _('Coiling')
    FORMING = 'forming', _('Forming')
    TEMPERING = 'tempering', _('Tempering')
    COATING = 'coating', _('Coating')
    BLANKING = 'blanking', _('Blanking')
    PIERCING = 'piercing', _('Piercing')
    DEBURRING = 'deburring', _('Deburring')
    IRONING = 'ironing', _('Ironing')
    CHAMPERING = 'champering', _('Champering')
    BENDING = 'bending', _('Bending')
    PLATING = 'plating', _('Plating')
    BLUE_COATING = 'blue_coating', _('Blue Coating')
    BUSH_ASSEMBLY = 'bush_assembly', _('Bush Assembly')
    RIVETING = 'riveting', _('Riveting')
    REMAR = 'remar', _('Remar')
    BRASS_WELDING = 'brass_welding', _('Brass Welding')
    GRINDING_BUFFING = 'grinding_buffing', _('Grinding & Buffing')
    BLACKING = 'blacking', _('Blacking')
    PHOSPHATING = 'phosphating', _('Phosphating')
    FINAL_INSPECTION = 'final_inspection', _('Final Inspection')
    PACKING_ZONE = 'packing_zone', _('Packing Zone')
    FG = 'fg', _('FG Store')
    DISPATCHED = 'dispatched', _('Dispatched')


class TransactionTypeChoices(models.TextChoices):
    INWARD = 'inward', _('Inward Receipt')
    OUTWARD = 'outward', _('Outward Issue')
    TRANSFER = 'transfer', _('Location Transfer')
    ADJUSTMENT = 'adjustment', _('Stock Adjustment')
    CONSUMPTION = 'consumption', _('Process Consumption')
    PRODUCTION = 'production', _('Process Output')
    SCRAP = 'scrap', _('Scrap Generation')
    RETURN = 'return', _('Return to Stock')


class ReferenceTypeChoices(models.TextChoices):
    MO = 'mo', _('Manufacturing Order')
    PO = 'po', _('Purchase Order')
    PROCESS = 'process', _('Process Execution')
    ADJUSTMENT = 'adjustment', _('Stock Adjustment')
    RM_RETURN = 'rm_return', _('RM Return to Vendor')


class GRNStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending Receipt')
    PARTIAL = 'partial', _('Partially Received')
    COMPLETED = 'completed', _('Completed')
    CANCELLED = 'cancelled', _('Cancelled')


class HandoverStatusChoices(models.TextChoices):
    PENDING_HANDOVER = 'pending_handover', _('Pending Handover')
    VERIFIED = 'verified', _('Verified')
    ISSUE_REPORTED = 'issue_reported', _('Issue Reported')


class HandoverIssueTypeChoices(models.TextChoices):
    INCORRECT_WEIGHT = 'incorrect_weight', _('Incorrect Weight')
    DAMAGED_MATERIAL = 'damaged_material', _('Damaged Material')
    WRONG_MATERIAL = 'wrong_material', _('Wrong Material')


class RMReturnReasonChoices(models.TextChoices):
    RECEIVED_WRONG_RM = 'received_wrong_rm', _('Received Wrong RM')
    ENOUGH_QTY_REACHED = 'enough_qty_reached', _('Enough Qty Reached')
    DEFECT_IN_QUALITY = 'defect_in_quality', _('Defect in Quality')


class RMReturnDispositionChoices(models.TextChoices):
    PENDING = 'pending', _('Pending Action')
    RETURN_TO_RM = 'return_to_rm', _('Return to RM')
    RETURN_TO_VENDOR = 'return_to_vendor', _('Return to Vendor')
    SCRAP = 'scrap', _('Scrap')


# ============================================================================
# MANUFACTURING & PRODUCTION CHOICES
# ============================================================================

class MOStatusChoices(models.TextChoices):
    SUBMITTED = 'submitted', _('Submitted')
    RM_ALLOCATED = 'rm_allocated', _('RM Allocated')
    MO_APPROVED = 'mo_approved', _('MO Approved')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    CANCELLED = 'cancelled', _('Cancelled')
    STOPPED = 'stopped', _('Stopped')
    REJECTED = 'rejected', _('Rejected')
    ON_HOLD = 'on_hold', _('On Hold')


class PriorityChoices(models.TextChoices):
    LOW = 'low', _('Low')
    MEDIUM = 'medium', _('Medium')
    HIGH = 'high', _('High')
    URGENT = 'urgent', _('Urgent')


class POStatusChoices(models.TextChoices):
    PO_INITIATED = 'po_initiated', _('Purchase Order Initiated')
    PO_APPROVED = 'po_approved', _('Approved by GM')
    PO_CANCELLED = 'po_cancelled', _('Cancelled')
    RM_IN_PROGRESS = 'rm_in_progress', _('RM In Progress')
    RM_COMPLETED = 'rm_completed', _('Goods Receipt Completed')


class ExecutionStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    ON_HOLD = 'on_hold', _('On Hold')
    FAILED = 'failed', _('Failed')
    SKIPPED = 'skipped', _('Skipped')


class StepStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    FAILED = 'failed', _('Failed')
    SKIPPED = 'skipped', _('Skipped')


class QualityStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    PASSED = 'passed', _('Passed')
    FAILED = 'failed', _('Failed')
    REWORK_REQUIRED = 'rework_required', _('Rework Required')


class AlertTypeChoices(models.TextChoices):
    DELAY = 'delay', _('Process Delay')
    QUALITY_ISSUE = 'quality_issue', _('Quality Issue')
    EQUIPMENT_FAILURE = 'equipment_failure', _('Equipment Failure')
    MATERIAL_SHORTAGE = 'material_shortage', _('Material Shortage')
    OPERATOR_ISSUE = 'operator_issue', _('Operator Issue')
    CUSTOM = 'custom', _('Custom Alert')


class SeverityChoices(models.TextChoices):
    LOW = 'low', _('Low')
    MEDIUM = 'medium', _('Medium')
    HIGH = 'high', _('High')
    CRITICAL = 'critical', _('Critical')


class BatchStatusChoices(models.TextChoices):
    CREATED = 'created', _('Created')
    RM_ALLOCATED = 'rm_allocated', _('Raw Material Allocated')
    IN_PROCESS = 'in_process', _('In Process')
    QUALITY_CHECK = 'quality_check', _('Quality Check')
    COMPLETED = 'completed', _('Completed')
    PACKED = 'packed', _('Packed')
    DISPATCHED = 'dispatched', _('Dispatched')
    CANCELLED = 'cancelled', _('Cancelled')
    RETURNED_TO_RM = 'returned_to_rm', _('Returned to RM Store')


class OutsourcingStatusChoices(models.TextChoices):
    DRAFT = 'draft', _('Draft')
    SENT = 'sent', _('Sent')
    RETURNED = 'returned', _('Returned')
    CLOSED = 'closed', _('Closed')


class MOApprovalWorkflowStatusChoices(models.TextChoices):
    PENDING_MANAGER_APPROVAL = 'pending_manager_approval', _('Pending Manager Approval')
    MANAGER_APPROVED = 'manager_approved', _('Manager Approved')
    MANAGER_REJECTED = 'manager_rejected', _('Manager Rejected')
    RM_ALLOCATION_PENDING = 'rm_allocation_pending', _('RM Allocation Pending')
    RM_ALLOCATED = 'rm_allocated', _('RM Allocated')
    READY_FOR_PRODUCTION = 'ready_for_production', _('Ready for Production')


class ProcessAssignmentStatusChoices(models.TextChoices):
    ASSIGNED = 'assigned', _('Assigned')
    ACCEPTED = 'accepted', _('Accepted by Operator')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    REASSIGNED = 'reassigned', _('Reassigned')
    CANCELLED = 'cancelled', _('Cancelled')


class BatchAllocationStatusChoices(models.TextChoices):
    ALLOCATED = 'allocated', _('Allocated')
    IN_TRANSIT = 'in_transit', _('In Transit')
    RECEIVED = 'received', _('Received by Process')
    IN_PROCESS = 'in_process', _('In Process')
    COMPLETED = 'completed', _('Completed')
    RETURNED = 'returned', _('Returned to RM Store')


class ProcessExecutionActionChoices(models.TextChoices):
    STARTED = 'started', _('Process Started')
    PAUSED = 'paused', _('Process Paused')
    RESUMED = 'resumed', _('Process Resumed')
    COMPLETED = 'completed', _('Process Completed')
    QUALITY_CHECK = 'quality_check', _('Quality Check')
    ISSUE_REPORTED = 'issue_reported', _('Issue Reported')
    MATERIAL_REQUESTED = 'material_requested', _('Material Requested')
    TOOL_CHANGE = 'tool_change', _('Tool Change')
    MAINTENANCE = 'maintenance', _('Maintenance')


class FGVerificationStatusChoices(models.TextChoices):
    PENDING_VERIFICATION = 'pending_verification', _('Pending Verification')
    QUALITY_CHECK_PASSED = 'quality_check_passed', _('Quality Check Passed')
    QUALITY_CHECK_FAILED = 'quality_check_failed', _('Quality Check Failed')
    APPROVED_FOR_DISPATCH = 'approved_for_dispatch', _('Approved for Dispatch')
    DISPATCHED = 'dispatched', _('Dispatched')


class RMAllocationStatusChoices(models.TextChoices):
    RESERVED = 'reserved', _('Reserved')
    SWAPPED = 'swapped', _('Swapped to Higher Priority MO')
    LOCKED = 'locked', _('Locked (MO Approved)')
    RELEASED = 'released', _('Released Back to Stock')


class FGReservationTypeChoices(models.TextChoices):
    BATCH_RESERVE = 'batch_reserve', _('Batch Reserve')
    CUSTOMER_RESERVE = 'customer_reserve', _('Customer Reserve')
    MO_RESERVE = 'mo_reserve', _('MO Reserve')


# ============================================================================
# DISPATCH & FG STORE CHOICES
# ============================================================================

class DispatchBatchStatusChoices(models.TextChoices):
    PENDING_DISPATCH = 'pending_dispatch', _('Pending Dispatch')
    PARTIALLY_DISPATCHED = 'partially_dispatched', _('Partially Dispatched')
    FULLY_DISPATCHED = 'fully_dispatched', _('Fully Dispatched')
    CANCELLED = 'cancelled', _('Cancelled')


class DispatchTransactionStatusChoices(models.TextChoices):
    PENDING_CONFIRMATION = 'pending_confirmation', _('Pending Confirmation')
    CONFIRMED = 'confirmed', _('Confirmed')
    RECEIVED = 'received', _('Received by Customer')
    CANCELLED = 'cancelled', _('Cancelled')


class FGStockAlertTypeChoices(models.TextChoices):
    LOW_STOCK = 'low_stock', _('Low Stock')
    EXPIRING = 'expiring', _('Expiring Batch')
    OVERSTOCK = 'overstock', _('Overstock')
    CUSTOM = 'custom', _('Custom Alert')


class DispatchOrderStatusChoices(models.TextChoices):
    DRAFT = 'draft', _('Draft')
    PENDING_CONFIRMATION = 'pending_confirmation', _('Pending Confirmation')
    CONFIRMED = 'confirmed', _('Confirmed')
    PARTIALLY_DISPATCHED = 'partially_dispatched', _('Partially Dispatched')
    FULLY_DISPATCHED = 'fully_dispatched', _('Fully Dispatched')
    CANCELLED = 'cancelled', _('Cancelled')


# ============================================================================
# LOGISTICS & PACKING CHOICES
# ============================================================================

class PackedItemStatusChoices(models.TextChoices):
    PACKED = 'packed', _('Packed')
    IN_FG_STORE = 'in_fg_store', _('In FG Store')
    DISPATCHED = 'dispatched', _('Dispatched')


# ============================================================================
# NOTIFICATION CHOICES
# ============================================================================

class NotificationAlertTypeChoices(models.TextChoices):
    LOW_STOCK = 'low_stock', _('Low Stock')
    DELAY = 'delay', _('Process Delay')
    QUALITY_FAIL = 'quality_fail', _('Quality Failure')
    MACHINE_DOWN = 'machine_down', _('Machine Breakdown')
    CUSTOM = 'custom', _('Custom Rule')


class AlertStatusChoices(models.TextChoices):
    ACTIVE = 'active', _('Active')
    ACKNOWLEDGED = 'acknowledged', _('Acknowledged')
    RESOLVED = 'resolved', _('Resolved')
    DISMISSED = 'dismissed', _('Dismissed')


class DeliveryStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending')
    SENT = 'sent', _('Sent')
    DELIVERED = 'delivered', _('Delivered')
    FAILED = 'failed', _('Failed')


class WorkflowNotificationTypeChoices(models.TextChoices):
    MO_CREATED = 'mo_created', _('MO Created')
    MO_APPROVED = 'mo_approved', _('MO Approved')
    MO_REJECTED = 'mo_rejected', _('MO Rejected')
    RM_ALLOCATION_REQUIRED = 'rm_allocation_required', _('RM Allocation Required')
    RM_ALLOCATED = 'rm_allocated', _('RM Allocated')
    PROCESS_ASSIGNED = 'process_assigned', _('Process Assigned')
    PROCESS_REASSIGNED = 'process_reassigned', _('Process Reassigned')
    SUPERVISOR_ASSIGNED = 'supervisor_assigned', _('Supervisor Assigned')
    BATCH_ALLOCATED = 'batch_allocated', _('Batch Allocated')
    BATCH_RECEIVED = 'batch_received', _('Batch Received')
    PROCESS_STARTED = 'process_started', _('Process Started')
    PROCESS_COMPLETED = 'process_completed', _('Process Completed')
    QUALITY_CHECK_REQUIRED = 'quality_check_required', _('Quality Check Required')
    FG_VERIFICATION_REQUIRED = 'fg_verification_required', _('FG Verification Required')
    READY_FOR_DISPATCH = 'ready_for_dispatch', _('Ready for Dispatch')
    PATROL_DUTY_ASSIGNED = 'patrol_duty_assigned', _('Patrol Duty Assigned')
    PATROL_DUTY_ENDING_SOON = 'patrol_duty_ending_soon', _('Patrol Duty Ending Soon')


# ============================================================================
# PRODUCT & PROCESS CHOICES
# ============================================================================

class ProductTypeChoices(models.TextChoices):
    SPRING = 'spring', _('Spring')
    PRESS_COMPONENT = 'press_component', _('PRESS COMPONENT')
    STAMP = 'stamp', _('Stamp')  # For BOM model


class SpringTypeChoices(models.TextChoices):
    TENSION = 'tension', _('TENSION SPRING')
    WIRE_FORM = 'wire_form', _('WIRE FORM SPRING')
    COMPRESSION = 'compression', _('COMPRESSION SPRING')
    TORSION = 'torsion', _('TORSION SPRING')
    CLIP = 'clip', _('CLIP')
    RIVET = 'rivet', _('RIVET')
    HELICAL = 'helical', _('HELICAL SPRING')
    LENGTH_PIN = 'length_pin', _('LENGTH PIN')
    LENGTH_ROD = 'length_rod', _('LENGTH ROD')
    DOUBLE_TORSION = 'double_torsion', _('DOUBLE TORSION SPRING')
    COTTER_PIN = 'cotter_pin', _('COTTER PIN')
    CONICAL = 'conical', _('CONICAL SPRING')
    RING = 'ring', _('RING')
    S_SPRING = 's-spring', _('S-SPRING')


# ============================================================================
# QUALITY CHOICES
# ============================================================================

class QualityResultChoices(models.TextChoices):
    PASS = 'pass', _('Pass')
    FAIL = 'fail', _('Fail')
    REWORK = 'rework', _('Rework')


# ============================================================================
# REPORTING CHOICES
# ============================================================================

class ReportTypeChoices(models.TextChoices):
    PRODUCTION = 'production', _('Production Report')
    INVENTORY = 'inventory', _('Inventory Report')
    QUALITY = 'quality', _('Quality Report')
    EFFICIENCY = 'efficiency', _('Efficiency Report')


class ScheduleTypeChoices(models.TextChoices):
    DAILY = 'daily', _('Daily')
    WEEKLY = 'weekly', _('Weekly')
    MONTHLY = 'monthly', _('Monthly')
    CUSTOM = 'custom', _('Custom Cron')


# ============================================================================
# RESOURCE & MACHINE CHOICES
# ============================================================================

class MachineStatusChoices(models.TextChoices):
    AVAILABLE = 'available', _('Available')
    OCCUPIED = 'occupied', _('Occupied')
    MAINTENANCE = 'maintenance', _('Under Maintenance')
    BREAKDOWN = 'breakdown', _('Breakdown')


class MachineScheduleStatusChoices(models.TextChoices):
    SCHEDULED = 'scheduled', _('Scheduled')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    CANCELLED = 'cancelled', _('Cancelled')


# ============================================================================
# THIRD PARTY (VENDOR & CUSTOMER) CHOICES
# ============================================================================

class VendorTypeChoices(models.TextChoices):
    RM_VENDOR = 'rm_vendor', _('RM Vendor')
    OUTSOURCE_VENDOR = 'outsource_vendor', _('Outsource Vendor')


# ============================================================================
# PATROL MANAGEMENT CHOICES
# ============================================================================

class PatrolDutyStatusChoices(models.TextChoices):
    ACTIVE = 'active', _('Active')
    COMPLETED = 'completed', _('Completed')
    CANCELLED = 'cancelled', _('Cancelled')


class PatrolUploadStatusChoices(models.TextChoices):
    PENDING = 'pending', _('Pending Upload')
    SUBMITTED = 'submitted', _('Submitted')
    MISSED = 'missed', _('Missed')
    REUPLOADED = 'reuploaded', _('Reuploaded')


class IndustryTypeChoices(models.TextChoices):
    BRAKE_INDUSTRY = 'brake_industry', _('Brake Industry')
    AUTOMOTIVE = 'automotive', _('Automotive')
    TEMPERATURE_SENSOR = 'temperature_sensor', _('Temperature Sensor')
    INSTRUMENTS = 'instruments', _('Instruments')
    THERMAL_CERAMICS = 'thermal_ceramics', _('Thermal Ceramics')
    ELECTRIC_LOCO_SHED = 'electric_loco_shed', _('Electric Loco Shed')
    SEATING_SYSTEM = 'seating_system', _('Seating System')
    HARNESS = 'harness', _('Harness')
    TECHNOLOGY_SERVICES = 'technology_services', _('Technology & Services')
    TECHNOLOGY = 'technology', _('Technology')
    MOTOR_ELECTRONICS = 'motor_electronics', _('Motor Electronics')
    SPRINGS = 'springs', _('Springs')
    OTHER = 'other', _('Other')

