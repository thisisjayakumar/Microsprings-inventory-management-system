# processes/management/commands/import_spring_processes.py
import csv
from django.core.management.base import BaseCommand
from django.db import transaction
from processes.models import Process, SubProcess, ProcessStep, BOM
from products.models import Product
from inventory.models import RawMaterial
from third_party.models import Customer


class Command(BaseCommand):
    help = "Import spring products and processes from updated CSV format"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to Spring processes CSV")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving to the database",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        dry_run = options["dry_run"]
        
        created_products = 0
        updated_products = 0
        created_boms = 0
        skipped_rows = 0

        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
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
                        spring_type = row.get("Spring_Type", "").strip()
                        material_code = row.get("Material_Code", "").strip()
                        cust_id = row.get("Cust_ID", "").strip()
                        grams_per_product = row.get("gram/product", "").strip()
                        
                        # Map product type
                        mapped_product_type = "spring" if product_type.lower() == "spring" else "spring"
                        
                        # Map spring type
                        mapped_spring_type = self._map_spring_type(spring_type)
                        
                        self.stdout.write(
                            f"[{'DRY' if dry_run else 'SAVE'}] Row {row_num}: Processing Product={product_code}, "
                            f"Type={mapped_product_type}, SpringType={mapped_spring_type}, Material={material_code}, Customer={cust_id}"
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
                                            f"Row {row_num}: Material with code '{material_code}' not found. "
                                            f"Skipping product creation for {product_code}"
                                        )
                                    )
                                    skipped_rows += 1
                                    continue
                            else:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"Row {row_num}: No material code provided for {product_code}. Skipping."
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
                                            f"Row {row_num}: Customer with c_id '{cust_id}' not found. "
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
                                            f"Row {row_num}: Invalid gram/product value '{grams_per_product}'. "
                                            f"Setting to None."
                                        )
                                    )
                            
                            # Create or update product
                            product, product_created = Product.objects.update_or_create(
                                product_code=product_code,
                                defaults={
                                    "product_type": mapped_product_type,
                                    "spring_type": mapped_spring_type,
                                    "material": material,
                                    "customer_c_id": customer,
                                    "internal_product_code": row.get("IPC_Code", "").strip() or None,
                                    "grams_per_product": grams_value,
                                }
                            )
                            
                            if product_created:
                                created_products += 1
                                self.stdout.write(f"    Created product: {product_code}")
                            else:
                                updated_products += 1
                                self.stdout.write(f"    Updated product: {product_code}")

                            # Process the manufacturing steps
                            process_columns = [
                                "Coiling/Forming", "Stress_Relieving", "Coating", 
                                "Grinding", "Plating", "Blackening"
                            ]
                            
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
                                # Note: If import_process_config was run first, this will use existing
                                # SubProcess with outsourcing/optional flags already set
                                subprocess, _ = SubProcess.objects.get_or_create(
                                    process=process,
                                    name=subprocess_name,
                                    defaults={
                                        'is_outsourced': False,
                                        'is_optional': False
                                    }
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

                                # Create BOM entry
                                bom, bom_created = BOM.objects.update_or_create(
                                    product_code=product_code,
                                    process_step=step,
                                    defaults={
                                        "type": "spring",
                                        "material": material,
                                        "is_active": True,
                                    }
                                )
                                
                                if bom_created:
                                    created_boms += 1
                                
                                self.stdout.write(
                                    f"    {'Created' if step_created else 'Updated'} process step: "
                                    f"{process_col} -> {subprocess_name} (seq: {sequence})"
                                )

                                sequence += 1

                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"Row {row_num}: Error processing row - {str(e)}")
                        )
                        skipped_rows += 1
                        continue

        # Summary
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Import completed successfully!\n"
                    f"Products created: {created_products}\n"
                    f"Products updated: {updated_products}\n"
                    f"BOM entries created: {created_boms}\n"
                    f"Rows skipped: {skipped_rows}"
                )
            )

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
