"""
Serializers for third_party app models
"""

from rest_framework import serializers
from .models import Vendor, Brand, Customer


class BrandSerializer(serializers.ModelSerializer):
    """Serializer for Brand model"""
    
    class Meta:
        model = Brand
        fields = [
            'id', 'name', 'description', 'is_active', 
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model"""
    brands_display = serializers.CharField(source='get_brands_display', read_only=True)
    brands_list = serializers.CharField(read_only=True)
    vendor_type_display = serializers.CharField(source='get_vendor_type_display', read_only=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'name', 'vendor_type', 'vendor_type_display',
            'products_process', 'service_type', 'brands', 'brands_display', 'brands_list',
            'gst_no', 'address', 'contact_no', 'email', 'contact_person',
            'is_active', 'created_at', 'updated_at', 'created_by'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'brands_display', 
            'brands_list', 'vendor_type_display'
        ]

    def validate_gst_no(self, value):
        """Custom validation for GST number"""
        if value and len(value) != 15:
            raise serializers.ValidationError("GST number must be exactly 15 characters long.")
        return value


class VendorListSerializer(serializers.ModelSerializer):
    """Simplified serializer for vendor list GET requests"""
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'name', 'vendor_type', 'gst_no', 'contact_no', 'contact_person'
        ]


class CustomerSerializer(serializers.ModelSerializer):
    """Serializer for Customer model"""
    industry_type_display = serializers.CharField(source='get_industry_type_display', read_only=True)
    primary_contact = serializers.CharField(read_only=True)
    all_contacts = serializers.SerializerMethodField()
    contacts_display = serializers.CharField(source='get_contacts_display', read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'industry_type', 'industry_type_display',
            'gst_no', 'address',
            'point_of_contact', 'contact_no_1', 'contact_no_2', 'email_id',
            'is_active', 'notes',
            'primary_contact', 'all_contacts', 'contacts_display',
            'created_at', 'updated_at', 'created_by'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'industry_type_display',
            'primary_contact', 'all_contacts', 'contacts_display'
        ]

    def get_all_contacts(self, obj):
        """Get all contacts as a dictionary"""
        return obj.all_contacts

    def validate_gst_no(self, value):
        """Custom validation for GST number"""
        if value and len(value) != 15:
            raise serializers.ValidationError("GST number must be exactly 15 characters long.")
        return value

    def validate(self, data):
        """Custom validation for the entire customer object"""
        # Ensure at least one contact method is provided
        has_contact = any([
            data.get('point_of_contact'),
            data.get('contact_no_1'),
            data.get('contact_no_2'),
            data.get('email_id')
        ])
        
        if not has_contact:
            raise serializers.ValidationError(
                "At least one contact method must be provided (point of contact, contact number, or email)."
            )
        
        return data


class CustomerListSerializer(serializers.ModelSerializer):
    """Simplified serializer for customer list views"""
    industry_type_display = serializers.CharField(source='get_industry_type_display', read_only=True)
    primary_contact = serializers.CharField(read_only=True)
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Customer
        fields = [
            'id', 'c_id', 'name', 'display_name', 'industry_type', 'industry_type_display',
            'gst_no', 'primary_contact', 'contact_no_1', 'email_id',
            'is_active', 'created_at'
        ]
    
    def get_display_name(self, obj):
        """Return formatted display name for dropdown"""
        return f"{obj.c_id} - {obj.name}"


class CustomerCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating customers with validation"""
    
    class Meta:
        model = Customer
        fields = [
            'name', 'industry_type', 'gst_no', 'address',
            'point_of_contact', 'contact_no_1', 'contact_no_2', 'email_id',
            'is_active', 'notes'
        ]

    def validate_gst_no(self, value):
        """Custom validation for GST number"""
        if value and len(value) != 15:
            raise serializers.ValidationError("GST number must be exactly 15 characters long.")
        return value

    def validate(self, data):
        """Custom validation for the entire customer object"""
        # Ensure at least one contact method is provided
        has_contact = any([
            data.get('point_of_contact'),
            data.get('contact_no_1'),
            data.get('contact_no_2'),
            data.get('email_id')
        ])
        
        if not has_contact:
            raise serializers.ValidationError(
                "At least one contact method must be provided (point of contact, contact number, or email)."
            )
        
        return data

    def create(self, validated_data):
        """Create a new customer"""
        # Set created_by if user is available in context
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
        
        return Customer.objects.create(**validated_data)