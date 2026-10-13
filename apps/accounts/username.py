"""
Changing one's username. The username is public (search, shares, coaching,
attributions), so a change is confirmed like other sensitive account actions,
limited to one every USERNAME_CHANGE_COOLDOWN_DAYS, and rewrites the frozen
"@old" mentions in other people's copies so they follow the person.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.training.services import Conflict

from .mentions import rewrite_mentions


def next_change_allowed_at(user):
    if user.username_changed_at is None:
        return None
    return user.username_changed_at + timedelta(days=settings.USERNAME_CHANGE_COOLDOWN_DAYS)


@transaction.atomic
def change_username(user, new_username: str):
    allowed_at = next_change_allowed_at(user)
    if allowed_at and timezone.now() < allowed_at:
        raise Conflict(
            f"Ya cambiaste tu nombre de usuario hace poco. Vas a poder cambiarlo de nuevo el {timezone.localtime(allowed_at):%d/%m/%Y}.",
            code="username_change_too_soon",
        )
    old = user.username
    user.username = new_username
    user.username_changed_at = timezone.now()
    user.save(update_fields=["username", "username_changed_at"])
    rewrite_mentions(old, f"@{new_username}", exclude_owner_id=user.pk)
    return user
