# Unified CSV Format for Spring and Stamp Process Import

This document describes the unified CSV format for importing both Spring and Stamp (Press Component) products and their manufacturing processes.

## CSV Header Format

The unified CSV should include the following columns:

### Required Columns (All Product Types)
- `S.No` - Serial number (optional, for reference)
- `Product_Code` - Unique product identifier (REQUIRED)
- `Product_Type` - Product type: "Spring" or "Press Component" (REQUIRED)
- `Material_Code` - Raw material code (REQUIRED)
- `Cust_ID` - Customer ID (optional)
- `gram/product` - Weight in grams per product (optional)

### Spring-Specific Columns
- `Spring_Type` - Type of spring (e.g., "TENSION SPRING", "COMPRESSION SPRING", etc.)
- `IPC_Code` - Internal Product Code (optional)

### Stamp/Press Component-Specific Columns
- `Sheet_Length_L` - Sheet length in mm (for whole sheet dimensions)
- `Sheet_Breadth_B` - Sheet breadth in mm (for whole sheet dimensions)
- `Strip_Length_L` - Strip length in mm (for cut strip dimensions)
- `Strip_Breadth_B` - Strip breadth in mm (for cut strip dimensions)
- `Strip_Count` - Number of strips per sheet
- `Pcs_per_Strip` - Number of pieces per strip
- `Pcs_per_Sheet` - Total pieces per sheet

### Process Columns

#### Spring Process Columns (use when Product_Type = "Spring")
- `Coiling/Forming` - Coiling or forming subprocess name
- `Stress_Relieving` - Stress relieving subprocess name
- `Coating` - Coating subprocess name
- `Grinding` - Grinding subprocess name
- `Plating` - Plating subprocess name
- `Blackening` - Blackening subprocess name

#### Stamp Process Columns (use when Product_Type = "Press Component")
- `Process 1` - First process step subprocess name
- `Process 2` - Second process step subprocess name
- `Process 3` - Third process step subprocess name
- `Process 4` - Fourth process step subprocess name
- `Process 5` - Fifth process step subprocess name
- `Process 6` - Sixth process step subprocess name
- `Process 7` - Seventh process step subprocess name

## Usage

### Command Syntax
```bash
python manage.py import_unified_processes <csv_file_path> [--dry-run]
```

### Examples

#### Dry Run (Preview without saving)
```bash
python manage.py import_unified_processes management_data/unified_processes.csv --dry-run
```

#### Actual Import
```bash
python manage.py import_unified_processes management_data/unified_processes.csv
```

## CSV Format Rules

1. **Product Type Detection**: The command automatically detects the product type from the `Product_Type` column:
   - "Spring" → Creates spring products with spring-specific fields
   - "Press Component" → Creates press_component products with stamp-specific fields

2. **Process Columns**: 
   - For Spring products, only Spring process columns are processed
   - For Press Component products, only Stamp process columns (Process 1-7) are processed
   - Empty process columns are skipped

3. **Field Mapping**:
   - Spring products: `gram/product` → `grams_per_product`, `Spring_Type` → `spring_type`, `IPC_Code` → `internal_product_code`
   - Press Component products: Sheet/Strip dimensions are mapped to Product and BOM fields

4. **BOM Creation**:
   - Each non-empty process column creates a Process, SubProcess, ProcessStep, and BOM entry
   - Spring BOMs have type="spring"
   - Stamp BOMs have type="stamp" and include dimension fields

## Example CSV Rows

### Spring Product Example
```csv
S.No,Product_Code,Product_Type,Spring_Type,Material_Code,Cust_ID,IPC_Code,gram/product,Sheet_Length_L,Sheet_Breadth_B,Strip_Length_L,Strip_Breadth_B,Strip_Count,Pcs_per_Strip,Pcs_per_Sheet,Coiling/Forming,Stress_Relieving,Coating,Grinding,Plating,Blackening,Process 1,Process 2,Process 3,Process 4,Process 5,Process 6,Process 7
1,SPRING-001,Spring,TENSION SPRING,Coil-ST Spring Steel-DH-1.80mm,C_001,IPC-001,8.000,,,,,,,,Coiling,Stress_Relieving,,,Olive_Green,,,,
```

### Stamp/Press Component Product Example
```csv
S.No,Product_Code,Product_Type,Spring_Type,Material_Code,Cust_ID,IPC_Code,gram/product,Sheet_Length_L,Sheet_Breadth_B,Strip_Length_L,Strip_Breadth_B,Strip_Count,Pcs_per_Strip,Pcs_per_Sheet,Coiling/Forming,Stress_Relieving,Coating,Grinding,Plating,Blackening,Process 1,Process 2,Process 3,Process 4,Process 5,Process 6,Process 7
2,STAMP-001,Press Component,,Sheet-SS Stainless Steel-304-1.50mm,C_003,IPC-002,21.000,2500,1250,560,50,110,13,1430,,,,,,Piercing & Parting,Deburring,Ironing,1.6mm Pirecing,Champering,Bending,
```

## Notes

- Empty cells are allowed and will be skipped
- Material codes must exist in the RawMaterial table
- Customer IDs are optional but must exist in the Customer table if provided
- The command maintains the same insertion flow as the original separate commands
- All operations are wrapped in a database transaction for data integrity

