from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import Role, UserRole
from inventory.models import RawMaterial
from manufacturing.models import Batch, ManufacturingOrder
from products.models import Product
from third_party.models import Customer

from .models import DispatchBatch, DispatchTransaction

User = get_user_model()


class DispatchBatchModelTest(TestCase):
    """Test cases for DispatchBatch model"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            first_name='Test',
            last_name='User'
        )
        
        self.customer = Customer.objects.create(
            name='Test Customer',
            industry_type='automotive',
            address='Test Address',
            gst_no='22AAAAA0000A1Z5'
        )
        
        self.raw_material = RawMaterial.objects.create(
            material_code='RM001',
            material_name='Test Material',
            material_type='coil',
            grade='Test Grade',
            wire_diameter_mm=2.5,
            weight_kg=10.0
        )
        
        self.product = Product.objects.create(
            product_code='PROD001',
            product_type='spring',
            spring_type='compression',
            material=self.raw_material,
            customer_c_id=self.customer,
            grams_per_product=5.0
        )
        
        self.mo = ManufacturingOrder.objects.create(
            product_code=self.product,
            quantity=1000,
            customer_c_id=self.customer,
            planned_start_date=timezone.now(),
            planned_end_date=timezone.now() + timedelta(days=7),
            status='completed'
        )
        
        self.batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=1000,
            actual_quantity_completed=1000,
            status='completed'
        )
    
    def test_dispatch_batch_creation(self):
        """Test creating a dispatch batch"""
        dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            created_by=self.user
        )
        
        self.assertEqual(dispatch_batch.batch_id, f"DISPATCH-BATCH-{self.mo.mo_id}-001")
        self.assertEqual(dispatch_batch.status, 'pending_dispatch')
        self.assertEqual(dispatch_batch.quantity_available, 1000)
        self.assertEqual(dispatch_batch.dispatch_percentage, 0)
    
    def test_quantity_available_calculation(self):
        """Test quantity available calculation"""
        dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            quantity_dispatched=300,
            created_by=self.user
        )
        
        self.assertEqual(dispatch_batch.quantity_available, 700)
    
    def test_can_dispatch_validation(self):
        """Test dispatch validation"""
        dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            created_by=self.user
        )
        
        self.assertTrue(dispatch_batch.can_dispatch(500))
        self.assertFalse(dispatch_batch.can_dispatch(1500))
        self.assertFalse(dispatch_batch.can_dispatch(0))
        self.assertFalse(dispatch_batch.can_dispatch(-100))
    
    def test_status_update(self):
        """Test status update based on dispatch quantities"""
        dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            created_by=self.user
        )
        
        # Partially dispatched
        dispatch_batch.quantity_dispatched = 500
        dispatch_batch.update_status()
        self.assertEqual(dispatch_batch.status, 'partially_dispatched')
        
        # Fully dispatched
        dispatch_batch.quantity_dispatched = 1000
        dispatch_batch.update_status()
        self.assertEqual(dispatch_batch.status, 'fully_dispatched')


class DispatchTransactionModelTest(TestCase):
    """Test cases for DispatchTransaction model"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            first_name='Test',
            last_name='User'
        )
        
        self.supervisor = User.objects.create_user(
            email='supervisor@example.com',
            username='supervisor',
            first_name='Supervisor',
            last_name='User'
        )
        
        self.customer = Customer.objects.create(
            name='Test Customer',
            industry_type='automotive',
            address='Test Address',
            gst_no='22AAAAA0000A1Z5'
        )
        
        self.raw_material = RawMaterial.objects.create(
            material_code='RM001',
            material_name='Test Material',
            material_type='coil',
            grade='Test Grade',
            wire_diameter_mm=2.5,
            weight_kg=10.0
        )
        
        self.product = Product.objects.create(
            product_code='PROD001',
            product_type='spring',
            spring_type='compression',
            material=self.raw_material,
            customer_c_id=self.customer,
            grams_per_product=5.0
        )
        
        self.mo = ManufacturingOrder.objects.create(
            product_code=self.product,
            quantity=1000,
            customer_c_id=self.customer,
            planned_start_date=timezone.now(),
            planned_end_date=timezone.now() + timedelta(days=7),
            status='completed'
        )
        
        self.batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=1000,
            actual_quantity_completed=1000,
            status='completed'
        )
        
        self.dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            created_by=self.user
        )
    
    def test_dispatch_transaction_creation(self):
        """Test creating a dispatch transaction"""
        transaction = DispatchTransaction.objects.create(
            mo=self.mo,
            dispatch_batch=self.dispatch_batch,
            customer_c_id=self.customer,
            quantity_dispatched=500,
            supervisor_id=self.supervisor,
            created_by=self.user
        )
        
        self.assertTrue(transaction.transaction_id.startswith('DISPATCH-TXN-'))
        self.assertEqual(transaction.status, 'pending_confirmation')
        self.assertEqual(transaction.quantity_dispatched, 500)
    
    def test_confirm_dispatch(self):
        """Test confirming a dispatch transaction"""
        transaction = DispatchTransaction.objects.create(
            mo=self.mo,
            dispatch_batch=self.dispatch_batch,
            customer_c_id=self.customer,
            quantity_dispatched=500,
            supervisor_id=self.supervisor,
            created_by=self.user
        )
        
        transaction.confirm_dispatch(self.supervisor)
        
        self.assertEqual(transaction.status, 'confirmed')
        self.assertIsNotNone(transaction.confirmed_at)
        
        # Check if dispatch batch quantities were updated
        self.dispatch_batch.refresh_from_db()
        self.assertEqual(self.dispatch_batch.quantity_dispatched, 500)
        self.assertEqual(self.dispatch_batch.status, 'partially_dispatched')


