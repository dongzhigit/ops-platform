from django.urls import path

from .views import (
    ApprovalDecisionView,
    ApprovalView,
    AuditEventView,
    AuditVerifyView,
    OperationPreviewView,
)


urlpatterns = [
    path('approvals/', ApprovalView.as_view()),
    path('approvals/<uuid:approval_id>/decision/', ApprovalDecisionView.as_view()),
    path('preview/', OperationPreviewView.as_view()),
    path('events/', AuditEventView.as_view()),
    path('verify/', AuditVerifyView.as_view()),
]
