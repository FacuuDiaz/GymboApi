"""
Copies keep a frozen attribution text ("Basada en «X» de @ana"). When @ana
changes her username or deletes her account, those texts in other people's
routines are rewritten so they never point at a name someone else may take.
"""
import re


def rewrite_mentions(username: str, replacement: str, *, exclude_owner_id) -> int:
    """Replaces every exact "@username" mention (case-insensitive, whole name only) in others' routine attributions."""
    from apps.training.models import Routine

    mention = re.compile(rf"@{re.escape(username)}(?![\w.@+-])", re.IGNORECASE)
    changed = 0
    for routine in Routine.objects.filter(attribution__icontains=f"@{username}").exclude(owner_id=exclude_owner_id):
        text = mention.sub(replacement, routine.attribution)
        if text != routine.attribution:
            routine.attribution = text[:160]
            routine.version += 1  # so the owner's devices pull the new text
            routine.save()
            changed += 1
    return changed
