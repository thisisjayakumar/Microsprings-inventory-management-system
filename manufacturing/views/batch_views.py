from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q
from decimal import Decimal

from ..models import Batch, MOProcessExecution
from ..serializers import BatchListSerializer
from inventory.location_tracker import BatchLocationTracker


class BatchProcessExecutionViewSet(viewsets.ViewSet):
    """
    ViewSet for batch process execution management
    Handles starting and completing batches in specific processes
    """
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='start')
    def start_batch_process(self, request):
        """Start a batch in a specific process"""
        batch_id = request.data.get('batch_id')
        process_id = request.data.get('process_id')
        
        if not batch_id or not process_id:
            return Response(
                {'error': 'batch_id and process_id are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            batch = Batch.objects.get(id=batch_id)
            process_execution = MOProcessExecution.objects.get(id=process_id)
        except (Batch.DoesNotExist, MOProcessExecution.DoesNotExist):
            return Response(
                {'error': 'Batch or process execution not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if user has permission (supervisor or assigned to MO)
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'supervisor' not in user_roles and batch.mo.assigned_supervisor != request.user:
            return Response(
                {'error': 'Only supervisors or assigned supervisors can start batch processes'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate batch and process belong to same MO
        if batch.mo != process_execution.mo:
            return Response(
                {'error': 'Batch and process must belong to the same MO'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if batch can start in this process (sequential logic)
        process_sequence = process_execution.sequence_order
        if process_sequence > 1:
            # Check if batch completed previous processes
            previous_processes = MOProcessExecution.objects.filter(
                mo=batch.mo,
                sequence_order__lt=process_sequence
            )
            
            # For now, we'll simulate this check since we don't have batch process execution model yet
            # In a full implementation, you'd check a BatchProcessExecution model
            pass
        
        # Start the process if it's not already started
        if process_execution.status == 'pending':
            process_execution.status = 'in_progress'
            process_execution.actual_start_time = timezone.now()
            process_execution.assigned_operator = request.user
            process_execution.save()
        
        # Update batch status if needed and lock RM allocations
        if batch.status == 'created':
            # Lock RM allocations for this batch
            from manufacturing.services.rm_allocation import RMAllocationService
            lock_result = RMAllocationService.lock_allocations_for_batch(
                batch=batch,
                locked_by_user=request.user
            )
            
            # Log the result but don't fail if locking fails (log for debugging)
            if not lock_result.get('success'):
                print(f"Warning: Failed to lock RM allocations for batch {batch.batch_id}: {lock_result.get('message')}")
            else:
                print(f"Locked {lock_result.get('locked_count', 0)} RM allocations ({lock_result.get('locked_quantity_kg', 0)}kg) for batch {batch.batch_id}")
            
            batch.status = 'in_process'
            batch.actual_start_date = timezone.now()
            batch.save()
        
        # Store batch-process execution state in batch notes for now
        # Since notes is a TextField, we'll use a simple string format
        batch_process_key = f"PROCESS_{process_execution.id}_STATUS"
        current_notes = batch.notes or ""
        
        # Remove any existing status for this process
        import re
        pattern = f"{batch_process_key}:[^;]*;"
        current_notes = re.sub(pattern, "", current_notes)
        
        # Add new status
        new_status = f"{batch_process_key}:in_progress;"
        batch.notes = current_notes + new_status
        batch.save()
        
        # Move batch to appropriate location for this process
        location_result = BatchLocationTracker.move_batch_to_process(
            batch_id=batch.id,
            process_name=process_execution.process.name,
            user=request.user,
            reference_id=process_execution.id
        )
        
        if not location_result['success']:
            # Log the error but don't fail the process start
            print(f"Warning: Failed to move batch to process location: {location_result.get('error')}")
        
        return Response({
            'message': f'Batch {batch.batch_id} started in process {process_execution.process.name}',
            'batch': BatchListSerializer(batch).data,
            'process_execution_id': process_execution.id
        })

    @action(detail=False, methods=['post'], url_path='complete')
    def complete_batch_process(self, request):
        """Complete a batch in a specific process"""
        batch_id = request.data.get('batch_id')
        process_id = request.data.get('process_id')
        
        # Better error message for missing parameters
        if not batch_id:
            return Response(
                {'error': 'batch_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not process_id:
            return Response(
                {'error': 'process_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Log the request for debugging
        print(f"Complete batch process request: batch_id={batch_id}, process_id={process_id}")
        
        try:
            batch = Batch.objects.get(id=batch_id)
            process_execution = MOProcessExecution.objects.get(id=process_id)
        except (Batch.DoesNotExist, MOProcessExecution.DoesNotExist):
            return Response(
                {'error': 'Batch or process execution not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if user has permission
        user_roles = request.user.user_roles.values_list('role__name', flat=True)
        if 'supervisor' not in user_roles and batch.mo.assigned_supervisor != request.user:
            return Response(
                {'error': 'Only supervisors or assigned supervisors can complete batch processes'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate batch and process belong to same MO
        if batch.mo != process_execution.mo:
            return Response(
                {'error': 'Batch and process must belong to the same MO'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if process is in progress OR if MO is stopped but batch has started
        # When MO is stopped, in-progress batches should be allowed to complete
        mo_is_stopped = batch.mo.status == 'stopped'
        batch_has_started = batch.status in ['in_process', 'in_progress']
        
        if process_execution.status != 'in_progress':
            # If process is not in progress, check if this is an allowed exception
            if not (mo_is_stopped and batch_has_started):
                return Response(
                    {'error': 'Process must be in progress to complete batch'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Update batch-process execution state
        batch_process_key = f"PROCESS_{process_execution.id}_STATUS"
        current_notes = batch.notes or ""
        
        # Remove any existing status for this process
        import re
        pattern = f"{batch_process_key}:[^;]*;"
        current_notes = re.sub(pattern, "", current_notes)
        
        # Add completed status
        new_status = f"{batch_process_key}:completed;"
        batch.notes = current_notes + new_status
        batch.save()
        
        # Check if this batch has completed all processes
        all_processes = process_execution.mo.process_executions.all().order_by('sequence_order')
        batch_completed_all = True
        
        for proc in all_processes:
            proc_key = f"PROCESS_{proc.id}_STATUS"
            if f"{proc_key}:completed;" not in (batch.notes or ""):
                batch_completed_all = False
                break
        
        # If batch completed all processes, mark as completed and move to packing zone (mandatory step)
        if batch_completed_all:
            batch.status = 'completed'
            batch.actual_end_date = timezone.now()
            batch.save()
            
            packing_move_result = BatchLocationTracker.move_batch_to_packing(
                batch_id=batch.id,
                user=request.user,
                mo_id=batch.mo.id
            )
            
            if not packing_move_result['success']:
                print(f"Warning: Failed to move completed batch to packing zone: {packing_move_result.get('error')}")
        
        # Calculate cumulative RM from batches to determine if process should be completed
        mo = batch.mo
        product = batch.product_code
        
        # Calculate total RM allocated for the MO (from RawMaterialAllocation)
        from manufacturing.models import RawMaterialAllocation
        total_allocated_rm_kg = Decimal('0')
        allocations = RawMaterialAllocation.objects.filter(
            mo=mo,
            status__in=['reserved', 'locked']
        )
        total_allocated_rm_kg = sum(
            Decimal(str(alloc.allocated_quantity_kg)) 
            for alloc in allocations
        )
        
        # Calculate cumulative RM from all non-cancelled batches (that have been created/started)
        cumulative_batch_rm_kg = Decimal('0')
        mo_batches = Batch.objects.filter(mo=mo).exclude(status='cancelled')
        
        for mo_batch in mo_batches:
            batch_rm_kg = Decimal('0')
            
            if product.material_type == 'coil' and product.grams_per_product:
                # For coil-based products: planned_quantity is in grams
                batch_quantity_grams = mo_batch.planned_quantity
                batch_rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
                
                # Apply tolerance (same as MO tolerance)
                tolerance = mo.tolerance_percentage or Decimal('2.00')
                tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
                batch_rm_kg = batch_rm_base_kg * tolerance_factor
                
            elif product.material_type == 'sheet' and product.pcs_per_strip:
                # For sheet-based products: calculate proportionally
                batch_strips = Decimal(str(mo_batch.planned_quantity))
                if hasattr(product, 'calculate_strips_required'):
                    strips_calc = product.calculate_strips_required(mo.quantity)
                    mo_total_strips = Decimal(str(strips_calc.get('strips_required', mo.quantity)))
                else:
                    mo_total_strips = Decimal(str(mo.quantity)) / Decimal(str(product.pcs_per_strip)) if product.pcs_per_strip > 0 else Decimal(str(mo.quantity))
                
                if mo_total_strips > 0 and mo.rm_required_kg:
                    batch_proportion = batch_strips / mo_total_strips
                    batch_rm_kg = Decimal(str(mo.rm_required_kg)) * batch_proportion
            
            cumulative_batch_rm_kg += batch_rm_kg
        
        # Calculate what percentage of allocated RM has been batched
        rm_batched_percentage = Decimal('0')
        if total_allocated_rm_kg > 0:
            rm_batched_percentage = (cumulative_batch_rm_kg / total_allocated_rm_kg) * Decimal('100')
        else:
            # If no RM allocated yet, don't allow process completion
            print(f"Warning: No RM allocated for MO {mo.mo_id}, cannot determine completion percentage")
        
        # Check if batches completed this process
        mo_batches = Batch.objects.filter(mo=batch.mo).exclude(status='cancelled')
        all_batches_completed_process = True
        
        for mo_batch in mo_batches:
            batch_proc_key = f"PROCESS_{process_execution.id}_STATUS"
            if f"{batch_proc_key}:completed;" not in (mo_batch.notes or ""):
                all_batches_completed_process = False
                break
        
        # Update process progress based on batch completion
        completed_batches = 0
        total_batches = mo_batches.count()
        
        for mo_batch in mo_batches:
            batch_proc_key = f"PROCESS_{process_execution.id}_STATUS"
            if f"{batch_proc_key}:completed;" in (mo_batch.notes or ""):
                completed_batches += 1
        
        # Calculate progress percentage based on batch completion
        if total_batches > 0:
            progress_percentage = (completed_batches / total_batches) * 100
            process_execution.progress_percentage = progress_percentage
            
            # Only mark process as completed if:
            # 1. All existing batches have completed this process, AND
            # 2. RM batched is >= 90% of total allocated RM
            should_complete_process = (
                all_batches_completed_process and 
                rm_batched_percentage >= Decimal('90') and
                total_allocated_rm_kg > 0  # Must have allocated RM
            )
            
            if should_complete_process:
                # Only mark as completed if not already completed, or if already completed but progress should be 100%
                if process_execution.status != 'completed':
                    process_execution.status = 'completed'
                    process_execution.actual_end_time = timezone.now()
                # Always set progress to 100 when all batches are completed
                process_execution.progress_percentage = 100
                print(f"Process {process_execution.id} completed: RM batched {rm_batched_percentage}% >= 90%")
            else:
                # If not all batches completed, ensure status is in_progress and progress reflects actual completion
                if process_execution.status == 'completed':
                    # Process was completed but new batches were added, revert to in_progress
                    process_execution.status = 'in_progress'
                    process_execution.actual_end_time = None
                # Progress percentage already calculated above based on batch completion
                print(f"Process {process_execution.id} not completed yet: {completed_batches}/{total_batches} batches completed ({progress_percentage}%), RM batched {rm_batched_percentage}%")
            
            process_execution.save()
        else:
            # No batches exist yet, cannot complete process
            print(f"Process {process_execution.id} not completed: No batches exist for MO {mo.mo_id}")
            process_execution.save()
        
        # Handle batch location after process completion
        completion_result = BatchLocationTracker.complete_batch_process(
            batch_id=batch.id,
            process_name=process_execution.process.name,
            user=request.user,
            reference_id=process_execution.id
        )
        
        # If this batch has completed all processes, move to packing zone (mandatory step)
        if batch_completed_all:
            packing_move_result = BatchLocationTracker.move_batch_to_packing(
                batch_id=batch.id,
                user=request.user,
                mo_id=batch.mo.id
            )
            
            if not packing_move_result['success']:
                print(f"Warning: Failed to move completed batch to packing zone: {packing_move_result.get('error')}")
        
        return Response({
            'message': f'Batch {batch.batch_id} completed in process {process_execution.process.name}',
            'batch': BatchListSerializer(batch).data,
            'process_execution_id': process_execution.id,
            'process_completed': process_execution.status == 'completed',
            'batch_completed_all': batch_completed_all,
            'rm_batched_percentage': float(rm_batched_percentage),
            'total_allocated_rm_kg': float(total_allocated_rm_kg),
            'cumulative_batch_rm_kg': float(cumulative_batch_rm_kg),
            'process_progress': float(process_execution.progress_percentage) if process_execution.progress_percentage else 0
        })

    @action(detail=False, methods=['get'])
    def get_batch_process_executions(self, request):
        """Get batch process execution status for an MO"""
        mo_id = request.query_params.get('mo_id')
        
        if not mo_id:
            return Response(
                {'error': 'mo_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get batches and process executions for the MO
        batches = Batch.objects.filter(mo_id=mo_id)
        process_executions = MOProcessExecution.objects.filter(mo_id=mo_id).order_by('sequence_order')
        
        # In a full implementation, you'd have a BatchProcessExecution model
        # For now, return the basic batch and process data
        
        return Response({
            'batches': BatchListSerializer(batches, many=True).data,
            'process_executions': [
                {
                    'id': pe.id,
                    'process_name': pe.process.name,
                    'sequence_order': pe.sequence_order,
                    'status': pe.status,
                    'actual_start_time': pe.actual_start_time,
                    'actual_end_time': pe.actual_end_time,
                }
                for pe in process_executions
            ]
        })
