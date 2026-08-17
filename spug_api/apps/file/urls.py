# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.urls import path

from .views import *

urlpatterns = [
    path('', FileView.as_view()),
    path('object/', ObjectView.as_view()),
    path('uploads/', UploadSessionView.as_view()),
    path('upload-batches/', UploadBatchView.as_view()),
    path('upload-batches/<uuid:batch_id>/', UploadBatchDetailView.as_view()),
    path('uploads/<uuid:upload_id>/', UploadSessionDetailView.as_view()),
    path('uploads/<uuid:upload_id>/chunk/', UploadChunkView.as_view()),
    path('uploads/<uuid:upload_id>/complete/', UploadCompleteView.as_view()),
]
