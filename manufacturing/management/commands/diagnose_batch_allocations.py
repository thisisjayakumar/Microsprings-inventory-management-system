"""
Django management command to diagnose batch and RM allocation issues
Usage: python manage.py diagnose_batch_allocations
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from manufacturing.models import ManufacturingOrder, Batch
from manufacturing.core_models import RawMaterialAllocation
from decimal import Decimal


class Command(BaseCommand):
    help = 'Diagnose batch and RM allocation data integrity issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mo-id',
            type=str,
            help='Check specific MO by its database ID',
        )
        parser.add_argument(
            '--recent-days',
            type=int,
            default=7,
            help='Check MOs created in the last N days (default: 7)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=== Batch & RM Allocation Diagnostic Tool ===\n'))
        
        mo_id = options.get('mo_id')
        recent_days = options.get('recent_days')
        
        if mo_id:
            # Check specific MO
            self.check_specific_mo(mo_id)
        else:
            # Check recent MOs
            from django.utils import timezone
            from datetime import timedelta
            
            cutoff_date = timezone.now() - timedelta(days=recent_days)
            recent_mos = ManufacturingOrder.objects.filter(
                created_at__gte=cutoff_date
            ).order_by('-created_at')
            
            self.stdout.write(f'\nChecking {recent_mos.count()} MOs created in the last {recent_days} days...\n')
            
            for mo in recent_mos:
                self.check_mo_data(mo)
        
        self.stdout.write(self.style.SUCCESS('\n=== Diagnostic Complete ==='))
    
    def check_specific_mo(self, mo_id):
        """Check a specific MO by database ID"""
        try:
            mo = ManufacturingOrder.objects.get(id=mo_id)
            self.stdout.write(f'\nChecking MO: {mo.mo_id} (DB ID: {mo.id})\n')
            self.check_mo_data(mo, verbose=True)
        except ManufacturingOrder.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'MO with DB ID {mo_id} not found'))
    
    def check_mo_data(self, mo, verbose=False):
        """Check batch and RM allocation data for an MO"""
        # Get batches
        batches = Batch.objects.filter(mo=mo)
        
        # Get RM allocations
        rm_allocations = RawMaterialAllocation.objects.filter(mo=mo)
        
        # Check for issues
        issues = []
        
        # 1. Check if batches actually belong to this MO
        for batch in batches:
            if batch.mo.id != mo.id:
                issues.append(f'CRITICAL: Batch {batch.batch_id} has mo_id={batch.mo.id} but queried for mo_id={mo.id}')
        
        # 2. Check if RM allocations actually belong to this MO
        for alloc in rm_allocations:
            if alloc.mo.id != mo.id:
                issues.append(f'CRITICAL: RM Allocation {alloc.id} has mo_id={alloc.mo.id} but queried for mo_id={mo.id}')
        
        # 3. Check for orphaned batches (batches with non-existent MO references)
        # This shouldn't happen due to CASCADE, but let's check
        
        # 4. Calculate totals
        total_batch_rm = sum(Decimal(str(b.planned_quantity / 1000)) for b in batches)
        total_allocated_rm = sum(alloc.allocated_quantity_kg for alloc in rm_allocations)
        
        # Output results
        if verbose or issues:
            self.stdout.write(f'\n--- MO: {mo.mo_id} (DB ID: {mo.id}) ---')
            self.stdout.write(f'  Status: {mo.status}')
            self.stdout.write(f'  Created: {mo.created_at}')
            self.stdout.write(f'  Product: {mo.product_code.product_code if mo.product_code else "N/A"}')
            self.stdout.write(f'  Batches: {batches.count()}')
            
            if batches.count() > 0:
                for batch in batches:
                    self.stdout.write(f'    - {batch.batch_id}: Status={batch.status}, Qty={batch.planned_quantity}g, Created={batch.created_at}')
            
            self.stdout.write(f'  RM Allocations: {rm_allocations.count()}')
            
            if rm_allocations.count() > 0:
                for alloc in rm_allocations:
                    self.stdout.write(f'    - Alloc #{alloc.id}: Status={alloc.status}, Qty={alloc.allocated_quantity_kg}kg, Created={alloc.created_at}')
            
            self.stdout.write(f'  Total Batch RM: {total_batch_rm}kg')
            self.stdout.write(f'  Total Allocated RM: {total_allocated_rm}kg')
            
            if issues:
                self.stdout.write(self.style.ERROR(f'\n  ISSUES FOUND:'))
                for issue in issues:
                    self.stdout.write(self.style.ERROR(f'    ! {issue}'))
            else:
                self.stdout.write(self.style.SUCCESS('  ✓ No data integrity issues found'))
            
            self.stdout.write('')

