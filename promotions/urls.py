from django.urls import path

from .views import (
    PromotionBatchApplyView,
    PromotionBatchDeleteView,
    PromotionBatchDetailView,
    PromotionBatchListView,
    PromotionMetaView,
    PromotionPreviewView,
    PromotionRecordStatusUpdateView,
)

urlpatterns = [
    path("admin/meta/", PromotionMetaView.as_view(), name="promotion-meta"),
    path("admin/preview/", PromotionPreviewView.as_view(), name="promotion-preview"),
    path("admin/batches/", PromotionBatchListView.as_view(), name="promotion-batches"),
    path("admin/batches/<int:batch_id>/", PromotionBatchDetailView.as_view(), name="promotion-batch-detail"),
    path(
        "admin/batches/<int:batch_id>/records/<int:record_id>/",
        PromotionRecordStatusUpdateView.as_view(),
        name="promotion-record-status-update",
    ),
    path("admin/batches/<int:batch_id>/delete/", PromotionBatchDeleteView.as_view(), name="promotion-batch-delete"),
    path("admin/batches/<int:batch_id>/apply/", PromotionBatchApplyView.as_view(), name="promotion-batch-apply"),
]
