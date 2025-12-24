"""
Inventory Transaction Manager
Comprehensive tracking of materials and products throughout the manufacturing lifecycle
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import (
    InventoryTransaction, ProductLocation, Location, 
    RawMaterial, RMStockBalance, InventoryTransactionHeat, HeatNumber
)
from .utils import generate_transaction_id

User = get_user_model()


class InventoryTransactionManager:
    """
    Centralized manager for creating inventory transactions
    Ensures consistent tracking across all stages
    """
    
    @staticmethod
    def get_or_create_location(location_name):
        """Get or create a location by name"""
        location, created = Location.objects.get_or_create(
            location_name=location_name,
            defaults={'code': location_name.upper().replace(' ', '_')}
        )
        return location
    
    @staticmethod
    @transaction.atomic
    def create_po_approved_transaction(po, user):
        """
        Track PO approval - materials ordered
        """
        transaction_id = generate_transaction_id('PO_APPROVED')
        
        # For raw materials, we don't have a product yet, so we'll track in notes
        notes = f"PO {po.po_id} approved - {po.rm_code.material_name} ordered from {po.vendor_name.name}"
        
        # Create transaction without product (this is just an order tracking)
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='outward',  # Order placed (outward from supplier perspective)
            product=None,  # No product yet
            manufacturing_order=None,
            location_from=None,  # Vendor location
            location_to=None,  # Will be RM Store when received
            quantity=po.quantity,
            unit_cost=po.unit_price,
            total_value=po.total_amount,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='po',
            reference_id=str(po.id),
            notes=notes
        )
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_grn_receipt_transaction(grn_receipt, heat_numbers_data, user):
        """
        Track GRN receipt - materials received at RM Store
        Creates transaction for each heat number
        """
        transactions = []
        rm_store_location = InventoryTransactionManager.get_or_create_location('rm_store')
        
        # Get first PO for transaction details (or use all POs if needed)
        first_po = grn_receipt.purchase_orders.first()
        if not first_po:
            raise ValueError(f"GRN {grn_receipt.grn_number} has no associated purchase orders")
        
        # Get vendor names (could be multiple)
        vendor_names = [po.vendor_name.name for po in grn_receipt.purchase_orders.all() if po.vendor_name]
        vendor_name_str = ', '.join(set(vendor_names)) if vendor_names else 'Unknown Vendor'
        
        for heat_data in heat_numbers_data:
            heat_number = heat_data['heat_number']
            quantity_kg = heat_data['quantity_kg']
            
            transaction_id = generate_transaction_id('GRN')
            
            notes = f"GRN {grn_receipt.grn_number} - Material received from {vendor_name_str}"
            if grn_receipt.truck_number:
                notes += f" (Truck: {grn_receipt.truck_number})"
            
            inv_transaction = InventoryTransaction.objects.create(
                transaction_id=transaction_id,
                transaction_type='inward',
                product=None,  # Raw material receipt
                manufacturing_order=None,
                location_from=None,  # Vendor
                location_to=rm_store_location,
                quantity=quantity_kg,
                unit_cost=first_po.unit_price,
                total_value=Decimal(str(quantity_kg)) * first_po.unit_price,
                transaction_datetime=timezone.now(),
                created_by=user,
                reference_type='po',
                reference_id=str(first_po.id),  # Use first PO ID
                notes=notes
            )
            
            # Create heat tracking
            InventoryTransactionHeat.objects.create(
                inventory_transaction=inv_transaction,
                heat_number=heat_number,
                quantity_kg=quantity_kg,
                coils_count=heat_number.coils_received,
                sheets_count=heat_number.sheets_received,
                grn_number=grn_receipt.grn_number
            )
            
            transactions.append(inv_transaction)
        
        return transactions
    
    @staticmethod
    @transaction.atomic
    def create_grn_completion_transaction(grn_receipt, user):
        """
        Track GRN completion - materials added to stock
        """
        rm_store_location = InventoryTransactionManager.get_or_create_location('rm_store')
        
        for heat_number in grn_receipt.heat_numbers.all():
            # Update stock balance
            stock_balance, created = RMStockBalance.objects.get_or_create(
                raw_material=heat_number.raw_material,
                defaults={'available_quantity': Decimal('0')}
            )
            stock_balance.available_quantity += heat_number.total_weight_kg or Decimal('0')
            stock_balance.save()
            
            # Update ProductLocation for raw material
            product_location, created = ProductLocation.objects.get_or_create(
                raw_material=heat_number.raw_material,
                current_location=rm_store_location,
                defaults={
                    'quantity': Decimal('0'),
                    'last_moved_by': user
                }
            )
            product_location.quantity += heat_number.total_weight_kg or Decimal('0')
            product_location.last_moved_by = user
            product_location.save()
        
        return True
    
    @staticmethod
    @transaction.atomic
    def create_batch_allocation_transaction(batch, rm_quantity_kg, user, heat_number=None):
        """
        Track raw material allocation to batch
        Moves RM from RM Store to production
        """
        transaction_id = generate_transaction_id('BATCH_ALLOC')
        rm_store_location = InventoryTransactionManager.get_or_create_location('rm_store')
        production_location = InventoryTransactionManager.get_or_create_location('coiling')  # First process
        
        raw_material = batch.mo.product_code.material
        
        notes = f"RM allocated to Batch {batch.batch_id} for MO {batch.mo.mo_id}"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='transfer',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=rm_store_location,
            location_to=production_location,
            quantity=rm_quantity_kg,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='mo',
            reference_id=str(batch.mo.id),
            notes=notes
        )
        
        # Update stock balance
        if raw_material:
            stock_balance, created = RMStockBalance.objects.get_or_create(
                raw_material=raw_material,
                defaults={'available_quantity': Decimal('0')}
            )
            stock_balance.available_quantity -= Decimal(str(rm_quantity_kg))
            stock_balance.save()
            
            # Update ProductLocation for raw material (reduce from RM Store)
            try:
                product_location = ProductLocation.objects.get(
                    raw_material=raw_material,
                    current_location=rm_store_location
                )
                product_location.quantity -= Decimal(str(rm_quantity_kg))
                if product_location.quantity <= 0:
                    product_location.delete()
                else:
                    product_location.last_moved_by = user
                    product_location.save()
            except ProductLocation.DoesNotExist:
                pass
        
        # Create ProductLocation for batch at production
        ProductLocation.objects.create(
            batch=batch,
            current_location=production_location,
            quantity=rm_quantity_kg,
            last_moved_by=user,
            last_transaction=inv_transaction
        )
        
        # Track heat number if provided
        if heat_number:
            InventoryTransactionHeat.objects.create(
                inventory_transaction=inv_transaction,
                heat_number=heat_number,
                quantity_kg=rm_quantity_kg,
                coils_count=1,  # Adjust based on actual consumption
                sheets_count=0,
                grn_number=heat_number.grn_receipt.grn_number
            )
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_process_start_transaction(process_execution, batch, user):
        """
        Track process start - batch moved to process location
        """
        transaction_id = generate_transaction_id('PROC_START')
        
        # Determine location based on process name
        process_name = process_execution.process.name.lower()
        
        # Map process names to locations
        location_mapping = {
            'coiling': 'coiling',
            'forming': 'forming',
            'tempering': 'tempering',
            'coating': 'coating',
            'blanking': 'blanking',
            'piercing': 'piercing',
            'deburring': 'deburring',
            'ironing': 'ironing',
            'champering': 'champering',
            'bending': 'bending',
            'plating': 'plating',
            'blue coating': 'blue_coating',
            'bush assembly': 'bush_assembly',
            'riveting': 'riveting',
            'remar': 'remar',
            'brass welding': 'brass_welding',
            'grinding & buffing': 'grinding_buffing',
            'blacking': 'blacking',
            'phosphating': 'phosphating',
            'final inspection': 'final_inspection',
            'outsourcing': 'dispatched',  # Outsourcing treated as dispatched temporarily
        }
        
        location_name = location_mapping.get(process_name, 'coiling')
        current_location = InventoryTransactionManager.get_or_create_location(location_name)
        
        # Get previous location (could be RM Store or previous process)
        try:
            previous_location_obj = ProductLocation.objects.filter(
                batch=batch
            ).latest('last_moved_at')
            location_from = previous_location_obj.current_location
        except ProductLocation.DoesNotExist:
            location_from = InventoryTransactionManager.get_or_create_location('rm_store')
        
        notes = f"Process '{process_execution.process.name}' started for Batch {batch.batch_id}"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='transfer',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=location_from,
            location_to=current_location,
            quantity=batch.planned_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='process',
            reference_id=str(process_execution.id),
            notes=notes
        )
        
        # Update ProductLocation
        ProductLocation.objects.update_or_create(
            batch=batch,
            defaults={
                'current_location': current_location,
                'quantity': batch.planned_quantity,
                'last_moved_by': user,
                'last_transaction': inv_transaction
            }
        )
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_process_complete_transaction(process_execution, batch, actual_quantity, user):
        """
        Track process completion - batch ready to move to next process
        """
        transaction_id = generate_transaction_id('PROC_COMP')
        
        # Get current location
        try:
            current_location_obj = ProductLocation.objects.get(batch=batch)
            current_location = current_location_obj.current_location
        except ProductLocation.DoesNotExist:
            current_location = InventoryTransactionManager.get_or_create_location('coiling')
        
        notes = f"Process '{process_execution.process.name}' completed for Batch {batch.batch_id}"
        if actual_quantity != batch.planned_quantity:
            notes += f" (Output: {actual_quantity} from {batch.planned_quantity} planned)"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='production',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=current_location,
            location_to=current_location,  # Same location, just marking completion
            quantity=actual_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='process',
            reference_id=str(process_execution.id),
            notes=notes
        )
        
        # Update ProductLocation quantity if there's scrap/loss
        if actual_quantity != batch.planned_quantity:
            try:
                product_location = ProductLocation.objects.get(batch=batch)
                product_location.quantity = actual_quantity
                product_location.last_moved_by = user
                product_location.save()
            except ProductLocation.DoesNotExist:
                pass
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_packing_transaction(batch, user):
        """
        Track movement to packing zone
        """
        transaction_id = generate_transaction_id('PACKING')
        
        packing_location = InventoryTransactionManager.get_or_create_location('packing_zone')
        
        # Get current location
        try:
            current_location_obj = ProductLocation.objects.get(batch=batch)
            location_from = current_location_obj.current_location
        except ProductLocation.DoesNotExist:
            location_from = InventoryTransactionManager.get_or_create_location('final_inspection')
        
        notes = f"Batch {batch.batch_id} moved to Packing Zone"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='transfer',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=location_from,
            location_to=packing_location,
            quantity=batch.actual_quantity_completed or batch.planned_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='mo',
            reference_id=str(batch.mo.id),
            notes=notes
        )
        
        # Update ProductLocation
        ProductLocation.objects.update_or_create(
            batch=batch,
            defaults={
                'current_location': packing_location,
                'quantity': batch.actual_quantity_completed or batch.planned_quantity,
                'last_moved_by': user,
                'last_transaction': inv_transaction
            }
        )
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_fg_store_transaction(batch, user):
        """
        Track movement to FG Store (Finished Goods)
        """
        transaction_id = generate_transaction_id('FG_STORE')
        
        fg_location = InventoryTransactionManager.get_or_create_location('fg')
        
        # Get current location
        try:
            current_location_obj = ProductLocation.objects.get(batch=batch)
            location_from = current_location_obj.current_location
        except ProductLocation.DoesNotExist:
            location_from = InventoryTransactionManager.get_or_create_location('packing_zone')
        
        notes = f"Batch {batch.batch_id} moved to FG Store - Ready for dispatch"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='transfer',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=location_from,
            location_to=fg_location,
            quantity=batch.actual_quantity_completed or batch.planned_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='mo',
            reference_id=str(batch.mo.id),
            notes=notes
        )
        
        # Update ProductLocation
        ProductLocation.objects.update_or_create(
            batch=batch,
            defaults={
                'current_location': fg_location,
                'quantity': batch.actual_quantity_completed or batch.planned_quantity,
                'last_moved_by': user,
                'last_transaction': inv_transaction
            }
        )
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_dispatch_transaction(mo, customer, dispatch_quantity, user, dispatch_notes=""):
        """
        Track dispatch to customer
        """
        transaction_id = generate_transaction_id('DISPATCH')
        
        fg_location = InventoryTransactionManager.get_or_create_location('fg')
        dispatched_location = InventoryTransactionManager.get_or_create_location('dispatched')
        
        notes = f"MO {mo.mo_id} dispatched to {customer.name}"
        if dispatch_notes:
            notes += f" - {dispatch_notes}"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='outward',
            product=mo.product_code,
            manufacturing_order=mo,
            location_from=fg_location,
            location_to=dispatched_location,
            quantity=dispatch_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='mo',
            reference_id=str(mo.id),
            notes=notes
        )
        
        # Update ProductLocation - reduce from FG Store
        for batch in mo.batches.filter(status='completed'):
            try:
                product_location = ProductLocation.objects.get(
                    batch=batch,
                    current_location=fg_location
                )
                
                # Mark as dispatched
                product_location.current_location = dispatched_location
                product_location.last_moved_by = user
                product_location.last_transaction = inv_transaction
                product_location.save()
                
            except ProductLocation.DoesNotExist:
                pass
        
        return inv_transaction
    
    @staticmethod
    @transaction.atomic
    def create_scrap_transaction(batch, scrap_quantity, user, reason=""):
        """
        Track scrap generation
        """
        transaction_id = generate_transaction_id('SCRAP')
        
        # Get current location
        try:
            current_location_obj = ProductLocation.objects.get(batch=batch)
            current_location = current_location_obj.current_location
        except ProductLocation.DoesNotExist:
            current_location = InventoryTransactionManager.get_or_create_location('coiling')
        
        notes = f"Scrap generated from Batch {batch.batch_id}"
        if reason:
            notes += f" - Reason: {reason}"
        
        inv_transaction = InventoryTransaction.objects.create(
            transaction_id=transaction_id,
            transaction_type='scrap',
            product=batch.product_code,
            manufacturing_order=batch.mo,
            location_from=current_location,
            location_to=None,  # Scrap has no destination
            quantity=scrap_quantity,
            transaction_datetime=timezone.now(),
            created_by=user,
            reference_type='mo',
            reference_id=str(batch.mo.id),
            notes=notes
        )
        
        # Update ProductLocation - reduce quantity
        try:
            product_location = ProductLocation.objects.get(batch=batch)
            product_location.quantity -= Decimal(str(scrap_quantity))
            if product_location.quantity <= 0:
                product_location.delete()
            else:
                product_location.last_moved_by = user
                product_location.save()
        except ProductLocation.DoesNotExist:
            pass
        
        return inv_transaction
    
    @staticmethod
    def get_batch_current_location(batch):
        """Get current location of a batch"""
        try:
            product_location = ProductLocation.objects.get(batch=batch)
            return {
                'location': product_location.current_location.get_location_name_display(),
                'location_code': product_location.current_location.location_name,
                'quantity': float(product_location.quantity),
                'last_moved_at': product_location.last_moved_at
            }
        except ProductLocation.DoesNotExist:
            return {
                'location': 'Not tracked',
                'location_code': None,
                'quantity': 0,
                'last_moved_at': None
            }
    
    @staticmethod
    def get_mo_location_summary(mo):
        """Get location summary for all batches in an MO"""
        batches = mo.batches.exclude(status='cancelled')
        
        location_summary = {}
        for batch in batches:
            batch_location = InventoryTransactionManager.get_batch_current_location(batch)
            location = batch_location['location']
            
            if location not in location_summary:
                location_summary[location] = {
                    'batch_count': 0,
                    'total_quantity': 0,
                    'batches': []
                }
            
            location_summary[location]['batch_count'] += 1
            location_summary[location]['total_quantity'] += batch_location['quantity']
            location_summary[location]['batches'].append({
                'batch_id': batch.batch_id,
                'quantity': batch_location['quantity']
            })
        
        return location_summary

