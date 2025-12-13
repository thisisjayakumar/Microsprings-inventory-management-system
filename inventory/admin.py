from django.contrib import admin
from django.utils.html import format_html
from django.db import models
from django.forms import TextInput, Textarea
from .models import (
    RawMaterial, Location, InventoryTransaction, RMStockBalance,
    GRNReceipt, HeatNumber, RMStockBalanceHeat, InventoryTransactionHeat,
    HandoverIssue, RMReturn
)


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ('material_code', 'material_name', 'material_type', 'grade', 'get_specifications', 'created_at')
    list_filter = ('material_name', 'material_type', 'created_at')
    search_fields = ('material_code', 'grade', 'material_name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('material_code', 'material_name', 'material_type', 'grade', 'finishing')
        }),
        ('Coil Specifications', {
            'fields': ('wire_diameter_mm', 'weight_kg'),
            'classes': ('collapse',),
            'description': 'Fill these fields only for Coil type materials'
        }),
        ('Sheet Specifications', {
            'fields': ('thickness_mm',),
            'classes': ('collapse',),
            'description': 'Fill these fields only for Sheet type materials'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '20'})},
        models.DecimalField: {'widget': TextInput(attrs={'size': '15'})},
    }
    
    def get_specifications(self, obj):
        """Display material specifications in a readable format"""
        specs = []
        if obj.material_type == 'coil':
            if obj.wire_diameter_mm:
                specs.append(f"⌀{obj.wire_diameter_mm}mm")
            if obj.weight_kg:
                specs.append(f"{obj.weight_kg}kg")
        elif obj.material_type == 'sheet':
            if obj.thickness_mm:
                specs.append(f"t{obj.thickness_mm}mm")
        
        return ', '.join(specs) if specs else '-'
    
    get_specifications.short_description = 'Specifications'
    
    class Media:
        js = ('admin/js/collapse.js',)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('code', 'location_name', 'parent_location', 'get_location_hierarchy')
    list_filter = ('location_name', 'parent_location')
    search_fields = ('code', 'location_name')
    ordering = ('location_name', 'code')
    
    fieldsets = (
        ('Location Details', {
            'fields': ('code', 'location_name', 'parent_location')
        }),
    )
    
    def get_location_hierarchy(self, obj):
        """Show the full location hierarchy"""
        hierarchy = []
        current = obj
        while current:
            hierarchy.insert(0, current.get_location_name_display())
            current = current.parent_location
        return ' → '.join(hierarchy)
    
    get_location_hierarchy.short_description = 'Full Hierarchy'


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'transaction_id', 'transaction_type', 'product', 'quantity', 
        'location_from', 'location_to', 'transaction_datetime', 'created_by'
    )
    list_filter = (
        'transaction_type', 'reference_type', 'transaction_datetime', 
        'location_from', 'location_to', 'created_by'
    )
    search_fields = ('transaction_id', 'product__product_code', 'reference_id', 'notes')
    ordering = ('-transaction_datetime',)
    readonly_fields = ('created_at',)
    date_hierarchy = 'transaction_datetime'
    
    fieldsets = (
        ('Transaction Details', {
            'fields': ('transaction_id', 'transaction_type', 'transaction_datetime')
        }),
        ('Product Information', {
            'fields': ('product', 'manufacturing_order')
        }),
        ('Location Movement', {
            'fields': ('location_from', 'location_to'),
            'description': 'For transfers, specify both locations. For inward/outward, use appropriate location.'
        }),
        ('Quantity & Value', {
            'fields': ('quantity', 'unit_cost', 'total_value')
        }),
        ('Reference & Tracking', {
            'fields': ('reference_type', 'reference_id', 'idempotency_key'),
            'classes': ('collapse',)
        }),
        ('Additional Information', {
            'fields': ('notes', 'created_by', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 60})},
        models.CharField: {'widget': TextInput(attrs={'size': '30'})},
    }
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set created_by for new objects
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'product', 'manufacturing_order', 'location_from', 'location_to', 'created_by'
        )


