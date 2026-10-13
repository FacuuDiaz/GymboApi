"""
Account deletion. Immediate and irreversible: there is no "deactivated"
limbo holding personal data. What happens to each kind of data is documented
in README.md ("Eliminación de cuenta") — keep both in sync.

The rule is: everything that is the person's goes; what other people built on
top of it (their copies, their assigned routines, their sessions) stays theirs,
untouched, and loses only the link back to the deleted person.
"""
import logging

from django.db import transaction
from django.db.models import F, Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from .mentions import rewrite_mentions
from .tokens import revoke_all_sessions

logger = logging.getLogger("gymbo.auth")

DELETED_AUTHOR = "una cuenta eliminada"


def _anonymize_attributions(user) -> int:
    """'Basada en «X» de @ana' → 'Basada en «X» de una cuenta eliminada' in other people's routines."""
    return rewrite_mentions(user.username, DELETED_AUTHOR, exclude_owner_id=user.pk)


def _release_exercises(user) -> int:
    """
    The person's exercises are deleted, except published ones that someone
    else's plan points to directly: those stay, without an author and out of
    the catalog, so that plan keeps working. Name and description are kept;
    they are exercise content, not data about the person.
    """
    from apps.training.models import Exercise, RoutineVersion

    others_plans = RoutineVersion.objects.exclude(routine__owner=user).annotate(text=Cast("content", TextField()))
    kept = [
        exercise.pk
        for exercise in Exercise.objects.filter(owner=user, visibility="public")
        if others_plans.filter(text__contains=str(exercise.pk)).exists()
    ]
    Exercise.objects.filter(pk__in=kept).update(owner=None, published_at=None, version=F("version") + 1, updated_at=timezone.now())
    Exercise.objects.filter(owner=user).delete()
    return len(kept)


@transaction.atomic
def delete_account(user) -> None:
    from apps.coaching.models import CoachingRelationship
    from apps.coaching.services import end_relationship

    user_id = user.pk
    # Coaching first, through the normal path: assignments end and every
    # student keeps their routines, unlocked, with all their history.
    open_relationships = CoachingRelationship.objects.filter(
        Q(coach=user) | Q(student=user), status__in=[CoachingRelationship.Status.PENDING, CoachingRelationship.Status.ACTIVE]
    )
    for rel in open_relationships:
        end_relationship(user, rel.pk)

    _anonymize_attributions(user)
    _release_exercises(user)

    # Every session ends now; the token rows themselves go too.
    revoke_all_sessions(user)
    OutstandingToken.objects.filter(user=user).delete()

    # Cascades: routines (versions, date overrides, shares, links, community
    # publication), sessions, measurements, preferences, linked Google identity,
    # shares received, coaching relationships and groups. Reports the person
    # filed stay for moderation, without the reporter.
    user.delete()
    logger.info("account_deleted user_id=%s", user_id)
