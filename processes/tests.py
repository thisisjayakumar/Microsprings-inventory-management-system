from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Process

User = get_user_model()


class ProcessAPITestCase(APITestCase):
    """Test case for Process API endpoints"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User'
        )
        self.client.force_authenticate(user=self.user)

        # Create test process
        self.process = Process.objects.create(
            name='Test Process',
            code=100,
            description='Test process description'
        )

    def test_process_list(self):
        """Test process list endpoint"""
        url = reverse('processes:process-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_process_create_is_not_allowed(self):
        """Process creation is not exposed; the viewset is read-only."""
        url = reverse('processes:process-list')
        data = {
            'name': 'New Process',
            'code': 200,
            'description': 'New process description',
            'is_active': True
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(Process.objects.count(), 1)

    def test_process_detail(self):
        """Test process detail endpoint"""
        url = reverse('processes:process-detail', kwargs={'pk': self.process.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Test Process')
