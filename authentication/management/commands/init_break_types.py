from django.core.management.base import BaseCommand
from django.db import transaction
from authentication.models import BreakType


class Command(BaseCommand):
    help = 'Initialize default break types for testing (Lunch Break, Tea Break, Emergency Break)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Initializing break types...'))
        
        break_types_data = [
            {
                'name': 'Lunch Break',
                'description': 'Standard lunch break for supervisors and operators',
                'is_active': True,
            },
            {
                'name': 'Tea Break',
                'description': 'Short tea break for refreshment',
                'is_active': True,
            },
            {
                'name': 'Emergency Break',
                'description': 'Emergency break for urgent personal or work-related matters',
                'is_active': True,
            },
        ]
        
        with transaction.atomic():
            for break_data in break_types_data:
                break_type, created = BreakType.objects.get_or_create(
                    name=break_data['name'],
                    defaults={
                        'description': break_data['description'],
                        'is_active': break_data['is_active'],
                    }
                )
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f'Created break type: {break_type.name}')
                    )
                else:
                    # Update existing break type if needed
                    updated = False
                    if break_type.description != break_data['description']:
                        break_type.description = break_data['description']
                        updated = True
                    if break_type.is_active != break_data['is_active']:
                        break_type.is_active = break_data['is_active']
                        updated = True
                    
                    if updated:
                        break_type.save()
                        self.stdout.write(
                            self.style.WARNING(f'Updated break type: {break_type.name}')
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(f'Break type already exists: {break_type.name}')
                        )
        
        self.stdout.write(
            self.style.SUCCESS('Successfully initialized break types!')
        )