class FGStoreAPITest(APITestCase):
    """Test cases for FG Store API endpoints"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            first_name='Test',
            last_name='User'
        )
        
        # Create FG Store role for the API test user
        role, _ = Role.objects.get_or_create(
            name='fg_store',
            defaults={'description': 'FG Store'},
        )
        UserRole.objects.get_or_create(
            user=self.user,
            role=role,
            defaults={'is_active': True},
        )
        
        self.customer = Customer.objects.create(
            name='Test Customer',
            industry_type='automotive',
            address='Test Address',
            gst_no='22AAAAA0000A1Z5'
        )
        
        self.raw_material = RawMaterial.objects.create(
            material_code='RM001',
            material_name='Test Material',
            material_type='coil',
            grade='Test Grade',
            wire_diameter_mm=2.5,
            weight_kg=10.0
        )
        
        self.product = Product.objects.create(
            product_code='PROD001',
            product_type='spring',
            spring_type='compression',
            material=self.raw_material,
            customer_c_id=self.customer,
            grams_per_product=5.0
        )
        
        self.mo = ManufacturingOrder.objects.create(
            product_code=self.product,
            quantity=1000,
            customer_c_id=self.customer,
            planned_start_date=timezone.now(),
            planned_end_date=timezone.now() + timedelta(days=7),
            status='completed'
        )
        
        self.batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=1000,
            actual_quantity_completed=1000,
            status='completed'
        )
        
        self.dispatch_batch = DispatchBatch.objects.create(
            mo=self.mo,
            production_batch=self.batch,
            product_code=self.product,
            quantity_produced=1000,
            quantity_packed=1000,
            created_by=self.user
        )
    
    def test_stock_levels_endpoint(self):
        """Test FG Stock Level endpoint"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.get('/api/fg-store/dashboard/stock_levels/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        
        if response.data:
            stock_item = response.data[0]
            self.assertIn('batch_id', stock_item)
            self.assertIn('mo_id', stock_item)
            self.assertIn('product_code', stock_item)
            self.assertIn('quantity_in_stock', stock_item)
    
    def test_pending_dispatch_mos_endpoint(self):
        """Test Pending Dispatch MOs endpoint"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.get('/api/fg-store/dashboard/pending_dispatch_mos/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        
        if response.data:
            mo_item = response.data[0]
            self.assertIn('mo_id', mo_item)
            self.assertIn('customer_name', mo_item)
            self.assertIn('quantity_ordered', mo_item)
            self.assertIn('quantity_packed', mo_item)
    
    def test_validate_dispatch_endpoint(self):
        """Test dispatch validation endpoint"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.get(
            f'/api/fg-store/dashboard/validate_dispatch/?batch_id={self.dispatch_batch.batch_id}&quantity_to_dispatch=500'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('is_valid', response.data)
        self.assertIn('available_qty', response.data)
        self.assertIn('warnings', response.data)
        self.assertIn('errors', response.data)
        
        self.assertTrue(response.data['is_valid'])
        self.assertEqual(response.data['available_qty'], 1000)
    
    def test_create_dispatch_transaction(self):
        """Test creating a dispatch transaction"""
        self.client.force_authenticate(user=self.user)
        
        data = {
            'mo': self.mo.id,
            'dispatch_batch': self.dispatch_batch.id,
            'customer_c_id': self.customer.c_id,
            'quantity_dispatched': 500,
            'supervisor_id': self.user.id,
            'notes': 'Test dispatch'
        }
        
        response = self.client.post('/api/fg-store/dispatch-transactions/', data)
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('transaction_id', response.data)
        self.assertEqual(response.data['quantity_dispatched'], 500)
        self.assertEqual(response.data['status'], 'pending_confirmation')