@admin.register(RMStockBalance)
class RMStockBalanceAdmin(admin.ModelAdmin):
    list_display = (
        'raw_material', 'available_quantity', 'get_stock_status', 'last_updated'
    )
    list_filter = ('last_updated',)
    search_fields = ('raw_material__material_code',)
    ordering = ('-last_updated',)
    raw_id_fields = ('raw_material',)
    readonly_fields = ('last_updated',)
    
    fieldsets = (
        ('Raw Material', {
            'fields': ('raw_material',)
        }),
        ('Stock Levels', {
            'fields': ('available_quantity',)
        }),
        ('Tracking', {
            'fields': ('last_updated',),
            'classes': ('collapse',)
        }),
    )
    
    def get_stock_status(self, obj):
        """Visual indicator for stock status"""
        if obj.available_quantity <= 0:
            color = 'red'
            status = 'Out of Stock'
        elif obj.available_quantity < 10:  # Assuming low stock threshold
            color = 'orange'
            status = 'Low Stock'
        else:
            color = 'green'
            status = 'In Stock'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, status
        )
    
    get_stock_status.short_description = 'Stock Status'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('raw_material')


class HeatNumberInline(admin.TabularInline):
    model = HeatNumber
    extra = 1
    fields = ('heat_number', 'raw_material', 'coils_received', 'total_weight_kg', 'sheets_received', 'items', 'quality_certificate_number')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(GRNReceipt)
