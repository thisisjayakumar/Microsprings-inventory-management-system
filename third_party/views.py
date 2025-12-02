"""
API Views for third_party app models
"""

from rest_framework import generics, status, filters
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

from .models import Vendor, Brand, Customer
from .serializers import (
    VendorSerializer, VendorListSerializer, BrandSerializer, CustomerSerializer,
    CustomerListSerializer, CustomerCreateUpdateSerializer
)


# Brand Views
class BrandListCreateView(generics.ListCreateAPIView):
    """List all brands or create a new brand"""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']


class BrandDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a brand"""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [IsAuthenticated]


# Vendor Views
class VendorListCreateView(generics.ListCreateAPIView):
    """List all vendors or create a new vendor"""
    queryset = Vendor.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['vendor_type', 'service_type', 'is_active', 'brands']
    search_fields = ['name', 'products_process', 'service_type', 'brands__name', 'gst_no', 'contact_person']
    ordering_fields = ['name', 'vendor_type', 'created_at']
    ordering = ['name']

    def get_serializer_class(self):
        """Use different serializers for list and create operations"""
        if self.request.method == 'GET':
            return VendorListSerializer
        return VendorSerializer


class VendorDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a vendor"""
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    permission_classes = [IsAuthenticated]


# Customer Views
class CustomerListCreateView(generics.ListCreateAPIView):
    """List all customers or create a new customer"""
    queryset = Customer.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['industry_type', 'is_active']
    search_fields = [
        'name', 'gst_no', 'address',
        'point_of_contact', 'contact_no_1', 'contact_no_2', 'email_id'
    ]
    ordering_fields = ['name', 'industry_type', 'created_at']
    ordering = ['name']

    def get_serializer_class(self):
        """Use different serializers for list and create operations"""
        if self.request.method == 'GET':
            return CustomerListSerializer
        return CustomerCreateUpdateSerializer


class CustomerDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a customer"""
    queryset = Customer.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """Use different serializers for retrieve and update operations"""
        if self.request.method == 'GET':
            return CustomerSerializer
        return CustomerCreateUpdateSerializer


# Additional API endpoints
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def customer_search(request):
    """Advanced customer search with multiple criteria"""
    query = request.GET.get('q', '')
    industry_type = request.GET.get('industry_type', '')
    is_active = request.GET.get('is_active', '')
    
    customers = Customer.objects.all()
    
    if query:
        customers = customers.filter(
            Q(name__icontains=query) |
            Q(gst_no__icontains=query) |
            Q(address__icontains=query) |
            Q(point_of_contact__icontains=query) |
            Q(contact_no_1__icontains=query) |
            Q(contact_no_2__icontains=query) |
            Q(email_id__icontains=query)
        )
    
    if industry_type:
        customers = customers.filter(industry_type=industry_type)
    
    if is_active:
        customers = customers.filter(is_active=is_active.lower() == 'true')
    
    serializer = CustomerListSerializer(customers, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def customer_contacts(request, pk):
    """Get all contact information for a specific customer"""
    try:
        customer = Customer.objects.get(pk=pk)
        contacts_data = {
            'customer_id': customer.id,
            'customer_name': customer.name,
            'contacts': customer.all_contacts
        }
        return Response(contacts_data)
    except Customer.DoesNotExist:
        return Response(
            {'error': 'Customer not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def industry_types(request):
    """Get all available industry types"""
    industry_choices = [
        {'value': choice[0], 'label': choice[1]} 
        for choice in Customer.INDUSTRY_TYPE_CHOICES
    ]
    return Response(industry_choices)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_customer_update(request):
    """Bulk update customer status (active/inactive)"""
    customer_ids = request.data.get('customer_ids', [])
    is_active = request.data.get('is_active', True)
    
    if not customer_ids:
        return Response(
            {'error': 'No customer IDs provided'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    updated_count = Customer.objects.filter(
        id__in=customer_ids
    ).update(is_active=is_active)
    
    return Response({
        'message': f'{updated_count} customers updated successfully',
        'updated_count': updated_count
    })
