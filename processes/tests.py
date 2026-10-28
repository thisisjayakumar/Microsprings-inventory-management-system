from unittest import skip

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Process, ProcessStep, SubProcess

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
    
    @skip("Process dropdown endpoint is not implemented; revisit in Phase 3")
    def test_process_dropdown(self):
        """Test process dropdown endpoint"""
        url = reverse('processes:process-dropdown')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)


class SubProcessAPITestCase(APITestCase):
    """Test case for SubProcess API endpoints"""
    
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
        
        # Create test process and subprocess
        self.process = Process.objects.create(
            name='Test Process',
            code=100,
            description='Test process description'
        )
        self.subprocess = SubProcess.objects.create(
            process=self.process,
            name='Test SubProcess',
            description='Test subprocess description'
        )
    
    @skip("SubProcess endpoints are not implemented; revisit in Phase 3")
    def test_subprocess_list(self):
        """Test subprocess list endpoint"""
        url = reverse('processes:subprocess-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
    
    @skip("SubProcess endpoints are not implemented; revisit in Phase 3")
    def test_subprocess_create(self):
        """Test subprocess creation"""
        url = reverse('processes:subprocess-list')
        data = {
            'process': self.process.id,
            'name': 'New SubProcess',
            'description': 'New subprocess description'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SubProcess.objects.count(), 2)


class ProcessStepAPITestCase(APITestCase):
    """Test case for ProcessStep API endpoints"""
    
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
        
        # Create test process, subprocess, and process step
        self.process = Process.objects.create(
            name='Test Process',
            code=100,
            description='Test process description'
        )
        self.subprocess = SubProcess.objects.create(
            process=self.process,
            name='Test SubProcess',
            description='Test subprocess description'
        )
        self.process_step = ProcessStep.objects.create(
            step_name='Test Step',
            step_code='TS001',
            process=self.process,
            subprocess=self.subprocess,
            sequence_order=1,
            description='Test step description'
        )
    
    @skip("ProcessStep endpoints are not implemented; revisit in Phase 3")
    def test_process_step_list(self):
        """Test process step list endpoint"""
        url = reverse('processes:processstep-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
    
    @skip("ProcessStep endpoints are not implemented; revisit in Phase 3")
    def test_process_step_create(self):
        """Test process step creation"""
        url = reverse('processes:processstep-list')
        data = {
            'step_name': 'New Step',
            'step_code': 'NS001',
            'process': self.process.id,
            'subprocess': self.subprocess.id,
            'sequence_order': 2,
            'description': 'New step description'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProcessStep.objects.count(), 2)
