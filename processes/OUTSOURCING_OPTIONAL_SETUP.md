# Outsourcing and Optional Process Configuration

This document explains how to configure outsourcing and optional flags for processes and subprocesses.

## Overview

The system supports marking processes/subprocesses as:
- **Outsourced**: Processes handled externally, not by internal supervisors
- **Optional**: Processes that can be skipped if needed

## Setup Workflow

### Step 1: Import Process Configuration

First, import the outsourcing and optional configuration from CSV:

```bash
python manage.py import_process_config management_data/process_config.csv
```

**CSV Format:**
```csv
Parent_Process_Name,Process_Name,Out_Sourcing Process,Optional Process
Coiling,,False,False
Forming,,False,False
Upbend,,False,True
Stress Relieving,,False,False
Coating,Nylon Coating,False,False
Coating,PVC Coating,False,False
Coating,Powder Coating Pastel Blue,True,False
Coating,Blue Coating,False,False
```

**Columns:**
- `Parent_Process_Name`: Main process name (e.g., "Coating", "Coiling")
- `Process_Name`: Subprocess name (e.g., "Nylon Coating", "PVC Coating")
- `Out_Sourcing Process`: "True" or "False" (case-insensitive)
- `Optional Process`: "True" or "False" (case-insensitive)

### Step 2: Import Products and Processes

After importing the configuration, import products and their processes:

```bash
# For spring products
python manage.py import_spring_processes management_data/spring_process.csv

# For stamp/press component products
python manage.py import_stamp_processes management_data/stamp_process.csv
```

**Note:** The import commands will:
- Use existing SubProcess records (with outsourcing/optional flags) if they exist
- Create new SubProcess records with default values (False, False) if they don't exist

## How It Works

### Model Structure

1. **SubProcess Model** has two new fields:
   - `is_outsourced`: Boolean flag for outsourced processes
   - `is_optional`: Boolean flag for optional processes

2. **ProcessStep** has properties:
   - `is_outsourced`: Checks if subprocess is outsourced
   - `is_optional`: Checks if subprocess is optional

3. **BOM** has properties:
   - `is_outsourced`: Checks if process step is outsourced
   - `is_optional`: Checks if process step is optional

### Supervisor Dashboard Filtering

- **Outsourced processes are automatically filtered out** from supervisor dashboards
- Supervisors will not see or be assigned to outsourced processes
- Process initialization (`initialize_processes`) skips outsourced processes

### Process Execution Filtering

- `MOProcessExecutionViewSet` filters out outsourced processes for supervisors
- `supervisor_dashboard` endpoint excludes MOs with only outsourced processes

## Example: Spring Products

For spring products, common outsourced processes:
- **Plating**: Usually outsourced
- **Blackening**: Usually outsourced

These will be marked as `Out_Sourcing Process=True` in the CSV, and supervisors will not see them in their dashboards.

## Example: Stamp Products

For stamp/press component products, some subprocesses might be outsourced:
- Specific coating types
- Special finishing processes

## Verification

To verify the configuration:

```python
from processes.models import SubProcess

# Check outsourced subprocesses
outsourced = SubProcess.objects.filter(is_outsourced=True)
for sp in outsourced:
    print(f"{sp.process.name} -> {sp.name} (Outsourced)")

# Check optional subprocesses
optional = SubProcess.objects.filter(is_optional=True)
for sp in optional:
    print(f"{sp.process.name} -> {sp.name} (Optional)")
```

## Updating Configuration

To update outsourcing/optional flags:

1. Update the CSV file
2. Re-run the import command:
   ```bash
   python manage.py import_process_config management_data/process_config.csv
   ```
3. The command will update existing SubProcess records with new flags

## Migration

After adding the new fields, run migrations:

```bash
python manage.py makemigrations processes
python manage.py migrate processes
```

