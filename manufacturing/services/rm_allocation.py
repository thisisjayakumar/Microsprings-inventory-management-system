"""
Raw Material Allocation Service
Handles RM reservation, swapping, and locking for Manufacturing Orders
"""

from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
import logging

from manufacturing.models import (
    ManufacturingOrder, RawMaterialAllocation, RMAllocationHistory
)
from inventory.models import RawMaterial, RMStockBalanceHeat

logger = logging.getLogger(__name__)


class RMAllocationService:
    """
    Service for managing raw material allocations for Manufacturing Orders
    """
    
    @staticmethod
    def allocate_rm_for_mo(mo, allocated_by_user):
        logger.info(f"[DEBUG] allocate_rm_for_mo - Starting allocation for MO {mo.mo_id}")
        logger.info(f"[DEBUG] allocate_rm_for_mo - MO Status: {mo.status}, rm_required_kg: {mo.rm_required_kg}")
        
        with transaction.atomic():
            allocations = []
            
            # Get product's raw material requirements
            if not mo.product_code:
                logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - No product_code assigned")
                raise ValidationError("MO must have a product code")
            
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Product: {mo.product_code.product_code}")
            
            if not mo.product_code.material:
                logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Product {mo.product_code.product_code} has no associated raw material")
                raise ValidationError("Product must have associated raw material")
            
            raw_material = mo.product_code.material
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Raw material: {raw_material.material_code} (ID: {raw_material.id})")
            
            # Calculate required quantity
            required_quantity_kg = Decimal(str(mo.rm_required_kg)) if mo.rm_required_kg else Decimal('0')
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Required quantity: {required_quantity_kg}kg")
            
            if required_quantity_kg <= 0:
                logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Required quantity is 0 or negative. Possible cause: rm_required_kg not calculated")
                raise ValidationError("Required RM quantity must be greater than 0. Ensure MO.calculate_rm_requirements() was called.")
            
            # Check if stock is available
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Checking stock for material ID: {raw_material.id}")
            
            stock_balance = RMStockBalanceHeat.objects.filter(
                raw_material=raw_material
            ).first()
            
            if not stock_balance:
                logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - No RMStockBalanceHeat record found for material {raw_material.material_code} (ID: {raw_material.id})")
                # Try to find using RMStockBalance as fallback
                from inventory.models import RMStockBalance
                legacy_stock = RMStockBalance.objects.filter(raw_material=raw_material).first()
                if legacy_stock:
                    logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Found legacy RMStockBalance with {legacy_stock.available_quantity}kg")
                    available_qty = legacy_stock.available_quantity
                else:
                    logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - No stock balance record (RMStockBalanceHeat or RMStockBalance) found")
                    available_qty = Decimal('0')
            else:
                available_qty = stock_balance.total_available_quantity_kg
                logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - RMStockBalanceHeat found: Available stock: {available_qty}kg")
            
            if available_qty < required_quantity_kg:
                logger.error(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - INSUFFICIENT STOCK. Required: {required_quantity_kg}kg, Available: {available_qty}kg")
                raise ValidationError(
                    f"Insufficient stock for {raw_material.material_code}. "
                    f"Required: {required_quantity_kg}kg, "
                    f"Available: {available_qty}kg"
                )
            
            # Check for existing allocations to avoid duplicates
            existing_allocation = RawMaterialAllocation.objects.filter(
                mo=mo,
                raw_material=raw_material,
                status__in=['reserved', 'locked']
            ).first()
            
            if existing_allocation:
                existing_qty = float(existing_allocation.allocated_quantity_kg)
                logger.warning(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Already has allocation: {existing_allocation.id}, Status: {existing_allocation.status}, Qty: {existing_qty}kg")
                
                # Check if existing allocation has enough quantity
                if existing_qty >= required_quantity_kg:
                    logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Existing allocation sufficient, returning existing")
                    allocations.append(existing_allocation)
                    return allocations
                else:
                    logger.warning(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Existing allocation insufficient ({existing_qty}kg < {required_quantity_kg}kg), will create new")
                    # Continue to create new allocation if quantity is insufficient
            
            # Create allocation (reserved status - not locked yet)
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Creating new reservation...")
            allocation = RawMaterialAllocation.objects.create(
                mo=mo,
                raw_material=raw_material,
                allocated_quantity_kg=required_quantity_kg,
                status='reserved',
                can_be_swapped=True,
                allocated_by=allocated_by_user,
                notes=f"Initial allocation for MO {mo.mo_id}"
            )
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Allocation created: ID={allocation.id}, Status={allocation.status}, Qty={allocation.allocated_quantity_kg}kg")
            
            # Create history record
            RMAllocationHistory.objects.create(
                allocation=allocation,
                action='reserved',
                from_mo=None,
                to_mo=mo,
                quantity_kg=required_quantity_kg,
                performed_by=allocated_by_user,
                reason="Initial RM allocation for MO creation"
            )
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - History record created")
            
            allocations.append(allocation)
            logger.info(f"[DEBUG] allocate_rm_for_mo - MO {mo.mo_id} - Allocation complete. Total allocations: {len(allocations)}")
            
            return allocations
    
    @staticmethod
    def find_swappable_allocations(target_mo):
        """
        Find RM allocations that can be swapped to the target MO
        Based on:
        1. Same raw material required
        2. Lower priority than target MO
        3. Not locked (MO not yet approved)
        
        Args:
            target_mo: ManufacturingOrder that needs RM
            
        Returns:
            QuerySet of RawMaterialAllocation instances that can be swapped
        """
        if not target_mo.product_code or not target_mo.product_code.material:
            return RawMaterialAllocation.objects.none()
        
        required_material = target_mo.product_code.material
        required_quantity = Decimal(str(target_mo.rm_required_kg))
        
        # Priority ordering
        priority_order = {'low': 1, 'medium': 2, 'high': 3, 'urgent': 4}
        target_priority_value = priority_order.get(target_mo.priority, 0)
        
        # Find all allocations with lower priority, same material, and can be swapped
        lower_priority_statuses = [
            key for key, value in priority_order.items() 
            if value < target_priority_value
        ]
        
        swappable_allocations = RawMaterialAllocation.objects.filter(
            raw_material=required_material,
            status='reserved',
            can_be_swapped=True,
            mo__status='on_hold',  # MO not yet approved
            mo__priority__in=lower_priority_statuses
        ).select_related('mo', 'raw_material').order_by(
            'mo__priority',  # Lowest priority first
            'allocated_at'  # Oldest first
        )
        
        return swappable_allocations
    
    @staticmethod
    def auto_swap_allocations(target_mo, requested_by_user):
        """
        Automatically swap RM allocations from lower priority MOs to target MO
        
        Args:
            target_mo: ManufacturingOrder that needs RM (higher priority)
            requested_by_user: User requesting the swap
            
        Returns:
            dict with swap results
        """
        with transaction.atomic():
            swappable = RMAllocationService.find_swappable_allocations(target_mo)
            
            if not swappable.exists():
                return {
                    'success': False,
                    'message': 'No swappable allocations found',
                    'swapped_count': 0
                }
            
            required_quantity = Decimal(str(target_mo.rm_required_kg))
            swapped_allocations = []
            total_swapped_quantity = Decimal('0')
            
            # Swap allocations until we have enough
            for allocation in swappable:
                if total_swapped_quantity >= required_quantity:
                    break
                
                success, message = allocation.swap_to_mo(
                    target_mo=target_mo,
                    swapped_by_user=requested_by_user,
                    reason=f"Auto-swapped due to higher priority MO {target_mo.mo_id}"
                )
                
                if success:
                    swapped_allocations.append(allocation)
                    total_swapped_quantity += allocation.allocated_quantity_kg
                    
                    # Create history record
                    RMAllocationHistory.objects.create(
                        allocation=allocation,
                        action='swapped',
                        from_mo=allocation.mo,
                        to_mo=target_mo,
                        quantity_kg=allocation.allocated_quantity_kg,
                        performed_by=requested_by_user,
                        reason=f"Auto-swapped to higher priority MO {target_mo.mo_id}"
                    )
            
            if total_swapped_quantity >= required_quantity:
                return {
                    'success': True,
                    'message': f'Successfully swapped {len(swapped_allocations)} allocations',
                    'swapped_count': len(swapped_allocations),
                    'total_quantity_kg': float(total_swapped_quantity),
                    'swapped_from_mos': [alloc.mo.mo_id for alloc in swapped_allocations]
                }
            else:
                return {
                    'success': False,
                    'message': f'Insufficient swappable quantity. Required: {required_quantity}kg, Available: {total_swapped_quantity}kg',
                    'swapped_count': len(swapped_allocations),
                    'total_quantity_kg': float(total_swapped_quantity),
                    'required_quantity_kg': float(required_quantity)
                }
    
    @staticmethod
    def lock_allocations_for_mo(mo, locked_by_user):
        """
        Lock all RM allocations for an MO (when MO is approved)
        This deducts the RM from available stock
        
        Args:
            mo: ManufacturingOrder being approved
            locked_by_user: User approving the MO
            
        Returns:
            dict with lock results
        """
        with transaction.atomic():
            allocations = RawMaterialAllocation.objects.filter(
                mo=mo,
                status='reserved'
            ).select_related('raw_material')
            
            if not allocations.exists():
                return {
                    'success': False,
                    'message': 'No reserved allocations found for this MO',
                    'locked_count': 0
                }
            
            locked_count = 0
            for allocation in allocations:
                success = allocation.lock_allocation(locked_by_user)
                if success:
                    locked_count += 1
                    
                    # Create history record
                    RMAllocationHistory.objects.create(
                        allocation=allocation,
                        action='locked',
                        from_mo=None,
                        to_mo=mo,
                        quantity_kg=allocation.allocated_quantity_kg,
                        performed_by=locked_by_user,
                        reason=f"MO {mo.mo_id} approved - allocation locked"
                    )
            
            return {
                'success': True,
                'message': f'Locked {locked_count} allocations for MO {mo.mo_id}',
                'locked_count': locked_count
            }
    
    @staticmethod
    def release_allocations_for_mo(mo, released_by_user, reason=""):
        """
        Release RM allocations back to stock (when MO is cancelled)
        
        Args:
            mo: ManufacturingOrder being cancelled
            released_by_user: User cancelling the MO
            reason: Reason for release
            
        Returns:
            dict with release results
        """
        with transaction.atomic():
            allocations = RawMaterialAllocation.objects.filter(
                mo=mo,
                status__in=['reserved', 'locked']
            ).select_related('raw_material')
            
            if not allocations.exists():
                return {
                    'success': False,
                    'message': 'No allocations found for this MO',
                    'released_count': 0
                }
            
            released_count = 0
            for allocation in allocations:
                success = allocation.release_allocation()
                if success:
                    released_count += 1
                    
                    # Create history record
                    RMAllocationHistory.objects.create(
                        allocation=allocation,
                        action='released',
                        from_mo=mo,
                        to_mo=None,
                        quantity_kg=allocation.allocated_quantity_kg,
                        performed_by=released_by_user,
                        reason=reason or f"MO {mo.mo_id} cancelled - allocation released"
                    )
            
            return {
                'success': True,
                'message': f'Released {released_count} allocations for MO {mo.mo_id}',
                'released_count': released_count
            }
    
    @staticmethod
    def lock_allocations_for_batch(batch, locked_by_user):
        """
        Lock RM allocations for a batch when it starts production
        This locks the RM quantity corresponding to the batch's planned quantity
        Once locked, these allocations cannot be swapped to another MO
        
        Args:
            batch: Batch instance being started
            locked_by_user: User starting the batch
            
        Returns:
            dict with lock results
        """
        from manufacturing.models import Batch
        
        logger.info(f"[DEBUG] lock_allocations_for_batch - Starting for batch {batch.batch_id}")
        
        with transaction.atomic():
            mo = batch.mo
            product = batch.product_code
            
            if not product:
                return {
                    'success': False,
                    'message': 'Batch has no product code',
                    'locked_count': 0,
                    'locked_quantity_kg': 0
                }
            
            # Calculate batch RM requirement
            batch_rm_required_kg = Decimal('0')
            
            if product.material_type == 'coil' and product.grams_per_product:
                # For coil-based products: planned_quantity is in grams
                batch_quantity_grams = batch.planned_quantity
                batch_rm_base_kg = Decimal(str(batch_quantity_grams / 1000))
                
                # Apply tolerance (same as MO tolerance)
                tolerance = mo.tolerance_percentage or Decimal('2.00')
                tolerance_factor = Decimal('1') + (tolerance / Decimal('100'))
                batch_rm_required_kg = batch_rm_base_kg * tolerance_factor
                
            elif product.material_type == 'sheet' and product.pcs_per_strip:
                # For sheet-based products: planned_quantity is in strips
                # Calculate proportionally based on MO total strips and batch strips
                batch_strips = Decimal(str(batch.planned_quantity))
                
                # Calculate MO total strips from quantity in pieces
                if hasattr(product, 'calculate_strips_required'):
                    strips_calc = product.calculate_strips_required(mo.quantity)
                    mo_total_strips = Decimal(str(strips_calc.get('strips_required', mo.quantity)))
                else:
                    # Fallback: use pcs_per_strip to calculate
                    if product.pcs_per_strip > 0:
                        mo_total_strips = Decimal(str(mo.quantity)) / Decimal(str(product.pcs_per_strip))
                    else:
                        mo_total_strips = Decimal(str(mo.quantity))
                
                if mo_total_strips > 0 and mo.rm_required_kg:
                    # Calculate what fraction of MO this batch represents
                    batch_proportion = batch_strips / mo_total_strips
                    # Apply proportion to MO's total RM requirement
                    batch_rm_required_kg = Decimal(str(mo.rm_required_kg)) * batch_proportion
                    logger.info(f"[DEBUG] lock_allocations_for_batch - Sheet: batch_strips={batch_strips}, mo_total_strips={mo_total_strips}, proportion={batch_proportion}, batch_rm={batch_rm_required_kg}kg")
                else:
                    logger.warning(f"[DEBUG] lock_allocations_for_batch - Cannot calculate sheet RM proportion")
                    batch_rm_required_kg = Decimal(str(mo.rm_required_kg)) if mo.rm_required_kg else Decimal('0')
            
            else:
                # Fallback: if we can't calculate, use MO's total requirement
                logger.warning(f"[DEBUG] lock_allocations_for_batch - Cannot calculate batch RM, using MO total")
                batch_rm_required_kg = Decimal(str(mo.rm_required_kg)) if mo.rm_required_kg else Decimal('0')
            
            logger.info(f"[DEBUG] lock_allocations_for_batch - Batch {batch.batch_id} needs {batch_rm_required_kg}kg RM")
            
            if batch_rm_required_kg <= 0:
                return {
                    'success': False,
                    'message': f'Batch RM requirement is 0 or invalid (calculated: {batch_rm_required_kg}kg)',
                    'locked_count': 0,
                    'locked_quantity_kg': 0
                }
            
            # Get reserved allocations for the MO
            allocations = RawMaterialAllocation.objects.filter(
                mo=mo,
                status='reserved'
            ).select_related('raw_material').order_by('allocated_at')
            
            if not allocations.exists():
                logger.warning(f"[DEBUG] lock_allocations_for_batch - No reserved allocations found for MO {mo.mo_id}")
                return {
                    'success': False,
                    'message': 'No reserved RM allocations found for this MO',
                    'locked_count': 0,
                    'locked_quantity_kg': 0
                }
            
            # Calculate total reserved quantity
            total_reserved = sum(alloc.allocated_quantity_kg for alloc in allocations)
            logger.info(f"[DEBUG] lock_allocations_for_batch - Total reserved: {total_reserved}kg, Need to lock: {batch_rm_required_kg}kg")
            
            # Lock allocations in order until we've locked the required quantity
            # If an allocation is larger than needed, split it
            locked_count = 0
            total_locked = Decimal('0')
            locked_allocations = []
            
            for allocation in allocations:
                if total_locked >= batch_rm_required_kg:
                    break
                
                remaining_needed = batch_rm_required_kg - total_locked
                allocation_qty = allocation.allocated_quantity_kg
                
                # Check if we need to split this allocation
                if allocation_qty > remaining_needed and remaining_needed > 0:
                    # Split the allocation: lock only what we need, keep the rest reserved
                    logger.info(f"[DEBUG] lock_allocations_for_batch - Splitting allocation {allocation.id}: {allocation_qty}kg -> Lock {remaining_needed}kg, Reserve {allocation_qty - remaining_needed}kg")
                    
                    # Create a new locked allocation with the needed quantity
                    locked_allocation = RawMaterialAllocation.objects.create(
                        mo=mo,
                        raw_material=allocation.raw_material,
                        allocated_quantity_kg=remaining_needed,
                        status='locked',
                        can_be_swapped=False,
                        locked_at=timezone.now(),
                        locked_by=locked_by_user,
                        allocated_by=allocation.allocated_by or locked_by_user,
                        notes=f"Split from allocation {allocation.id} for batch {batch.batch_id}"
                    )
                    
                    # Update the original allocation to have the remaining quantity
                    remaining_qty = allocation_qty - remaining_needed
                    if remaining_qty > 0:
                        allocation.allocated_quantity_kg = remaining_qty
                        allocation.save()
                    else:
                        # This shouldn't happen if our logic is correct, but handle it gracefully
                        logger.warning(f"[DEBUG] lock_allocations_for_batch - Remaining quantity is 0 or negative, deleting allocation {allocation.id}")
                        allocation.delete()
                    
                    # Deduct from available stock (only for the locked portion)
                    from inventory.models import RMStockBalanceHeat
                    stock_balance = RMStockBalanceHeat.objects.filter(
                        raw_material=allocation.raw_material
                    ).first()
                    
                    if stock_balance:
                        stock_balance.total_available_quantity_kg -= remaining_needed
                        stock_balance.save()
                    
                    # Create history record for the split
                    RMAllocationHistory.objects.create(
                        allocation=locked_allocation,
                        action='locked',
                        from_mo=None,
                        to_mo=mo,
                        quantity_kg=remaining_needed,
                        performed_by=locked_by_user,
                        reason=f"Batch {batch.batch_id} started - split and locked {remaining_needed}kg from allocation {allocation.id}"
                    )
                    
                    locked_count += 1
                    locked_allocations.append(locked_allocation)
                    total_locked += remaining_needed
                    
                    logger.info(f"[DEBUG] lock_allocations_for_batch - Split and locked {remaining_needed}kg from allocation {allocation.id}")
                    
                else:
                    # Lock the entire allocation
                    success = allocation.lock_allocation(locked_by_user)
                    if success:
                        locked_count += 1
                        locked_allocations.append(allocation)
                        total_locked += allocation.allocated_quantity_kg
                        
                        # Create history record
                        RMAllocationHistory.objects.create(
                            allocation=allocation,
                            action='locked',
                            from_mo=None,
                            to_mo=mo,
                            quantity_kg=allocation.allocated_quantity_kg,
                            performed_by=locked_by_user,
                            reason=f"Batch {batch.batch_id} started - allocation locked"
                        )
                        
                        logger.info(f"[DEBUG] lock_allocations_for_batch - Locked allocation {allocation.id}: {allocation.allocated_quantity_kg}kg")
            
            logger.info(f"[DEBUG] lock_allocations_for_batch - Locked {locked_count} allocations, total: {total_locked}kg")
            
            return {
                'success': True,
                'message': f'Locked {locked_count} RM allocations ({total_locked}kg) for batch {batch.batch_id}',
                'locked_count': locked_count,
                'locked_quantity_kg': float(total_locked),
                'required_quantity_kg': float(batch_rm_required_kg)
            }
    
    @staticmethod
    def get_allocation_summary_for_mo(mo):
        """
        Get summary of RM allocations for an MO
        
        Args:
            mo: ManufacturingOrder instance
            
        Returns:
            dict with allocation summary
        """
        allocations = RawMaterialAllocation.objects.filter(
            mo=mo
        ).select_related('raw_material', 'swapped_to_mo')
        
        summary = {
            'mo_id': mo.mo_id,
            'mo_priority': mo.priority,
            'mo_status': mo.status,
            'required_rm_kg': float(mo.rm_required_kg),
            'allocations': []
        }
        
        total_reserved = Decimal('0')
        total_locked = Decimal('0')
        total_swapped = Decimal('0')
        
        for allocation in allocations:
            alloc_data = {
                'id': allocation.id,
                'raw_material': allocation.raw_material.material_code,
                'quantity_kg': float(allocation.allocated_quantity_kg),
                'status': allocation.status,
                'can_be_swapped': allocation.can_be_swapped,
                'allocated_at': allocation.allocated_at.isoformat(),
            }
            
            if allocation.status == 'reserved':
                total_reserved += allocation.allocated_quantity_kg
            elif allocation.status == 'locked':
                total_locked += allocation.allocated_quantity_kg
            elif allocation.status == 'swapped':
                total_swapped += allocation.allocated_quantity_kg
                alloc_data['swapped_to_mo'] = allocation.swapped_to_mo.mo_id if allocation.swapped_to_mo else None
            
            summary['allocations'].append(alloc_data)
        
        summary['total_reserved_kg'] = float(total_reserved)
        summary['total_locked_kg'] = float(total_locked)
        summary['total_swapped_kg'] = float(total_swapped)
        summary['is_fully_allocated'] = (total_reserved + total_locked) >= Decimal(str(mo.rm_required_kg))
        
        return summary
    
    @staticmethod
    def check_rm_availability_for_mo(mo):
        """
        Check if RM is available for an MO
        Considers:
        1. Currently allocated RM (reserved/locked)
        2. Potential swappable RM from lower priority MOs
        
        Args:
            mo: ManufacturingOrder instance
            
        Returns:
            dict with availability status
        """
        if not mo.product_code or not mo.product_code.material:
            return {
                'available': False,
                'message': 'Product has no associated raw material'
            }
        
        raw_material = mo.product_code.material
        required_quantity = Decimal(str(mo.rm_required_kg))
        
        # Check current allocations
        current_allocations = RawMaterialAllocation.objects.filter(
            mo=mo,
            status__in=['reserved', 'locked']
        ).aggregate(
            total=models.Sum('allocated_quantity_kg')
        )
        
        current_allocated = Decimal(str(current_allocations['total'] or 0))
        
        # Check stock balance
        stock_balance = RMStockBalanceHeat.objects.filter(
            raw_material=raw_material
        ).first()
        
        available_in_stock = Decimal(str(stock_balance.total_available_quantity_kg if stock_balance else 0))
        
        # Check swappable allocations
        swappable_allocations = RMAllocationService.find_swappable_allocations(mo)
        swappable_quantity = sum(
            alloc.allocated_quantity_kg for alloc in swappable_allocations
        )
        
        total_available = current_allocated + available_in_stock + swappable_quantity
        
        return {
            'available': total_available >= required_quantity,
            'required_kg': float(required_quantity),
            'current_allocated_kg': float(current_allocated),
            'available_in_stock_kg': float(available_in_stock),
            'swappable_kg': float(swappable_quantity),
            'total_available_kg': float(total_available),
            'shortage_kg': float(max(0, required_quantity - total_available)),
            'can_swap': swappable_quantity > 0,
            'swappable_from_mos': [alloc.mo.mo_id for alloc in swappable_allocations[:5]]  # Show first 5
        }


# Import at the end to avoid circular imports
from django.db import models

