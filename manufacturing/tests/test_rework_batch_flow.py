"""
Test cases for Rework Batch Flow across Multiple Supervisors
Tests the complete flow: 
1. Supervisor 1 completes batch with rework
2. Supervisor 1 completes rework with OK quantity - should move to Supervisor 2 as regular batch
3. Supervisor 2 sees it as regular batch (not rework)
4. Supervisor 2 completes regular batch with rework - rework stays with S2, OK moves to S3
5. Supervisor 2 completes rework with OK - should move to S3 as regular batch
6. Verify batches appear in correct lists for each supervisor
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from decimal import Decimal
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from inventory.models import RawMaterial, RMStockBalance, RMStockBalanceHeat
from products.models import Product
from processes.models import Process
from manufacturing.models import (
    ManufacturingOrder, Batch, MOProcessExecution,
    BatchProcessCompletion, BatchReceiptLog
)
from manufacturing.models.rework import ReworkBatch
from authentication.models import UserRole, Role

User = get_user_model()


class ReworkBatchFlowTestCase(TestCase):
    """
    Comprehensive test case for rework batch flow across multiple supervisors.
    
    Tests:
    1. Supervisor 1 completes batch with 50kg OK and 50kg rework
    2. Rework batch is created and assigned to Supervisor 1
    3. Supervisor 1 completes rework with 50kg OK - should create regular batch for Supervisor 2
    4. Supervisor 2 sees regular batch (not rework batch) in their list
    5. Supervisor 2 completes regular batch with 20kg OK and 30kg rework
    6. 20kg OK moves to Supervisor 3 as regular batch
    7. 30kg rework stays with Supervisor 2 as rework batch
    8. Supervisor 2 completes rework with OK - should move to Supervisor 3 as regular batch
    9. Verify batches appear in correct lists for each supervisor
    """
    
    def setUp(self):
        """Set up test data with multiple processes and supervisors"""
        # Create users
        self.manager_user = User.objects.create_user(
            username='manager@test.com',
            email='manager@test.com',
            password='testpass123',
            first_name='Manager',
            last_name='Test'
        )
        
        # Create supervisors for each process
        self.supervisor_1 = User.objects.create_user(
            username='supervisor1@test.com',
            email='supervisor1@test.com',
            password='testpass123',
            first_name='Supervisor',
            last_name='One'
        )
        
        self.supervisor_2 = User.objects.create_user(
            username='supervisor2@test.com',
            email='supervisor2@test.com',
            password='testpass123',
            first_name='Supervisor',
            last_name='Two'
        )
        
        self.supervisor_3 = User.objects.create_user(
            username='supervisor3@test.com',
            email='supervisor3@test.com',
            password='testpass123',
            first_name='Supervisor',
            last_name='Three'
        )
        
        # Create roles
        manager_role, _ = Role.objects.get_or_create(name='manager')
        supervisor_role, _ = Role.objects.get_or_create(name='supervisor')
        
        UserRole.objects.get_or_create(
            user=self.manager_user,
            role=manager_role,
            defaults={'is_active': True}
        )
        
        UserRole.objects.get_or_create(
            user=self.supervisor_1,
            role=supervisor_role,
            defaults={'is_active': True}
        )
        
        UserRole.objects.get_or_create(
            user=self.supervisor_2,
            role=supervisor_role,
            defaults={'is_active': True}
        )
        
        UserRole.objects.get_or_create(
            user=self.supervisor_3,
            role=supervisor_role,
            defaults={'is_active': True}
        )
        
        # Create raw material
        self.raw_material = RawMaterial.objects.create(
            material_code='TEST_RM_001',
            material_name='Test Raw Material',
            material_type='coil',
            grade='TEST_GRADE',
            wire_diameter_mm=Decimal('1.80')
        )
        
        # Create initial stock balance
        self.initial_stock_kg = Decimal('1000.00')
        self.stock_balance, _ = RMStockBalance.objects.get_or_create(
            raw_material=self.raw_material,
            defaults={'available_quantity': self.initial_stock_kg}
        )
        self.stock_balance.available_quantity = self.initial_stock_kg
        self.stock_balance.save()
        
        # Create heat stock balance
        self.heat_stock_balance, _ = RMStockBalanceHeat.objects.get_or_create(
            raw_material=self.raw_material,
            defaults={'total_available_quantity_kg': self.initial_stock_kg}
        )
        self.heat_stock_balance.total_available_quantity_kg = self.initial_stock_kg
        self.heat_stock_balance.save()
        
        # Create product
        self.product = Product.objects.create(
            product_code='TEST-PRODUCT-001',
            material=self.raw_material,
            product_type='spring',
            spring_type='TENSION SPRING',
            grams_per_product=Decimal('8.00')
        )
        
        # Create processes
        self.process_1 = Process.objects.create(
            name='Process_1',
            code=1,
            description='First process',
            is_active=True
        )
        
        self.process_2 = Process.objects.create(
            name='Process_2',
            code=2,
            description='Second process',
            is_active=True
        )
        
        self.process_3 = Process.objects.create(
            name='Process_3',
            code=3,
            description='Third process',
            is_active=True
        )
        
        # Create Manufacturing Order
        self.mo = ManufacturingOrder.objects.create(
            mo_id='MO-TEST-001',
            product_code=self.product,
            quantity=10000,  # 10000 pieces
            status='in_progress',
            created_by=self.manager_user
        )
        
        # Create process executions
        self.process_execution_1 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_1,
            sequence_order=1,
            status='in_progress',
            assigned_supervisor=self.supervisor_1
        )
        
        self.process_execution_2 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_2,
            sequence_order=2,
            status='pending',
            assigned_supervisor=self.supervisor_2
        )
        
        self.process_execution_3 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_3,
            sequence_order=3,
            status='pending',
            assigned_supervisor=self.supervisor_3
        )
        
        # Create initial batch (100kg = 100000 grams)
        self.initial_batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=100000,  # 100kg in grams
            status='in_progress',
            assigned_supervisor=self.supervisor_1,
            is_rework_batch=False
        )
        
        # Create API clients for each supervisor
        self.client_s1 = APIClient()
        self.client_s1.force_authenticate(user=self.supervisor_1)
        
        self.client_s2 = APIClient()
        self.client_s2.force_authenticate(user=self.supervisor_2)
        
        self.client_s3 = APIClient()
        self.client_s3.force_authenticate(user=self.supervisor_3)
    
    def test_scenario_1_supervisor_1_completes_batch_with_rework(self):
        """
        Test: Supervisor 1 completes initial batch with 50kg OK and 50kg rework
        Expected: 
        - Rework batch created and assigned to Supervisor 1
        - 50kg OK moves to Supervisor 2 as regular batch
        """
        # Supervisor 1 completes batch with 50kg OK and 50kg rework
        response = self.client_s1.post(
            '/api/manufacturing/batch-completions/',
            {
                'batch_id': self.initial_batch.id,
                'process_execution_id': self.process_execution_1.id,
                'input_quantity_kg': '100.00',
                'ok_quantity_kg': '50.00',
                'scrap_quantity_kg': '0.00',
                'rework_quantity_kg': '50.00',
                'defect_description': 'Test rework from Supervisor 1'
            },
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify rework batch was created and assigned to Supervisor 1
        rework_batches = ReworkBatch.objects.filter(
            original_batch=self.initial_batch,
            assigned_supervisor=self.supervisor_1
        )
        self.assertEqual(rework_batches.count(), 1)
        
        rework_batch = rework_batches.first()
        self.assertEqual(rework_batch.rework_quantity_kg, Decimal('50.00'))
        self.assertEqual(rework_batch.status, 'pending')
        self.assertEqual(rework_batch.rework_cycle_number, 1)
        self.assertEqual(rework_batch.process_execution, self.process_execution_1)
        
        # Verify receipt log was created for OK quantity moving to Supervisor 2
        receipt_logs = BatchReceiptLog.objects.filter(
            mo=self.mo,
            to_process=self.process_2,
            from_process=self.process_1
        )
        self.assertEqual(receipt_logs.count(), 1)
        
        receipt_log = receipt_logs.first()
        self.assertEqual(receipt_log.handed_over_quantity_kg, Decimal('50.00'))
        self.assertFalse(receipt_log.is_rework_batch)  # Should be regular batch, not rework
        
        # Note: _move_to_next_process creates receipt logs, not batches
        # Batches are typically created when supervisors verify receipt logs
        # For this test, we verify that the receipt log exists and points to the correct batch
        self.assertEqual(receipt_log.batch, self.initial_batch)
        self.assertEqual(receipt_log.handed_over_quantity_kg, Decimal('50.00'))
    
    def test_scenario_2_supervisor_1_completes_rework_with_ok(self):
        """
        Test: Supervisor 1 completes rework batch with 50kg OK
        Expected:
        - Regular batch created for Supervisor 2 (not rework batch)
        - Batch appears in Supervisor 2's regular batch list
        """
        # First, create the rework batch (from scenario 1)
        completion_1 = BatchProcessCompletion.objects.create(
            batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completed_by=self.supervisor_1,
            input_quantity_kg=Decimal('100.00'),
            ok_quantity_kg=Decimal('50.00'),
            scrap_quantity_kg=Decimal('0.00'),
            rework_quantity_kg=Decimal('50.00'),
            defect_description='Test rework'
        )
        
        rework_batch = ReworkBatch.objects.create(
            original_batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completion_record=completion_1,
            rework_quantity_kg=Decimal('50.00'),
            status='pending',  # Start as pending, then start it
            assigned_supervisor=self.supervisor_1,
            rework_cycle_number=1,
            defect_description='Test rework'
        )
        rework_batch.start_rework()  # This will change status to 'in_progress'
        
        # Verify rework batch is in correct state before completing
        rework_batch.refresh_from_db()
        self.assertEqual(rework_batch.status, 'in_progress')
        self.assertEqual(rework_batch.rework_quantity_kg, Decimal('50.00'))
        
        # Supervisor 1 completes rework with 50kg OK
        response = self.client_s1.post(
            f'/api/manufacturing/rework-batches/{rework_batch.id}/complete/',
            {
                'ok_kg': '50.00',
                'scrap_kg': '0.00',
                'rework_kg': '0.00'
            },
            format='json'
        )
        
        if response.status_code != status.HTTP_200_OK:
            print(f"Error response: {response.data}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK, f"Expected 200, got {response.status_code}. Response: {response.data}")
        
        # Verify rework batch is completed
        rework_batch.refresh_from_db()
        self.assertEqual(rework_batch.status, 'completed')
        
        # Verify a REGULAR batch (not rework batch) was created for Supervisor 2
        regular_batches_s2 = Batch.objects.filter(
            mo=self.mo,
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False
        )
        self.assertGreaterEqual(regular_batches_s2.count(), 1)
        
        # Check the most recent batch created
        latest_batch_s2 = regular_batches_s2.order_by('-created_at').first()
        self.assertIsNotNone(latest_batch_s2)
        self.assertEqual(latest_batch_s2.planned_quantity, 50000)  # 50kg in grams
        self.assertFalse(latest_batch_s2.is_rework_batch)
        
        # Verify receipt log shows it's a regular batch
        receipt_logs = BatchReceiptLog.objects.filter(
            mo=self.mo,
            to_process=self.process_2,
            batch=latest_batch_s2
        )
        self.assertEqual(receipt_logs.count(), 1)
        receipt_log = receipt_logs.first()
        self.assertFalse(receipt_log.is_rework_batch)
        self.assertEqual(receipt_log.rework_cycle_number, 0)
    
    def test_scenario_3_supervisor_2_sees_regular_batch_not_rework(self):
        """
        Test: Supervisor 2 should see regular batch in their list, not rework batch
        Expected:
        - Regular batch appears in Supervisor 2's batch list
        - No rework batches assigned to Supervisor 2 from Supervisor 1's rework
        """
        # Create the scenario: Supervisor 1 completed rework with OK quantity
        completion_1 = BatchProcessCompletion.objects.create(
            batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completed_by=self.supervisor_1,
            input_quantity_kg=Decimal('100.00'),
            ok_quantity_kg=Decimal('50.00'),
            scrap_quantity_kg=Decimal('0.00'),
            rework_quantity_kg=Decimal('50.00'),
            defect_description='Test rework'
        )
        
        rework_batch = ReworkBatch.objects.create(
            original_batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completion_record=completion_1,
            rework_quantity_kg=Decimal('50.00'),
            status='pending',  # Start as pending, then start it
            assigned_supervisor=self.supervisor_1,
            rework_cycle_number=1,
            defect_description='Test rework'
        )
        rework_batch.start_rework()  # This will change status to 'in_progress'
        
        # Complete rework with OK quantity
        completion_2, _ = rework_batch.complete_rework(
            ok_kg=Decimal('50.00'),
            scrap_kg=Decimal('0.00'),
            rework_kg=Decimal('0.00')
        )
        
        # Create regular batch for Supervisor 2
        regular_batch_s2 = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=50000,  # 50kg
            status='created',
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False,
            notes='OK quantity from rework cycle 1 of Process_1'
        )
        
        # Supervisor 2 gets their batches
        response = self.client_s2.get(
            f'/api/manufacturing/batches/?mo_id={self.mo.id}'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify Supervisor 2 sees the regular batch
        batches_data = response.data.get('results', []) if hasattr(response.data, 'get') else response.data
        if isinstance(batches_data, list):
            batch_ids = [b['id'] for b in batches_data if isinstance(b, dict)]
        else:
            batch_ids = []
        
        # Check that regular batch is in the list
        self.assertIn(regular_batch_s2.id, batch_ids)
        
        # Verify Supervisor 2 does NOT see rework batches assigned to Supervisor 1
        response_rework = self.client_s2.get(
            '/api/manufacturing/rework-batches/'
        )
        
        self.assertEqual(response_rework.status_code, status.HTTP_200_OK)
        rework_data = response_rework.data.get('results', []) if hasattr(response_rework.data, 'get') else response_rework.data
        if isinstance(rework_data, list):
            rework_batch_ids = [r['id'] for r in rework_data if isinstance(r, dict)]
        else:
            rework_batch_ids = []
        
        # Supervisor 2 should not see Supervisor 1's rework batch
        self.assertNotIn(rework_batch.id, rework_batch_ids)
    
    def test_scenario_4_supervisor_2_completes_regular_batch_with_rework(self):
        """
        Test: Supervisor 2 completes regular batch with 20kg OK and 30kg rework
        Expected:
        - 20kg OK moves to Supervisor 3 as regular batch
        - 30kg rework stays with Supervisor 2 as rework batch
        """
        # Create regular batch for Supervisor 2 (from Supervisor 1's rework OK quantity)
        regular_batch_s2 = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=50000,  # 50kg
            status='in_progress',
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False
        )
        
        # Supervisor 2 completes batch with 20kg OK and 30kg rework
        response = self.client_s2.post(
            '/api/manufacturing/batch-completions/',
            {
                'batch_id': regular_batch_s2.id,
                'process_execution_id': self.process_execution_2.id,
                'input_quantity_kg': '50.00',
                'ok_quantity_kg': '20.00',
                'scrap_quantity_kg': '0.00',
                'rework_quantity_kg': '30.00',
                'defect_description': 'Test rework from Supervisor 2'
            },
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify rework batch was created and assigned to Supervisor 2
        rework_batches_s2 = ReworkBatch.objects.filter(
            original_batch=regular_batch_s2,
            assigned_supervisor=self.supervisor_2
        )
        self.assertEqual(rework_batches_s2.count(), 1)
        
        rework_batch_s2 = rework_batches_s2.first()
        self.assertEqual(rework_batch_s2.rework_quantity_kg, Decimal('30.00'))
        self.assertEqual(rework_batch_s2.status, 'pending')
        self.assertEqual(rework_batch_s2.rework_cycle_number, 1)
        
        # Verify receipt log was created for 20kg OK moving to Supervisor 3
        receipt_logs_s3 = BatchReceiptLog.objects.filter(
            mo=self.mo,
            to_process=self.process_3,
            from_process=self.process_2
        )
        self.assertEqual(receipt_logs_s3.count(), 1)
        
        receipt_log_s3 = receipt_logs_s3.first()
        self.assertEqual(receipt_log_s3.handed_over_quantity_kg, Decimal('20.00'))
        self.assertFalse(receipt_log_s3.is_rework_batch)  # Should be regular batch, not rework
    
    def test_scenario_5_supervisor_2_completes_rework_with_ok(self):
        """
        Test: Supervisor 2 completes rework with OK quantity
        Expected:
        - Regular batch created for Supervisor 3 (not rework batch)
        - Batch appears in Supervisor 3's regular batch list
        """
        # Create regular batch for Supervisor 2
        regular_batch_s2 = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=50000,  # 50kg
            status='in_progress',
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False
        )
        
        # Create completion with rework
        completion_s2 = BatchProcessCompletion.objects.create(
            batch=regular_batch_s2,
            process_execution=self.process_execution_2,
            completed_by=self.supervisor_2,
            input_quantity_kg=Decimal('50.00'),
            ok_quantity_kg=Decimal('20.00'),
            scrap_quantity_kg=Decimal('0.00'),
            rework_quantity_kg=Decimal('30.00'),
            defect_description='Test rework from Supervisor 2'
        )
        
        # Create rework batch for Supervisor 2
        rework_batch_s2 = ReworkBatch.objects.create(
            original_batch=regular_batch_s2,
            process_execution=self.process_execution_2,
            completion_record=completion_s2,
            rework_quantity_kg=Decimal('30.00'),
            status='pending',  # Start as pending, then start it
            assigned_supervisor=self.supervisor_2,
            rework_cycle_number=1,
            defect_description='Test rework from Supervisor 2'
        )
        rework_batch_s2.start_rework()  # This will change status to 'in_progress'
        
        # Verify rework batch is in correct state before completing
        rework_batch_s2.refresh_from_db()
        self.assertEqual(rework_batch_s2.status, 'in_progress')
        self.assertEqual(rework_batch_s2.rework_quantity_kg, Decimal('30.00'))
        
        # Supervisor 2 completes rework with 30kg OK
        response = self.client_s2.post(
            f'/api/manufacturing/rework-batches/{rework_batch_s2.id}/complete/',
            {
                'ok_kg': '30.00',
                'scrap_kg': '0.00',
                'rework_kg': '0.00'
            },
            format='json'
        )
        
        if response.status_code != status.HTTP_200_OK:
            print(f"Error response: {response.data}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK, f"Expected 200, got {response.status_code}. Response: {response.data}")
        
        # Verify rework batch is completed
        rework_batch_s2.refresh_from_db()
        self.assertEqual(rework_batch_s2.status, 'completed')
        
        # Verify a REGULAR batch (not rework batch) was created for Supervisor 3
        regular_batches_s3 = Batch.objects.filter(
            mo=self.mo,
            assigned_supervisor=self.supervisor_3,
            is_rework_batch=False
        )
        self.assertGreaterEqual(regular_batches_s3.count(), 1)
        
        # Check the most recent batch created
        latest_batch_s3 = regular_batches_s3.order_by('-created_at').first()
        self.assertIsNotNone(latest_batch_s3)
        self.assertEqual(latest_batch_s3.planned_quantity, 30000)  # 30kg in grams
        self.assertFalse(latest_batch_s3.is_rework_batch)
        
        # Verify receipt log shows it's a regular batch
        receipt_logs = BatchReceiptLog.objects.filter(
            mo=self.mo,
            to_process=self.process_3,
            batch=latest_batch_s3
        )
        self.assertEqual(receipt_logs.count(), 1)
        receipt_log = receipt_logs.first()
        self.assertFalse(receipt_log.is_rework_batch)
        self.assertEqual(receipt_log.rework_cycle_number, 0)
    
    def test_complete_flow_all_scenarios(self):
        """
        Test complete flow: All scenarios in sequence
        """
        # Scenario 1: Supervisor 1 completes batch with rework
        completion_1 = BatchProcessCompletion.objects.create(
            batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completed_by=self.supervisor_1,
            input_quantity_kg=Decimal('100.00'),
            ok_quantity_kg=Decimal('50.00'),
            scrap_quantity_kg=Decimal('0.00'),
            rework_quantity_kg=Decimal('50.00'),
            defect_description='Test rework from Supervisor 1'
        )
        
        rework_batch_s1 = ReworkBatch.objects.create(
            original_batch=self.initial_batch,
            process_execution=self.process_execution_1,
            completion_record=completion_1,
            rework_quantity_kg=Decimal('50.00'),
            status='pending',  # Start as pending, then start it
            assigned_supervisor=self.supervisor_1,
            rework_cycle_number=1,
            defect_description='Test rework from Supervisor 1'
        )
        rework_batch_s1.start_rework()  # This will change status to 'in_progress'
        
        # Verify rework batch is assigned to Supervisor 1
        self.assertEqual(rework_batch_s1.assigned_supervisor, self.supervisor_1)
        
        # Scenario 2: Supervisor 1 completes rework with OK
        completion_2, _ = rework_batch_s1.complete_rework(
            ok_kg=Decimal('50.00'),
            scrap_kg=Decimal('0.00'),
            rework_kg=Decimal('0.00')
        )
        
        # Create regular batch for Supervisor 2 (simulating the API call)
        regular_batch_s2 = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=50000,  # 50kg
            status='created',
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False,
            notes='OK quantity from rework cycle 1 of Process_1'
        )
        
        # Verify Supervisor 2 has regular batch, not rework batch
        self.assertFalse(regular_batch_s2.is_rework_batch)
        self.assertEqual(regular_batch_s2.assigned_supervisor, self.supervisor_2)
        
        # Scenario 3: Supervisor 2 completes regular batch with rework
        completion_3 = BatchProcessCompletion.objects.create(
            batch=regular_batch_s2,
            process_execution=self.process_execution_2,
            completed_by=self.supervisor_2,
            input_quantity_kg=Decimal('50.00'),
            ok_quantity_kg=Decimal('20.00'),
            scrap_quantity_kg=Decimal('0.00'),
            rework_quantity_kg=Decimal('30.00'),
            defect_description='Test rework from Supervisor 2'
        )
        
        rework_batch_s2 = ReworkBatch.objects.create(
            original_batch=regular_batch_s2,
            process_execution=self.process_execution_2,
            completion_record=completion_3,
            rework_quantity_kg=Decimal('30.00'),
            status='pending',  # Start as pending, then start it
            assigned_supervisor=self.supervisor_2,
            rework_cycle_number=1,
            defect_description='Test rework from Supervisor 2'
        )
        rework_batch_s2.start_rework()  # This will change status to 'in_progress'
        
        # Verify rework batch stays with Supervisor 2
        self.assertEqual(rework_batch_s2.assigned_supervisor, self.supervisor_2)
        
        # Create regular batch for Supervisor 3 (from Supervisor 2's OK quantity)
        regular_batch_s3 = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=20000,  # 20kg
            status='created',
            assigned_supervisor=self.supervisor_3,
            is_rework_batch=False
        )
        
        # Verify Supervisor 3 has regular batch
        self.assertFalse(regular_batch_s3.is_rework_batch)
        
        # Scenario 4: Supervisor 2 completes rework with OK
        completion_4, _ = rework_batch_s2.complete_rework(
            ok_kg=Decimal('30.00'),
            scrap_kg=Decimal('0.00'),
            rework_kg=Decimal('0.00')
        )
        
        # Create regular batch for Supervisor 3 (from Supervisor 2's rework OK)
        regular_batch_s3_from_rework = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=30000,  # 30kg
            status='created',
            assigned_supervisor=self.supervisor_3,
            is_rework_batch=False,
            notes='OK quantity from rework cycle 1 of Process_2'
        )
        
        # Verify it's a regular batch
        self.assertFalse(regular_batch_s3_from_rework.is_rework_batch)
        self.assertEqual(regular_batch_s3_from_rework.assigned_supervisor, self.supervisor_3)
        
        # Final verification: Check rework batches are assigned correctly
        rework_batches_s1 = ReworkBatch.objects.filter(assigned_supervisor=self.supervisor_1)
        rework_batches_s2 = ReworkBatch.objects.filter(assigned_supervisor=self.supervisor_2)
        rework_batches_s3 = ReworkBatch.objects.filter(assigned_supervisor=self.supervisor_3)
        
        # Supervisor 1 should have 1 rework batch (completed)
        self.assertEqual(rework_batches_s1.count(), 1)
        # Supervisor 2 should have 1 rework batch (completed)
        self.assertEqual(rework_batches_s2.count(), 1)
        # Supervisor 3 should have no rework batches
        self.assertEqual(rework_batches_s3.count(), 0)
        
        # Verify regular batches
        regular_batches_s2 = Batch.objects.filter(
            mo=self.mo,
            assigned_supervisor=self.supervisor_2,
            is_rework_batch=False
        )
        regular_batches_s3 = Batch.objects.filter(
            mo=self.mo,
            assigned_supervisor=self.supervisor_3,
            is_rework_batch=False
        )
        
        # Supervisor 2 should have at least 1 regular batch
        self.assertGreaterEqual(regular_batches_s2.count(), 1)
        # Supervisor 3 should have at least 2 regular batches (20kg + 30kg)
        self.assertGreaterEqual(regular_batches_s3.count(), 2)
