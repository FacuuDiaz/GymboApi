import uuid

from django.conf import settings
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SyncedModel(TimestampedModel):
    """
    Base for personal entities that the mobile app creates offline and syncs.

    - `id` is the UUID the app generated locally, so retrying an upload is
      idempotent and needs no id remapping. The server still verifies that an
      incoming id is not already owned by someone else before accepting it.
    - `version` increments on every server-side change and backs optimistic
      concurrency (the client sends the version it based its edit on).
    - `deleted_at` is a tombstone: deletions must propagate to other devices,
      which a hard delete cannot do.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    version = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
