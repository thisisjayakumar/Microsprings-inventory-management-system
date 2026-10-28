from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import Role, UserRole
from inventory.models import (
    GRNReceipt,
    HeatNumber,
    InventoryTransaction,
    Location,
    RawMaterial,
    RMReturn,
    RMStockBalance,
    RMStockBalanceHeat,
)
from manufacturing.models import Batch, ManufacturingOrder
from products.models import Product

User = get_user_model()


class RMReturnDispositionFlowTests(APITestCase):
    """Characterize the stock effects of an RM return to the RM store."""

    def setUp(self):
        self.supervisor = User.objects.create_user(
            username='supervisor@test.com',
            email='supervisor@test.com',
            password='testpass123',
        )
        self.rm_user = User.objects.create_user(
            username='rmstore@test.com',
            email='rmstore@test.com',
            password='testpass123',
        )
        role, _ = Role.objects.get_or_create(
            name='rm_store', defaults={'description': 'RM Store'}
        )
        UserRole.objects.get_or_create(
            user=self.rm_user, role=role, defaults={'is_active': True}
        )

        self.raw_material = RawMaterial.objects.create(
            material_code='RET_RM_001',
            material_name='Return Test Material',
            material_type='coil',
            grade='TEST',
            wire_diameter_mm=Decimal('2.000'),
        )
        self.grn = GRNReceipt.objects.create(
            received_by=self.rm_user, status='completed'
        )
        self.heat = HeatNumber.objects.create(
            heat_number='HEAT-RET-001',
            grn_receipt=self.grn,
            raw_material=self.raw_material,
            coils_received=1,
            total_weight_kg=Decimal('100.000'),
        )
        self.product = Product.objects.create(
            product_code='RET_PROD_001',
            product_type='spring',
            material=self.raw_material,
            grams_per_product=Decimal('1.000'),
        )
        self.mo = ManufacturingOrder.objects.create(
            product_code=self.product, quantity=100, status='in_progress'
        )
        self.batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=100,
            status='in_process',
        )
        self.location = Location.objects.create(
            code='COILING', location_name='coiling'
        )

    def create_return(self):
        self.client.force_authenticate(user=self.supervisor)
        return self.client.post(
            '/api/inventory/rm-returns/',
            {
                'raw_material': self.raw_material.id,
                'heat_number': self.heat.id,
                'batch': self.batch.id,
                'manufacturing_order': self.mo.id,
                'returned_from_location_code': 'COILING',
                'quantity_kg': '10.000',
                'return_reason': 'enough_qty_reached',
            },
            format='json',
        )

    def test_return_to_rm_adds_stock_and_creates_ledger_row(self):
        created = self.create_return()
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        rm_return = RMReturn.objects.get()

        self.client.force_authenticate(user=self.rm_user)
        response = self.client.post(
            f'/api/inventory/rm-returns/{rm_return.id}/process-disposition/',
            {'disposition': 'return_to_rm', 'received_kg': '10.000'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(
            RMStockBalance.objects.get(raw_material=self.raw_material)
            .available_quantity,
            Decimal('10.000'),
        )
        self.assertEqual(
            RMStockBalanceHeat.objects.get(raw_material=self.raw_material)
            .total_available_quantity_kg,
            Decimal('10.000'),
        )
        transaction = InventoryTransaction.objects.get(reference_type='rm_return')
        self.assertEqual(transaction.transaction_type, 'return')
        self.assertEqual(transaction.quantity, Decimal('10.00'))
