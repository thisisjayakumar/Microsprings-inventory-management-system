"""
Manufacturing Core Models

This module imports all manufacturing models from the properly organized models/ package.
All actual model definitions are in manufacturing/models/ subdirectory.

DO NOT define any models directly in this file to avoid duplicate model registration.
"""

# Import all models from the models package
from .models import (
    # Manufacturing Orders
    ManufacturingOrder,
    MOStatusHistory,
    MOTransactionHistory,
    
    # Purchase Orders
    PurchaseOrder,
    POStatusHistory,
    POTransactionHistory,
    
    # Batches
    Batch,
    
    # Process Execution
    MOProcessExecution,
    MOProcessStepExecution,
    MOProcessAlert,
    
    # Outsourcing
    OutsourcingRequest,
    OutsourcedItem,
    
    # Workflow
    MOApprovalWorkflow,
    ProcessAssignment,
    FinishedGoodsVerification,
    
    # Allocations
    BatchAllocation,
    ProcessExecutionLog,
    RawMaterialAllocation,
    RMAllocationHistory,
    
    # Additional RM
    AdditionalRMRequest,
    
    # Process Stop & Downtime
    ProcessStop,
    ProcessDowntimeSummary,
    
    # Rework Management
    BatchProcessCompletion,
    ReworkBatch,
    FinalInspectionRework,
    
    # Batch Verification
    BatchReceiptVerification,
    BatchReceiptLog,
    
    # Activity Logging
    ProcessActivityLog,
    BatchTraceabilityEvent,
    
    # MO Supervisor Configuration
    MOShiftConfiguration,
    MOSupervisorOverride,
    SupervisorChangeLog,
)

# RM Return model
from .models.rm_return import RMReturn

# Export all models for backward compatibility
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
    
    # RM Return
    'RMReturn',
]
