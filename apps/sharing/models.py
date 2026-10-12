"""
Sharing routines (with specific people or by link), the public community
catalog, and reports. Publishing or sharing a routine never exposes anything
else of its author: no profile, body weight, sessions or progress.
"""
import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import TimestampedModel


class ShareRole(models.TextChoices):
    VIEWER = "viewer", "Ver"
    COPIER = "copier", "Ver y copiar"
    EDITOR = "editor", "Ver, copiar y editar"


class LinkRole(models.TextChoices):
    """Links never grant editing."""

    VIEWER = "viewer", "Ver"
    COPIER = "copier", "Ver y copiar"


class RoutineShare(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ACCEPTED = "accepted", "Aceptada"
        DECLINED = "declined", "Rechazada"
        REVOKED = "revoked", "Revocada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    routine = models.ForeignKey("training.Routine", on_delete=models.CASCADE, related_name="shares")
    grantee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="routines_shared_with_me")
    role = models.CharField(max_length=8, choices=ShareRole.choices, default=ShareRole.VIEWER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["routine", "grantee"], condition=Q(status__in=["pending", "accepted"]), name="share_one_open_per_grantee"),
        ]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ShareLink(TimestampedModel):
    """
    A secret link to one routine. Only the SHA-256 of the token is stored: a
    database leak does not leak working links. Links grant view or view+copy,
    never edit, and only to that routine.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    routine = models.ForeignKey("training.Routine", on_delete=models.CASCADE, related_name="links")
    token_hash = models.CharField(max_length=64, unique=True)
    # First characters of the token, so the owner can tell links apart. Not enough to use one.
    token_hint = models.CharField(max_length=6)
    role = models.CharField(max_length=8, choices=LinkRole.choices, default=LinkRole.VIEWER)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    uses = models.PositiveIntegerField(default=0)


class ContentReport(TimestampedModel):
    class Kind(models.TextChoices):
        ROUTINE = "routine", "Rutina"
        EXERCISE = "exercise", "Ejercicio"

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam o publicidad"
        INAPPROPRIATE = "inappropriate", "Contenido inapropiado"
        DANGEROUS = "dangerous", "Peligroso para la salud"
        COPYRIGHT = "copyright", "Copia sin permiso"
        OTHER = "other", "Otro"

    class Status(models.TextChoices):
        OPEN = "open", "Abierta"
        RESOLVED = "resolved", "Resuelta (contenido oculto)"
        DISMISSED = "dismissed", "Descartada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    object_id = models.UUIDField()
    reason = models.CharField(max_length=16, choices=Reason.choices)
    details = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reporter", "kind", "object_id"], condition=Q(status="open"), name="report_one_open_per_reporter"),
        ]
        indexes = [models.Index(fields=["kind", "object_id", "status"], name="report_object_idx")]
