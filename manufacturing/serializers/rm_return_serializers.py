"""
RM Return Serializers
"""
from rest_framework import serializers
from django.utils import timezone
from manufacturing.models.rm_return import RMReturn, RMReturnReasonChoices
from manufacturing.models.batch import Batch
from inventory.models import HeatNumber, RMStockBalance, RMStockBalanceHeat
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class RMReturnSerializer(serializers.ModelSerializer):
    """Serializer for RM Return"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    return_reason_display = serializers.CharField(source='get_return_reason_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    # Material details from MO
    material_code = serializers.SerializerMethodField()
    material_name = serializers.SerializerMethodField()
    grn_number = serializers.SerializerMethodField()
    
    class Meta:
        model = RMReturn
        fields = [
            'id', 'return_id', 'batch', 'batch_id', 'mo', 'mo_id',
            'return_reason', 'return_reason_display', 'returned_kg', 'user_entered_kg',
            'heat_number', 'coil_number', 'material_code', 'material_name',
            'grn_number', 'notes', 'is_processed',
            'processed_at', 'created_at', 'created_by', 'created_by_name'
        ]
        read_only_fields = ['return_id', 'is_processed', 'processed_at', 'created_at']
    
    def get_material_code(self, obj):
        """Get material code from MO"""
        if obj.mo and obj.mo.product_code and obj.mo.product_code.material:
            return obj.mo.product_code.material.material_code
        return None
    
    def get_material_name(self, obj):
        """Get material name from MO"""
        if obj.mo and obj.mo.product_code and obj.mo.product_code.material:
            return obj.mo.product_code.material.material_name
        return None
    
    def get_grn_number(self, obj):
        """Get GRN number from heat number"""
        try:
            if obj.mo and obj.mo.product_code and obj.mo.product_code.material:
                material_code = obj.mo.product_code.material.material_code
                heat_obj = HeatNumber.objects.filter(
                    heat_number=obj.heat_number,
                    raw_material__material_code=material_code
                ).first()
                if heat_obj:
                    return heat_obj.grn_number
        except Exception:
            pass
        return None


class RMReturnCreateSerializer(serializers.Serializer):
    """Serializer for creating RM returns"""
    batch_id = serializers.IntegerField(required=True, help_text="Batch ID from which RM is being returned")
    return_reason = serializers.ChoiceField(
        choices=RMReturnReasonChoices.choices,
        required=True,
        help_text="Reason for return: left_over, quality_defect, or wrong_rm"
    )
    coil_number = serializers.CharField(required=True, help_text="Coil number to return")
    heat_number = serializers.CharField(required=True, help_text="Heat number of the coil")
    returned_kg = serializers.DecimalField(
        max_digits=10,
        decimal_places=3,
        required=True,
        help_text="Quantity being returned in kg"
    )
    notes = serializers.CharField(required=False, allow_blank=True, help_text="Additional notes")
    
    def validate_batch_id(self, value):
        """Validate batch exists"""
        try:
            batch = Batch.objects.get(id=value)
            return value
        except Batch.DoesNotExist:
            raise serializers.ValidationError("Batch not found")
    
    def validate_returned_kg(self, value):
        """Validate returned quantity is positive"""
        if value <= 0:
            raise serializers.ValidationError("Returned quantity must be greater than 0")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        batch_id = data.get('batch_id')
        coil_number = data.get('coil_number')
        heat_number = data.get('heat_number')
        returned_kg = data.get('returned_kg')
        return_reason = data.get('return_reason')
        
        try:
            # Fetch batch with related MO, product_code, and material to avoid additional queries
            batch = Batch.objects.select_related(
                'mo',
                'mo__product_code',
                'mo__product_code__material'
            ).get(id=batch_id)
            
            # Validate that the coil was actually released in this batch
            released_coils = batch.released_coils or []
            coil_found = False
            released_coil_weight = Decimal('0')
            
            for coil in released_coils:
                if (coil.get('coil_number') == coil_number and 
                    coil.get('heat_number') == heat_number):
                    coil_found = True
                    # Convert to Decimal to avoid type mixing errors
                    released_coil_weight = Decimal(str(coil.get('weight', 0)))
                    break
            
            if not coil_found:
                raise serializers.ValidationError(
                    f"Coil {coil_number} from Heat {heat_number} was not released in this batch"
                )
            
            # For quality defect, we don't validate quantity (entire heat number is quarantined)
            if return_reason != 'quality_defect':
                # Validate returned quantity doesn't exceed released quantity
                # Calculate already returned quantity for this coil
                existing_returns = RMReturn.objects.filter(
                    batch=batch,
                    coil_number=coil_number,
                    heat_number=heat_number,
                    return_reason__in=['left_over', 'wrong_rm']  # Only count these for quantity validation
                )
                # Convert all to Decimal for consistent calculations
                total_returned = sum(Decimal(str(r.returned_kg)) for r in existing_returns)
                returned_kg_decimal = Decimal(str(returned_kg))
                
                if returned_kg_decimal + total_returned > released_coil_weight:
                    available_to_return = released_coil_weight - total_returned
                    raise serializers.ValidationError(
                        f"Cannot return {returned_kg}kg. Coil weight: {released_coil_weight}kg, "
                        f"Already returned: {total_returned}kg, Available to return: {available_to_return}kg"
                    )
            
            data['batch'] = batch
            data['mo'] = batch.mo
            data['released_coil_weight'] = released_coil_weight  # Store as Decimal for use in create()
            
        except Batch.DoesNotExist:
            raise serializers.ValidationError("Batch not found")
        
        return data
    
    def create(self, validated_data):
        """Create RM return and update stock accordingly"""
        from django.db import transaction
        from inventory.models import HeatNumber
        from notifications.models import WorkflowNotification
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        
        batch = validated_data.pop('batch')
        mo = validated_data.pop('mo')
        return_reason = validated_data['return_reason']
        returned_kg = validated_data['returned_kg']
        heat_number = validated_data['heat_number']
        coil_number = validated_data['coil_number']
        released_coil_weight = validated_data.pop('released_coil_weight', 0)  # Get from validation
        
        # Store user's entered value for audit purposes
        user_entered_kg = Decimal(str(returned_kg))
        
        with transaction.atomic():
            # Prepare notes for quality_defect to indicate entire heat number quarantine
            # Note: For quality_defect, we'll update returned_kg after calculating available quantity
            notes = validated_data.get('notes', '')
            if return_reason == 'quality_defect':
                # We'll update the notes after we know the actual returned quantity
                # For now, just prepare the base note
                if notes:
                    notes += f"\n\n⚠️ QUALITY DEFECT: Entire heat number {heat_number} has been quarantined. "
                else:
                    notes = f"⚠️ QUALITY DEFECT: Entire heat number {heat_number} has been quarantined. "
                notes += f"All available coils under this heat number are being returned. "
                notes += f"This return (coil {coil_number}) triggered the quarantine. "
                notes += f"User entered: {user_entered_kg}kg. "
                notes += f"Actual returned kg will reflect the complete available quantity of the heat number."
            
            # Create RM return record
            # Store both user_entered_kg (for audit) and returned_kg (will be updated for quality_defect)
            rm_return = RMReturn.objects.create(
                batch=batch,
                mo=mo,
                notes=notes,
                user_entered_kg=user_entered_kg,  # Store user's input for audit
                **{k: v for k, v in validated_data.items() if k != 'notes'},
                created_by=self.context['request'].user
            )
            
            # Update batch returned quantity
            # For quality_defect, we'll update this after calculating the actual available quantity
            # For other reasons, update immediately with user's input
            if return_reason != 'quality_defect':
                # Convert both to Decimal to avoid type mixing errors
                existing_returned = Decimal(str(batch.rm_returned_kg or 0))
                returned_kg_decimal = Decimal(str(returned_kg))
                batch.rm_returned_kg = existing_returned + returned_kg_decimal
                batch.save(update_fields=['rm_returned_kg'])
            
            # Process based on return reason
            if return_reason == 'quality_defect':
                # Quality Defect: Remove entire heat number from stock (quarantine all coils)
                # For quality defect, we return ALL available quantity in the heat number,
                # not just what the user entered
                try:
                    # Get material_code from MO -> product_code -> material
                    material_code = mo.product_code.material.material_code if mo.product_code and mo.product_code.material else None
                    if not material_code:
                        raise serializers.ValidationError("Cannot determine material code from Manufacturing Order")
                    
                    heat_obj = HeatNumber.objects.get(
                        heat_number=heat_number,
                        raw_material__material_code=material_code
                    )
                    
                    # Calculate total available quantity (all available coils in the heat number)
                    total_weight = heat_obj.total_weight_kg or Decimal('0')
                    old_consumed = heat_obj.consumed_quantity_kg
                    available_quantity = total_weight - old_consumed
                    
                    # Get all coils from the heat number's items array first
                    # We need this to calculate individual coil weights
                    items = heat_obj.items if heat_obj.items else []
                    if not isinstance(items, list):
                        items = []
                    
                    # Get all available coils (those still in items array - not yet consumed)
                    available_coils = []
                    total_coils_weight = Decimal('0')
                    for item in items:
                        item_coil_number = item.get('coil_number') or item.get('number', '')
                        item_weight = Decimal(str(item.get('weight') or item.get('weight_kg', 0)))
                        if item_coil_number and item_weight > 0:
                            available_coils.append({
                                'coil_number': item_coil_number,
                                'weight': item_weight
                            })
                            total_coils_weight += item_weight
                    
                    # Find the triggering coil's actual weight from available_coils
                    # Only count coils that are actually in the items array (available)
                    triggering_coil_weight = None
                    triggering_coil_in_items = False
                    for coil_data in available_coils:
                        if coil_data['coil_number'] == coil_number:
                            triggering_coil_weight = coil_data['weight']
                            triggering_coil_in_items = True
                            break
                    
                    # If triggering coil not found in items, it means it was already consumed/released
                    # In this case, we should only quarantine the coils that are actually available
                    if triggering_coil_weight is None:
                        logger.warning(
                            f"Triggering coil {coil_number} not found in heat number {heat_number} items. "
                            f"This coil was already consumed/released. Only available coils will be quarantined."
                        )
                        # Use 0 for triggering coil since it's not available
                        triggering_coil_weight = Decimal('0')
                    
                    # Use the sum of individual coil weights for batch returned quantity
                    actual_returned_total = total_coils_weight if total_coils_weight > 0 else available_quantity
                    
                    # For quality defect, update returned_kg based on whether triggering coil is in items
                    # If triggering coil is in items: use its weight, and create records for other coils
                    # If triggering coil is NOT in items: delete the main return record and only create records for available coils
                    # This avoids double-counting in quarantined calculation
                    if triggering_coil_in_items and triggering_coil_weight > 0:
                        # Triggering coil is in items - use its weight for main record
                        rm_return.returned_kg = triggering_coil_weight  # Triggering coil's actual weight
                        rm_return.save(update_fields=['returned_kg'])
                    elif not triggering_coil_in_items and actual_returned_total > 0:
                        # Triggering coil not in items (was already consumed)
                        # Delete the main return record since it references a coil that doesn't exist
                        # We'll create records only for available coils below
                        rm_return.delete()
                        rm_return = None  # Mark as deleted
                        logger.info(
                            f"Triggering coil {coil_number} not in items (already consumed). "
                            f"Deleted main return record. Will create records only for available coils: {actual_returned_total}kg total"
                        )
                    else:
                        logger.warning(
                            f"No available quantity for triggering coil {coil_number} in heat number {heat_number}."
                        )
                    
                    # DO NOT update batch.rm_returned_kg for quality_defect returns
                    # Quality defect returns are quarantined and should NOT be counted in batch returned quantity
                    # They are completely removed from available stock and don't affect used RM calculations
                    # The batch summary endpoint will calculate quarantined RM separately by querying RMReturn records
                    logger.info(
                        f"Quality defect return: NOT updating batch.rm_returned_kg. "
                        f"Quarantined RM ({actual_returned_total}kg) will be tracked separately in batch summary."
                    )
                    
                    # Update the original return record (for the triggering coil) if it still exists
                    if rm_return:
                        updated_notes = rm_return.notes or ''
                        if available_coils:
                            coil_list = ', '.join([f"{c['coil_number']} ({c['weight']}kg)" for c in available_coils])
                            if 'Actual returned kg will reflect' in updated_notes:
                                updated_notes = updated_notes.replace(
                                    'Actual returned kg will reflect the complete available quantity of the heat number.',
                                    f'Entire heat number quarantined. All coils returned: {coil_list}. Total: {actual_returned_total}kg. '
                                    f'This coil ({coil_number}) returned: {triggering_coil_weight}kg.'
                                )
                            else:
                                if updated_notes:
                                    updated_notes += f"\n\nEntire heat number quarantined. All coils returned: {coil_list}. Total: {actual_returned_total}kg. "
                                    updated_notes += f"This coil ({coil_number}) returned: {triggering_coil_weight}kg."
                                else:
                                    updated_notes = f"User entered: {rm_return.user_entered_kg}kg. Entire heat number quarantined. All coils returned: {coil_list}. Total: {actual_returned_total}kg. "
                                    updated_notes += f"This coil ({coil_number}) returned: {triggering_coil_weight}kg."
                        else:
                            if 'Actual returned kg will reflect' in updated_notes:
                                updated_notes = updated_notes.replace(
                                    'Actual returned kg will reflect the complete available quantity of the heat number.',
                                    f'Actual returned: {triggering_coil_weight}kg (this coil\'s available quantity).'
                                )
                            else:
                                if updated_notes:
                                    updated_notes += f"\n\nActual returned: {triggering_coil_weight}kg (this coil's available quantity)."
                                else:
                                    updated_notes = f"User entered: {rm_return.user_entered_kg}kg. Actual returned: {triggering_coil_weight}kg (this coil's available quantity)."
                        rm_return.notes = updated_notes
                        rm_return.save(update_fields=['notes'])
                    
                    # Create RMReturn records for all available coils in the heat number
                    # This ensures each coil has its own audit trail in the transaction log
                    # IMPORTANT: Only create records for coils that are actually in the items array (available)
                    created_return_ids = []
                    
                    if rm_return:
                        # Main return record exists (triggering coil was in items)
                        created_return_ids.append(rm_return.return_id)
                    
                    # Create records for all available coils
                    # If triggering coil was in items, skip it (already has a record)
                    # If triggering coil was NOT in items, create records for all available coils
                    for coil_data in available_coils:
                        other_coil_number = coil_data['coil_number']
                        other_coil_weight = coil_data['weight']
                        
                        # Skip if this is the triggering coil AND it was in items (already has a record)
                        if other_coil_number == coil_number and triggering_coil_in_items:
                            continue
                        
                        # Create RMReturn record for this coil with its actual weight
                        other_rm_return = RMReturn.objects.create(
                            batch=batch,
                            mo=mo,
                            return_reason=return_reason,
                            returned_kg=other_coil_weight,  # This coil's actual available weight
                            user_entered_kg=user_entered_kg,  # Same user input for all
                            heat_number=heat_number,
                            coil_number=other_coil_number,
                            notes=f"⚠️ QUALITY DEFECT: Quarantined as part of heat number {heat_number} quality defect. "
                                  f"Triggered by coil {coil_number} return (user entered: {user_entered_kg}kg). "
                                  + (f"Note: Triggering coil {coil_number} was already consumed/released. " if not triggering_coil_in_items else "") +
                                  f"Coil {other_coil_number} actual returned: {other_coil_weight}kg (original available quantity).",
                            is_processed=True,
                            processed_at=timezone.now(),
                            created_by=self.context['request'].user
                        )
                        created_return_ids.append(other_rm_return.return_id)
                        
                        logger.info(
                            f"Created quality defect return record for coil {other_coil_number} "
                            f"(heat {heat_number}): Return ID {other_rm_return.return_id}, "
                            f"Actual returned: {other_coil_weight}kg (original available quantity)"
                        )
                    
                    # Mark entire heat number as consumed and unavailable
                    # This quarantines all coils in the heat number
                    heat_obj.consumed_quantity_kg = total_weight
                    heat_obj.is_available = False
                    heat_obj.save(update_fields=['consumed_quantity_kg', 'is_available'])
                    
                    # Update stock balances
                    heat_obj.update_stock_balance()
                    
                    # Also update basic RMStockBalance
                    from inventory.models import RMStockBalance
                    try:
                        stock_balance = RMStockBalance.objects.get(raw_material=heat_obj.raw_material)
                        total_available = sum(
                            Decimal(str(heat.get_available_quantity_kg())) 
                            for heat in HeatNumber.objects.filter(
                                raw_material=heat_obj.raw_material,
                                is_available=True
                            )
                        )
                        stock_balance.available_quantity = total_available
                        stock_balance.save()
                    except RMStockBalance.DoesNotExist:
                        # Create if doesn't exist
                        total_available = sum(
                            Decimal(str(heat.get_available_quantity_kg())) 
                            for heat in HeatNumber.objects.filter(
                                raw_material=heat_obj.raw_material,
                                is_available=True
                            )
                        )
                        RMStockBalance.objects.create(
                            raw_material=heat_obj.raw_material,
                            available_quantity=total_available
                        )
                    
                    # Note: Inventory transaction creation is skipped for quality_defect returns
                    # because InventoryTransaction model requires a Product, but we're dealing with RawMaterial.
                    # The RMReturn record itself serves as the audit trail and will appear in the Return RM tab.
                    # The heat number quarantine (is_available=False) and consumed_quantity_kg update
                    # provide the stock adjustment tracking.
                    logger.info(
                        f"Quality defect return logged: {len(created_return_ids)} return record(s) created. "
                        f"Return IDs: {', '.join(created_return_ids) if created_return_ids else 'N/A'}. "
                        f"Entire heat number {heat_number} quarantined. "
                        f"Total weight: {total_weight}kg, Available (returned): {actual_returned_total}kg, "
                        f"Previously consumed: {old_consumed}kg. "
                        f"Coils quarantined: {len(available_coils)} coil(s). "
                        f"Triggering coil in items: {triggering_coil_in_items}"
                    )
                    
                    logger.info(
                        f"Heat number {heat_number} quarantined due to quality defect. "
                        f"Batch: {batch.batch_id}, Triggering coil: {coil_number}. "
                        f"Consumed: {old_consumed} -> {total_weight}kg (entire heat number). "
                        f"Total quarantined: {actual_returned_total}kg across {len(available_coils)} available coil(s). "
                        f"All available coils now have individual return records in transaction log."
                    )
                    
                except HeatNumber.DoesNotExist:
                    logger.warning(f"Heat number {heat_number} not found for quality defect processing")
                
                # Mark as processed
                rm_return.is_processed = True
                rm_return.processed_at = timezone.now()
                rm_return.save(update_fields=['is_processed', 'processed_at'])
                
            elif return_reason in ['left_over', 'wrong_rm']:
                # Left Over / Wrong RM: Add back to stock with same heat/coil
                try:
                    # Get material_code from MO -> product_code -> material
                    material_code = mo.product_code.material.material_code if mo.product_code and mo.product_code.material else None
                    if not material_code:
                        raise serializers.ValidationError("Cannot determine material code from Manufacturing Order")
                    
                    heat_obj = HeatNumber.objects.get(
                        heat_number=heat_number,
                        raw_material__material_code=material_code
                    )
                    
                    # DEBUG: Log initial state
                    logger.info(f"[RM Return DEBUG] Heat Number ID: {heat_obj.id}, Heat: {heat_number}")
                    logger.info(f"[RM Return DEBUG] Initial consumed_quantity_kg: {heat_obj.consumed_quantity_kg}")
                    logger.info(f"[RM Return DEBUG] Initial items (type: {type(heat_obj.items)}): {heat_obj.items}")
                    logger.info(f"[RM Return DEBUG] Coil to return: {coil_number}, Weight: {returned_kg}kg")
                    
                    # Calculate the original coil weight from released_coils in batch
                    # This helps us determine how much was actually consumed
                    original_coil_weight = released_coil_weight  # Already Decimal from validation
                    logger.info(f"[RM Return DEBUG] Original coil weight from batch: {original_coil_weight}kg")
                    
                    # Update consumed quantity logic:
                    # If coil exists in items, calculate consumed based on original weight vs returned
                    # consumed = original_weight - returned_weight
                    # This ensures available_quantity_kg = total_weight_kg - consumed_quantity_kg shows correct available
                    coil_in_items = None
                    for item in (heat_obj.items or []):
                        if isinstance(item, dict):
                            item_coil = item.get('coil_number') or item.get('number', '')
                            if str(item_coil).upper() == str(coil_number).upper():
                                coil_in_items = item
                                break
                    
                    if coil_in_items:
                        # Coil exists in items - calculate consumed based on original vs current
                        # If we're returning 50kg from a 53kg coil, consumed should be 3kg
                        current_item_weight = Decimal(str(coil_in_items.get('weight') or coil_in_items.get('weight_kg', 0)))
                        logger.info(f"[RM Return DEBUG] Current item weight in DB: {current_item_weight}kg")
                        
                        # Calculate consumed: original_weight - returned_weight
                        # This ensures the math works: available = total - consumed
                        # Both are already Decimal, but ensure returned_kg is Decimal
                        returned_kg_decimal = Decimal(str(returned_kg))
                        calculated_consumed = max(Decimal('0'), original_coil_weight - returned_kg_decimal)
                        old_consumed = heat_obj.consumed_quantity_kg
                        
                        # Update consumed_quantity_kg to reflect what was actually consumed
                        # If returning 50kg from 53kg coil, consumed = 3kg
                        heat_obj.consumed_quantity_kg = calculated_consumed
                        logger.info(f"[RM Return DEBUG] Updated consumed_quantity_kg: {old_consumed} -> {calculated_consumed} (original: {original_coil_weight}kg - returned: {returned_kg_decimal}kg)")
                    else:
                        # Coil not in items - reduce consumed by returned amount
                        old_consumed = heat_obj.consumed_quantity_kg
                        returned_kg_decimal = Decimal(str(returned_kg))
                        new_consumed = max(Decimal('0'), old_consumed - returned_kg_decimal)
                        heat_obj.consumed_quantity_kg = new_consumed
                        logger.info(f"[RM Return DEBUG] Updated consumed_quantity_kg: {old_consumed} -> {new_consumed} (reduced by returned amount)")
                    
                    logger.info(f"[RM Return DEBUG] Total weight: {heat_obj.total_weight_kg}, Available after return: {heat_obj.get_available_quantity_kg()}")
                    
                    # Add the coil back to heat number's items array if it doesn't exist
                    # This makes it available again in RM stock inventory
                    # IMPORTANT: Create a new list to ensure Django detects the change
                    items = list(heat_obj.items) if heat_obj.items else []
                    if not isinstance(items, list):
                        logger.warning(f"[RM Return DEBUG] Items is not a list, converting. Type: {type(items)}, Value: {items}")
                        items = []
                    
                    logger.info(f"[RM Return DEBUG] Items before modification: {items}")
                    logger.info(f"[RM Return DEBUG] Number of items before: {len(items)}")
                    
                    # Check if coil already exists in items
                    coil_exists = False
                    coil_index = -1
                    for idx, item in enumerate(items):
                        if not isinstance(item, dict):
                            logger.warning(f"[RM Return DEBUG] Item at index {idx} is not a dict: {item}")
                            continue
                        item_coil_number = item.get('coil_number') or item.get('number', '')
                        logger.info(f"[RM Return DEBUG] Checking item {idx}: coil_number={item_coil_number}, comparing with {coil_number}")
                        if str(item_coil_number).upper() == str(coil_number).upper():
                            coil_exists = True
                            coil_index = idx
                            logger.info(f"[RM Return DEBUG] Coil {coil_number} found at index {idx}")
                            # The coil exists - update its weight to the returned amount (available quantity)
                            # This makes it available again in stock with the returned weight
                            old_weight = item.get('weight') or item.get('weight_kg', 0)
                            # Convert to float for JSON storage (JSONField stores numbers as float)
                            returned_kg_float = float(Decimal(str(returned_kg)))
                            items[idx] = {
                                **item,  # Preserve other fields
                                'coil_number': coil_number,
                                'number': coil_number,  # Support both field names
                                'weight': returned_kg_float,  # Available weight = returned amount
                                'weight_kg': returned_kg_float
                            }
                            logger.info(f"[RM Return DEBUG] Updated item weight: {old_weight} -> {returned_kg} (returned amount)")
                            break
                    
                    # If coil doesn't exist in items, add it back with the returned weight
                    # This happens when the coil was completely consumed and removed from items
                    if not coil_exists:
                        logger.info(f"[RM Return DEBUG] Coil {coil_number} not found in items, adding back with returned weight")
                        # Convert to float for JSON storage (JSONField stores numbers as float)
                        returned_kg_float = float(Decimal(str(returned_kg)))
                        new_item = {
                            'coil_number': coil_number,
                            'number': coil_number,  # Support both field names
                            'weight': returned_kg_float,  # Available weight = returned amount
                            'weight_kg': returned_kg_float
                        }
                        items.append(new_item)
                        logger.info(f"[RM Return DEBUG] Added new item: {new_item}")
                    
                    logger.info(f"[RM Return DEBUG] Items after modification: {items}")
                    logger.info(f"[RM Return DEBUG] Number of items after: {len(items)}")
                    
                    # IMPORTANT: Assign a new list to ensure Django detects the change
                    # Django's JSONField needs a new reference to detect changes
                    heat_obj.items = items
                    
                    # DEBUG: Verify the assignment
                    logger.info(f"[RM Return DEBUG] heat_obj.items after assignment: {heat_obj.items}")
                    logger.info(f"[RM Return DEBUG] heat_obj.items type: {type(heat_obj.items)}")
                    logger.info(f"[RM Return DEBUG] heat_obj.items is items: {heat_obj.items is items}")
                    
                    # Ensure heat number is marked as available when returning RM
                    # This is critical - if is_available=False, it won't be included in stock balance calculations
                    if not heat_obj.is_available:
                        heat_obj.is_available = True
                        logger.info(f"[RM Return DEBUG] Marking heat number {heat_number} as available")
                    
                    # Save the heat number with all updated fields
                    heat_obj.save(update_fields=['consumed_quantity_kg', 'items', 'is_available'])
                    
                    # DEBUG: Refresh from DB to verify the save
                    heat_obj.refresh_from_db()
                    logger.info(f"[RM Return DEBUG] After save and refresh - items: {heat_obj.items}")
                    logger.info(f"[RM Return DEBUG] After save and refresh - consumed_quantity_kg: {heat_obj.consumed_quantity_kg}")
                    logger.info(f"[RM Return DEBUG] After save and refresh - is_available: {heat_obj.is_available}")
                    
                    # Update stock balance to reflect the returned quantity
                    # This updates RMStockBalanceHeat
                    heat_obj.update_stock_balance()
                    
                    # Also update the basic RMStockBalance if it exists
                    # This ensures the main stock display shows the correct quantity
                    try:
                        stock_balance = RMStockBalance.objects.get(raw_material=heat_obj.raw_material)
                        # Calculate total available quantity from all available heat numbers
                        total_available = sum(
                            Decimal(str(heat.get_available_quantity_kg())) 
                            for heat in HeatNumber.objects.filter(
                                raw_material=heat_obj.raw_material,
                                is_available=True
                            )
                        )
                        stock_balance.available_quantity = total_available
                        stock_balance.save()
                        logger.info(f"[RM Return DEBUG] Updated RMStockBalance: {heat_obj.raw_material.material_code} = {total_available}kg")
                    except RMStockBalance.DoesNotExist:
                        # Create RMStockBalance if it doesn't exist
                        total_available = sum(
                            Decimal(str(heat.get_available_quantity_kg())) 
                            for heat in HeatNumber.objects.filter(
                                raw_material=heat_obj.raw_material,
                                is_available=True
                            )
                        )
                        RMStockBalance.objects.create(
                            raw_material=heat_obj.raw_material,
                            available_quantity=total_available
                        )
                        logger.info(f"[RM Return DEBUG] Created RMStockBalance: {heat_obj.raw_material.material_code} = {total_available}kg")
                    
                    logger.info(f"RM Return processed - {returned_kg}kg returned to heat {heat_number}, coil {coil_number}. Batch: {batch.batch_id}, Reason: {rm_return.get_return_reason_display()}")
                    
                except HeatNumber.DoesNotExist:
                    logger.warning(f"Heat number {heat_number} not found for return processing")
                    raise serializers.ValidationError(f"Heat number {heat_number} not found for material {material_code}")
                
                # Mark as processed
                rm_return.is_processed = True
                rm_return.processed_at = timezone.now()
                rm_return.save(update_fields=['is_processed', 'processed_at'])
            
            return rm_return

