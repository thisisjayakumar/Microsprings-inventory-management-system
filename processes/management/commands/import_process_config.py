# processes/management/commands/import_process_config.py
import csv
from django.core.management.base import BaseCommand
from django.db import transaction
from processes.models import Process, SubProcess


class Command(BaseCommand):
    help = "Import process outsourcing and optional configuration from CSV"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to process configuration CSV")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving to the database",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        dry_run = options["dry_run"]
        
        updated_count = 0
        created_count = 0
        skipped_count = 0
        errors = []

        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
            # Verify required columns exist
            required_cols = ["Parent_Process_Name", "Process_Name", "Out_Sourcing Process", "Optional Process"]
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
                        parent_process_name = row["Parent_Process_Name"].strip()
                        process_name = row["Process_Name"].strip()
                        out_sourcing = row["Out_Sourcing Process"].strip().lower()
                        optional = row["Optional Process"].strip().lower()
                        
                        # Skip if parent process or process name is empty
                        if not parent_process_name or not process_name:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Row {row_num}: Skipping - Parent_Process_Name or Process_Name is empty"
                                )
                            )
                            skipped_count += 1
                            continue
                        
                        # Parse boolean values
                        is_outsourced = out_sourcing in ['true', '1', 'yes', 'y']
                        is_optional = optional in ['true', '1', 'yes', 'y']
                        
                        self.stdout.write(
                            f"[{'DRY' if dry_run else 'SAVE'}] Row {row_num}: "
                            f"Process={parent_process_name}, SubProcess={process_name}, "
                            f"Outsourced={is_outsourced}, Optional={is_optional}"
                        )
                        
                        if not dry_run:
                            # Find or create the parent process
                            process, process_created = Process.objects.get_or_create(
                                name=parent_process_name,
                                defaults={"code": abs(hash(parent_process_name)) % 10000}
                            )
                            
                            if process_created:
                                self.stdout.write(f"    Created Process: {parent_process_name}")
                            
                            # Find or create the subprocess
                            subprocess, subprocess_created = SubProcess.objects.get_or_create(
                                process=process,
                                name=process_name,
                                defaults={
                                    "is_outsourced": is_outsourced,
                                    "is_optional": is_optional
                                }
                            )
                            
                            if subprocess_created:
                                created_count += 1
                                self.stdout.write(
                                    f"    ✓ Created SubProcess: {process_name} "
                                    f"(Outsourced: {is_outsourced}, Optional: {is_optional})"
                                )
                            else:
                                # Update existing subprocess
                                updated = False
                                if subprocess.is_outsourced != is_outsourced:
                                    subprocess.is_outsourced = is_outsourced
                                    updated = True
                                if subprocess.is_optional != is_optional:
                                    subprocess.is_optional = is_optional
                                    updated = True
                                
                                if updated:
                                    subprocess.save()
                                    updated_count += 1
                                    self.stdout.write(
                                        f"    ✓ Updated SubProcess: {process_name} "
                                        f"(Outsourced: {is_outsourced}, Optional: {is_optional})"
                                    )
                                else:
                                    self.stdout.write(
                                        f"    - No changes needed for SubProcess: {process_name}"
                                    )

                    except Exception as e:
                        error_msg = f"Row {row_num}: Error processing row - {str(e)}"
                        self.stdout.write(self.style.ERROR(error_msg))
                        errors.append(error_msg)
                        skipped_count += 1
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
                    f"SubProcesses created: {created_count}\n"
                    f"SubProcesses updated: {updated_count}\n"
                    f"Rows skipped: {skipped_count}"
                )
            )
            if errors:
                self.stdout.write(self.style.WARNING(f"\nErrors encountered: {len(errors)}"))
                for error in errors[:10]:  # Show first 10 errors
                    self.stdout.write(self.style.WARNING(f"  - {error}"))
            self.stdout.write(self.style.SUCCESS("="*50))

