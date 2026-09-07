from django.urls import path

from .views import (
    TopologyEdgeView,
    TopologyGraphView,
    TopologyNodeView,
    TopologyRuntimeScanView,
    TopologySchemaView,
    TopologySourceView,
)


urlpatterns = [
    path('schema/', TopologySchemaView.as_view()),
    path('graph/', TopologyGraphView.as_view()),
    path('runtime-scan/', TopologyRuntimeScanView.as_view()),
    path('sources/', TopologySourceView.as_view()),
    path('nodes/', TopologyNodeView.as_view()),
    path('edges/', TopologyEdgeView.as_view()),
]
