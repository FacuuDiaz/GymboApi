from django.contrib.auth.models import UserManager as DjangoUserManager


def normalize_email(email: str) -> str:
    """Trim and lowercase the whole address so `Ana@Mail.com` and `ana@mail.com` are one account."""
    return (email or "").strip().lower()


class UserManager(DjangoUserManager):
    def _create_user(self, username, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        return super()._create_user(username.strip(), normalize_email(email), password, **extra_fields)

    def get_by_natural_key(self, username):
        # Case-insensitive login by username, matching the case-insensitive unique constraint.
        return self.get(username__iexact=username)
