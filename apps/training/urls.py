from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("routines", views.RoutineViewSet, basename="routine")
router.register("workouts", views.WorkoutSessionViewSet, basename="workout")
router.register("measurements", views.MeasurementViewSet, basename="measurement")

urlpatterns = [
    *router.urls,
    path("progress/weekly/", views.WeeklyProgressView.as_view(), name="progress-weekly"),
    path("progress/exercises/<str:exercise_ref>/", views.ExerciseProgressView.as_view(), name="progress-exercise"),
]
