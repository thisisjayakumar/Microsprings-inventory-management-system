from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ManufacturingOrderViewSet, PurchaseOrderViewSet,
    MOProcessExecutionViewSet, MOProcessStepExecutionViewSet, MOProcessAlertViewSet,
    BatchViewSet, OutsourcingRequestViewSet, RawMaterialAllocationViewSet,
    # Workflow API views
    create_mo_workflow, approve_mo, allocate_rm_to_mo, assign_process_to_operator,
    reassign_process, allocate_batch_to_process, receive_batch_by_operator,
    complete_process, verify_finished_goods,
    # Heat number API
    get_available_heat_numbers_for_mo
)
from .views.batch_views import BatchProcessExecutionViewSet
from .views.additional_rm_views import AdditionalRMRequestViewSet
from .views.supervisor_views import (
    ProcessStopViewSet,
    ProcessDowntimeAnalyticsViewSet,
    BatchProcessCompletionViewSet,
    ReworkBatchViewSet,
    BatchReceiptVerificationViewSet,
    FinalInspectionReworkViewSet,
    ProcessActivityLogViewSet,
    BatchTraceabilityViewSet,
    ReworkAnalyticsViewSet
)
from .views.supervisor_config_views import (
    WorkCenterSupervisorShiftViewSet,
    DailySupervisorStatusViewSet,
    MOSupervisorConfigViewSet,
    SupervisorChangeLogViewSet,
    SupervisorReportViewSet
)

# Create router and register viewsets
router = DefaultRouter()
router.register(r'manufacturing-orders', ManufacturingOrderViewSet, basename='manufacturingorder')
router.register(r'purchase-orders', PurchaseOrderViewSet, basename='purchaseorder')
router.register(r'process-executions', MOProcessExecutionViewSet, basename='processexecution')
router.register(r'step-executions', MOProcessStepExecutionViewSet, basename='stepexecution')
router.register(r'process-alerts', MOProcessAlertViewSet, basename='processalert')
router.register(r'batches', BatchViewSet, basename='batch')
router.register(r'batch-process-executions', BatchProcessExecutionViewSet, basename='batchprocessexecution')
router.register(r'outsourcing', OutsourcingRequestViewSet, basename='outsourcingrequest')
router.register(r'rm-allocations', RawMaterialAllocationViewSet, basename='rmallocation')
router.register(r'additional-rm-requests', AdditionalRMRequestViewSet, basename='additionalrmrequest')

# Supervisor Process Management ViewSets
router.register(r'process-stops', ProcessStopViewSet, basename='process-stop')
router.register(r'downtime-analytics', ProcessDowntimeAnalyticsViewSet, basename='downtime-analytics')
router.register(r'batch-completions', BatchProcessCompletionViewSet, basename='batch-completion')
router.register(r'rework-batches', ReworkBatchViewSet, basename='rework-batch')
router.register(r'batch-receipts', BatchReceiptVerificationViewSet, basename='batch-receipt')
router.register(r'fi-reworks', FinalInspectionReworkViewSet, basename='fi-rework')
router.register(r'activity-logs', ProcessActivityLogViewSet, basename='activity-log')
router.register(r'batch-traceability', BatchTraceabilityViewSet, basename='batch-traceability')
router.register(r'rework-analytics', ReworkAnalyticsViewSet, basename='rework-analytics')

# Supervisor Configuration ViewSets
router.register(r'supervisor-shifts', WorkCenterSupervisorShiftViewSet, basename='supervisor-shift')
router.register(r'daily-supervisor-status', DailySupervisorStatusViewSet, basename='daily-supervisor-status')
router.register(r'mo-supervisor-config', MOSupervisorConfigViewSet, basename='mo-supervisor-config')
router.register(r'supervisor-change-logs', SupervisorChangeLogViewSet, basename='supervisor-change-log')
router.register(r'supervisor-reports', SupervisorReportViewSet, basename='supervisor-report')

app_name = 'manufacturing'

urlpatterns = [
    path('', include(router.urls)),
    
    # Enhanced Workflow API endpoints
    path('workflow/create-mo/', create_mo_workflow, name='create_mo_workflow'),
    path('workflow/approve-mo/', approve_mo, name='approve_mo'),
    path('workflow/allocate-rm/', allocate_rm_to_mo, name='allocate_rm_to_mo'),
    path('workflow/assign-process/', assign_process_to_operator, name='assign_process_to_operator'),
    path('workflow/reassign-process/', reassign_process, name='reassign_process'),
    path('workflow/allocate-batch/', allocate_batch_to_process, name='allocate_batch_to_process'),
    path('workflow/receive-batch/', receive_batch_by_operator, name='receive_batch_by_operator'),
    path('workflow/complete-process/', complete_process, name='complete_process'),
    path('workflow/verify-fg/', verify_finished_goods, name='verify_finished_goods'),
    
    # Heat number management
    path('manufacturing-orders/<int:mo_id>/available-heat-numbers/', get_available_heat_numbers_for_mo, name='get_available_heat_numbers_for_mo'),
]