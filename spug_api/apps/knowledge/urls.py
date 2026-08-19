from django.urls import path

from .views import (
    KnowledgeDocumentRevisionView,
    KnowledgeDocumentSearchView,
    KnowledgeDocumentView,
    KnowledgeMembershipView,
    KnowledgeSpaceView,
    KnowledgeSpaceUserView,
)


urlpatterns = [
    path('spaces/', KnowledgeSpaceView.as_view()),
    path('spaces/<uuid:space_id>/members/', KnowledgeMembershipView.as_view()),
    path('spaces/<uuid:space_id>/users/', KnowledgeSpaceUserView.as_view()),
    path('documents/', KnowledgeDocumentView.as_view()),
    path('documents/search/', KnowledgeDocumentSearchView.as_view()),
    path('documents/<uuid:document_id>/revisions/', KnowledgeDocumentRevisionView.as_view()),
]
