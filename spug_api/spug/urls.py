"""spug URL Configuration
# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include

urlpatterns = [
    # Nginx removes the public /api prefix before proxying to Django.
    path('v1/audit/', include('apps.audit.urls')),
    path('v1/assets/', include('apps.assets.urls')),
    path('v1/gateway/', include('apps.gateway.urls')),
    path('v1/observability/', include('apps.observability.urls')),
    path('account/', include('apps.account.urls')),
    path('host/', include('apps.host.urls')),
    path('exec/', include('apps.exec.urls')),
    path('schedule/', include('apps.schedule.urls')),
    path('monitor/', include('apps.monitor.urls')),
    path('alarm/', include('apps.alarm.urls')),
    path('setting/', include('apps.setting.urls')),
    path('config/', include('apps.config.urls')),
    path('app/', include('apps.app.urls')),
    path('deploy/', include('apps.deploy.urls')),
    path('repository/', include('apps.repository.urls')),
    path('home/', include('apps.home.urls')),
    path('notify/', include('apps.notify.urls')),
    path('file/', include('apps.file.urls')),
    path('apis/', include('apps.apis.urls')),
]
