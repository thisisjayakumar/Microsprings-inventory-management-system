"""
RM Return Serializers
"""
from rest_framework import serializers
from django.utils import timezone
from manufacturing.models.rm_return import RMReturn, RMReturnReasonChoices
from manufacturing.models.batch import Batch
from inventory.models import HeatNumber
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class RMReturnSerializer(serializers.ModelSerializer):
    """Serializer for RM Return"""
    batch_id = serializers.CharField(source='batch.batch_id', read_only=True)
    mo_id = serializers.CharField(source='mo.mo_id', read_only=True)
    return_reason_display = serializers.CharField(source='get_return_reason_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = RMReturn
        fields = [
            'id', 'return_id', 'batch', 'batch_id', 'mo', 'mo_id',
            'return_reason', 'return_reason_display', 'returned_kg',
            'heat_number', 'coil_number', 'notes', 'is_processed',
            'processed_at', 'created_at', 'created_by', 'created_by_name'
        ]
        read_only_fields = ['return_id', 'is_processed', 'processed_at', 'created_at']


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
            released_coil_weight = 0
            
            for coil in released_coils:
                if (coil.get('coil_number') == coil_number and 
                    coil.get('heat_number') == heat_number):
                    coil_found = True
                    released_coil_weight = float(coil.get('weight', 0))
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
                total_returned = sum(float(r.returned_kg) for r in existing_returns)
                
                if float(returned_kg) + total_returned > released_coil_weight:
                    raise serializers.ValidationError(
                        f"Cannot return {returned_kg}kg. Coil weight: {released_coil_weight}kg, "
                        f"Already returned: {total_returned}kg, Available to return: {released_coil_weight - total_returned}kg"
                    )
            
            data['batch'] = batch
            data['mo'] = batch.mo
            
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
        
        with transaction.atomic():
            # Create RM return record
            rm_return = RMReturn.objects.create(
                batch=batch,
                mo=mo,
                **validated_data,
                created_by=self.context['request'].user
            )
            
            # Update batch returned quantity
            batch.rm_returned_kg = float(batch.rm_returned_kg or 0) + float(returned_kg)
            batch.save(update_fields=['rm_returned_kg'])
            
            # Process based on return reason
            if return_reason == 'quality_defect':
                # Quality Defect: Remove entire heat number from stock
                try:
                    # Get material_code from MO -> product_code -> material
                    material_code = mo.product_code.material.material_code if mo.product_code and mo.product_code.material else None
                    if not material_code:
                        raise serializers.ValidationError("Cannot determine material code from Manufacturing Order")
                    
                    heat_obj = HeatNumber.objects.get(
                        heat_number=heat_number,
                        raw_material__material_code=material_code
                    )
                    
                    # Mark heat number as unavailable (quarantined)
                    heat_obj.is_available = False
                    heat_obj.save(update_fields=['is_available'])
                    
                    logger.info(f"Heat number {heat_number} quarantined due to quality defect. Batch: {batch.batch_id}")
                    
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
                    
                    # Update consumed quantity (reduce it)
                    heat_obj.consumed_quantity_kg = max(
                        0,
                        float(heat_obj.consumed_quantity_kg) - float(returned_kg)
                    )
                    heat_obj.save(update_fields=['consumed_quantity_kg'])
                    
                    logger.info(f"RM Return processed - {returned_kg}kg returned to heat {heat_number}, coil {coil_number}. Batch: {batch.batch_id}, Reason: {rm_return.get_return_reason_display()}")
                    
                except HeatNumber.DoesNotExist:
                    logger.warning(f"Heat number {heat_number} not found for return processing")
                
                # Mark as processed
                rm_return.is_processed = True
                rm_return.processed_at = timezone.now()
                rm_return.save(update_fields=['is_processed', 'processed_at'])
            
            return rm_return

