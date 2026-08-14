from django.urls import path

from .views import BindingView, CredentialView, GrantView, IdentityView


urlpatterns = [
    path('credentials/', CredentialView.as_view()),
    path('identities/', IdentityView.as_view()),
    path('bindings/', BindingView.as_view()),
    path('grants/', GrantView.as_view()),
]
