from django.db import models
from django.contrib.auth import get_user_model
from utils.enums import ProductTypeChoices, SpringTypeChoices

User = get_user_model()


class Product(models.Model):
    product_code = models.CharField(max_length=120, unique=True)
    product_type = models.CharField(max_length=20, choices=ProductTypeChoices.choices, default='spring')
    spring_type = models.CharField(max_length=20, choices=SpringTypeChoices.choices, default='tension')
    
    # Foreign key to RawMaterial for material details
    material = models.ForeignKey(
        'inventory.RawMaterial', 
        on_delete=models.PROTECT, 
        related_name='products',
        help_text="Raw material used for this product"
    )
    
    # Foreign key to Customer using c_id field for customer relationship
    customer_c_id = models.ForeignKey(
        'third_party.Customer',
        to_field='c_id',
        on_delete=models.PROTECT,
        related_name='products',
        help_text="Customer associated with this product (references c_id)",
        null=True,
        blank=True
    )
    
    # Product specifications for RM calculation
    grams_per_product = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Weight in grams per single product unit (used for RM calculation)"
    )
    
    # Dimensions for press components (sheet materials)
    length_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Length in mm (for press components/sheet materials)"
    )
    breadth_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Breadth in mm (for press components/sheet materials)"
    )
    
    # Sheet Calculation Fields (for press components using sheet materials)
    # Whole Sheet Size
    whole_sheet_length_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Standard sheet length we purchase (L) in mm"
    )
    whole_sheet_breadth_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Standard sheet breadth we purchase (B) in mm"
    )
    
    # Strip Size (cut size from sheet for each part)
    strip_length_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Strip length cut from sheet (L) in mm"
    )
    strip_breadth_mm = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Strip breadth cut from sheet (B) in mm"
    )
    
    # Strip and piece counts
    strips_per_sheet = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="How many strips come from one full sheet (for RM ordering calculation)"
    )
    pcs_per_strip = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of finished parts per strip"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_products')
    updated_at = models.DateTimeField(auto_now=True)
    internal_product_code = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    packing_size = models.PositiveIntegerField(
        null=True, 
        blank=True, 
        help_text="Number of units per standard packing box/unit"
    )

    class Meta:
        verbose_name = 'Product'
        verbose_name_plural = 'Products'

    def __str__(self):
        customer_info = f" ({self.customer_c_id.c_id})" if self.customer_c_id else ""
        return f"{self.product_code}{customer_info}"
    
    # Properties to access material details
    @property
    def material_type(self):
        return self.material.material_type if self.material else None
    
    @property
    def material_name(self):
        return self.material.material_name if self.material else None
    
    @property
    def grade(self):
        return self.material.grade if self.material else None
    
    @property
    def wire_diameter_mm(self):
        return self.material.wire_diameter_mm if self.material else None
    
    @property
    def thickness_mm(self):
        return self.material.thickness_mm if self.material else None
    
    @property
    def finishing(self):
        return self.material.get_finishing_display() if self.material else None
    
    @property
    def weight_kg(self):
        return self.material.weight_kg if self.material else None
    
    @property
    def material_type_display(self):
        return self.material.get_material_type_display() if self.material else None
    
    # Properties to access customer details
    @property
    def customer_name(self):
        return self.customer_c_id.name if self.customer_c_id else None
    
    @property
    def customer_id(self):
        return self.customer_c_id.c_id if self.customer_c_id else None
    
    @property
    def customer_industry(self):
        return self.customer_c_id.get_industry_type_display() if self.customer_c_id else None
    
    def save(self, *args, **kwargs):
        """No auto-calculation needed for strips_per_sheet or pcs_per_strip"""
        super().save(*args, **kwargs)
    
    def clean(self):
        """Validate sheet calculation fields for press components"""
        from django.core.exceptions import ValidationError
        errors = {}
        
        # If this is a press component with sheet material, validate sheet calculation fields
        if self.product_type == 'press_component' and self.material:
            if self.material.material_type == 'sheet':
                # Check if sheet calculation fields are provided
                sheet_fields = [
                    self.whole_sheet_length_mm,
                    self.whole_sheet_breadth_mm,
                    self.strip_length_mm,
                    self.strip_breadth_mm,
                    self.strips_per_sheet,
                    self.pcs_per_strip
                ]
                
                # If any sheet calculation field is provided, all should be provided
                if any(field is not None for field in sheet_fields):
                    if not self.whole_sheet_length_mm:
                        errors['whole_sheet_length_mm'] = "Whole sheet length is required for sheet-based press components"
                    if not self.whole_sheet_breadth_mm:
                        errors['whole_sheet_breadth_mm'] = "Whole sheet breadth is required for sheet-based press components"
                    if not self.strip_length_mm:
                        errors['strip_length_mm'] = "Strip length is required for sheet-based press components"
                    if not self.strip_breadth_mm:
                        errors['strip_breadth_mm'] = "Strip breadth is required for sheet-based press components"
                    if not self.strips_per_sheet:
                        errors['strips_per_sheet'] = "Strips per sheet is required for sheet-based press components"
                    if not self.pcs_per_strip:
                        errors['pcs_per_strip'] = "Pieces per strip is required for sheet-based press components"
                
                # Validate that strip dimensions don't exceed sheet dimensions
                if self.strip_length_mm and self.whole_sheet_length_mm:
                    if self.strip_length_mm > self.whole_sheet_length_mm:
                        errors['strip_length_mm'] = "Strip length cannot exceed whole sheet length"
                
                if self.strip_breadth_mm and self.whole_sheet_breadth_mm:
                    if self.strip_breadth_mm > self.whole_sheet_breadth_mm:
                        errors['strip_breadth_mm'] = "Strip breadth cannot exceed whole sheet breadth"
        
        if errors:
            raise ValidationError(errors)
    
    def calculate_strips_required(self, quantity):
        """
        Calculate number of strips required for a given quantity of products
        
        Args:
            quantity (int): Number of products to manufacture
            
        Returns:
            dict: {
                'strips_required': int,  # Rounded up to whole strips
                'total_pieces_from_strips': int,
                'excess_pieces': int,
                'pcs_per_strip': int
            }
        """
        if not self.pcs_per_strip or self.pcs_per_strip == 0:
            return {
                'strips_required': 0,
                'total_pieces_from_strips': 0,
                'excess_pieces': 0,
                'error': 'Strip calculation data not available for this product'
            }
        
        import math
        strips_required = math.ceil(quantity / self.pcs_per_strip)
        total_pieces_from_strips = strips_required * self.pcs_per_strip
        excess_pieces = total_pieces_from_strips - quantity
        
        return {
            'strips_required': strips_required,
            'total_pieces_from_strips': total_pieces_from_strips,
            'excess_pieces': excess_pieces,
            'pcs_per_strip': self.pcs_per_strip
        }
    
    def calculate_sheets_for_rm_ordering(self, strips_needed):
        """
        Calculate sheets required when ordering RM (when strips are low)
        Used for RM ordering - how many whole sheets to buy to get strips
        
        Args:
            strips_needed (int): Number of strips required
            
        Returns:
            dict: {
                'sheets_required': int,  # Rounded up to whole sheets
                'total_strips_from_sheets': int,
                'excess_strips': int
            }
        """
        if not self.strips_per_sheet or self.strips_per_sheet == 0:
            return {
                'sheets_required': 0,
                'total_strips_from_sheets': 0,
                'excess_strips': 0,
                'error': 'Sheet calculation data not available for this product'
            }
        
        import math
        sheets_required = math.ceil(strips_needed / self.strips_per_sheet)
        total_strips_from_sheets = sheets_required * self.strips_per_sheet
        excess_strips = total_strips_from_sheets - strips_needed
        
        return {
            'sheets_required': sheets_required,
            'total_strips_from_sheets': total_strips_from_sheets,
            'excess_strips': excess_strips,
            'strips_per_sheet': self.strips_per_sheet
        }

