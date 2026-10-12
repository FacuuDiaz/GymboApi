from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("coaching/relationships", views.RelationshipViewSet, basename="coaching-relationship")
router.register("coaching/groups", views.GroupViewSet, basename="coaching-group")
router.register("coaching/assignments", views.AssignmentViewSet, basename="coaching-assignment")

urlpatterns = router.urls
