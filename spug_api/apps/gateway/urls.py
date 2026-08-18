from django.urls import path

from .views import EndpointView, SessionDetailView, SessionLaunchView, SessionView


urlpatterns = [
    path('endpoints/', EndpointView.as_view()),
    path('sessions/', SessionView.as_view()),
    path('sessions/<uuid:session_id>/', SessionDetailView.as_view()),
    path('sessions/<uuid:session_id>/launch/', SessionLaunchView.as_view()),
]
