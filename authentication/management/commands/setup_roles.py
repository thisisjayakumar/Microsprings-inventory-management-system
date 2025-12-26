from django.core.management.base import BaseCommand
from django.db import transaction
from authentication.models import Role


class Command(BaseCommand):
    help = 'Setup MSP-ERP role hierarchy and permissions'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Setting up MSP-ERP roles...'))
        
        roles_data = [
            {
                'name': 'admin',
                'description': 'System Administrator with full access to all operations',
                'hierarchy_level': 1,
                'permissions': {
                    'users': ['create', 'read', 'update', 'delete'],
                    'roles': ['create', 'read', 'update', 'delete'],
                    'manufacturing': ['create', 'read', 'update', 'delete'],
                    'manufacturing_orders': ['create', 'read', 'update', 'delete'],
                    'inventory': ['create', 'read', 'update', 'delete'],
                    'rawmaterials': ['create', 'read', 'update', 'delete'],
                    'rmstock': ['create', 'read', 'update', 'delete'],
                    'quality': ['create', 'read', 'update', 'delete'],
                    'reports': ['create', 'read', 'update', 'delete'],
                    'settings': ['create', 'read', 'update', 'delete'],
                    'processes': ['create', 'read', 'update', 'delete']
                },
                'restricted_departments': []  # No restrictions
            },
            {
                'name': 'manager',
                'description': 'Manager with high level access - MO Management, Stock, Allocation, Reports, Part Master',
                'hierarchy_level': 2,
                'permissions': {
                    'users': ['read', 'update'],
                    'manufacturing_orders': ['create', 'read', 'update', 'delete'],
                    'inventory': ['read', 'update'],
                    'stock_allocation': ['create', 'read', 'update', 'delete'],
                    'part_master': ['create', 'read', 'update', 'delete'],
                    'reports': ['read'],
                    'quality': ['read'],
                    'processes': ['read']
                },
                'restricted_departments': []  # Access to all departments
            },
            {
                'name': 'production_head',
                'description': 'Production Head with all manager operations plus production oversight',
                'hierarchy_level': 2,
                'permissions': {
                    'users': ['read', 'update'],
                    'manufacturing_orders': ['create', 'read', 'update', 'delete'],
                    'inventory': ['read', 'update'],
                    'stock_allocation': ['create', 'read', 'update', 'delete'],
                    'part_master': ['create', 'read', 'update', 'delete'],
                    'reports': ['read'],
                    'quality': ['read', 'update'],
                    'processes': ['read', 'update']
                },
                'restricted_departments': []  # Access to all departments
            },
            {
                'name': 'supervisor',
                'description': 'Supervisor with limited access to process-specific tasks',
                'hierarchy_level': 3,
                'permissions': {
                    'processes': ['read', 'update'],
                    'batches': ['read', 'update'],
                    'operators': ['read', 'update'],
                    'quality_checks': ['create', 'read', 'update'],
                    'machine_allocation': ['read', 'update']
                },
                'restricted_departments': ['coiling', 'tempering', 'plating', 'packing']
            },
            {
                'name': 'rm_store',
                'description': 'RM Store with access to processes, inventory management, RM stock, and raw materials',
                'hierarchy_level': 4,
                'permissions': {
                    'processes': ['read', 'update'],
                    'inventory': ['create', 'read', 'update'],
                    'rawmaterials': ['create', 'read', 'update', 'delete'],
                    'rmstock': ['create', 'read', 'update', 'delete'],
                    'stock_transactions': ['create', 'read', 'update'],
                    'purchase_orders': ['read', 'update']
                },
                'restricted_departments': ['rm_store']
            },
            {
                'name': 'fg_store',
                'description': 'FG Store with access to process management and finished goods',
                'hierarchy_level': 5,
                'permissions': {
                    'processes': ['read', 'update'],
                    'dispatch_orders': ['create', 'read', 'update'],
                    'packaging': ['read', 'update'],
                    'finished_goods': ['create', 'read', 'update']
                },
                'restricted_departments': ['fg_store']
            },
            {
                'name': 'patrol',
                'description': 'Patrol user with access to quality control monitoring, upload QC sheets, and view patrol duties',
                'hierarchy_level': 6,
                'permissions': {
                    'patrol_duties': ['read'],
                    'patrol_uploads': ['create', 'read', 'update'],
                    'patrol_alerts': ['read', 'update'],
                    'quality_control': ['create', 'read'],
                    'qc_sheets': ['read']
                },
                'restricted_departments': []  # Patrol users can monitor all departments
            },
            {
                'name': 'outsourcing_incharge',
                'description': 'Outsourcing Incharge with access to send and receive batches for outsourcing processes, track quantities and manage vendor operations',
                'hierarchy_level': 7,
                'permissions': {
                    'outsourcing_requests': ['create', 'read', 'update', 'delete'],
                    'send_outsource': ['create', 'read', 'update'],
                    'receive_outsource': ['create', 'read', 'update'],
                    'manage_outsource_batches': ['create', 'read', 'update', 'delete'],
                    'view_outsource_history': ['read'],
                    'vendor_management': ['read'],
                    'inventory_transactions': ['create', 'read']
                },
                'restricted_departments': []  # Outsourcing incharge can manage all departments
            },
            {
                'name': 'packing_zone',
                'description': 'Packing Zone role with access to packing operations, labels, and box management',
                'hierarchy_level': 8,
                'permissions': {
                    'packing_zone': ['create', 'read', 'update'],
                    'labels': ['read', 'print'],
                    'box_management': ['create', 'read', 'update'],
                    'packing_history': ['read']
                },
                'restricted_departments': ['packing']
            }
        ]
        
        with transaction.atomic():
            for role_data in roles_data:
                role, created = Role.objects.get_or_create(
                    name=role_data['name'],
                    defaults={
                        'description': role_data['description'],
                        'hierarchy_level': role_data['hierarchy_level'],
                        'permissions': role_data['permissions'],
                        'restricted_departments': role_data['restricted_departments']
                    }
                )
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f'Created role: {role.get_name_display()}')
                    )
                else:
                    # Update existing role
                    role.description = role_data['description']
                    role.hierarchy_level = role_data['hierarchy_level']
                    role.permissions = role_data['permissions']
                    role.restricted_departments = role_data['restricted_departments']
                    role.save()
                    self.stdout.write(
                        self.style.WARNING(f'Updated role: {role.get_name_display()}')
                    )
        
        self.stdout.write(
            self.style.SUCCESS('Successfully setup MSP-ERP roles!')
        )
