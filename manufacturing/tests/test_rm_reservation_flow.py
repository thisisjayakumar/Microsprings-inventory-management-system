"""
Test cases for Raw Material Reservation Flow
Tests the complete flow: Initial stock -> MO Approval -> RM Reservation -> MO Stop -> Reserved Quantity Release
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from decimal import Decimal
from django.utils import timezone

from inventory.models import RawMaterial, RMStockBalance, RMStockBalanceHeat
from products.models import Product
from manufacturing.models import ManufacturingOrder, RawMaterialAllocation, MOApprovalWorkflow
from manufacturing.services.workflow import ManufacturingWorkflowService
from inventory.serializers import RawMaterialBasicSerializer

User = get_user_model()


class RMRervationFlowTestCase(TestCase):
    """Test cases for RM reservation flow when MO is approved and stopped"""
    
    def setUp(self):
        """Set up test data"""
        # Create test user
        self.manager_user = User.objects.create_user(
            username='manager@test.com',
            email='manager@test.com',
            password='testpass123',
            first_name='Manager',
            last_name='Test'
        )
        
        # Create raw material
        self.raw_material = RawMaterial.objects.create(
            material_code='TEST_RM_001',
            material_name='Test Raw Material',
            material_type='coil',
            grade='TEST_GRADE',
            wire_diameter_mm=Decimal('5.00')
        )
        
        # Create product
        self.product = Product.objects.create(
            product_code='TEST_PROD_001',
            material=self.raw_material,
            product_type='spring',
            grams_per_product=Decimal('100.00')
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
        
        # Create manufacturing order
        self.mo = ManufacturingOrder.objects.create(
            mo_id='MO_TEST_001',
            product_code=self.product,
            quantity=1000,
            priority='high',
            status='on_hold'
        )
        
        # Calculate RM requirements
        self.mo.calculate_rm_requirements()
        self.mo.save()
        
        # Create approval workflow (required for MO approval)
        MOApprovalWorkflow.objects.create(
            mo=self.mo,
            status='pending_manager_approval'
        )
    
    def get_reserved_quantity(self, raw_material):
        """Helper method to get reserved quantity for a raw material"""
        from manufacturing.models import RawMaterialAllocation
        from django.db.models import Sum
        
        total_reserved = RawMaterialAllocation.objects.filter(
            raw_material=raw_material,
            status__in=['reserved', 'locked']
        ).aggregate(
            total=Sum('allocated_quantity_kg')
        )['total']
        
        return Decimal(str(total_reserved)) if total_reserved is not None else Decimal('0')
    
    def get_available_quantity(self, raw_material):
        """Helper method to get available quantity from RMStockBalance"""
        stock_balance = RMStockBalance.objects.filter(
            raw_material=raw_material
        ).first()
        return stock_balance.available_quantity if stock_balance else Decimal('0')
    
    def get_heat_available_quantity(self, raw_material):
        """Helper method to get available quantity from RMStockBalanceHeat"""
        heat_stock = RMStockBalanceHeat.objects.filter(
            raw_material=raw_material
        ).first()
        return heat_stock.total_available_quantity_kg if heat_stock else Decimal('0')
    
    def test_01_initial_state(self):
        """Test 1: Verify initial state - total stock, reserved quantity, and available quantity"""
        print("\n=== Test 1: Initial State ===")
        
        # Get initial values
        initial_total_stock = self.get_available_quantity(self.raw_material)
        initial_heat_stock = self.get_heat_available_quantity(self.raw_material)
        initial_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"Initial Total Stock (RMStockBalance): {initial_total_stock} kg")
        print(f"Initial Total Stock (RMStockBalanceHeat): {initial_heat_stock} kg")
        print(f"Initial Reserved Quantity: {initial_reserved} kg")
        print(f"MO Required RM: {self.mo.rm_required_kg} kg")
        
        # Assertions
        self.assertEqual(initial_total_stock, self.initial_stock_kg, 
                        "Initial stock should match setup")
        self.assertEqual(initial_heat_stock, self.initial_stock_kg,
                        "Initial heat stock should match setup")
        self.assertEqual(initial_reserved, Decimal('0'),
                        "Initial reserved quantity should be 0")
        self.assertGreater(self.mo.rm_required_kg, Decimal('0'),
                          "MO should have required RM quantity")
        
        print("✓ Initial state verified")
    
    def test_02_after_mo_approval(self):
        """Test 2: After MO approval, verify RM is reserved and quantities are updated"""
        print("\n=== Test 2: After MO Approval ===")
        
        # Get initial values
        initial_total_stock = self.get_available_quantity(self.raw_material)
        initial_heat_stock = self.get_heat_available_quantity(self.raw_material)
        initial_reserved = self.get_reserved_quantity(self.raw_material)
        mo_required_kg = self.mo.rm_required_kg
        
        print(f"Before Approval:")
        print(f"  Total Stock: {initial_total_stock} kg")
        print(f"  Heat Stock: {initial_heat_stock} kg")
        print(f"  Reserved: {initial_reserved} kg")
        print(f"  MO Required: {mo_required_kg} kg")
        
        # Approve MO
        try:
            ManufacturingWorkflowService.approve_mo(
                mo_id=self.mo.id,
                manager_user=self.manager_user,
                approval_notes="Test approval"
            )
            self.mo.refresh_from_db()
        except Exception as e:
            self.fail(f"MO approval failed: {str(e)}")
        
        # Get values after approval
        after_total_stock = self.get_available_quantity(self.raw_material)
        after_heat_stock = self.get_heat_available_quantity(self.raw_material)
        after_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"\nAfter Approval:")
        print(f"  Total Stock: {after_total_stock} kg")
        print(f"  Heat Stock: {after_heat_stock} kg")
        print(f"  Reserved: {after_reserved} kg")
        
        # Verify allocations were created
        allocations = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status__in=['reserved', 'locked']
        )
        print(f"  Allocations created: {allocations.count()}")
        for alloc in allocations:
            print(f"    - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg} kg")
        
        # Assertions
        self.assertGreater(allocations.count(), 0, 
                         "At least one allocation should be created")
        self.assertEqual(after_reserved, mo_required_kg,
                        f"Reserved quantity should equal MO required quantity ({mo_required_kg} kg)")
        self.assertEqual(after_total_stock, initial_total_stock - mo_required_kg,
                        f"Available stock should decrease by reserved quantity")
        
        print("✓ MO approval and RM reservation verified")
    
    def test_03_after_mo_stop(self):
        """Test 3: After MO stop, verify reserved quantity is released and quantities are restored"""
        print("\n=== Test 3: After MO Stop ===")
        
        # First approve MO to create reservations
        ManufacturingWorkflowService.approve_mo(
            mo_id=self.mo.id,
            manager_user=self.manager_user,
            approval_notes="Test approval"
        )
        self.mo.refresh_from_db()
        
        # Get values after approval
        after_approval_total_stock = self.get_available_quantity(self.raw_material)
        after_approval_heat_stock = self.get_heat_available_quantity(self.raw_material)
        after_approval_reserved = self.get_reserved_quantity(self.raw_material)
        mo_required_kg = self.mo.rm_required_kg
        
        print(f"After Approval:")
        print(f"  Total Stock: {after_approval_total_stock} kg")
        print(f"  Heat Stock: {after_approval_heat_stock} kg")
        print(f"  Reserved: {after_approval_reserved} kg")
        
        # Get allocations before stop
        allocations_before = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status__in=['reserved', 'locked']
        )
        print(f"  Active Allocations: {allocations_before.count()}")
        for alloc in allocations_before:
            print(f"    - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg} kg")
        
        # Stop MO
        try:
            released_resources = self.mo.stop_mo(
                stop_reason="Test stop reason",
                stopped_by_user=self.manager_user
            )
            self.mo.refresh_from_db()
        except Exception as e:
            self.fail(f"MO stop failed: {str(e)}")
        
        # Get values after stop
        after_stop_total_stock = self.get_available_quantity(self.raw_material)
        after_stop_heat_stock = self.get_heat_available_quantity(self.raw_material)
        after_stop_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"\nAfter Stop:")
        print(f"  Total Stock: {after_stop_total_stock} kg")
        print(f"  Heat Stock: {after_stop_heat_stock} kg")
        print(f"  Reserved: {after_stop_reserved} kg")
        print(f"  MO Status: {self.mo.status}")
        
        # Get allocations after stop
        allocations_after = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status__in=['reserved', 'locked']
        )
        released_allocations = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status='released'
        )
        print(f"  Active Allocations: {allocations_after.count()}")
        print(f"  Released Allocations: {released_allocations.count()}")
        for alloc in released_allocations:
            print(f"    - Allocation ID: {alloc.id}, Status: {alloc.status}, Qty: {alloc.allocated_quantity_kg} kg")
        
        # Assertions
        self.assertEqual(self.mo.status, 'stopped',
                       "MO status should be 'stopped'")
        self.assertEqual(allocations_after.count(), 0,
                        "No active allocations should remain")
        self.assertEqual(released_allocations.count(), allocations_before.count(),
                        "All allocations should be released")
        self.assertEqual(after_stop_reserved, Decimal('0'),
                        "Reserved quantity should be 0 after stop")
        self.assertEqual(after_stop_total_stock, after_approval_total_stock + mo_required_kg,
                        f"Available stock should increase by released quantity")
        # Note: Reserved allocations don't affect RMStockBalanceHeat, so it should remain unchanged
        self.assertEqual(after_stop_heat_stock, after_approval_heat_stock,
                        f"Heat stock should remain unchanged (reserved allocations don't affect it)")
        
        print("✓ MO stop and RM release verified")
    
    def test_04_complete_flow(self):
        """Test 4: Complete flow from start to finish - comprehensive test"""
        print("\n=== Test 4: Complete Flow ===")
        
        # Step 1: Initial state
        initial_total = self.get_available_quantity(self.raw_material)
        initial_heat = self.get_heat_available_quantity(self.raw_material)
        initial_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"Step 1 - Initial State:")
        print(f"  Total Stock: {initial_total} kg")
        print(f"  Heat Stock: {initial_heat} kg")
        print(f"  Reserved: {initial_reserved} kg")
        print(f"  MO Required: {self.mo.rm_required_kg} kg")
        
        # Step 2: Approve MO
        ManufacturingWorkflowService.approve_mo(
            mo_id=self.mo.id,
            manager_user=self.manager_user,
            approval_notes="Test approval"
        )
        self.mo.refresh_from_db()
        
        after_approval_total = self.get_available_quantity(self.raw_material)
        after_approval_heat = self.get_heat_available_quantity(self.raw_material)
        after_approval_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"\nStep 2 - After Approval:")
        print(f"  Total Stock: {after_approval_total} kg (decreased by {initial_total - after_approval_total} kg)")
        print(f"  Heat Stock: {after_approval_heat} kg (decreased by {initial_heat - after_approval_heat} kg)")
        print(f"  Reserved: {after_approval_reserved} kg (increased by {after_approval_reserved - initial_reserved} kg)")
        
        # Verify approval changes
        self.assertEqual(after_approval_reserved, self.mo.rm_required_kg,
                        "Reserved should equal MO required")
        self.assertEqual(after_approval_total, initial_total - self.mo.rm_required_kg,
                        "Total stock should decrease by reserved amount")
        
        # Step 3: Stop MO
        self.mo.stop_mo(
            stop_reason="Test stop",
            stopped_by_user=self.manager_user
        )
        self.mo.refresh_from_db()
        
        after_stop_total = self.get_available_quantity(self.raw_material)
        after_stop_heat = self.get_heat_available_quantity(self.raw_material)
        after_stop_reserved = self.get_reserved_quantity(self.raw_material)
        
        print(f"\nStep 3 - After Stop:")
        print(f"  Total Stock: {after_stop_total} kg (increased by {after_stop_total - after_approval_total} kg)")
        print(f"  Heat Stock: {after_stop_heat} kg (increased by {after_stop_heat - after_approval_heat} kg)")
        print(f"  Reserved: {after_stop_reserved} kg (decreased by {after_approval_reserved - after_stop_reserved} kg)")
        
        # Verify stop changes
        self.assertEqual(after_stop_reserved, Decimal('0'),
                        "Reserved should be 0 after stop")
        self.assertEqual(after_stop_total, initial_total,
                        "Total stock should return to initial value")
        self.assertEqual(after_stop_heat, initial_heat,
                        "Heat stock should return to initial value")
        
        print("\n✓ Complete flow verified - All quantities restored correctly")
    
    def test_05_reserved_quantity_excludes_released(self):
        """Test 5: Verify that reserved quantity calculation excludes released allocations"""
        print("\n=== Test 5: Reserved Quantity Excludes Released ===")
        
        # Approve MO to create reservations
        ManufacturingWorkflowService.approve_mo(
            mo_id=self.mo.id,
            manager_user=self.manager_user,
            approval_notes="Test approval"
        )
        self.mo.refresh_from_db()
        
        # Get reserved quantity after approval
        reserved_after_approval = self.get_reserved_quantity(self.raw_material)
        total_allocations = RawMaterialAllocation.objects.filter(mo=self.mo).count()
        active_allocations = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status__in=['reserved', 'locked']
        ).count()
        released_allocations = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status='released'
        ).count()
        
        print(f"After Approval:")
        print(f"  Reserved Quantity: {reserved_after_approval} kg")
        print(f"  Total Allocations: {total_allocations}")
        print(f"  Active Allocations (reserved/locked): {active_allocations}")
        print(f"  Released Allocations: {released_allocations}")
        
        self.assertEqual(active_allocations, total_allocations,
                        "All allocations should be active after approval")
        self.assertEqual(released_allocations, 0,
                        "No allocations should be released after approval")
        self.assertEqual(reserved_after_approval, self.mo.rm_required_kg,
                        "Reserved quantity should equal MO required")
        
        # Stop MO
        self.mo.stop_mo(
            stop_reason="Test stop",
            stopped_by_user=self.manager_user
        )
        self.mo.refresh_from_db()
        
        # Get reserved quantity after stop
        reserved_after_stop = self.get_reserved_quantity(self.raw_material)
        total_allocations_after = RawMaterialAllocation.objects.filter(mo=self.mo).count()
        active_allocations_after = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status__in=['reserved', 'locked']
        ).count()
        released_allocations_after = RawMaterialAllocation.objects.filter(
            mo=self.mo,
            status='released'
        ).count()
        
        print(f"\nAfter Stop:")
        print(f"  Reserved Quantity: {reserved_after_stop} kg")
        print(f"  Total Allocations: {total_allocations_after}")
        print(f"  Active Allocations (reserved/locked): {active_allocations_after}")
        print(f"  Released Allocations: {released_allocations_after}")
        
        # Verify released allocations are excluded from reserved quantity
        self.assertEqual(active_allocations_after, 0,
                        "No active allocations should remain")
        self.assertEqual(released_allocations_after, total_allocations_after,
                        "All allocations should be released")
        self.assertEqual(reserved_after_stop, Decimal('0'),
                        "Reserved quantity should be 0 (released allocations excluded)")
        
        # Manually verify by checking allocation statuses
        all_allocations = RawMaterialAllocation.objects.filter(mo=self.mo)
        for alloc in all_allocations:
            self.assertEqual(alloc.status, 'released',
                           f"Allocation {alloc.id} should be released")
            self.assertNotIn(alloc.status, ['reserved', 'locked'],
                           f"Allocation {alloc.id} should not be in reserved/locked status")
        
        print("✓ Reserved quantity correctly excludes released allocations")

