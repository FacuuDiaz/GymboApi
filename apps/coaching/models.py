"""
Coach ↔ student relationships as explicit records (never a role flag on the
user): one account can be a student of several coaches and coach many students
at the same time.

Privacy by default: an active relationship grants the coach only what the
student authorized (`can_view_*`), and nothing at all before the student accepts.
"""
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.common.models import TimestampedModel

INVITATION_TTL = timedelta(days=14)


def default_expiry():
    return timezone.now() + INVITATION_TTL


class CoachingGroup(TimestampedModel):
    """A coach's own way of organizing students (by schedule, gym, level...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coach = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coaching_groups")
    name = models.CharField(max_length=60)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["coach", "name"], name="coaching_group_name_unique")]


class CoachingRelationship(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ACTIVE = "active", "Activa"
        REJECTED = "rejected", "Rechazada"
        EXPIRED = "expired", "Vencida"
        REVOKED = "revoked", "Revocada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coach = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coaching_as_coach")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coaching_as_student")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    message = models.CharField(max_length=280, blank=True)
    # What the student lets the coach see. Chosen by the student when accepting; editable by the student only.
    can_view_progress = models.BooleanField(default=False)
    can_view_sessions = models.BooleanField(default=False)
    can_view_measurements = models.BooleanField(default=False)
    group = models.ForeignKey(CoachingGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="relationships")
    expires_at = models.DateTimeField(default=default_expiry)
    responded_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(coach=F("student")), name="coaching_not_self"),
            # At most one open (pending or active) relationship per pair.
            models.UniqueConstraint(
                fields=["coach", "student"], condition=Q(status__in=["pending", "active"]), name="coaching_one_open_per_pair"
            ),
        ]
        indexes = [
            models.Index(fields=["coach", "status"], name="coaching_coach_status_idx"),
            models.Index(fields=["student", "status"], name="coaching_student_status_idx"),
        ]

    @property
    def is_expired(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at <= timezone.now()


class RoutineAssignment(TimestampedModel):
    """
    A coach's routine assigned to a student. The student gets their OWN routine
    (`student_routine`); every new version of the coach's routine is applied to
    it automatically (decision D5: the coach wins, and anything the student had
    edited stays in the version history). History is never rewritten: sessions
    point at the exact version they were trained with.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Activa"
        ENDED = "ended", "Finalizada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    relationship = models.ForeignKey(CoachingRelationship, on_delete=models.CASCADE, related_name="assignments")
    source_routine = models.ForeignKey("training.Routine", on_delete=models.CASCADE, related_name="assignments_as_source")
    student_routine = models.OneToOneField("training.Routine", on_delete=models.CASCADE, related_name="assignment")
    applied_version_number = models.PositiveIntegerField()
    can_edit_plan = models.BooleanField(default=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["relationship", "source_routine"], condition=Q(status="active"), name="assignment_one_active_per_source"
            ),
        ]
