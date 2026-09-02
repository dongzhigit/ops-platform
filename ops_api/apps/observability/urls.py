from django.urls import path

from .views import (
    AlertEventView,
    AlertmanagerWebhookView,
    MetricCatalogView,
    MetricQueryView,
    MetricSummaryView,
    MetricTargetView,
    PrometheusDiscoveryView,
    TopologyView,
)


urlpatterns = [
    path('discovery/targets/', PrometheusDiscoveryView.as_view()),
    path('alertmanager/webhook/', AlertmanagerWebhookView.as_view()),
    path('targets/', MetricTargetView.as_view()),
    path('metrics/catalog/', MetricCatalogView.as_view()),
    path('metrics/query/', MetricQueryView.as_view()),
    path('metrics/summary/', MetricSummaryView.as_view()),
    path('topology/', TopologyView.as_view()),
    path('alerts/', AlertEventView.as_view()),
]
