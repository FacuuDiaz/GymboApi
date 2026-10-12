from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("routine-shares", views.ShareViewSet, basename="routine-share")
router.register("community/routines", views.CommunityRoutineViewSet, basename="community-routine")
router.register("community/exercises", views.CommunityExerciseViewSet, basename="community-exercise")

sharing = views.RoutineSharingView
shared = views.SharedRoutineView
publishing = views.ExercisePublishingView

urlpatterns = [
    *router.urls,
    path("routines/<uuid:pk>/shares/", sharing.as_view({"get": "list_shares", "post": "create_share"}), name="routine-shares"),
    path("routines/<uuid:pk>/links/", sharing.as_view({"get": "list_links", "post": "create_link"}), name="routine-links"),
    path("routines/<uuid:pk>/publish/", sharing.as_view({"post": "publish"}), name="routine-publish"),
    path("routines/<uuid:pk>/unpublish/", sharing.as_view({"post": "unpublish"}), name="routine-unpublish"),
    path("exercises/<uuid:pk>/publish/", publishing.as_view({"post": "publish"}), name="exercise-publish"),
    path("exercises/<uuid:pk>/unpublish/", publishing.as_view({"post": "unpublish"}), name="exercise-unpublish"),
    path("share-links/<uuid:pk>/", views.LinkRevokeView.as_view(), name="share-link"),
    path("shared/routines/<uuid:pk>/", shared.as_view({"get": "retrieve", "patch": "partial_update"}), name="shared-routine"),
    path("shared/routines/<uuid:pk>/copy/", shared.as_view({"post": "copy"}), name="shared-routine-copy"),
    path("shared-links/resolve/", views.LinkResolveView.as_view(), name="shared-link-resolve"),
    path("shared-links/copy/", views.LinkCopyView.as_view(), name="shared-link-copy"),
    path("reports/", views.ReportView.as_view(), name="reports"),
]
