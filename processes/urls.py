from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProcessViewSet,
    WorkCenterMasterViewSet,
    DailySupervisorStatusViewSet,
    SupervisorActivityLogViewSet
)

router = DefaultRouter()
router.register(r'processes', ProcessViewSet, basename='process')
router.register(r'work-centers', WorkCenterMasterViewSet, basename='work-center')
router.register(r'supervisor-status', DailySupervisorStatusViewSet, basename='supervisor-status')
router.register(r'supervisor-activity', SupervisorActivityLogViewSet, basename='supervisor-activity')

urlpatterns = [
    path('', include(router.urls)),
]
