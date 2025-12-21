import csv
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from inventory.models import RawMaterial, GRNReceipt, HeatNumber, RMStockBalance, RMStockBalanceHeat
from manufacturing.models import PurchaseOrder
from third_party.models import Vendor

User = get_user_model()


class Command(BaseCommand):
    help = "Import heat numbers with coils for raw materials from CSV"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to CSV file with heat numbers")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving to the database",
        )

    def get_or_create_system_vendor(self):
        """Get or create a system vendor for bulk imports"""
        vendor, created = Vendor.objects.get_or_create(
            name="System Import Vendor",
            defaults={
                "contact_person": "System",
                "contact_no": "0000000000",
                "address": "Bulk Import",
                "gst_no": "SYSTEM-IMPORT",
            },
        )
        if created:
            self.stdout.write(f"Created system vendor: {vendor.name}")
        return vendor

    def get_or_create_system_po(self, raw_material, vendor, total_qty):
        """Get or create a system purchase order for bulk imports"""
        # Use a shorter PO ID format (max 20 chars) - use hash of material code
        import hashlib
        material_hash = hashlib.md5(raw_material.material_code.encode()).hexdigest()[:8]
        po_id = f"SYS-IMP-{material_hash}"
        po, created = PurchaseOrder.objects.get_or_create(
            po_id=po_id,
            defaults={
                "rm_code": raw_material,
                "vendor_name": vendor,
                "quantity_ordered": total_qty,
                "expected_date": timezone.now().date(),
                "unit_price": Decimal("0.00"),
                "status": "po_approved",
            },
        )
        if created:
            self.stdout.write(f"Created system PO: {po.po_id}")
        return po

    def get_or_create_system_grn(self, purchase_order, user):
        """Get or create a system GRN receipt for bulk imports"""
        # Use shorter GRN format
        grn_number = f"SYS-IMP-{purchase_order.po_id[-8:]}"
        grn, created = GRNReceipt.objects.get_or_create(
            grn_number=grn_number,
            defaults={
                "purchase_order": purchase_order,
                "truck_number": "SYS-IMPORT",
                "driver_name": "System Import",
                "status": "completed",
                "received_by": user,
                "quality_check_passed": True,
            },
        )
        if created:
            self.stdout.write(f"Created system GRN: {grn.grn_number}")
        return grn

    def parse_heat_numbers_from_row(self, headers, row_values):
        """
        Parse heat numbers and coils from a CSV row.
        Expected format: Material code, Qty, Heat No.1, Coil No.1, Wt, Coil No.2, Wt, Heat No.2, Coil No.1, Wt, Coil No.2, Wt
        Returns: dict with heat_number as key, list of coils as value
        
        Args:
            headers: List of column names
            row_values: List of values for this row
        """
        heat_numbers_data = {}
        current_heat_number = None
        current_coil_number = None
        
        # Iterate through columns
        for i, col_name in enumerate(headers):
            if i >= len(row_values):
                break
                
            value = str(row_values[i]).strip() if row_values[i] else ""
            
            # Skip Material code and Qty columns
            if col_name in ["Material code", "Qty"]:
                continue
            
            # Check if this is a heat number column
            if col_name.startswith("Heat No."):
                if value:
                    current_heat_number = value
                    if current_heat_number not in heat_numbers_data:
                        heat_numbers_data[current_heat_number] = []
                current_coil_number = None  # Reset coil when new heat number starts
            
            # Check if this is a coil number column
            elif col_name.startswith("Coil No."):
                if value:
                    current_coil_number = value
                else:
                    # Empty coil number - reset
                    current_coil_number = None
            
            # Check if this is a weight column (Wt, Weight, etc.)
            elif col_name.lower() in ["wt", "weight", "w"] or "weight" in col_name.lower():
                # Only process if we have both heat number and coil number, and weight is not empty
                if value and current_heat_number and current_coil_number:
                    try:
                        weight = Decimal(value)
                        if weight > 0:  # Only add if weight is positive
                            # Add coil to current heat number
                            heat_numbers_data[current_heat_number].append({
                                "coil_number": current_coil_number,
                                "weight_kg": float(weight),
                            })
                    except (InvalidOperation, ValueError):
                        self.stdout.write(
                            self.style.WARNING(
                                f"Invalid weight '{value}' for coil {current_coil_number} in heat {current_heat_number}"
                            )
                        )
                # Reset coil number after processing weight (whether successful or not)
                current_coil_number = None
        
        # Remove heat numbers with no coils
        heat_numbers_data = {
            heat: coils for heat, coils in heat_numbers_data.items() if coils
        }
        
        return heat_numbers_data

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No changes will be made to the database")
            )

        # Get or create system vendor and admin user
        system_vendor = self.get_or_create_system_vendor()
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.first()
        if not admin_user:
            raise CommandError("No user found. Please create a user first.")

        imported_count = 0
        skipped_count = 0
        errors = []

        try:
            with open(csv_file, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                
                # Read header row
                headers = next(reader)
                
                # Verify required columns
                if "Material code" not in headers:
                    raise CommandError(
                        f"CSV missing required column 'Material code'. Found: {headers}"
                    )
                
                material_code_index = headers.index("Material code")

                with transaction.atomic():
                    for row_num, row_values in enumerate(reader, start=2):
                        if len(row_values) <= material_code_index:
                            self.stdout.write(
                                self.style.WARNING(f"Row {row_num}: Skipping - Row too short")
                            )
                            skipped_count += 1
                            continue
                            
                        material_code = row_values[material_code_index].strip() if row_values[material_code_index] else ""
                        if not material_code:
                            self.stdout.write(
                                self.style.WARNING(f"Row {row_num}: Skipping - Material code is empty")
                            )
                            skipped_count += 1
                            continue

                        try:
                            # Get raw material
                            try:
                                raw_material = RawMaterial.objects.get(material_code=material_code)
                            except RawMaterial.DoesNotExist:
                                errors.append(
                                    f"Row {row_num}: Raw material '{material_code}' not found"
                                )
                                skipped_count += 1
                                continue

                            # Parse heat numbers and coils from the row
                            heat_numbers_data = self.parse_heat_numbers_from_row(headers, row_values)
                            
                            if not heat_numbers_data:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"Row {row_num}: No heat numbers found for {material_code}"
                                    )
                                )
                                skipped_count += 1
                                continue

                            # Calculate total quantity
                            total_qty = Decimal("0")
                            for heat_number, coils in heat_numbers_data.items():
                                for coil in coils:
                                    total_qty += Decimal(str(coil["weight_kg"]))

                            # Get or create system PO and GRN for this material
                            if not dry_run:
                                system_po = self.get_or_create_system_po(
                                    raw_material, system_vendor, total_qty
                                )
                                system_grn = self.get_or_create_system_grn(system_po, admin_user)

                            # Process each heat number
                            for heat_number_str, coils in heat_numbers_data.items():
                                # Calculate total weight and coil count for this heat number
                                heat_total_weight = sum(
                                    Decimal(str(coil["weight_kg"])) for coil in coils
                                )
                                coil_count = len(coils)

                                self.stdout.write(
                                    f"[{'DRY' if dry_run else 'SAVE'}] "
                                    f"Material: {material_code}, Heat: {heat_number_str}, "
                                    f"Coils: {coil_count}, Weight: {heat_total_weight}kg"
                                )

                                if not dry_run:
                                    # Check if heat number already exists for this GRN and material
                                    existing_heat = HeatNumber.objects.filter(
                                        heat_number=heat_number_str,
                                        grn_receipt=system_grn,
                                        raw_material=raw_material,
                                    ).first()

                                    if existing_heat:
                                        self.stdout.write(
                                            self.style.WARNING(
                                                f"Heat number {heat_number_str} already exists for {material_code} in {system_grn.grn_number}. Skipping."
                                            )
                                        )
                                        continue

                                    # Create heat number
                                    heat_number = HeatNumber.objects.create(
                                        heat_number=heat_number_str,
                                        grn_receipt=system_grn,
                                        raw_material=raw_material,
                                        coils_received=coil_count,
                                        total_weight_kg=heat_total_weight,
                                        items=coils,  # Store coil details in JSONField
                                        is_available=True,
                                    )

                                    # Update stock balance
                                    heat_number.update_stock_balance()
                                    
                                    # Also update the basic RMStockBalance
                                    stock_balance, created = RMStockBalance.objects.get_or_create(
                                        raw_material=raw_material,
                                        defaults={"available_quantity": Decimal("0")},
                                    )
                                    stock_balance.available_quantity += heat_total_weight
                                    stock_balance.save()

                                    imported_count += 1

                        except Exception as e:
                            error_msg = f"Row {row_num}: Error processing {material_code} - {str(e)}"
                            errors.append(error_msg)
                            self.stdout.write(self.style.ERROR(error_msg))
                            skipped_count += 1

        except FileNotFoundError:
            raise CommandError(f'CSV file "{csv_file}" not found.')
        except Exception as e:
            raise CommandError(f"Error reading CSV file: {str(e)}")

        # Summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("Import Summary:"))
        self.stdout.write(f"  Imported: {imported_count} heat numbers")
        self.stdout.write(f"  Skipped: {skipped_count} rows")
        if errors:
            self.stdout.write(self.style.ERROR(f"  Errors: {len(errors)}"))
            for error in errors[:10]:  # Show first 10 errors
                self.stdout.write(self.style.ERROR(f"    - {error}"))
            if len(errors) > 10:
                self.stdout.write(
                    self.style.ERROR(f"    ... and {len(errors) - 10} more errors")
                )

