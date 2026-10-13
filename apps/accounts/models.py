import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from apps.common.models import TimestampedModel
from apps.training.choices import RoutineGoal, ThemeName, TrainingLevel, WeightUnit

from .managers import UserManager, normalize_email


class Gender(models.TextChoices):
    FEMALE = "female", "Femenino"
    MALE = "male", "Masculino"
    NON_BINARY = "non_binary", "No binario"
    OTHER = "other", "Otro"


class User(AbstractUser):
    """
    A registered Gymbo account. Guests never get a row here: their data stays
    on the device until they register and explicitly choose to sync it.

    Only username, email and password are required. Every personal field is
    optional and blank by default — Gymbo works without any of them.
    Roles (athlete, student, coach, author) are not flags on the user; they
    derive from explicit relationships (see the coaching app).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email")
    phone = models.CharField(max_length=32, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    # Access tokens issued before this instant are rejected (see
    # apps.accounts.authentication). Set on "log out everywhere", password
    # change and refresh-token reuse, so revocation does not wait for expiry.
    sessions_revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Last username change, for the cooldown between changes (see accounts.username).
    username_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
    # People search is opt-in: nobody appears in "Buscar" unless they turn this on.
    discoverable = models.BooleanField(default=False)
    # Shown as a coach in search (only together with `discoverable`). A label the
    # person chooses; coaching permissions still come from accepted relationships.
    is_coach = models.BooleanField(default=False)

    objects = UserManager()

    REQUIRED_FIELDS = ["email"]

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("username"), name="user_username_ci_unique"),
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
            models.CheckConstraint(condition=~models.Q(email=""), name="user_email_not_blank"),
        ]

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class SocialIdentity(TimestampedModel):
    """
    A linked external identity (Google). Identified by the provider's stable
    subject (`sub`), never by email: an email match alone must not link or
    take over an existing account.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_identities")
    provider = models.CharField(max_length=16, choices=Provider.choices)
    subject = models.CharField(max_length=255)
    # Informational only (shown in "linked accounts"); never used for lookup.
    email_at_link = models.EmailField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="social_identity_provider_subject_unique"),
            models.UniqueConstraint(fields=["user", "provider"], name="social_identity_one_per_provider"),
        ]


class TrainingPreferences(TimestampedModel):
    """
    The synced part of the app's `UserProfile`. Device-specific settings
    (training reminders, rest-timer vibration) intentionally stay on the device.
    Conflicts resolve last-write-wins per field — acceptable for preferences.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    level = models.CharField(max_length=16, choices=TrainingLevel.choices, default=TrainingLevel.BEGINNER)
    goal = models.CharField(max_length=16, choices=RoutineGoal.choices, default=RoutineGoal.HYPERTROPHY)
    unit = models.CharField(max_length=2, choices=WeightUnit.choices, default=WeightUnit.KG)
    target_body_weight = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    days_per_week = models.PositiveSmallIntegerField(default=3)
    theme = models.CharField(max_length=8, choices=ThemeName.choices, default=ThemeName.DARK)
    increment_barbell = models.DecimalField(max_digits=5, decimal_places=2, default=2.5)
    increment_dumbbell = models.DecimalField(max_digits=5, decimal_places=2, default=2)
    increment_machine = models.DecimalField(max_digits=5, decimal_places=2, default=5)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(days_per_week__gte=1, days_per_week__lte=7), name="prefs_days_per_week_range"),
            models.CheckConstraint(
                condition=models.Q(target_body_weight__isnull=True) | models.Q(target_body_weight__gt=0),
                name="prefs_target_body_weight_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(increment_barbell__gte=0, increment_dumbbell__gte=0, increment_machine__gte=0),
                name="prefs_increments_non_negative",
            ),
        ]
