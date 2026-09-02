from django.urls import path

from .views import (
    AIConfigView,
    AIEvidencePreviewView,
    AIInvestigationView,
    AIScopeView,
)


urlpatterns = [
    path('config/', AIConfigView.as_view()),
    path('scope/', AIScopeView.as_view()),
    path('evidence/preview/', AIEvidencePreviewView.as_view()),
    path('investigations/', AIInvestigationView.as_view()),
]
