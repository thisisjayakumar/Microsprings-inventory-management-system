from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase

from authentication.models import Role, UserRole
from inventory.models import InventoryTransaction, Location
from manufacturing.models import OutsourcedItem, OutsourcingRequest
from third_party.models import Vendor

User = get_user_model()


class OutsourcingRequestModelTest(TestCase):
    """Test cases for OutsourcingRequest model"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User'
        )
        
        self.vendor = Vendor.objects.create(
            name='Test Vendor',
            vendor_type='outsource_vendor',
            contact_person='John Doe',
            email='vendor@example.com',
            is_active=True
        )
    
    def test_create_outsourcing_request(self):
        """Test creating a basic outsourcing request"""
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            vendor_contact_person='Jane Smith',
            notes='Test outsourcing request'
        )
        
        self.assertEqual(request.created_by, self.user)
        self.assertEqual(request.vendor, self.vendor)
        self.assertEqual(request.status, 'draft')
        self.assertEqual(request.vendor_contact_person, 'Jane Smith')
        self.assertEqual(request.notes, 'Test outsourcing request')
        self.assertTrue(request.request_id.startswith('OUT-'))
    
    def test_request_id_auto_generation(self):
        """Test that request_id is auto-generated"""
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        self.assertTrue(request.request_id.startswith('OUT-'))
        self.assertEqual(len(request.request_id), 17)  # OUT-YYYYMMDD-NNNN format
    
    def test_is_overdue_property(self):
        """Test the is_overdue property"""
        # Not overdue - future date
        future_request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            status='sent'
        )
        self.assertFalse(future_request.is_overdue)
        
        # Not overdue - already returned
        returned_request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() - timedelta(days=1),
            status='returned'
        )
        self.assertFalse(returned_request.is_overdue)
        
        # Overdue - past date and sent status
        overdue_request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() - timedelta(days=1),
            status='sent'
        )
        self.assertTrue(overdue_request.is_overdue)
    
    def test_total_items_property(self):
        """Test the total_items property"""
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        # No items initially
        self.assertEqual(request.total_items, 0)
        
        # Add items
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-002',
            product_code='PROD-002',
            qty=200,
            kg=Decimal('10.0')
        )
        
        self.assertEqual(request.total_items, 2)
    
    def test_total_qty_property(self):
        """Test the total_qty property"""
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-002',
            product_code='PROD-002',
            qty=200,
            kg=Decimal('10.0')
        )
        
        self.assertEqual(request.total_qty, 300)
    
    def test_total_kg_property(self):
        """Test the total_kg property"""
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-002',
            product_code='PROD-002',
            qty=200,
            kg=Decimal('10.0')
        )
        
        self.assertEqual(request.total_kg, Decimal('15.5'))


class OutsourcedItemModelTest(TestCase):
    """Test cases for OutsourcedItem model"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User'
        )
        
        self.vendor = Vendor.objects.create(
            name='Test Vendor',
            vendor_type='outsource_vendor',
            contact_person='John Doe',
            email='vendor@example.com',
            is_active=True
        )
        
        self.request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
    
    def test_create_outsourced_item(self):
        """Test creating an outsourced item"""
        item = OutsourcedItem.objects.create(
            request=self.request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5'),
            notes='Test item'
        )
        
        self.assertEqual(item.request, self.request)
        self.assertEqual(item.mo_number, 'MO-001')
        self.assertEqual(item.product_code, 'PROD-001')
        self.assertEqual(item.qty, 100)
        self.assertEqual(item.kg, Decimal('5.5'))
        self.assertEqual(item.returned_qty, 0)
        self.assertEqual(item.returned_kg, Decimal('0'))
        self.assertEqual(item.notes, 'Test item')
    
    def test_item_validation_qty_or_kg_required(self):
        """Test that at least qty or kg is required"""
        # Should work with qty only
        item1 = OutsourcedItem.objects.create(
            request=self.request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100
        )
        self.assertEqual(item1.qty, 100)
        self.assertIsNone(item1.kg)
        
        # Should work with kg only
        item2 = OutsourcedItem.objects.create(
            request=self.request,
            mo_number='MO-002',
            product_code='PROD-002',
            kg=Decimal('5.5')
        )
        self.assertIsNone(item2.qty)
        self.assertEqual(item2.kg, Decimal('5.5'))
        
        # Should fail with neither qty nor kg
        with self.assertRaises(Exception):
            OutsourcedItem.objects.create(
                request=self.request,
                mo_number='MO-003',
                product_code='PROD-003'
            )
    
    def test_returned_quantities_defaults(self):
        """Test that returned quantities default to 0"""
        item = OutsourcedItem.objects.create(
            request=self.request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        
        self.assertEqual(item.returned_qty, 0)
        self.assertEqual(item.returned_kg, Decimal('0'))


class OutsourcingRequestViewSetTest(TransactionTestCase):
    """Test cases for OutsourcingRequestViewSet API endpoints"""
    
    def setUp(self):
        # Permission classes cache user roles for 300s; clear between tests.
        cache.clear()
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User'
        )
        
        self.manager = User.objects.create_user(
            username='manager@example.com',
            email='manager@example.com',
            password='testpass123',
            first_name='Manager',
            last_name='User'
        )
        
        supervisor_role, _ = Role.objects.get_or_create(
            name='supervisor', defaults={'description': 'Supervisor'}
        )
        manager_role, _ = Role.objects.get_or_create(
            name='manager', defaults={'description': 'Manager'}
        )
        UserRole.objects.get_or_create(
            user=self.user, role=supervisor_role, defaults={'is_active': True}
        )
        UserRole.objects.get_or_create(
            user=self.manager, role=manager_role, defaults={'is_active': True}
        )
        
        self.vendor = Vendor.objects.create(
            name='Test Vendor',
            vendor_type='outsource_vendor',
            contact_person='John Doe',
            email='vendor@example.com',
            is_active=True
        )
        
        # Create default locations for inventory transactions
        self.dispatch_location = Location.objects.create(
            code='DISPATCH_ZONE',
            location_name='dispatched',
        )
        
        self.fg_location = Location.objects.create(
            code='FG_STORE',
            location_name='fg',
        )
    
    def test_create_outsourcing_request(self):
        """Test creating an outsourcing request via API"""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        
        client = APIClient()
        token = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        
        data = {
            'vendor_id': self.vendor.id,
            'expected_return_date': (date.today() + timedelta(days=7)).isoformat(),
            'vendor_contact_person': 'Jane Smith',
            'notes': 'Test request',
            'items_data': [
                {
                    'mo_number': 'MO-001',
                    'product_code': 'PROD-001',
                    'qty': 100,
                    'kg': 5.5,
                    'notes': 'Test item'
                }
            ]
        }
        
        response = client.post('/api/manufacturing/outsourcing/', data, format='json')
        self.assertEqual(response.status_code, 201)
        
        request_data = response.json()
        self.assertEqual(request_data['vendor'], self.vendor.id)
        self.assertEqual(request_data['vendor_contact_person'], 'Jane Smith')
        self.assertEqual(len(request_data['items']), 1)
    
    def test_send_request_creates_inventory_transactions(self):
        """Test that sending a request creates OUT inventory transactions"""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create request with items
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        
        client = APIClient()
        token = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        
        data = {
            'date_sent': date.today().isoformat(),
            'vendor_contact_person': 'Jane Smith'
        }
        
        response = client.post(f'/api/manufacturing/outsourcing/{request.id}/send/', data, format='json')
        self.assertEqual(response.status_code, 200)
        
        # Check that request status changed
        request.refresh_from_db()
        self.assertEqual(request.status, 'sent')
        self.assertEqual(request.date_sent, date.today())
        
        # Check that inventory transactions were created
        transactions = InventoryTransaction.objects.filter(
            reference_type='outsourcing',
            reference_id=str(request.id)
        )
        self.assertEqual(transactions.count(), 1)
        
        transaction = transactions.first()
        self.assertEqual(transaction.transaction_type, 'outward')
        self.assertEqual(transaction.quantity, Decimal('100'))
        self.assertEqual(transaction.location_from, self.dispatch_location)
    
    def test_return_items_creates_inventory_transactions(self):
        """Test that returning items creates IN inventory transactions"""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create sent request with items
        request = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            status='sent',
            date_sent=date.today()
        )
        
        item = OutsourcedItem.objects.create(
            request=request,
            mo_number='MO-001',
            product_code='PROD-001',
            qty=100,
            kg=Decimal('5.5')
        )
        
        client = APIClient()
        token = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        
        data = {
            'collection_date': date.today().isoformat(),
            'collected_by_id': self.user.id,
            'returned_items': [
                {
                    'id': item.id,
                    'returned_qty': 95,
                    'returned_kg': Decimal('5.2')
                }
            ]
        }
        
        response = client.post(f'/api/manufacturing/outsourcing/{request.id}/return_items/', data, format='json')
        self.assertEqual(response.status_code, 200)
        
        # Check that request status changed
        request.refresh_from_db()
        self.assertEqual(request.status, 'returned')
        self.assertEqual(request.collection_date, date.today())
        
        # Check that item returned quantities were updated
        item.refresh_from_db()
        self.assertEqual(item.returned_qty, 95)
        self.assertEqual(item.returned_kg, Decimal('5.2'))
        
        # Check that inventory transactions were created
        transactions = InventoryTransaction.objects.filter(
            reference_type='outsourcing',
            reference_id=str(request.id)
        )
        # This request was created directly in 'sent' state, so only the
        # return (IN) transaction exists; the OUT came from the send endpoint.
        self.assertEqual(transactions.count(), 1)

        in_transaction = transactions.filter(transaction_type='inward').first()
        self.assertEqual(in_transaction.transaction_type, 'inward')
        self.assertEqual(in_transaction.quantity, Decimal('95'))
        self.assertEqual(in_transaction.location_to, self.fg_location)
    
    def test_permissions_manager_can_view_all_supervisor_only_own(self):
        """Test that managers can view all requests, supervisors only their own"""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create requests by different users
        request1 = OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        other_user = User.objects.create_user(
            username='other@example.com',
            email='other@example.com',
            password='testpass123',
            first_name='Other',
            last_name='User'
        )
        
        OutsourcingRequest.objects.create(
            created_by=other_user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7)
        )
        
        # Test supervisor can only see their own requests
        client = APIClient()
        token = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        
        response = client.get('/api/manufacturing/outsourcing/')
        self.assertEqual(response.status_code, 200)
        
        requests_data = response.json()['results']
        # Should only see request1 (created by self.user)
        self.assertEqual(len(requests_data), 1)
        self.assertEqual(requests_data[0]['id'], request1.id)
    
    def test_summary_endpoint(self):
        """Test the summary endpoint returns correct statistics"""
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        
        # Create requests with different statuses
        OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            status='draft'
        )
        
        OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            status='sent'
        )
        
        OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() - timedelta(days=1),
            status='sent'  # This should be overdue
        )
        
        OutsourcingRequest.objects.create(
            created_by=self.user,
            vendor=self.vendor,
            expected_return_date=date.today() + timedelta(days=7),
            status='returned'
        )
        
        client = APIClient()
        token = RefreshToken.for_user(self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        
        response = client.get('/api/manufacturing/outsourcing/summary/')
        self.assertEqual(response.status_code, 200)
        
        summary = response.json()
        self.assertEqual(summary['total_requests'], 4)
        self.assertEqual(summary['pending_returns'], 2)  # 2 sent requests
        self.assertEqual(summary['overdue_returns'], 1)  # 1 overdue request
        self.assertEqual(summary['recent_requests'], 4)  # All created today
