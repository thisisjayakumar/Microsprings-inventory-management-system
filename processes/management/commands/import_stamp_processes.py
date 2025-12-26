# processes/management/commands/import_stamp_processes.py
import csv
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand
from django.db import transaction
from processes.models import Process, SubProcess, ProcessStep, BOM
from products.models import Product
from inventory.models import RawMaterial
from third_party.models import Customer


class Command(BaseCommand):
    help = "Import stamp products and processes from updated CSV format with sheet/strip dimensions"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to Stamp processes CSV")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving to the database",
        )

    def safe_decimal(self, value, row_num, field_name):
        """Safely convert a value to Decimal, returning None if invalid"""
        if not value or not str(value).strip():
            return None
        try:
            return Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            self.stdout.write(
                self.style.WARNING(
                    f"Row {row_num}: Invalid {field_name} value '{value}'. Setting to None."
                )
            )
            return None

    def safe_int(self, value, row_num, field_name):
        """Safely convert a value to int, returning None if invalid"""
        if not value or not str(value).strip():
            return None
        try:
            return int(float(str(value).strip()))
        except (ValueError, TypeError):
            self.stdout.write(
                self.style.WARNING(
                    f"Row {row_num}: Invalid {field_name} value '{value}'. Setting to None."
                )
            )
            return None

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        dry_run = options["dry_run"]
        
        created_products = 0
        updated_products = 0
        created_boms = 0
        updated_boms = 0
        skipped_rows = 0

        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
            # Verify required columns exist
            required_cols = ["Product_Code", "Material_Code"]
            if not all(col in reader.fieldnames for col in required_cols):
                self.stdout.write(
                    self.style.ERROR(
                        f"CSV missing required columns. Found: {reader.fieldnames}"
                    )
                )
                return
            
            with transaction.atomic():
                for row_num, row in enumerate(reader, start=2):  # Start from 2 since row 1 is header
                    try:
                        product_code = row["Product_Code"].strip()
                        if not product_code:
                            self.stdout.write(
                                self.style.WARNING(f"Row {row_num}: Skipping - Product_Code is empty")
                            )
                            skipped_rows += 1
                            continue

                        # Extract product information
                        product_type = row.get("Product_Type", "").strip()
                        material_code = row.get("Material_Code", "").strip()
                        cust_id = row.get("Cust_ID", "").strip()
                        grams_per_product = row.get("gram/product", "").strip()
                        packing_size = row.get("Packing_Size", "").strip()
                        
                        # Extract sheet dimensions (first occurrence)
                        sheet_length = row.get("Length (L)", "").strip()
                        sheet_breadth = row.get("Breadth (B)", "").strip()
                        
                        # Extract strip dimensions (second occurrence)
                        strip_length = None
                        strip_breadth = None
                        
                        # Handle duplicate column names by checking for the second occurrence
                        length_count = 0
                        breadth_count = 0
                        
                        for i, key in enumerate(reader.fieldnames):
                            if key == "Length (L)":
                                length_count += 1
                                if length_count == 2:  # Second occurrence is strip length
                                    strip_length = row.get(key, "").strip()
                            elif key == "Breadth (B)":
                                breadth_count += 1
                                if breadth_count == 2:  # Second occurrence is strip breadth
                                    strip_breadth = row.get(key, "").strip()
                        
                        # Get other strip-related fields
                        strip_count = row.get("Strip Count", "").strip()
                        pcs_per_strip = row.get("Pcs per Strip", "").strip()
                        pcs_per_sheet = row.get("Pcs per Sheet", "").strip()
                        
                        # Map product type - stamp products are press components
                        mapped_product_type = "press_component" if product_type.lower() == "press component" else "press_component"
                        
                        self.stdout.write(
                            f"\n[{'DRY' if dry_run else 'SAVE'}] Row {row_num}: Processing Product={product_code}"
                        )
                        self.stdout.write(
                            f"    Type={mapped_product_type}, Material={material_code}, Customer={cust_id}"
                        )
                        self.stdout.write(
                            f"    Sheet: {sheet_length}x{sheet_breadth}, Strip: {strip_length}x{strip_breadth}"
                        )
                        self.stdout.write(
                            f"    Strips/Sheet: {strip_count}, Pcs/Strip: {pcs_per_strip}, Pcs/Sheet: {pcs_per_sheet}"
                        )

                        if not dry_run:
                            # Try to find the material
                            material = None
                            if material_code:
                                try:
                                    material = RawMaterial.objects.get(material_code=material_code)
                                except RawMaterial.DoesNotExist:
                                    self.stdout.write(
                                        self.style.WARNING(
                                            f"    Material with code '{material_code}' not found. "
                                            f"Skipping product creation for {product_code}"
                                        )
                                    )
                                    skipped_rows += 1
                                    continue
                            else:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"    No material code provided for {product_code}. Skipping."
                                    )
                                )
                                skipped_rows += 1
                                continue

                            # Try to find the customer
                            customer = None
                            if cust_id:
                                try:
                                    customer = Customer.objects.get(c_id=cust_id)
                                except Customer.DoesNotExist:
                                    self.stdout.write(
                                        self.style.WARNING(
                                            f"    Customer with c_id '{cust_id}' not found. "
                                            f"Product will be created without customer link."
                                        )
                                    )

                            # Parse grams_per_product
                            grams_value = None
                            if grams_per_product:
                                try:
                                    grams_value = float(grams_per_product)
                                except (ValueError, TypeError):
                                    self.stdout.write(
                                        self.style.WARNING(
                                            f"    Invalid gram/product value '{grams_per_product}'. "
                                            f"Setting to None."
                                        )
                                    )
                            
                            # Create or update product with dimensions
                            product, created = Product.objects.update_or_create(
                                product_code=product_code,
                                defaults={
                                    'product_type': mapped_product_type,
                                    'material': material,
                                    'customer_c_id': customer,
                                    'grams_per_product': grams_value,
                                    # Sheet dimensions (whole sheet size)
                                    'whole_sheet_length_mm': self.safe_decimal(sheet_length, row_num, 'whole_sheet_length_mm'),
                                    'whole_sheet_breadth_mm': self.safe_decimal(sheet_breadth, row_num, 'whole_sheet_breadth_mm'),
                                    # Strip dimensions (cut size from sheet)
                                    'strip_length_mm': self.safe_decimal(strip_length, row_num, 'strip_length_mm'),
                                    'strip_breadth_mm': self.safe_decimal(strip_breadth, row_num, 'strip_breadth_mm'),
                                    # Strip and piece counts
                                    'strips_per_sheet': self.safe_int(strip_count, row_num, 'strips_per_sheet'),
                                    'pcs_per_strip': self.safe_int(pcs_per_strip, row_num, 'pcs_per_strip'),
                                    'packing_size': self.safe_int(packing_size, row_num, 'packing_size'),
                                }
                            )
                            
                            if created:
                                created_products += 1
                                self.stdout.write(f"    ✓ Created product: {product_code}")
                            else:
                                updated_products += 1
                                self.stdout.write(f"    ✓ Updated product: {product_code}")

                            # Convert dimensions to proper types
                            sheet_length_decimal = self.safe_decimal(sheet_length, row_num, "Sheet Length")
                            sheet_breadth_decimal = self.safe_decimal(sheet_breadth, row_num, "Sheet Breadth")
                            strip_length_decimal = self.safe_decimal(strip_length, row_num, "Strip Length")
                            strip_breadth_decimal = self.safe_decimal(strip_breadth, row_num, "Strip Breadth")
                            strip_count_int = self.safe_int(strip_count, row_num, "Strip Count")
                            pcs_per_strip_int = self.safe_int(pcs_per_strip, row_num, "Pcs per Strip")
                            pcs_per_sheet_int = self.safe_int(pcs_per_sheet, row_num, "Pcs per Sheet")
                            packing_size_int = self.safe_int(packing_size, row_num, "Packing Size")

                            # Process the manufacturing steps
                            process_columns = [
                                "Process 1", "Process 2", "Process 3", "Process 4", 
                                "Process 5", "Process 6", "Process 7"
                            ]
                            
                            # Collect all non-empty subprocess names
                            subprocess_names = []
                            for process_col in process_columns:
                                subprocess_name = row.get(process_col, "").strip()
                                if subprocess_name:
                                    subprocess_names.append(subprocess_name)
                            
                            if not subprocess_names:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"    No processes found for {product_code}. Skipping BOM creation."
                                    )
                                )
                                continue
                            
                            # Use the first subprocess name as the main Process name
                            main_process_name = subprocess_names[0]
                            
                            self.stdout.write(
                                f"    Main Process: {main_process_name}, SubProcesses: {subprocess_names}"
                            )
                            
                            # Create or get the main Process (using first subprocess name)
                            process, process_created = Process.objects.get_or_create(
                                name=main_process_name,
                                defaults={"code": abs(hash(main_process_name)) % 10000}
                            )
                            
                            if process_created:
                                self.stdout.write(f"    ✓ Created Process: {main_process_name}")
                            else:
                                self.stdout.write(f"    ✓ Using existing Process: {main_process_name}")
                            
                            # Create all SubProcesses under the same Process
                            # First subprocess has the same name as the Process
                            sequence = 1
                            for subprocess_name in subprocess_names:
                                # Create or get subprocess
                                # Note: If import_process_config was run first, this will use existing
                                # SubProcess with outsourcing/optional flags already set
                                subprocess, subprocess_created = SubProcess.objects.get_or_create(
                                    process=process,
                                    name=subprocess_name,
                                    defaults={
                                        'is_outsourced': False,
                                        'is_optional': False
                                    }
                                )
                                
                                if subprocess_created:
                                    self.stdout.write(
                                        f"    ✓ Created SubProcess: {subprocess_name} (seq: {sequence})"
                                    )
                                else:
                                    self.stdout.write(
                                        f"    ✓ Using existing SubProcess: {subprocess_name} (seq: {sequence})"
                                    )

                                # Create or update process step
                                step_code = f"{product_code}-{sequence}"
                                step, step_created = ProcessStep.objects.update_or_create(
                                    step_code=step_code,
                                    defaults={
                                        "step_name": subprocess_name,
                                        "process": process,
                                        "subprocess": subprocess,
                                        "sequence_order": sequence,
                                    }
                                )
                                
                                if step_created:
                                    self.stdout.write(
                                        f"    ✓ Created ProcessStep: {subprocess_name} (seq: {sequence})"
                                    )
                                else:
                                    self.stdout.write(
                                        f"    ✓ Updated ProcessStep: {subprocess_name} (seq: {sequence})"
                                    )

                                # Create or update BOM entry with sheet/strip dimensions
                                bom, bom_created = BOM.objects.update_or_create(
                                    product_code=product_code,
                                    process_step=step,
                                    defaults={
                                        "material": material,
                                        "type": "stamp",
                                        "sheet_length": sheet_length_decimal,
                                        "sheet_breadth": sheet_breadth_decimal,
                                        "strip_length": strip_length_decimal,
                                        "strip_breadth": strip_breadth_decimal,
                                        "strip_count": strip_count_int,
                                        "pcs_per_strip": pcs_per_strip_int,
                                        "pcs_per_sheet": pcs_per_sheet_int,
                                        "packing_size": packing_size_int,
                                        "is_active": True,
                                    }
                                )
                                
                                if bom_created:
                                    created_boms += 1
                                    self.stdout.write(
                                        f"    ✓ Created BOM for: {main_process_name} -> {subprocess_name} (seq: {sequence})"
                                    )
                                else:
                                    updated_boms += 1
                                    self.stdout.write(
                                        f"    ✓ Updated BOM for: {main_process_name} -> {subprocess_name} (seq: {sequence})"
                                    )

                                sequence += 1

                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"Row {row_num}: Error processing row - {str(e)}")
                        )
                        import traceback
                        self.stdout.write(traceback.format_exc())
                        skipped_rows += 1
                        continue

        # Summary
        if dry_run:
            self.stdout.write(self.style.WARNING("\n" + "="*50))
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made"))
            self.stdout.write(self.style.WARNING("="*50))
        else:
            self.stdout.write(self.style.SUCCESS("\n" + "="*50))
            self.stdout.write(
                self.style.SUCCESS(
                    f"Import completed successfully!\n"
                    f"Products created: {created_products}\n"
                    f"Products updated: {updated_products}\n"
                    f"BOM entries created: {created_boms}\n"
                    f"BOM entries updated: {updated_boms}\n"
                    f"Rows skipped: {skipped_rows}"
                )
            )
            self.stdout.write(self.style.SUCCESS("="*50))