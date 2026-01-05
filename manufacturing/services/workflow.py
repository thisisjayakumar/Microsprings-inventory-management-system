"""
Manufacturing Workflow Service
Handles the complete MO workflow from creation to finished goods dispatch
"""

from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from decimal import Decimal
import logging

from manufacturing.models import (
    ManufacturingOrder, MOApprovalWorkflow, ProcessAssignment, 
    BatchAllocation, ProcessExecutionLog, FinishedGoodsVerification,
    Batch, MOProcessExecution
)
from notifications.models import WorkflowNotification, NotificationTemplate
from inventory.models import HeatNumber

User = get_user_model()


class ManufacturingWorkflowService:
    """
    Central service for managing the complete manufacturing workflow
    """
    
    @staticmethod
    def create_mo_workflow(mo_id, created_by):
        """
        Create MO and initialize approval workflow
        """
        try:
            mo = ManufacturingOrder.objects.get(id=mo_id)
            
            # Create approval workflow
            workflow, created = MOApprovalWorkflow.objects.get_or_create(
                mo=mo,
                defaults={
                    'status': 'pending_manager_approval'
                }
            )
            
            # Send notification to managers
            ManufacturingWorkflowService._send_mo_created_notification(mo, created_by)
            
            return workflow
            
        except ManufacturingOrder.DoesNotExist:
            raise ValidationError("Manufacturing Order not found")
    
    @staticmethod
    def approve_mo(mo_id, manager_user, approval_notes=""):
        """
        Manager approves MO and moves to RM allocation phase
        Reserve RM and reduce stock balance when MO is approved
        """
        from manufacturing.services.rm_allocation import RMAllocationService
        from manufacturing.models import RawMaterialAllocation
        from inventory.models import RMStockBalance, RMStockBalanceHeat
        
        logger = logging.getLogger(__name__)
        
        with transaction.atomic():
            try:
                mo = ManufacturingOrder.objects.get(id=mo_id)
                
                # Ensure RM requirements are calculated before approval
                if not mo.rm_required_kg or mo.rm_required_kg == 0:
                    logger.warning(f"[RM_RESERVATION] MO {mo.mo_id} - rm_required_kg not calculated. Calculating now...")
                    mo.calculate_rm_requirements()
                    mo.save()
                
                # Try to get workflow, but don't fail if it doesn't exist
                try:
                    workflow = mo.approval_workflow
                    if workflow.status != 'pending_manager_approval':
                        logger.warning(f"[RM_RESERVATION] MO {mo.mo_id} - Workflow status is {workflow.status}, not pending_manager_approval. Continuing anyway.")
                    
                    # Update workflow
                    workflow.status = 'manager_approved'
                    workflow.manager_approved_at = timezone.now()
                    workflow.manager_approved_by = manager_user
                    workflow.manager_approval_notes = approval_notes
                    workflow.save()
                except AttributeError:
                    # Workflow doesn't exist - create it or continue without it
                    logger.warning(f"[RM_RESERVATION] MO {mo.mo_id} - No approval workflow found. Creating one...")
                    from manufacturing.models import MOApprovalWorkflow
                    workflow, _ = MOApprovalWorkflow.objects.get_or_create(
                        mo=mo,
                        defaults={
                            'status': 'manager_approved',
                            'manager_approved_at': timezone.now(),
                            'manager_approved_by': manager_user,
                            'manager_approval_notes': approval_notes
                        }
                    )
                
                # Update MO status
                mo.status = 'mo_approved'
                mo.gm_approved_at = timezone.now()
                mo.gm_approved_by = manager_user
                mo.save()
                
                # Reserve RM and reduce stock balance when MO is approved
                try:
                    # Check if RM allocations already exist
                    existing_allocations = RawMaterialAllocation.objects.filter(mo=mo, status__in=['reserved', 'locked'])
                    
                    logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - Workflow approval check: existing_allocations={existing_allocations.count()}, rm_required_kg={mo.rm_required_kg}, rm_pending_from_po={mo.rm_pending_from_po}, pending_po_quantity_kg={mo.pending_po_quantity_kg}")
                    
                    if not existing_allocations.exists() and mo.rm_required_kg and mo.rm_required_kg > 0:
                        logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - Approving MO via workflow. Reserving RM...")
                        
                        # Check if RM is pending from PO
                        if mo.rm_pending_from_po and mo.pending_po_quantity_kg:
                            # Only reserve available portion (excluding pending PO quantity)
                            available_rm_kg = mo.rm_required_kg - mo.pending_po_quantity_kg
                            logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - RM pending from PO. Reserving available portion: {available_rm_kg}kg")
                        else:
                            # Reserve full required quantity
                            available_rm_kg = mo.rm_required_kg
                            logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - No pending PO. Reserving full required quantity: {available_rm_kg}kg")
                        
                        # Reserve RM if quantity is positive
                        if available_rm_kg > 0:
                            reservation_result = RMAllocationService.allocate_rm_for_mo(mo, manager_user)
                            logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - RM allocated as RESERVED: {len(reservation_result) if reservation_result else 0}")
                            
                            if reservation_result:
                                # Decrement stock balance for reserved qty
                                raw_material = mo.product_code.material
                                if raw_material:
                                    total_reserved_kg = sum(a.allocated_quantity_kg for a in reservation_result)
                                    
                                    # Update RMStockBalanceHeat (primary model used for stock checks)
                                    stock_balance_heat, _ = RMStockBalanceHeat.objects.get_or_create(
                                        raw_material=raw_material,
                                        defaults={'total_available_quantity_kg': Decimal('0')}
                                    )
                                    stock_balance_heat.total_available_quantity_kg -= Decimal(str(total_reserved_kg))
                                    stock_balance_heat.save()
                                    logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - Deducted {total_reserved_kg}kg from RMStockBalanceHeat for material {raw_material.material_code}")
                                    
                                    # Also update legacy RMStockBalance for backward compatibility
                                    stock_balance, _ = RMStockBalance.objects.get_or_create(
                                        raw_material=raw_material,
                                        defaults={'available_quantity': Decimal('0')}
                                    )
                                    stock_balance.available_quantity -= Decimal(str(total_reserved_kg))
                                    stock_balance.save()
                                    logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - Deducted {total_reserved_kg}kg from RMStockBalance for material {raw_material.material_code}")
                    else:
                        if existing_allocations.exists():
                            logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - RM already reserved. Existing allocations: {existing_allocations.count()}")
                        else:
                            logger.info(f"[RM_RESERVATION] MO {mo.mo_id} - No RM required (rm_required_kg = {mo.rm_required_kg})")
                except Exception as alloc_error:
                    logger.error(f"[RM_RESERVATION] MO {mo.mo_id} - Failed to reserve RM during workflow approval: {str(alloc_error)}")
                    logger.exception(alloc_error)
                    # Don't fail MO approval if RM reservation fails - just log it
                
                # Send notifications
                ManufacturingWorkflowService._send_mo_approved_notification(mo, manager_user)
                ManufacturingWorkflowService._send_rm_allocation_required_notification(mo)
                
                return workflow
                
            except ManufacturingOrder.DoesNotExist:
                raise ValidationError("Manufacturing Order not found")
    
    @staticmethod
    def allocate_rm_to_mo(mo_id, rm_store_user, allocation_notes=""):
        """
        RM Store allocates raw materials to MO
        """
        with transaction.atomic():
            try:
                mo = ManufacturingOrder.objects.get(id=mo_id)
                workflow = mo.approval_workflow
                
                if workflow.status != 'manager_approved':
                    raise ValidationError("MO must be approved by manager first")
                
                # Update workflow
                workflow.status = 'rm_allocated'
                workflow.rm_store_allocated_at = timezone.now()
                workflow.rm_store_allocated_by = rm_store_user
                workflow.rm_allocation_notes = allocation_notes
                workflow.save()
                
                # Update MO status
                mo.status = 'rm_allocated'
                mo.rm_allocated_at = timezone.now()
                mo.rm_allocated_by = rm_store_user
                mo.save()
                
                # Send notification to Production Head
                ManufacturingWorkflowService._send_rm_allocated_notification(mo, rm_store_user)
                
                return workflow
                
            except ManufacturingOrder.DoesNotExist:
                raise ValidationError("Manufacturing Order not found")
    
    @staticmethod
    def assign_process_to_operator(mo_process_execution_id, operator_user, production_head_user, supervisor_user=None):
        """
        Production Head assigns process to operator
        """
        with transaction.atomic():
            try:
                mo_process_execution = MOProcessExecution.objects.get(id=mo_process_execution_id)
                
                # Create process assignment
                assignment = ProcessAssignment.objects.create(
                    mo_process_execution=mo_process_execution,
                    assigned_operator=operator_user,
                    assigned_supervisor=supervisor_user,
                    assigned_by=production_head_user,
                    status='assigned'
                )
                
                # Update MO process execution
                mo_process_execution.assigned_operator = operator_user
                mo_process_execution.assigned_supervisor = supervisor_user
                mo_process_execution.save()
                
                # Send notification to operator
                ManufacturingWorkflowService._send_process_assigned_notification(assignment)
                
                return assignment
                
            except MOProcessExecution.DoesNotExist:
                raise ValidationError("MO Process Execution not found")
    
    @staticmethod
    def reassign_process(assignment_id, new_operator_user, production_head_user, reassignment_reason=""):
        """
        Production Head reassigns process to different operator
        """
        with transaction.atomic():
            try:
                assignment = ProcessAssignment.objects.get(id=assignment_id)
                
                # Store previous operator
                previous_operator = assignment.assigned_operator
                
                # Update assignment
                assignment.previous_operator = previous_operator
                assignment.assigned_operator = new_operator_user
                assignment.reassigned_at = timezone.now()
                assignment.reassignment_reason = reassignment_reason
                assignment.status = 'reassigned'
                assignment.save()
                
                # Update MO process execution
                assignment.mo_process_execution.assigned_operator = new_operator_user
                assignment.mo_process_execution.save()
                
                # Send notifications
                ManufacturingWorkflowService._send_process_reassigned_notification(assignment, previous_operator)
                
                return assignment
                
            except ProcessAssignment.DoesNotExist:
                raise ValidationError("Process Assignment not found")
    
    @staticmethod
    def allocate_batch_to_process(batch_id, process_id, operator_user, rm_store_user, heat_numbers=None):
        """
        RM Store allocates batch to specific process
        """
        with transaction.atomic():
            try:
                batch = Batch.objects.get(id=batch_id)
                process = Process.objects.get(id=process_id)
                
                # Create batch allocation
                allocation = BatchAllocation.objects.create(
                    batch=batch,
                    allocated_to_process=process,
                    allocated_to_operator=operator_user,
                    allocated_by=rm_store_user,
                    status='allocated',
                    current_location='RM Store'
                )
                
                # Add heat numbers if provided
                if heat_numbers:
                    allocation.heat_numbers.set(heat_numbers)
                
                # Update batch status
                batch.status = 'rm_allocated'
                batch.save()
                
                # Send notification to operator
                ManufacturingWorkflowService._send_batch_allocated_notification(allocation)
                
                return allocation
                
            except Batch.DoesNotExist:
                raise ValidationError("Batch not found")
            except Process.DoesNotExist:
                raise ValidationError("Process not found")
    
    @staticmethod
    def receive_batch_by_operator(allocation_id, operator_user, location=""):
        """
        Operator receives batch and starts process
        """
        with transaction.atomic():
            try:
                allocation = BatchAllocation.objects.get(id=allocation_id)
                
                if allocation.status != 'allocated':
                    raise ValidationError("Batch must be allocated first")
                
                # Update allocation
                allocation.status = 'received'
                allocation.received_at = timezone.now()
                allocation.received_by = operator_user
                allocation.current_location = location
                allocation.save()
                
                # Update batch status
                allocation.batch.status = 'in_process'
                allocation.batch.actual_start_date = timezone.now()
                allocation.batch.save()
                
                # Create execution log
                ProcessExecutionLog.objects.create(
                    batch_allocation=allocation,
                    action='started',
                    performed_by=operator_user,
                    location=location
                )
                
                # Send notification
                ManufacturingWorkflowService._send_batch_received_notification(allocation)
                
                return allocation
                
            except BatchAllocation.DoesNotExist:
                raise ValidationError("Batch Allocation not found")
    
    @staticmethod
    def complete_process(allocation_id, operator_user, completion_notes="", quantity_processed=None):
        """
        Operator completes process
        """
        with transaction.atomic():
            try:
                allocation = BatchAllocation.objects.get(id=allocation_id)
                
                if allocation.status != 'received':
                    raise ValidationError("Batch must be received first")
                
                # Update allocation
                allocation.status = 'completed'
                allocation.save()
                
                # Update batch
                batch = allocation.batch
                batch.status = 'completed'
                batch.actual_end_date = timezone.now()
                if quantity_processed:
                    batch.actual_quantity_completed = quantity_processed
                batch.save()
                
                # Create execution log
                ProcessExecutionLog.objects.create(
                    batch_allocation=allocation,
                    action='completed',
                    performed_by=operator_user,
                    notes=completion_notes,
                    quantity_processed=quantity_processed
                )
                
                # Create finished goods verification
                FinishedGoodsVerification.objects.create(
                    batch=batch,
                    status='pending_verification'
                )
                
                # Send notification
                ManufacturingWorkflowService._send_process_completed_notification(allocation)
                ManufacturingWorkflowService._send_fg_verification_required_notification(batch)
                
                return allocation
                
            except BatchAllocation.DoesNotExist:
                raise ValidationError("Batch Allocation not found")
    
    @staticmethod
    def verify_finished_goods(batch_id, quality_user, quality_notes="", passed=True):
        """
        Quality check for finished goods
        """
        with transaction.atomic():
            try:
                batch = Batch.objects.get(id=batch_id)
                fg_verification = batch.fg_verification
                
                if fg_verification.status != 'pending_verification':
                    raise ValidationError("FG verification not pending")
                
                # Update verification
                fg_verification.quality_checked_at = timezone.now()
                fg_verification.quality_checked_by = quality_user
                fg_verification.quality_notes = quality_notes
                
                if passed:
                    fg_verification.status = 'quality_check_passed'
                else:
                    fg_verification.status = 'quality_check_failed'
                
                fg_verification.save()
                
                # Send notification
                ManufacturingWorkflowService._send_quality_check_completed_notification(batch, quality_user, passed)
                
                return fg_verification
                
            except Batch.DoesNotExist:
                raise ValidationError("Batch not found")
    
    # Notification helper methods
    @staticmethod
    def _send_mo_created_notification(mo, created_by):
        """Send notification to managers about new MO"""
        # Get all managers
        managers = User.objects.filter(
            user_roles__role__name='manager',
            user_roles__is_active=True
        ).distinct()
        
        for manager in managers:
            WorkflowNotification.objects.create(
                notification_type='mo_created',
                title=f'New MO Created: {mo.mo_id}',
                message=f'Manufacturing Order {mo.mo_id} for {mo.product_code.product_code} has been created and requires your approval.',
                recipient=manager,
                related_mo=mo,
                action_required=True,
                created_by=created_by
            )
    
    @staticmethod
    def _send_mo_approved_notification(mo, manager_user):
        """Send notification to RM Store about approved MO"""
        rm_store_users = User.objects.filter(
            user_roles__role__name='rm_store_manager',
            user_roles__is_active=True
        ).distinct()
        
        for rm_user in rm_store_users:
            WorkflowNotification.objects.create(
                notification_type='mo_approved',
                title=f'MO Approved: {mo.mo_id}',
                message=f'Manufacturing Order {mo.mo_id} has been approved and requires RM allocation.',
                recipient=rm_user,
                related_mo=mo,
                action_required=True,
                created_by=manager_user
            )
    
    @staticmethod
    def _send_rm_allocation_required_notification(mo):
        """Send notification to RM Store about RM allocation requirement"""
        pass  # Already handled in _send_mo_approved_notification
    
    @staticmethod
    def _send_rm_allocated_notification(mo, rm_store_user):
        """Send notification to Production Head about RM allocation"""
        production_heads = User.objects.filter(
            user_roles__role__name='production_head',
            user_roles__is_active=True
        ).distinct()
        
        for ph_user in production_heads:
            WorkflowNotification.objects.create(
                notification_type='rm_allocated',
                title=f'RM Allocated: {mo.mo_id}',
                message=f'Raw materials have been allocated for MO {mo.mo_id}. Ready for process assignment.',
                recipient=ph_user,
                related_mo=mo,
                action_required=True,
                created_by=rm_store_user
            )
    
    @staticmethod
    def _send_process_assigned_notification(assignment):
        """Send notification to operator about process assignment"""
        WorkflowNotification.objects.create(
            notification_type='process_assigned',
            title=f'Process Assigned: {assignment.mo_process_execution.process.name}',
            message=f'You have been assigned to process "{assignment.mo_process_execution.process.name}" for MO {assignment.mo_process_execution.mo.mo_id}.',
            recipient=assignment.assigned_operator,
            related_mo=assignment.mo_process_execution.mo,
            related_process_assignment=assignment,
            action_required=True,
            created_by=assignment.assigned_by
        )
    
    @staticmethod
    def _send_process_reassigned_notification(assignment, previous_operator):
        """Send notification about process reassignment"""
        # Notify new operator
        WorkflowNotification.objects.create(
            notification_type='process_reassigned',
            title=f'Process Reassigned: {assignment.mo_process_execution.process.name}',
            message=f'You have been assigned to process "{assignment.mo_process_execution.process.name}" for MO {assignment.mo_process_execution.mo.mo_id}.',
            recipient=assignment.assigned_operator,
            related_mo=assignment.mo_process_execution.mo,
            related_process_assignment=assignment,
            action_required=True,
            created_by=assignment.assigned_by
        )
        
        # Notify previous operator
        WorkflowNotification.objects.create(
            notification_type='process_reassigned',
            title=f'Process Reassigned: {assignment.mo_process_execution.process.name}',
            message=f'Process "{assignment.mo_process_execution.process.name}" for MO {assignment.mo_process_execution.mo.mo_id} has been reassigned.',
            recipient=previous_operator,
            related_mo=assignment.mo_process_execution.mo,
            related_process_assignment=assignment,
            action_required=False,
            created_by=assignment.assigned_by
        )
    
    @staticmethod
    def _send_batch_allocated_notification(allocation):
        """Send notification to operator about batch allocation"""
        WorkflowNotification.objects.create(
            notification_type='batch_allocated',
            title=f'Batch Allocated: {allocation.batch.batch_id}',
            message=f'Batch {allocation.batch.batch_id} has been allocated to you for process "{allocation.allocated_to_process.name}".',
            recipient=allocation.allocated_to_operator,
            related_batch=allocation.batch,
            action_required=True,
            created_by=allocation.allocated_by
        )
    
    @staticmethod
    def _send_batch_received_notification(allocation):
        """Send notification about batch received"""
        WorkflowNotification.objects.create(
            notification_type='batch_received',
            title=f'Batch Received: {allocation.batch.batch_id}',
            message=f'Batch {allocation.batch.batch_id} has been received and process started.',
            recipient=allocation.allocated_to_operator,
            related_batch=allocation.batch,
            action_required=False,
            created_by=allocation.received_by
        )
    
    @staticmethod
    def _send_process_completed_notification(allocation):
        """Send notification about process completion"""
        WorkflowNotification.objects.create(
            notification_type='process_completed',
            title=f'Process Completed: {allocation.batch.batch_id}',
            message=f'Process for batch {allocation.batch.batch_id} has been completed.',
            recipient=allocation.allocated_to_operator,
            related_batch=allocation.batch,
            action_required=False,
            created_by=allocation.allocated_to_operator
        )
    
    @staticmethod
    def _send_fg_verification_required_notification(batch):
        """Send notification about FG verification requirement"""
        quality_users = User.objects.filter(
            user_roles__role__name='quality_manager',
            user_roles__is_active=True
        ).distinct()
        
        for quality_user in quality_users:
            WorkflowNotification.objects.create(
                notification_type='fg_verification_required',
                title=f'FG Verification Required: {batch.batch_id}',
                message=f'Batch {batch.batch_id} is ready for finished goods verification.',
                recipient=quality_user,
                related_batch=batch,
                action_required=True,
                created_by=batch.assigned_operator
            )
    
    @staticmethod
    def _send_quality_check_completed_notification(batch, quality_user, passed):
        """Send notification about quality check completion"""
        status = "passed" if passed else "failed"
        
        WorkflowNotification.objects.create(
            notification_type='quality_check_required',
            title=f'Quality Check {status.title()}: {batch.batch_id}',
            message=f'Quality check for batch {batch.batch_id} has {status}.',
            recipient=batch.assigned_operator,
            related_batch=batch,
            action_required=False,
            created_by=quality_user
        )
