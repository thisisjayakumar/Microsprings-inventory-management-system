from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'inventory'

# Create router and register viewsets
router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'stock-balances', views.RMStockBalanceViewSet, basename='stock-balance')
router.register(r'raw-materials', views.RawMaterialViewSet, basename='raw-material')
router.register(r'transactions', views.InventoryTransactionViewSet, basename='inventory-transaction')

# GRM and Heat Number endpoints
router.register(r'grm-receipts', views.GRMReceiptViewSet, basename='grm-receipt')
router.register(r'heat-numbers', views.HeatNumberViewSet, basename='heat-number')
router.register(r'stock-balances-heat', views.RMStockBalanceHeatViewSet, basename='stock-balance-heat')
router.register(r'transactions-heat', views.InventoryTransactionHeatViewSet, basename='inventory-transaction-heat')

# RM Return endpoints
router.register(r'rm-returns', views.RMReturnViewSet, basename='rm-return')

urlpatterns = [
    # Health check
    path('health/', views.health_check, name='health_check'),
    
    # Dashboard stats
    path('dashboard/stats/', views.rm_store_dashboard_stats, name='dashboard_stats'),
    
    # Handover Verification endpoints for Coiling Supervisors
    path('coiling/pending-handover/', views.pending_handover_list, name='pending_handover_list'),
    path('coiling/verify/', views.verify_handover, name='verify_handover'),
    path('coiling/report/', views.report_handover_issue, name='report_handover_issue'),
    path('coiling/issues/', views.handover_issues_list, name='handover_issues_list'),
    
    # Material Heat Numbers and Coils APIs
    path('materials/heat-numbers-coils/', views.get_material_heat_numbers_coils, name='get_material_heat_numbers_coils'),
    path('materials/delete-coils/', views.delete_coils_by_heat_numbers, name='delete_coils_by_heat_numbers'),
    
    # Include router URLs
    path('', include(router.urls)),
]

# Available API endpoints for RM Store users:
"""
Products:
- GET    /api/inventory/products/                    - List all products with stock info (with filtering, search, pagination)
- POST   /api/inventory/products/                    - Create new product
- GET    /api/inventory/products/{id}/               - Get specific product details
- PUT    /api/inventory/products/{id}/               - Update product
- PATCH  /api/inventory/products/{id}/               - Partial update product
- DELETE /api/inventory/products/{id}/               - Delete product
- GET    /api/inventory/products/dashboard/          - Get products with stock for dashboard
- GET    /api/inventory/products/dropdown/           - Get products dropdown

Stock Balances:
- GET    /api/inventory/stock-balances/              - List all stock balances (with filtering, search, pagination)
- POST   /api/inventory/stock-balances/              - Create new stock balance
- GET    /api/inventory/stock-balances/{id}/         - Get specific stock balance details
- PUT    /api/inventory/stock-balances/{id}/         - Update stock balance
- PATCH  /api/inventory/stock-balances/{id}/         - Partial update stock balance
- DELETE /api/inventory/stock-balances/{id}/         - Delete stock balance
- POST   /api/inventory/stock-balances/bulk_update/  - Bulk update stock balances
- POST   /api/inventory/stock-balances/update_by_product_code/ - Update by internal_product_code

Raw Materials:
- GET    /api/inventory/raw-materials/               - List all raw materials (with filtering, search, pagination)
- GET    /api/inventory/raw-materials/{id}/          - Get specific raw material details
- GET    /api/inventory/raw-materials/dropdown/      - Get raw materials dropdown

Handover Verification (Coiling Supervisors only):
- GET    /api/inventory/coiling/pending-handover/    - List heat numbers pending handover verification
- POST   /api/inventory/coiling/verify/              - Verify handover of raw material
- POST   /api/inventory/coiling/report/               - Report handover issue
- GET    /api/inventory/coiling/issues/               - List reported handover issues

Dashboard:
- GET    /api/inventory/dashboard/stats/             - Get dashboard statistics

Query Parameters for Filtering:
Products:
- product_type: spring, press_component
- spring_type: tension, compression, etc.
- search: searches in product_code, internal_product_code, material__material_name
- ordering: product_code, internal_product_code, created_at (add - for descending)

Stock Balances:
- search: searches in product__product_code, product__internal_product_code
- ordering: available_quantity, last_updated (add - for descending)

Raw Materials:
- material_type: coil, sheet
- material_name: specific material name
- search: searches in material_code, material_name, grade
- ordering: material_code, material_name (add - for descending)

Example API Calls:
1. Get products dashboard:
   GET /api/inventory/products/dashboard/

2. Create product:
   POST /api/inventory/products/
   {
     "internal_product_code": "ABC123",
     "product_code": "SPR-001",
     "product_type": "spring",
     "spring_type": "tension",
     "material": 1
   }

3. Update stock balance by product code:
   POST /api/inventory/stock-balances/update_by_product_code/
   {
     "internal_product_code": "ABC123",
     "available_quantity": 100
   }

4. Bulk update stock balances:
   POST /api/inventory/stock-balances/bulk_update/
   [
     {"internal_product_code": "ABC123", "available_quantity": 100},
     {"internal_product_code": "DEF456", "available_quantity": 50}
   ]

5. Get dashboard stats:
   GET /api/inventory/dashboard/stats/

6. Search products:
   GET /api/inventory/products/?search=spring&product_type=spring

7. Get raw materials dropdown:
   GET /api/inventory/raw-materials/dropdown/
"""
