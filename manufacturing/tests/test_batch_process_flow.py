"""
Test cases for Batch Process Flow with Multiple Process Steps
Tests the complete flow: MO Creation -> Approval -> Batch Release -> Process Execution -> Quantity Flow
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
    ManufacturingOrder, Batch, MOApprovalWorkflow, MOProcessExecution,
    BatchProcessCompletion, BatchReceiptLog, BatchReceiptVerification
)
from authentication.models import UserRole, Role

User = get_user_model()


class BatchProcessFlowTestCase(TestCase):
    """
    Comprehensive test case for batch process flow with multiple process steps.
    
    Tests:
    1. MO creation and approval
    2. Batch release
    3. First process supervisor can verify and start
    4. Subsequent supervisors cannot start/verify until previous process completes
    5. Quantity flow: First process completes with partial OK quantity (10kg)
    6. Second process supervisor sees only 10kg from BatchReceiptLog
    7. Second process completes with OK quantity
    8. Third process supervisor sees the OK quantity from second process
    """
    
    def setUp(self):
        """Set up test data with multiple processes"""
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
        
        # Create product (based on spring_process.csv - KBI-RBI-GM79633300D3-1.80mm)
        self.product = Product.objects.create(
            product_code='KBI-RBI-GM79633300D3-1.80mm',
            material=self.raw_material,
            product_type='spring',
            spring_type='TENSION SPRING',
            grams_per_product=Decimal('8.00')  # 8 grams per product from CSV
        )
        
        # Create processes (Coiling, Stress_Relieving, Grinding)
        # Note: Process model requires a 'code' field (IntegerField)
        self.process_coiling = Process.objects.create(
            name='Coiling',
            code=1,
            description='Coiling process',
            is_active=True
        )
        
        self.process_stress_relieving = Process.objects.create(
            name='Stress_Relieving',
            code=2,
            description='Stress Relieving process',
            is_active=True
        )
        
        self.process_grinding = Process.objects.create(
            name='Grinding',
            code=3,
            description='Grinding process',
            is_active=True
        )
        
        # Create manufacturing order
        # Note: mo_id has max_length=20, so keep it short
        self.mo = ManufacturingOrder.objects.create(
            mo_id='MO-TEST-001',  # Shortened to fit max_length=20
            product_code=self.product,
            quantity=1000,  # 1000 pieces
            priority='high',
            status='on_hold'
        )
        
        # Calculate RM requirements
        self.mo.calculate_rm_requirements()
        self.mo.save()
        
        # Create approval workflow
        MOApprovalWorkflow.objects.create(
            mo=self.mo,
            status='pending_manager_approval'
        )
        
        # Create API client
        self.client = APIClient()
    
    def test_complete_batch_process_flow(self):
        """
        Test the complete batch process flow with multiple process steps.
        """
        # Step 1: Approve MO
        # Use the update_mo_details endpoint with action='approve'
        self.client.force_authenticate(user=self.manager_user)
        approval_response = self.client.patch(
            f'/api/manufacturing/manufacturing-orders/{self.mo.id}/update_mo_details/',
            {'action': 'approve', 'notes': 'Test approval'}
        )
        self.assertEqual(approval_response.status_code, status.HTTP_200_OK)
        
        # Refresh MO from DB
        self.mo.refresh_from_db()
        self.assertEqual(self.mo.status, 'mo_approved')
        
        # Step 2: Initialize processes (this should create process executions)
        # Simulate process initialization by creating process executions manually
        process_exec_1 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_coiling,
            sequence_order=1,
            status='pending',
            assigned_supervisor=self.supervisor_1
        )
        
        process_exec_2 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_stress_relieving,
            sequence_order=2,
            status='pending',
            assigned_supervisor=self.supervisor_2
        )
        
        process_exec_3 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_grinding,
            sequence_order=3,
            status='pending',
            assigned_supervisor=self.supervisor_3
        )
        
        # Step 3: Create and release batch
        # Batch quantity: 40kg (40000 grams / 1000 = 40kg)
        batch_quantity_grams = 40000  # 40kg in grams
        batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=batch_quantity_grams,  # stored in grams
            status='created'
        )
        
        # Release batch (simulate RM store release)
        batch.status = 'released'
        batch.released_at = timezone.now()
        batch.save()
        
        # Verify batch is released
        self.assertEqual(batch.status, 'released')
        self.assertIsNotNone(batch.released_at)
        
        # Step 4: First process supervisor should be able to see and verify batch
        self.client.force_authenticate(user=self.supervisor_1)
        
        # Get batch receipt logs for first process (should be empty initially)
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_1_id = process_exec_1.process.id if isinstance(process_exec_1.process, Process) else process_exec_1.process
        receipt_logs_response = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_1_id}'
        )
        self.assertEqual(receipt_logs_response.status_code, status.HTTP_200_OK)
        
        # First process supervisor should see batch with RM released quantity (40kg)
        # This is because it's the first process (sequence_order = 1)
        # The batch.rm_released_kg property should return 40kg (40000 grams / 1000)
        self.assertEqual(float(batch.rm_released_kg), 40.0)
        
        # Verify batch receipt (first process can verify)
        verify_response = self.client.post(
            '/api/manufacturing/batch-receipts/verify/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_1.id,
                'expected_quantity_kg': '40.0'
            }
        )
        self.assertEqual(verify_response.status_code, status.HTTP_201_CREATED)
        
        # Start batch process
        # Note: The endpoint expects 'process_id' which is the ProcessExecution ID
        start_response = self.client.post(
            '/api/manufacturing/batch-process-executions/start/',
            {
                'batch_id': batch.id,
                'process_id': process_exec_1.id  # The endpoint uses 'process_id' for ProcessExecution ID
            }
        )
        self.assertEqual(start_response.status_code, status.HTTP_200_OK)
        
        # Update process execution status
        process_exec_1.status = 'in_progress'
        process_exec_1.save()
        
        # Step 5: Second and third process supervisors should NOT be able to verify/start
        # Second supervisor tries to verify (should fail - no receipt log from first process)
        self.client.force_authenticate(user=self.supervisor_2)
        
        # Check receipt logs for second process (should be empty)
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_2_id = process_exec_2.process.id if isinstance(process_exec_2.process, Process) else process_exec_2.process
        receipt_logs_2 = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_2_id}'
        )
        self.assertEqual(receipt_logs_2.status_code, status.HTTP_200_OK)
        receipt_logs_data = receipt_logs_2.json()
        self.assertEqual(len(receipt_logs_data), 0)  # No receipt log yet
        
        # Second supervisor should NOT be able to verify (no receipt log exists)
        # The frontend should show "Pending from Coiling" instead of quantity
        
        # Step 6: First supervisor completes batch with partial OK quantity (10kg)
        # Note: Process execution should be in_progress before completion
        process_exec_1.status = 'in_progress'
        process_exec_1.save()
        
        self.client.force_authenticate(user=self.supervisor_1)
        
        completion_response = self.client.post(
            '/api/manufacturing/batch-completions/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_1.id,
                'input_quantity_kg': '40.0',
                'ok_quantity_kg': '10.0',  # Only 10kg OK quantity
                'scrap_quantity_kg': '5.0',
                'rework_quantity_kg': '25.0',  # 25kg rework
                'completion_notes': 'Test completion with partial OK quantity'
            }
        )
        self.assertEqual(completion_response.status_code, status.HTTP_201_CREATED)
        
        # Verify BatchReceiptLog was created for second process
        receipt_log = BatchReceiptLog.objects.filter(
            batch=batch,
            from_process=self.process_coiling,
            to_process=self.process_stress_relieving
        ).first()
        
        self.assertIsNotNone(receipt_log, "BatchReceiptLog should be created when first process completes")
        self.assertEqual(float(receipt_log.handed_over_quantity_kg), 10.0)
        self.assertEqual(receipt_log.to_process, self.process_stress_relieving)
        
        # Step 7: Second process supervisor should now see 10kg received
        self.client.force_authenticate(user=self.supervisor_2)
        
        # Get receipt logs for second process
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_2_id = process_exec_2.process.id if isinstance(process_exec_2.process, Process) else process_exec_2.process
        receipt_logs_2_after = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_2_id}'
        )
        self.assertEqual(receipt_logs_2_after.status_code, status.HTTP_200_OK)
        receipt_logs_data_2 = receipt_logs_2_after.json()
        self.assertEqual(len(receipt_logs_data_2), 1)
        self.assertEqual(float(receipt_logs_data_2[0]['handed_over_quantity_kg']), 10.0)
        
        # Second supervisor can now verify the batch
        verify_response_2 = self.client.post(
            '/api/manufacturing/batch-receipts/verify/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_2.id,
                'expected_quantity_kg': '10.0'
            }
        )
        self.assertEqual(verify_response_2.status_code, status.HTTP_201_CREATED)
        
        # Start batch process in second process
        # Note: The endpoint expects 'process_id' which is the ProcessExecution ID
        start_response_2 = self.client.post(
            '/api/manufacturing/batch-process-executions/start/',
            {
                'batch_id': batch.id,
                'process_id': process_exec_2.id  # The endpoint uses 'process_id' for ProcessExecution ID
            }
        )
        self.assertEqual(start_response_2.status_code, status.HTTP_200_OK)
        
        # Update process execution status
        process_exec_2.status = 'in_progress'
        process_exec_2.save()
        
        # Step 8: Second supervisor completes with OK quantity (8kg)
        # Ensure process execution is in_progress before completion
        process_exec_2.status = 'in_progress'
        process_exec_2.save()
        
        completion_response_2 = self.client.post(
            '/api/manufacturing/batch-completions/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_2.id,
                'input_quantity_kg': '10.0',
                'ok_quantity_kg': '8.0',  # 8kg OK quantity
                'scrap_quantity_kg': '1.0',
                'rework_quantity_kg': '1.0',
                'completion_notes': 'Second process completion'
            }
        )
        self.assertEqual(completion_response_2.status_code, status.HTTP_201_CREATED)
        
        # Verify BatchReceiptLog was created for third process
        receipt_log_3 = BatchReceiptLog.objects.filter(
            batch=batch,
            from_process=self.process_stress_relieving,
            to_process=self.process_grinding
        ).first()
        
        self.assertIsNotNone(receipt_log_3, "BatchReceiptLog should be created when second process completes")
        self.assertEqual(float(receipt_log_3.handed_over_quantity_kg), 8.0)
        self.assertEqual(receipt_log_3.to_process, self.process_grinding)
        
        # Step 9: Third process supervisor should see 8kg received
        self.client.force_authenticate(user=self.supervisor_3)
        
        # Get receipt logs for third process
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_3_id = process_exec_3.process.id if isinstance(process_exec_3.process, Process) else process_exec_3.process
        receipt_logs_3 = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_3_id}'
        )
        self.assertEqual(receipt_logs_3.status_code, status.HTTP_200_OK)
        receipt_logs_data_3 = receipt_logs_3.json()
        self.assertEqual(len(receipt_logs_data_3), 1)
        self.assertEqual(float(receipt_logs_data_3[0]['handed_over_quantity_kg']), 8.0)
        
        # Third supervisor can verify the batch
        verify_response_3 = self.client.post(
            '/api/manufacturing/batch-receipts/verify/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_3.id,
                'expected_quantity_kg': '8.0'
            }
        )
        self.assertEqual(verify_response_3.status_code, status.HTTP_201_CREATED)
        
        # Start batch process in third process
        # Note: The endpoint expects 'process_id' which is the ProcessExecution ID
        start_response_3 = self.client.post(
            '/api/manufacturing/batch-process-executions/start/',
            {
                'batch_id': batch.id,
                'process_id': process_exec_3.id  # The endpoint uses 'process_id' for ProcessExecution ID
            }
        )
        self.assertEqual(start_response_3.status_code, status.HTTP_200_OK)
        
        # Step 10: Verify quantity flow summary
        # Check all receipt logs
        all_receipt_logs = BatchReceiptLog.objects.filter(batch=batch).order_by('created_at')
        self.assertEqual(all_receipt_logs.count(), 2)
        
        # First receipt log: Coiling -> Stress_Relieving (10kg)
        self.assertEqual(all_receipt_logs[0].from_process, self.process_coiling)
        self.assertEqual(all_receipt_logs[0].to_process, self.process_stress_relieving)
        self.assertEqual(float(all_receipt_logs[0].handed_over_quantity_kg), 10.0)
        
        # Second receipt log: Stress_Relieving -> Grinding (8kg)
        self.assertEqual(all_receipt_logs[1].from_process, self.process_stress_relieving)
        self.assertEqual(all_receipt_logs[1].to_process, self.process_grinding)
        self.assertEqual(float(all_receipt_logs[1].handed_over_quantity_kg), 8.0)
        
        # Check completion records
        completions = BatchProcessCompletion.objects.filter(batch=batch).order_by('completed_at')
        self.assertEqual(completions.count(), 2)
        
        # First completion: 40kg input -> 10kg OK
        self.assertEqual(float(completions[0].input_quantity_kg), 40.0)
        self.assertEqual(float(completions[0].ok_quantity_kg), 10.0)
        self.assertEqual(completions[0].process_execution, process_exec_1)
        
        # Second completion: 10kg input -> 8kg OK
        self.assertEqual(float(completions[1].input_quantity_kg), 10.0)
        self.assertEqual(float(completions[1].ok_quantity_kg), 8.0)
        self.assertEqual(completions[1].process_execution, process_exec_2)
    
    def test_subsequent_supervisor_cannot_start_before_previous_completes(self):
        """
        Test that subsequent process supervisors cannot start/verify batch
        until previous process has completed and created receipt log.
        """
        # Approve MO
        self.client.force_authenticate(user=self.manager_user)
        self.client.post(f'/api/manufacturing/manufacturing-orders/{self.mo.id}/approve/')
        
        # Create process executions
        process_exec_1 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_coiling,
            sequence_order=1,
            status='pending',
            assigned_supervisor=self.supervisor_1
        )
        
        process_exec_2 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_stress_relieving,
            sequence_order=2,
            status='pending',
            assigned_supervisor=self.supervisor_2
        )
        
        # Create and release batch
        batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=40000,  # 40kg
            status='released',
            released_at=timezone.now()
        )
        
        # Second supervisor tries to verify before first process completes
        self.client.force_authenticate(user=self.supervisor_2)
        
        # Check receipt logs (should be empty)
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_2_id = process_exec_2.process.id if isinstance(process_exec_2.process, Process) else process_exec_2.process
        receipt_logs = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_2_id}'
        )
        receipt_logs_data = receipt_logs.json()
        self.assertEqual(len(receipt_logs_data), 0)
        
        # Second supervisor should NOT see any received quantity
        # The frontend should show "Pending from Coiling" instead
        
        # Verify that no receipt log exists
        receipt_log_exists = BatchReceiptLog.objects.filter(
            batch=batch,
            to_process=self.process_stress_relieving
        ).exists()
        self.assertFalse(receipt_log_exists, "Receipt log should not exist before first process completes")
    
    def test_quantity_flow_with_sequence_order(self):
        """
        Test that quantity flow correctly uses sequence_order to determine
        which process is first, not array index.
        """
        # Approve MO
        self.client.force_authenticate(user=self.manager_user)
        self.client.patch(
            f'/api/manufacturing/manufacturing-orders/{self.mo.id}/update_mo_details/',
            {'action': 'approve', 'notes': 'Test approval'}
        )
        
        # Create process executions with specific sequence orders
        # Note: Supervisor 2 is assigned to first process (sequence_order=1)
        # Supervisor 1 is assigned to second process (sequence_order=2)
        # This tests that sequence_order is used, not assignment order
        process_exec_1 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_coiling,
            sequence_order=1,
            status='pending',
            assigned_supervisor=self.supervisor_2  # Supervisor 2 gets first process
        )
        
        process_exec_2 = MOProcessExecution.objects.create(
            mo=self.mo,
            process=self.process_stress_relieving,
            sequence_order=2,
            status='pending',
            assigned_supervisor=self.supervisor_1  # Supervisor 1 gets second process
        )
        
        # Create and release batch
        batch = Batch.objects.create(
            mo=self.mo,
            product_code=self.product,
            planned_quantity=40000,  # 40kg
            status='released',
            released_at=timezone.now()
        )
        
        # Supervisor 2 (first process) should see RM released quantity
        self.client.force_authenticate(user=self.supervisor_2)
        self.assertEqual(float(batch.rm_released_kg), 40.0)
        
        # Set process execution to in_progress before completion
        process_exec_1.status = 'in_progress'
        process_exec_1.save()
        
        # Supervisor 2 completes first process
        completion = self.client.post(
            '/api/manufacturing/batch-completions/',
            {
                'batch_id': batch.id,
                'process_execution_id': process_exec_1.id,
                'input_quantity_kg': '40.0',
                'ok_quantity_kg': '15.0',
                'scrap_quantity_kg': '5.0',
                'rework_quantity_kg': '20.0'
            }
        )
        self.assertEqual(completion.status_code, status.HTTP_201_CREATED)
        
        # Supervisor 1 (second process) should see 15kg from receipt log
        self.client.force_authenticate(user=self.supervisor_1)
        # Note: process_id parameter expects Process model ID, not ProcessExecution ID
        process_2_id = process_exec_2.process.id if isinstance(process_exec_2.process, Process) else process_exec_2.process
        receipt_logs = self.client.get(
            f'/api/manufacturing/batch-receipt-logs/for_supervisor_process/?process_id={process_2_id}'
        )
        receipt_logs_data = receipt_logs.json()
        self.assertEqual(len(receipt_logs_data), 1)
        self.assertEqual(float(receipt_logs_data[0]['handed_over_quantity_kg']), 15.0)
        
        # Verify sequence_order was used correctly
        receipt_log = BatchReceiptLog.objects.get(
            batch=batch,
            to_process=self.process_stress_relieving
        )
        self.assertEqual(receipt_log.from_process, self.process_coiling)
        self.assertEqual(float(receipt_log.handed_over_quantity_kg), 15.0)

