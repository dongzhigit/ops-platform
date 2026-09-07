from django.urls import path

from .views import (
    AIConfigView,
    AIEvidencePreviewView,
    AIInvestigationView,
    AIRemediationApprovalView,
    AIRemediationExecutionView,
    AIRemediationPlatformReferenceView,
    AIRemediationProposalView,
    AIScopeView,
)


urlpatterns = [
    path('config/', AIConfigView.as_view()),
    path('scope/', AIScopeView.as_view()),
    path('evidence/preview/', AIEvidencePreviewView.as_view()),
    path('investigations/', AIInvestigationView.as_view()),
    path('remediations/', AIRemediationProposalView.as_view()),
    path('remediations/approval/', AIRemediationApprovalView.as_view()),
    path('remediations/executions/', AIRemediationExecutionView.as_view()),
    path('remediations/platform-references/', AIRemediationPlatformReferenceView.as_view()),
]