class GRNReceiptAdmin(admin.ModelAdmin):
    list_display = (
        'grn_number', 'purchase_order', 'truck_number', 'driver_name', 
        'status', 'total_items_received', 'total_items_expected', 'receipt_date'
    )
    list_filter = ('status', 'receipt_date', 'quality_check_passed', 'received_by')
    search_fields = ('grn_number', 'purchase_order__po_id', 'truck_number', 'driver_name')
    ordering = ('-receipt_date',)
    readonly_fields = ('grn_number', 'created_at', 'updated_at')
    date_hierarchy = 'receipt_date'
    inlines = [HeatNumberInline]
    
    fieldsets = (
        ('GRN Information', {
            'fields': ('grn_number', 'purchase_order', 'status')
        }),
        ('Delivery Details', {
            'fields': ('truck_number', 'driver_name', 'driver_contact', 'receipt_date', 'received_by')
        }),
        ('Receipt Tracking', {
            'fields': ('total_items_received', 'total_items_expected')
        }),
        ('Quality Control', {
            'fields': ('quality_check_passed', 'quality_check_by', 'quality_check_date'),
            'classes': ('collapse',)
        }),
        ('Additional Information', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set received_by for new objects
            obj.received_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'purchase_order', 'received_by', 'quality_check_by'
        )


@admin.register(HeatNumber)
class HeatNumberAdmin(admin.ModelAdmin):
    list_display = (
        'heat_number', 'grn_receipt', 'raw_material', 'total_weight_kg', 
        'coils_received', 'sheets_received', 'is_available', 'handover_status',
        'get_available_quantity'
    )
    list_filter = ('is_available', 'raw_material__material_type', 'grn_receipt__status', 'created_at')
    search_fields = ('heat_number', 'raw_material__material_code', 'grn_receipt__grn_number')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('grn_receipt', 'raw_material')
    
    fieldsets = (
        ('Heat Number Details', {
            'fields': ('heat_number', 'grn_receipt', 'raw_material')
        }),
        ('Quantities', {
            'fields': ('coils_received', 'total_weight_kg', 'sheets_received', 'consumed_quantity_kg')
        }),
        ('Individual Items', {
            'fields': ('items',),
            'description': 'JSON field containing individual coil/sheet details with numbers and weights'
        }),
        ('Quality Information', {
            'fields': ('quality_certificate_number', 'test_certificate_date'),
            'classes': ('collapse',)
        }),
        ('Storage Location', {
            'fields': ('storage_location', 'rack_number', 'shelf_number'),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('is_available',)
        }),
        ('Handover Verification', {
            'fields': ('handover_status', 'verified_at', 'verified_by'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_available_quantity(self, obj):
        """Show available quantity with color coding"""
        available = obj.get_available_quantity_kg()
        if available <= 0:
            color = 'red'
        elif available < obj.total_weight_kg * 0.2:  # Less than 20% remaining
            color = 'orange'
        else:
            color = 'green'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.2f} kg</span>',
            color, available
        )
    
    get_available_quantity.short_description = 'Available Quantity'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'grn_receipt', 'raw_material'
        )


@admin.register(RMStockBalanceHeat)
class RMStockBalanceHeatAdmin(admin.ModelAdmin):
    list_display = (
        'raw_material', 'total_available_quantity_kg', 'total_coils_available', 
        'total_sheets_available', 'active_heat_numbers_count', 'last_updated'
    )
    list_filter = ('last_updated', 'raw_material__material_type')
    search_fields = ('raw_material__material_code',)
    ordering = ('-last_updated',)
    raw_id_fields = ('raw_material',)
    readonly_fields = ('last_updated',)
    
    fieldsets = (
        ('Raw Material', {
            'fields': ('raw_material',)
        }),
        ('Aggregate Stock Levels', {
            'fields': (
                'total_available_quantity_kg', 'total_coils_available', 
                'total_sheets_available', 'active_heat_numbers_count'
            )
        }),
        ('Tracking', {
            'fields': ('last_transaction', 'last_updated'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('raw_material')


@admin.register(InventoryTransactionHeat)
class InventoryTransactionHeatAdmin(admin.ModelAdmin):
    list_display = (
        'inventory_transaction', 'heat_number', 'grn_number', 'quantity_kg', 
        'coils_count', 'sheets_count'
    )
    list_filter = ('grn_number', 'heat_number__raw_material__material_type')
    search_fields = ('grn_number', 'heat_number__heat_number', 'inventory_transaction__transaction_id')
    ordering = ('-inventory_transaction__transaction_datetime',)
    raw_id_fields = ('inventory_transaction', 'heat_number')
    
    fieldsets = (
        ('Transaction Link', {
            'fields': ('inventory_transaction', 'heat_number', 'grn_number')
        }),
        ('Quantities', {
            'fields': ('quantity_kg', 'coils_count', 'sheets_count')
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'inventory_transaction', 'heat_number', 'heat_number__raw_material'
        )


@admin.register(HandoverIssue)
class HandoverIssueAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'heat_number', 'issue_type', 'actual_weight', 'reported_by', 
        'reported_at', 'is_resolved', 'resolved_at'
    )
    list_filter = ('issue_type', 'is_resolved', 'reported_at', 'reported_by')
    search_fields = ('heat_number__heat_number', 'heat_number__raw_material__material_code', 'remarks')
    ordering = ('-reported_at',)
    readonly_fields = ('reported_at',)
    raw_id_fields = ('heat_number', 'reported_by', 'resolved_by')
    
    fieldsets = (
        ('Issue Details', {
            'fields': ('heat_number', 'issue_type', 'actual_weight', 'remarks')
        }),
        ('Reporting', {
            'fields': ('reported_by', 'reported_at')
        }),
        ('Resolution', {
            'fields': ('is_resolved', 'resolved_at', 'resolved_by', 'resolution_notes'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'heat_number', 'heat_number__raw_material', 'reported_by', 'resolved_by'
        )


@admin.register(RMReturn)
class RMReturnAdmin(admin.ModelAdmin):
    list_display = (
        'return_id', 'raw_material', 'batch', 'manufacturing_order', 
        'quantity_kg', 'disposition', 'returned_by', 'returned_at', 'disposed_by'
    )
    list_filter = ('disposition', 'returned_at', 'disposed_at', 'returned_from_location')
    search_fields = (
        'return_id', 'raw_material__material_code', 'batch__batch_id', 
        'manufacturing_order__mo_id', 'return_reason'
    )
    ordering = ('-returned_at',)
    readonly_fields = ('return_id', 'returned_at', 'created_at', 'updated_at')
    raw_id_fields = ('raw_material', 'heat_number', 'batch', 'manufacturing_order', 
                     'returned_from_location', 'returned_by', 'disposed_by', 'return_transaction')
    date_hierarchy = 'returned_at'
    
    fieldsets = (
        ('Return Information', {
            'fields': ('return_id', 'raw_material', 'heat_number', 'quantity_kg')
        }),
        ('Source Details', {
            'fields': ('batch', 'manufacturing_order', 'returned_from_location')
        }),
        ('Return Details', {
            'fields': ('return_reason', 'returned_by', 'returned_at')
        }),
        ('Disposition', {
            'fields': ('disposition', 'disposition_notes', 'disposed_by', 'disposed_at'),
            'classes': ('wide',)
        }),
        ('Transaction Tracking', {
            'fields': ('return_transaction',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'raw_material', 'heat_number', 'batch', 'manufacturing_order', 
            'returned_from_location', 'returned_by', 'disposed_by', 'return_transaction'
        )


# Custom admin site configuration
admin.site.site_header = "Microsprings Inventory Management"
admin.site.site_title = "MSP Inventory Admin"
admin.site.index_title = "Inventory Management System"