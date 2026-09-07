from django.urls import path

from .views import IncidentRoomView, IncidentScopeView, IncidentTimelineView


urlpatterns = [
    path('scope/', IncidentScopeView.as_view()),
    path('rooms/', IncidentRoomView.as_view()),
    path('timeline/', IncidentTimelineView.as_view()),
]
