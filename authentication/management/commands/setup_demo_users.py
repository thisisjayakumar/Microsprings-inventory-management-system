from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.hashers import make_password
from authentication.models import CustomUser, UserProfile, Role, UserRole, ProcessSupervisor
from datetime import date, timedelta
import random


class Command(BaseCommand):
    help = 'Create demo users for MSP-ERP system testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=20,
            help='Number of demo users to create (default: 20)'
        )

    def handle(self, *args, **options):
        count = options['count']
        
        self.stdout.write(f'Creating {count} demo users...')
        
        # Demo user templates based on role hierarchy
        user_templates = [
            # Admin (Hierarchy Level 0)
            {
                'role': 'admin',
                'department': 'admin',
                'users': [
                    ('John', 'Admin', 'john.admin@microsprings.com', 'ADM001'),
                ]
            },
            # Manager (Hierarchy Level 1)
            {
                'role': 'manager',
                'department': 'admin',
                'users': [
                    ('Sarah', 'Wilson', 'sarah.manager@microsprings.com', 'MGR001'),
                    ('Robert', 'Kumar', 'robert.kumar@microsprings.com', 'MGR002'),
                ]
            },
            # Production Head (Hierarchy Level 2)
            {
                'role': 'production_head',
                'department': 'admin',
                'users': [
                    ('Michael', 'Chen', 'michael.production@microsprings.com', 'PH001'),
                    ('Lisa', 'Anderson', 'lisa.production@microsprings.com', 'PH002'),
                ]
            },
            # Supervisors (Hierarchy Level 3)
            {
                'role': 'supervisor',
                'department': 'coiling',
                'users': [
                    ('Mike', 'Supervisor', 'mike.coiling@microsprings.com', 'SUP001'),
                    ('Anna', 'Martinez', 'anna.coiling@microsprings.com', 'SUP002'),
                ]
            },
            {
                'role': 'supervisor',
                'department': 'tempering',
                'users': [
                    ('David', 'Smith', 'david.tempering@microsprings.com', 'SUP003'),
                ]
            },
            {
                'role': 'supervisor',
                'department': 'plating',
                'users': [
                    ('Jennifer', 'Johnson', 'jennifer.plating@microsprings.com', 'SUP004'),
                ]
            },
            {
                'role': 'supervisor',
                'department': 'packing',
                'users': [
                    ('James', 'Brown', 'james.packing@microsprings.com', 'SUP005'),
                ]
            },
            {
                'role': 'supervisor',
                'department': 'quality',
                'users': [
                    ('Emily', 'Davis', 'emily.quality@microsprings.com', 'SUP006'),
                ]
            },
            # RM Store Staff (Hierarchy Level 4)
            {
                'role': 'rm_store',
                'department': 'rm_store',
                'users': [
                    ('Tom', 'Williams', 'tom.rmstore@microsprings.com', 'RMS001'),
                    ('Maria', 'Garcia', 'maria.rmstore@microsprings.com', 'RMS002'),
                ]
            },
            # FG Store Staff (Hierarchy Level 5) - Operators
            {
                'role': 'fg_store',
                'department': 'packing',
                'users': [
                    ('Michelle', 'Thomas', 'michelle.packing@microsprings.com', 'OP008'),
                    ('Brandon', 'Harris', 'brandon.packing@microsprings.com', 'OP009'),
                ]
            },
            {
                'role': 'fg_store',
                'department': 'fg_store',
                'users': [
                    ('Nicole', 'Clark', 'nicole.fgstore@microsprings.com', 'FGS001'),
                    ('Steven', 'Lewis', 'steven.fgstore@microsprings.com', 'FGS002'),
                ]
            },
            # Patrol Users (Hierarchy Level 6) - Quality Control
            {
                'role': 'patrol',
                'department': 'quality',
                'users': [
                    ('Alex', 'Patrol', 'alex.patrol@microsprings.com', 'PAT001'),
                    ('Rachel', 'Green', 'rachel.patrol@microsprings.com', 'PAT002'),
                    ('Kevin', 'White', 'kevin.patrol@microsprings.com', 'PAT003'),
                ]
            },
            # Outsourcing Incharge (Hierarchy Level 7) - Outsourcing Management
            {
                'role': 'outsourcing_incharge',
                'department': 'admin',
                'users': [
                    ('Daniel', 'Outsource', 'daniel.outsource@microsprings.com', 'OSI001'),
                    ('Sophia', 'Lee', 'sophia.outsource@microsprings.com', 'OSI002'),
                ]
            },
            {
                'role': 'packing_zone',
                'department': 'packing',
                'users': [
                    ('Alice', 'Packing', 'alice.packing@microsprings.com', 'PZ001'),
                    ('Bob', 'Packing', 'bob.packing@microsprings.com', 'PZ002'),
                ]
            },
        ]
        
        shifts = ['I', 'II', 'III']
        designations = {
            'admin': 'System Administrator',
            'manager': 'General Manager',
            'production_head': 'Production Head',
            'supervisor': 'Process Supervisor',
            'rm_store': 'Raw Material Store Keeper',
            'fg_store': 'Finished Goods Store Keeper / Operator',
            'patrol': 'Quality Control Patrol',
            'outsourcing_incharge': 'Outsourcing Incharge',
            'packing_zone': 'Packing Zone Incharge'
        }
        
        created_count = 0
        
        try:
            with transaction.atomic():
                for template in user_templates:
                    role_name = template['role']
                    department = template['department']
                    
                    try:
                        role = Role.objects.get(name=role_name)
                    except Role.DoesNotExist:
                        self.stdout.write(
                            self.style.ERROR(f'Role {role_name} not found. Run setup_roles first.')
                        )
                        continue
                    
                    for first_name, last_name, email, emp_id in template['users']:
                        if created_count >= count:
                            break
                        
                        # Skip if user exists
                        if CustomUser.objects.filter(email=email).exists():
                            continue
                        
                        # Create user
                        user = CustomUser.objects.create(
                            username=email,
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            password=make_password('demo123'),  # Default demo password
                            is_active=True
                        )
                        
                        # Create profile
                        UserProfile.objects.create(
                            user=user,
                            employee_id=emp_id,
                            designation=designations[role_name],
                            department=department,
                            shift=random.choice(shifts) if role_name == 'fg_store' else None,
                            date_of_joining=date.today() - timedelta(days=random.randint(30, 365)),
                            phone_number=f'+1-555-{random.randint(1000, 9999)}',
                            is_active=True
                        )
                        
                        # Assign role
                        UserRole.objects.create(
                            user=user,
                            role=role,
                            assigned_by=None,
                            is_active=True
                        )
                        
                        # Create process supervisor assignments for supervisors
                        if role_name == 'supervisor':
                            process_names = {
                                'coiling': ['Coiling Setup', 'Coiling Operation', 'Coiling QC'],
                                'tempering': ['Tempering Setup', 'Tempering Process', 'Tempering QC'],
                                'plating': ['Plating Preparation', 'Plating Process', 'Plating QC'],
                                'packing': ['Packing Setup', 'Packing Process', 'Label Printing']
                            }
                            
                            ProcessSupervisor.objects.create(
                                supervisor=user,
                                process_names=process_names.get(department, []),
                                department=department,
                                is_active=True
                            )
                        
                        created_count += 1
                        self.stdout.write(f'Created: {email} ({role_name})')
                
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully created {created_count} demo users!')
                )
                self.stdout.write(
                    self.style.WARNING('Default password for all demo users: demo123')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error creating demo users: {str(e)}')
            )
