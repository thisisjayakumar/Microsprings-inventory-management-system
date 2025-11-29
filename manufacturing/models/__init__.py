"""
Manufacturing Models Package
Organized model imports for better maintainability
"""

# Core models
from .manufacturing_order import (
    ManufacturingOrder,
    MOStatusHistory,
    MOTransactionHistory
)
from .purchase_order import (
    PurchaseOrder,
    POStatusHistory,
    POTransactionHistory
)
from .batch import Batch
from .process_execution import (
    MOProcessExecution,
    MOProcessStepExecution,
    MOProcessAlert
)
from .outsourcing import (
    OutsourcingRequest,
    OutsourcedItem
)
from .workflow import (
    MOApprovalWorkflow,
    ProcessAssignment,
    FinishedGoodsVerification
)
from .allocations import (
    BatchAllocation,
    ProcessExecutionLog,
    RawMaterialAllocation,
    RMAllocationHistory
)
from .additional_rm import AdditionalRMRequest
from .process_stop import (
    ProcessStop,
    ProcessDowntimeSummary
)
from .rework import (
    BatchProcessCompletion,
    ReworkBatch,
    FinalInspectionRework
)
from .batch_verification import (
    BatchReceiptVerification,
    BatchReceiptLog
)
from .activity_log import (
    ProcessActivityLog,
    BatchTraceabilityEvent
)
from .mo_supervisor_config import (
    MOShiftConfiguration,
    MOSupervisorOverride,
    SupervisorChangeLog
)

__all__ = [
    # Manufacturing Orders
    'ManufacturingOrder',
    'MOStatusHistory',
    'MOTransactionHistory',
    
    # Purchase Orders
    'PurchaseOrder',
    'POStatusHistory',
    'POTransactionHistory',
    
    # Batches
    'Batch',
    
    # Process Execution
    'MOProcessExecution',
    'MOProcessStepExecution',
    'MOProcessAlert',
    
    # Outsourcing
    'OutsourcingRequest',
    'OutsourcedItem',
    
    # Workflow
    'MOApprovalWorkflow',
    'ProcessAssignment',
    'FinishedGoodsVerification',
    
    # Allocations
    'BatchAllocation',
    'ProcessExecutionLog',
    'RawMaterialAllocation',
    'RMAllocationHistory',
    
    # Additional RM
    'AdditionalRMRequest',
    
    # Process Stop & Downtime
    'ProcessStop',
    'ProcessDowntimeSummary',
    
    # Rework Management
    'BatchProcessCompletion',
    'ReworkBatch',
    'FinalInspectionRework',
    
    # Batch Verification
    'BatchReceiptVerification',
    'BatchReceiptLog',
    
    # Activity Logging
    'ProcessActivityLog',
    'BatchTraceabilityEvent',
    
    # MO Supervisor Configuration
    'MOShiftConfiguration',
    'MOSupervisorOverride',
    'SupervisorChangeLog',
]

