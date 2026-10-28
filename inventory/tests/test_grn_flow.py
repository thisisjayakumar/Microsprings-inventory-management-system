from datetime import date
from decimal import Decimal
from unittest import expectedFailure

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import Role, UserRole
from inventory.models import (
    GRNReceipt,
    HeatNumber,
    RawMaterial,
    RMStockBalance,
    RMStockBalanceHeat,
)
from manufacturing.models import PurchaseOrder
from third_party.models import Vendor

User = get_user_model()


class GRNReceiptFlowTests(APITestCase):
    """Characterize the stock effects of receiving a GRN today."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='rmstore@test.com',
            email='rmstore@test.com',
            password='testpass123',
        )
        role, _ = Role.objects.get_or_create(
            name='rm_store', defaults={'description': 'RM Store'}
        )
        UserRole.objects.get_or_create(
            user=self.user, role=role, defaults={'is_active': True}
        )
        self.client.force_authenticate(user=self.user)

        self.vendor = Vendor.objects.create(
            name='GRN Test Vendor', vendor_type='rm_vendor'
        )
        self.raw_material = RawMaterial.objects.create(
            material_code='GRN_RM_001',
            material_name='GRN Test Material',
            material_type='coil',
            grade='TEST',
            wire_diameter_mm=Decimal('2.000'),
        )
        self.po = PurchaseOrder.objects.create(
            rm_code=self.raw_material,
            vendor_name=self.vendor,
            expected_date=date.today(),
            quantity_ordered=Decimal('100.00'),
            status='po_approved',
            created_by=self.user,
        )

    def create_grn(self):
        return self.client.post(
            '/api/inventory/grn-receipts/',
            {
                'purchase_order_ids': [self.po.id],
                'truck_number': 'TRUCK-1',
                'driver_name': 'Driver',
                'heat_numbers_data': [
                    {
                        'heat_number': 'HEAT-001',
                        'raw_material': self.raw_material.id,
                        'coils_received': 1,
                        'total_weight_kg': '100.000',
                    }
                ],
            },
            format='json',
        )

    def test_create_grn_adds_heat_and_legacy_stock(self):
        response = self.create_grn()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        heat = HeatNumber.objects.get(heat_number='HEAT-001')
        self.assertEqual(heat.get_available_quantity_kg(), Decimal('100.000'))
        self.assertEqual(
            RMStockBalanceHeat.objects.get(raw_material=self.raw_material)
            .total_available_quantity_kg,
            Decimal('100.000'),
        )
        self.assertEqual(
            RMStockBalance.objects.get(raw_material=self.raw_material)
            .available_quantity,
            Decimal('100.000'),
        )

    def test_completing_grn_succeeds_and_keeps_heat_stock(self):
        response = self.create_grn()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        grn = GRNReceipt.objects.get()

        complete = self.client.post(
            f'/api/inventory/grn-receipts/{grn.id}/complete_receipt/'
        )
        self.assertEqual(complete.status_code, status.HTTP_200_OK)
        self.assertEqual(
            HeatNumber.objects.get(heat_number='HEAT-001').get_available_quantity_kg(),
            Decimal('100.000'),
        )

    @expectedFailure
    def test_completing_grn_does_not_double_count_legacy_stock(self):
        # Known legacy bug: creation adds the weight to RMStockBalance and
        # completion adds it again. Phase 1 replaces both writes with
        # StockService. Remove this decorator when the test starts passing.
        response = self.create_grn()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        grn = GRNReceipt.objects.get()

        complete = self.client.post(
            f'/api/inventory/grn-receipts/{grn.id}/complete_receipt/'
        )
        self.assertEqual(complete.status_code, status.HTTP_200_OK)
        self.assertEqual(
            RMStockBalance.objects.get(raw_material=self.raw_material)
            .available_quantity,
            Decimal('100.000'),
        )
