from django.core.management.base import BaseCommand

from apps.training.builtin import load_builtin_exercises
from apps.training.models import Exercise


class Command(BaseCommand):
    help = "Insert/update the built-in exercise library from training/data/builtin_exercises.json (idempotent, never deletes)."

    def handle(self, *args, **options):
        created, updated = load_builtin_exercises(Exercise)
        self.stdout.write(self.style.SUCCESS(f"Built-in exercises: {created} created, {updated} updated."))
