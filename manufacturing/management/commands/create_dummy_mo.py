"""
Django management command to create a dummy MO and auto-approve it
Usage: python manage.py create_dummy_mo --product-code-id <id>
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model
import random

from manufacturing.models import ManufacturingOrder
from manufacturing.services.workflow import ManufacturingWorkflowService
from products.models import Product

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a dummy Manufacturing Order with given product code ID and auto-approve it with a random manager'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-code-id',
            type=int,
            required=True,
            help='Product code ID from database (required)',
        )
        parser.add_argument(
            '--quantity',
            type=int,
            default=100,
            help='Quantity to manufacture (default: 100)',
        )
        parser.add_argument(
            '--priority',
            type=str,
            default='medium',
            choices=['low', 'medium', 'high', 'urgent'],
            help='Priority level (default: medium)',
        )

    def handle(self, *args, **options):
        product_code_id = options['product_code_id']
        quantity = options['quantity']
        priority = options['priority']

        try:
            # Get the product
            try:
                product = Product.objects.select_related('material', 'customer_c_id').get(id=product_code_id)
            except Product.DoesNotExist:
                raise CommandError(f'Product with ID {product_code_id} not found in database')

            self.stdout.write(f'Creating MO for product: {product.product_code} (ID: {product.id})')

            # Get a random manager
            managers = User.objects.filter(
                user_roles__role__name__in=['manager', 'production_head'],
                user_roles__is_active=True,
                is_active=True
            ).distinct()

            if not managers.exists():
                raise CommandError('No active managers or production heads found in the system')

            manager = random.choice(list(managers))
            self.stdout.write(f'Selected manager: {manager.get_full_name() or manager.email} (ID: {manager.id})')

            with transaction.atomic():
                # Create the MO
                mo = ManufacturingOrder.objects.create(
                    product_code=product,
                    quantity=quantity,
                    priority=priority,
                    status='on_hold',  # Initial status
                    # Auto-populate product details
                    product_type=product.get_product_type_display() if product.product_type else '',
                    material_name=product.material_name or '',
                    material_type=product.material_type or '',
                    grade=product.grade or '',
                    wire_diameter_mm=product.wire_diameter_mm,
                    thickness_mm=product.thickness_mm,
                    finishing=product.finishing or '',
                    weight_kg=product.weight_kg,
                    customer_c_id=product.customer_c_id,
                    customer_name=product.customer_c_id.name if product.customer_c_id else '',
                    created_by=manager,  # Use manager as creator for dummy MO
                )

                # Calculate RM requirements
                mo.calculate_rm_requirements()
                mo.save()

                self.stdout.write(self.style.SUCCESS(f'✓ Created MO: {mo.mo_id} (DB ID: {mo.id})'))

                # Create workflow
                workflow = ManufacturingWorkflowService.create_mo_workflow(mo.id, manager)
                self.stdout.write(self.style.SUCCESS(f'✓ Created workflow with status: {workflow.status}'))

                # Approve the MO
                ManufacturingWorkflowService.approve_mo(mo.id, manager, approval_notes="Auto-approved by management command")
                self.stdout.write(self.style.SUCCESS(f'✓ Approved MO: {mo.mo_id} by {manager.get_full_name() or manager.email}'))

                # Refresh MO to get updated status
                mo.refresh_from_db()
                self.stdout.write(self.style.SUCCESS(f'\nMO Status: {mo.status}'))
                self.stdout.write(self.style.SUCCESS(f'MO ID: {mo.mo_id}'))
                self.stdout.write(self.style.SUCCESS(f'Product: {mo.product_code.product_code}'))
                self.stdout.write(self.style.SUCCESS(f'Quantity: {mo.quantity}'))
                self.stdout.write(self.style.SUCCESS(f'Approved by: {mo.gm_approved_by.get_full_name() if mo.gm_approved_by else "N/A"}'))
                self.stdout.write(self.style.SUCCESS(f'Approved at: {mo.gm_approved_at}'))

        except Exception as e:
            raise CommandError(f'Error creating MO: {str(e)}')

