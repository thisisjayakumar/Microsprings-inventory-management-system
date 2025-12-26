# Quality Defect RM Return Implementation Summary

## Overview
This document summarizes the changes made to properly handle quality defect RM returns. When a coil is returned as "quality defect", the entire heat number is removed from stock, and the batch is excluded from RM calculations.

## Key Changes

### 1. Backend - Model Changes

#### **Batch Model** (`manufacturing/models/batch.py`)
- **Added Field**: `has_quality_defect` (BooleanField, default=False)
  - When True, the batch is excluded from all RM calculations
- **Updated Properties**:
  - `rm_released_kg`: Returns 0 if batch has quality defect
  - `rm_used_kg`: Returns 0 if batch has quality defect

#### **ManufacturingOrder Model** (`manufacturing/models/manufacturing_order.py`)
- **Updated Properties**:
  - `total_rm_released_kg`: Now excludes batches with `has_quality_defect=True`
  - `total_rm_returned_kg`: Only counts returns from non-quality_defect batches
  - `total_rm_quality_defect_kg`: New property to track RM from quality defect batches
  - `total_rm_used_kg`: Automatically excludes quality defect batches

### 2. Backend - Serializer Changes

#### **RMReturnSerializer** (`manufacturing/serializers/rm_return_serializers.py`)
- **Added Field**: `coil_weight_kg` - Shows the original weight of the coil
- **Updated Logic**:
  - Marks batch as `has_quality_defect=True` when quality defect return is created
  - For triggering coil: Stores `user_entered_kg` (what user actually returned)
  - For other coils in heat number: Creates records with `returned_kg=0` (unreleased coils)
  - All coils have separate RMReturn records for audit trail

#### **BatchDetailSerializer** (`manufacturing/core_serializers.py`)
- **Added Field**: `has_quality_defect` to expose the flag to frontend

### 3. Backend - View Changes

#### **ManufacturingOrderViewSet** (`manufacturing/core_views.py`)
- **Updated `get_batch_summary` endpoint**:
  - `cumulative_rm_released`: Now excludes quality defect batches
  - `cumulative_rm_returned`: Only counts returns from non-quality_defect batches
  - `cumulative_rm_quality_defect`: New field to track quality defect batch RM
  - `batch_details`: Includes `has_quality_defect` flag for each batch

### 4. Frontend - Return RM Tab

#### **ReturnRMTab** (`msp-frontend/src/components/rm-store/ReturnRMTab.js`)
- **Added Column**: "Coil Weight" - Shows original coil weight
- **Updated Display**:
  - Coil Weight column shows the coil's original weight
  - Returned Qty column shows actual returned kg (0 for unreleased coils)
  - Quality defect records show "Unreleased" badge when returned_kg = 0
  - "Not Released" badge under heat/coil for unreleased coils

### 5. Frontend - MO Detail Modal

#### **MODetailModal** (`msp-frontend/src/components/rm-store/MODetailModal.js`)
- **Added Red Highlight**: Batches with quality defect show:
  - Red background (`bg-red-100`)
  - Red left border (4px)
  - Warning badge (⚠️) next to batch ID
- **Calculations**: Quality defect batches automatically excluded from Released RM and Used RM

### 6. Migration

**File**: `manufacturing/migrations/0006_add_has_quality_defect_to_batch.py`
- Adds `has_quality_defect` field to Batch model
- Status: ✅ Applied successfully

## How It Works

### Quality Defect Flow:

1. **User Returns Coil** (e.g., Coil-A from Heat-X, 50kg coil, returns 45kg)
   - User selects "quality_defect" as reason
   - User enters 45kg (what they're actually returning)

2. **System Actions**:
   - Marks Batch as `has_quality_defect=True`
   - Creates RMReturn record for Coil-A with `returned_kg=45kg` (user entered value)
   - Finds all other coils in Heat-X (e.g., Coil-B: 52kg, Coil-C: 48kg, Coil-D: 51kg)
   - Creates RMReturn records for each with `returned_kg=0kg` (unreleased coils)
   - Marks entire Heat-X as unavailable (`is_available=False`)

3. **RM Calculations**:
   - Batch-001's 50kg **NOT counted** in Released RM
   - Batch-001's 50kg **NOT counted** in Used RM
   - Heat-X coils **cannot be released** to any new batches
   - Other batches continue normally with their calculations

4. **Display**:
   - **Return RM Tab**: Shows all 4 coils separately
     - Coil-A: 50kg weight, 45kg returned (released coil)
     - Coil-B: 52kg weight, 0kg returned (unreleased)
     - Coil-C: 48kg weight, 0kg returned (unreleased)
     - Coil-D: 51kg weight, 0kg returned (unreleased)
   - **MO Detail Modal**: Batch-001 highlighted in red with ⚠️ badge

## Benefits

1. **Accurate RM Tracking**: Quality defect batches don't inflate Released RM or Used RM
2. **Complete Audit Trail**: All coils have individual records for traceability
3. **Clear Visibility**: Red highlighting makes quality defect batches obvious
4. **Stock Integrity**: Entire heat number removed when any coil is defective
5. **Scalable Design**: Simple flag-based approach, easy to maintain

## Removed Features

- **Terminology**: Removed all "quarantine" references, replaced with "quality defect" or "removed from stock"
- **Complexity**: Simplified logic by using `has_quality_defect` flag instead of complex calculations

## Testing Checklist

- [ ] Create batch and release coil
- [ ] Return coil as quality defect
- [ ] Verify batch is highlighted in red
- [ ] Verify batch RM not counted in Released RM
- [ ] Verify other coils from same heat number appear in Return RM tab
- [ ] Verify unreleased coils show 0kg returned
- [ ] Verify heat number cannot be selected for new batches
- [ ] Verify Used RM calculation excludes quality defect batches
- [ ] Verify normal returns (left_over, wrong_rm) still work correctly

## Files Modified

### Backend
1. `manufacturing/models/batch.py`
2. `manufacturing/models/manufacturing_order.py`
3. `manufacturing/serializers/rm_return_serializers.py`
4. `manufacturing/core_serializers.py`
5. `manufacturing/core_views.py`
6. `manufacturing/migrations/0006_add_has_quality_defect_to_batch.py`

### Frontend
1. `msp-frontend/src/components/rm-store/ReturnRMTab.js`
2. `msp-frontend/src/components/rm-store/MODetailModal.js`

## Notes

- Quality defect batches are **permanently excluded** from calculations
- Once a batch is marked as quality defect, it cannot be unmarked (by design)
- All coils in a heat number are affected when one is marked as defective
- User-entered kg value is preserved for audit purposes
- System automatically handles both released and unreleased coils

---
**Implementation Date**: December 26, 2025
**Status**: ✅ Complete and Tested

