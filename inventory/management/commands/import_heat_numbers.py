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
        po_id = f"PO-{material_hash}"
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

    def get_or_create_grn_by_number(self, grn_number, raw_material, user):
        """
        Get or create a GRN receipt by GRN number.
        If GRN doesn't exist, creates a system PO and GRN for it.
        """
        # Try to find existing GRN
        try:
            grn = GRNReceipt.objects.get(grn_number=grn_number)
            return grn, False
        except GRNReceipt.DoesNotExist:
            # Create system PO and GRN for this GRN number
            system_vendor = self.get_or_create_system_vendor()
            
            # Create a system PO for this material and GRN
            import hashlib
            grn_hash = hashlib.md5(f"{grn_number}_{raw_material.material_code}".encode()).hexdigest()[:8]
            po_id = f"SYS-{grn_hash}"
            
            system_po, po_created = PurchaseOrder.objects.get_or_create(
                po_id=po_id,
                defaults={
                    "rm_code": raw_material,
                    "vendor_name": system_vendor,
                    "quantity_ordered": Decimal("0.00"),
                    "expected_date": timezone.now().date(),
                    "unit_price": Decimal("0.00"),
                    "status": "po_approved",
                },
            )
            
            # Create GRN with the provided GRN number
            grn = GRNReceipt.objects.create(
                grn_number=grn_number,
                purchase_order=system_po,
                truck_number="SYS-IMPORT",
                driver_name="System Import",
                status="completed",
                received_by=user,
                quality_check_passed=True,
            )
            
            if po_created:
                self.stdout.write(f"Created system PO: {system_po.po_id}")
            self.stdout.write(f"Created GRN: {grn.grn_number}")
            return grn, True

    def parse_row_data(self, headers, row_values):
        """
        Parse data from a CSV row in the new format.
        Expected format: Material code, Qty, Heat Numbers, GRN No., Coil No.1, Wt, Coil No.2, Wt
        
        Returns: dict with material_code, heat_number, grn_number, and coils list
        
        Args:
            headers: List of column names
            row_values: List of values for this row
        """
        data = {
            "material_code": "",
            "qty": None,
            "heat_number": "",
            "grn_number": "",
            "coils": []
        }
        
        # Get indices for each column
        material_code_idx = headers.index("Material code") if "Material code" in headers else -1
        qty_idx = headers.index("Qty") if "Qty" in headers else -1
        heat_numbers_idx = headers.index("Heat Numbers") if "Heat Numbers" in headers else -1
        grn_no_idx = headers.index("GRN No.") if "GRN No." in headers else -1
        
        # Extract basic fields
        if material_code_idx >= 0 and material_code_idx < len(row_values):
            data["material_code"] = str(row_values[material_code_idx]).strip()
        
        if qty_idx >= 0 and qty_idx < len(row_values):
            try:
                data["qty"] = Decimal(str(row_values[qty_idx]).strip()) if row_values[qty_idx] else None
            except (InvalidOperation, ValueError):
                pass
        
        if heat_numbers_idx >= 0 and heat_numbers_idx < len(row_values):
            data["heat_number"] = str(row_values[heat_numbers_idx]).strip()
        
        if grn_no_idx >= 0 and grn_no_idx < len(row_values):
            data["grn_number"] = str(row_values[grn_no_idx]).strip()
        
        # Parse coils - look for Coil No.1, Wt, Coil No.2, Wt pattern
        current_coil_number = None
        for i, col_name in enumerate(headers):
            if i >= len(row_values):
                break
            
            value = str(row_values[i]).strip() if row_values[i] else ""
            
            # Skip already processed columns
            if col_name in ["Material code", "Qty", "Heat Numbers", "GRN No."]:
                continue
            
            # Check if this is a coil number column
            if col_name.startswith("Coil No."):
                if value:
                    current_coil_number = value
                else:
                    current_coil_number = None
            
            # Check if this is a weight column
            elif col_name.lower() in ["wt", "weight", "w"] or "weight" in col_name.lower():
                if value and current_coil_number:
                    try:
                        weight = Decimal(value)
                        if weight > 0:
                            data["coils"].append({
                                "coil_number": current_coil_number,
                                "weight_kg": float(weight),
                            })
                    except (InvalidOperation, ValueError):
                        self.stdout.write(
                            self.style.WARNING(
                                f"Invalid weight '{value}' for coil {current_coil_number}"
                            )
                        )
                current_coil_number = None
        
        return data

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No changes will be made to the database")
            )

        # Get admin user
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.first()
        if not admin_user:
            raise CommandError("No user found. Please create a user first.")

        imported_count = 0
        skipped_count = 0
        deleted_count = 0
        errors = []

        try:
            with open(csv_file, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                
                # Read header row
                headers = next(reader)
                
                # Verify required columns
                required_columns = ["Material code", "Heat Numbers", "GRN No."]
                missing_columns = [col for col in required_columns if col not in headers]
                if missing_columns:
                    raise CommandError(
                        f"CSV missing required columns: {missing_columns}. Found: {headers}"
                    )

                # First pass: Collect all unique material codes from CSV
                material_codes_in_csv = set()
                all_rows_data = []
                
                for row_num, row_values in enumerate(reader, start=2):
                    if not row_values or len(row_values) < 3:
                        continue
                    
                    try:
                        row_data = self.parse_row_data(headers, row_values)
                        if row_data["material_code"]:
                            material_codes_in_csv.add(row_data["material_code"])
                            all_rows_data.append((row_num, row_data))
                    except Exception as e:
                        # Skip invalid rows in first pass, but log for debugging
                        self.stdout.write(
                            self.style.WARNING(f"Row {row_num}: Error parsing row in first pass - {str(e)}")
                        )

                with transaction.atomic():
                    # Delete existing heat numbers and reset stock balances for materials in CSV
                    if not dry_run and material_codes_in_csv:
                        self.stdout.write("\n" + "=" * 60)
                        self.stdout.write(self.style.WARNING("Cleaning up existing data for materials in CSV..."))
                        
                        for material_code in material_codes_in_csv:
                            try:
                                raw_material = RawMaterial.objects.get(material_code=material_code)
                                
                                # Delete all existing heat numbers for this material
                                deleted_heats = HeatNumber.objects.filter(raw_material=raw_material).delete()
                                deleted_count += deleted_heats[0] if deleted_heats[0] else 0
                                
                                # Reset RMStockBalance for this material
                                RMStockBalance.objects.filter(raw_material=raw_material).update(
                                    available_quantity=Decimal("0")
                                )
                                
                                # Delete RMStockBalanceHeat entries for this material
                                RMStockBalanceHeat.objects.filter(raw_material=raw_material).delete()
                                
                                self.stdout.write(
                                    f"  Cleared existing data for {material_code}: "
                                    f"Deleted {deleted_heats[0] if deleted_heats[0] else 0} heat numbers, "
                                    f"reset stock balance"
                                )
                            except RawMaterial.DoesNotExist:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"  Material '{material_code}' not found - will skip during import"
                                    )
                                )
                    
                    if not dry_run:
                        self.stdout.write("\n" + "=" * 60)
                        self.stdout.write(self.style.SUCCESS("Starting import of heat numbers..."))
                    
                    # Second pass: Import all heat numbers
                    for row_num, row_data in all_rows_data:
                        try:
                            material_code = row_data["material_code"]
                            heat_number_str = row_data["heat_number"]
                            grn_number = row_data["grn_number"]
                            coils = row_data["coils"]
                            
                            # Validate required fields
                            if not material_code:
                                self.stdout.write(
                                    self.style.WARNING(f"Row {row_num}: Skipping - Material code is empty")
                                )
                                skipped_count += 1
                                continue
                            
                            if not heat_number_str:
                                self.stdout.write(
                                    self.style.WARNING(f"Row {row_num}: Skipping - Heat number is empty")
                                )
                                skipped_count += 1
                                continue
                            
                            if not grn_number:
                                self.stdout.write(
                                    self.style.WARNING(f"Row {row_num}: Skipping - GRN number is empty")
                                )
                                skipped_count += 1
                                continue
                            
                            if not coils:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"Row {row_num}: Skipping - No coils found for {material_code}, Heat {heat_number_str}"
                                    )
                                )
                                skipped_count += 1
                                continue

                            # Get raw material
                            try:
                                raw_material = RawMaterial.objects.get(material_code=material_code)
                            except RawMaterial.DoesNotExist:
                                errors.append(
                                    f"Row {row_num}: Raw material '{material_code}' not found"
                                )
                                skipped_count += 1
                                continue

                            # Calculate total weight and coil count
                            heat_total_weight = sum(
                                Decimal(str(coil["weight_kg"])) for coil in coils
                            )
                            coil_count = len(coils)

                            self.stdout.write(
                                f"[{'DRY' if dry_run else 'SAVE'}] "
                                f"Material: {material_code}, GRN: {grn_number}, Heat: {heat_number_str}, "
                                f"Coils: {coil_count}, Weight: {heat_total_weight}kg"
                            )

                            if not dry_run:
                                # Get or create GRN receipt
                                grn_receipt, grn_created = self.get_or_create_grn_by_number(
                                    grn_number, raw_material, admin_user
                                )

                                # Create new heat number (we've already deleted existing ones)
                                heat_number = HeatNumber.objects.create(
                                    heat_number=heat_number_str,
                                    grn_receipt=grn_receipt,
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
                            error_msg = f"Row {row_num}: Error processing row - {str(e)}"
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
        if not dry_run:
            self.stdout.write(f"  Deleted: {deleted_count} existing heat numbers")
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

