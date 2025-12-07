# processes/management/commands/import_unified_processes.py
import csv
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand
from django.db import transaction
from processes.models import Process, SubProcess, ProcessStep, BOM
from products.models import Product
from inventory.models import RawMaterial
from third_party.models import Customer


class Command(BaseCommand):
    help = "Import spring and stamp products and processes from unified CSV format"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to unified processes CSV")
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

    def _map_spring_type(self, spring_type):
        """Map CSV spring type to Product model choices"""
        if not spring_type:
            return "tension"  # default
        
        spring_type_lower = spring_type.lower()
        
        # Map common spring types
        mapping = {
            "tension spring": "tension",
            "wire form spring": "wire_form", 
            "compression spring": "compression",
            "torsion spring": "torsion",
            "clip": "clip",
            "rivet": "rivet",
            "helical spring": "helical",
            "length pin": "length_pin",
            "length rod": "length_rod",
            "double torsion spring": "double_torsion",
            "cotter pin": "cotter_pin",
            "conical spring": "conical",
            "ring": "ring",
            "s-spring": "s-spring",
        }
        
        return mapping.get(spring_type_lower, "tension")  # fallback to tension

    def _map_product_type(self, product_type):
        """Map CSV product type to Product model choices"""
        if not product_type:
            return "spring"  # default
        
        product_type_lower = product_type.lower()
        
        # Map product types
        if product_type_lower == "spring":
            return "spring"
        elif product_type_lower in ["press component", "press_component", "stamp"]:
            return "press_component"
        else:
            return "spring"  # fallback

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
            required_cols = ["Product_Code", "Product_Type", "Material_Code"]
            if not all(col in reader.fieldnames for col in required_cols):
                self.stdout.write(
                    self.style.ERROR(
                        f"CSV missing required columns. Found: {reader.fieldnames}\n"
                        f"Required: {required_cols}"
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
                        
                        # Map product type
                        mapped_product_type = self._map_product_type(product_type)
                        
                        # Extract product type specific fields
                        spring_type = row.get("Spring_Type", "").strip()
                        ipc_code = row.get("IPC_Code", "").strip()
                        
                        # Extract stamp-specific fields (dimensions)
                        sheet_length = row.get("Sheet_Length_L", "").strip()
                        sheet_breadth = row.get("Sheet_Breadth_B", "").strip()
                        strip_length = row.get("Strip_Length_L", "").strip()
                        strip_breadth = row.get("Strip_Breadth_B", "").strip()
                        strip_count = row.get("Strip_Count", "").strip()
                        pcs_per_strip = row.get("Pcs_per_Strip", "").strip()
                        pcs_per_sheet = row.get("Pcs_per_Sheet", "").strip()
                        
                        # Map spring type if it's a spring product
                        mapped_spring_type = None
                        if mapped_product_type == "spring":
                            mapped_spring_type = self._map_spring_type(spring_type)
                        
                        self.stdout.write(
                            f"\n[{'DRY' if dry_run else 'SAVE'}] Row {row_num}: Processing Product={product_code}"
                        )
                        self.stdout.write(
                            f"    Type={mapped_product_type}, Material={material_code}, Customer={cust_id}"
                        )
                        
                        if mapped_product_type == "spring":
                            self.stdout.write(
                                f"    SpringType={mapped_spring_type}, IPC={ipc_code}"
                            )
                        elif mapped_product_type == "press_component":
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
                            
                            # Prepare product defaults based on product type
                            product_defaults = {
                                'product_type': mapped_product_type,
                                'material': material,
                                'customer_c_id': customer,
                                'is_active': True,
                            }
                            
                            # Add spring-specific fields
                            if mapped_product_type == "spring":
                                product_defaults['spring_type'] = mapped_spring_type
                                product_defaults['internal_product_code'] = ipc_code or None
                                product_defaults['grams_per_product'] = grams_value
                            
                            # Add stamp/press_component-specific fields
                            elif mapped_product_type == "press_component":
                                product_defaults['grams_per_product'] = grams_value
                                product_defaults['whole_sheet_length_mm'] = self.safe_decimal(sheet_length, row_num, 'sheet_length')
                                product_defaults['whole_sheet_breadth_mm'] = self.safe_decimal(sheet_breadth, row_num, 'sheet_breadth')
                                product_defaults['strip_length_mm'] = self.safe_decimal(strip_length, row_num, 'strip_length')
                                product_defaults['strip_breadth_mm'] = self.safe_decimal(strip_breadth, row_num, 'strip_breadth')
                                product_defaults['strips_per_sheet'] = self.safe_int(strip_count, row_num, 'strip_count')
                                product_defaults['pcs_per_strip'] = self.safe_int(pcs_per_strip, row_num, 'pcs_per_strip')
                            
                            # Create or update product
                            product, created = Product.objects.update_or_create(
                                product_code=product_code,
                                defaults=product_defaults
                            )
                            
                            if created:
                                created_products += 1
                                self.stdout.write(f"    ✓ Created product: {product_code}")
                            else:
                                updated_products += 1
                                self.stdout.write(f"    ✓ Updated product: {product_code}")

                            # Process the manufacturing steps based on product type
                            if mapped_product_type == "spring":
                                # Spring process columns
                                process_columns = [
                                    "Coiling/Forming", "Stress_Relieving", "Coating", 
                                    "Grinding", "Plating", "Blackening"
                                ]
                                bom_type = "spring"
                            else:
                                # Stamp/Press Component process columns
                                process_columns = [
                                    "Process 1", "Process 2", "Process 3", "Process 4", 
                                    "Process 5", "Process 6", "Process 7"
                                ]
                                bom_type = "stamp"
                            
                            # Convert dimensions to proper types for stamp products
                            if mapped_product_type == "press_component":
                                sheet_length_decimal = self.safe_decimal(sheet_length, row_num, "Sheet Length")
                                sheet_breadth_decimal = self.safe_decimal(sheet_breadth, row_num, "Sheet Breadth")
                                strip_length_decimal = self.safe_decimal(strip_length, row_num, "Strip Length")
                                strip_breadth_decimal = self.safe_decimal(strip_breadth, row_num, "Strip Breadth")
                                strip_count_int = self.safe_int(strip_count, row_num, "Strip Count")
                                pcs_per_strip_int = self.safe_int(pcs_per_strip, row_num, "Pcs per Strip")
                                pcs_per_sheet_int = self.safe_int(pcs_per_sheet, row_num, "Pcs per Sheet")
                            
                            sequence = 1
                            for process_col in process_columns:
                                subprocess_name = row.get(process_col, "").strip()
                                if not subprocess_name:
                                    continue

                                # Create or get process
                                process, _ = Process.objects.get_or_create(
                                    name=process_col,
                                    defaults={"code": abs(hash(process_col)) % 10000}
                                )

                                # Create or get subprocess
                                subprocess, _ = SubProcess.objects.get_or_create(
                                    process=process,
                                    name=subprocess_name
                                )

                                # Create process step
                                step_code = f"{product_code}-{process_col}-{sequence}"
                                step, step_created = ProcessStep.objects.update_or_create(
                                    step_code=step_code,
                                    defaults={
                                        "step_name": subprocess_name,
                                        "process": process,
                                        "subprocess": subprocess,
                                        "sequence_order": sequence,
                                    }
                                )

                                # Prepare BOM defaults
                                bom_defaults = {
                                    "type": bom_type,
                                    "material": material,
                                    "is_active": True,
                                }
                                
                                # Add stamp-specific BOM fields
                                if mapped_product_type == "press_component":
                                    bom_defaults.update({
                                        "sheet_length": sheet_length_decimal,
                                        "sheet_breadth": sheet_breadth_decimal,
                                        "strip_length": strip_length_decimal,
                                        "strip_breadth": strip_breadth_decimal,
                                        "strip_count": strip_count_int,
                                        "pcs_per_strip": pcs_per_strip_int,
                                        "pcs_per_sheet": pcs_per_sheet_int,
                                    })

                                # Create BOM entry
                                bom, bom_created = BOM.objects.update_or_create(
                                    product_code=product_code,
                                    process_step=step,
                                    material=material,
                                    defaults=bom_defaults
                                )
                                
                                if bom_created:
                                    created_boms += 1
                                    self.stdout.write(
                                        f"    ✓ Created BOM for step: {process_col} -> {subprocess_name} (seq: {sequence})"
                                    )
                                else:
                                    updated_boms += 1
                                    self.stdout.write(
                                        f"    ✓ Updated BOM for step: {process_col} -> {subprocess_name} (seq: {sequence})"
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

