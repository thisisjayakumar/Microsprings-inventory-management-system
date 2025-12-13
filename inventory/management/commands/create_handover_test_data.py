from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from inventory.models import RawMaterial, GRNReceipt, HeatNumber
from manufacturing.models import ManufacturingOrder, PurchaseOrder
from third_party.models import Vendor
from django.utils import timezone

User = get_user_model()

class Command(BaseCommand):
    help = 'Create test data for handover verification feature'

    def handle(self, *args, **options):
        self.stdout.write('Creating test data for handover verification...')
        
        # Create test user
        test_user, created = User.objects.get_or_create(
            username='test_user',
            defaults={
                'email': 'test@example.com',
                'first_name': 'Test',
                'last_name': 'User',
                'is_active': True
            }
        )
        
        if created:
            self.stdout.write(f'Created test user: {test_user.username}')
        else:
            self.stdout.write(f'Test user already exists: {test_user.username}')
        
        # Create test raw material
        raw_material, created = RawMaterial.objects.get_or_create(
            material_code='TEST-COIL-001',
            defaults={
                'material_name': 'Test Steel Wire',
                'material_type': 'coil',
                'grade': 'SWRH82B',
                'wire_diameter_mm': 2.5,
                'weight_kg': 100.0,
                'finishing': 'bright'
            }
        )
        
        if created:
            self.stdout.write(f'Created raw material: {raw_material.material_code}')
        else:
            self.stdout.write(f'Raw material already exists: {raw_material.material_code}')
        
        # Create test vendor
        vendor, created = Vendor.objects.get_or_create(
            name='Test Vendor',
            defaults={
                'contact_person': 'John Doe',
                'contact_no': '1234567890',
                'address': 'Test Address',
                'gst_no': 'TEST123456789'
            }
        )
        
        if created:
            self.stdout.write(f'Created vendor: {vendor.name}')
        else:
            self.stdout.write(f'Vendor already exists: {vendor.name}')
        
        # Create test purchase order
        po, created = PurchaseOrder.objects.get_or_create(
            po_id='TEST-PO-001',
            defaults={
                'rm_code': raw_material,
                'vendor_name': vendor,
                'quantity_ordered': 100.0,
                'expected_date': timezone.now().date(),
                'unit_price': 50.0,
                'status': 'po_approved'
            }
        )
        
        if created:
            self.stdout.write(f'Created purchase order: {po.po_id}')
        else:
            self.stdout.write(f'Purchase order already exists: {po.po_id}')
        
        # Create test GRN receipt
        grn, created = GRNReceipt.objects.get_or_create(
            grn_number='TEST-GRN-001',
            defaults={
                'purchase_order': po,
                'truck_number': 'TN01AB1234',
                'driver_name': 'Test Driver',
                'status': 'completed',
                'total_items_received': 100.0,
                'total_items_expected': 100.0,
                'quality_check_passed': True,
                'received_by': test_user
            }
        )
        
        if created:
            self.stdout.write(f'Created GRN receipt: {grn.grn_number}')
        else:
            self.stdout.write(f'GRN receipt already exists: {grn.grn_number}')
        
        # Create test heat numbers with pending handover status
        heat_numbers_data = [
            {
                'heat_number': 'HN001',
                'coils_received': 5,
                'total_weight_kg': 25.0,
                'handover_status': 'pending_handover'
            },
            {
                'heat_number': 'HN002',
                'coils_received': 3,
                'total_weight_kg': 15.0,
                'handover_status': 'pending_handover'
            },
            {
                'heat_number': 'HN003',
                'coils_received': 4,
                'total_weight_kg': 20.0,
                'handover_status': 'verified'
            }
        ]
        
        for heat_data in heat_numbers_data:
            heat_number, created = HeatNumber.objects.get_or_create(
                heat_number=heat_data['heat_number'],
                grn_receipt=grn,
                raw_material=raw_material,
                defaults={
                    'coils_received': heat_data['coils_received'],
                    'total_weight_kg': heat_data['total_weight_kg'],
                    'handover_status': heat_data['handover_status'],
                    'is_available': True
                }
            )
            
            if created:
                self.stdout.write(f'Created heat number: {heat_number.heat_number}')
            else:
                self.stdout.write(f'Heat number already exists: {heat_number.heat_number}')
        
        self.stdout.write(
            self.style.SUCCESS('Test data created successfully!')
        )
        self.stdout.write('You can now test the handover verification feature.')
        self.stdout.write('Heat numbers HN001 and HN002 are pending handover verification.')
        self.stdout.write('Heat number HN003 is already verified.')
