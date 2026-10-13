import re
from datetime import date

from django.contrib.auth import password_validation
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .managers import normalize_email
from .models import Gender, SocialIdentity, User

PHONE_RE = re.compile(r"^\+?[0-9 ()\-]{6,32}$")


def _validate_birth_date(value):
    if value is None:
        return value
    if value > date.today():
        raise serializers.ValidationError("La fecha de nacimiento no puede ser futura.")
    if value.year < 1900:
        raise serializers.ValidationError("Fecha de nacimiento inválida.")
    return value


def _validate_phone(value):
    if value and not PHONE_RE.match(value):
        raise serializers.ValidationError("Teléfono inválido. Usá solo dígitos, espacios, +, - y paréntesis.")
    return value


class UserSerializer(serializers.ModelSerializer):
    """The authenticated user's own account. Never includes the password hash."""

    has_password = serializers.SerializerMethodField()
    linked_providers = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "phone", "gender", "birth_date",
            "date_joined", "has_password", "linked_providers", "discoverable", "is_coach",
        ]
        # Changing username/email needs its own verified flow; not editable here yet.
        read_only_fields = ["id", "username", "email", "date_joined"]

    def get_has_password(self, user) -> bool:
        return user.has_usable_password()

    def get_linked_providers(self, user) -> list[str]:
        return sorted(user.social_identities.values_list("provider", flat=True))

    def validate_birth_date(self, value):
        return _validate_birth_date(value)

    def validate_phone(self, value):
        return _validate_phone(value)


class DiscoverablePersonSerializer(serializers.ModelSerializer):
    """What people search shows about someone: only their username, the coach label and how many routines they published."""

    routines_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = ["username", "is_coach", "routines_count"]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    # Required
    username = serializers.CharField(min_length=3, max_length=30, validators=[UnicodeUsernameValidator()])
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False, style={"input_type": "password"})
    # Optional — never required to create an account
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    gender = serializers.ChoiceField(choices=Gender.choices, required=False, allow_blank=True)
    birth_date = serializers.DateField(required=False, allow_null=True)

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ese nombre de usuario ya está en uso.", code="username_taken")
        return value

    def validate_email(self, value):
        value = normalize_email(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe una cuenta con ese email.", code="email_taken")
        return value

    def validate_phone(self, value):
        return _validate_phone(value)

    def validate_birth_date(self, value):
        return _validate_birth_date(value)

    def validate(self, attrs):
        candidate = User(username=attrs["username"], email=attrs["email"], first_name=attrs.get("first_name", ""))
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class LoginSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254, help_text="Username o email")
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class AuthResponseSerializer(TokenPairSerializer):
    user = UserSerializer()


class GoogleAuthResponseSerializer(AuthResponseSerializer):
    created = serializers.BooleanField(help_text="true si se creó una cuenta nueva")


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class GoogleTokenSerializer(serializers.Serializer):
    id_token = serializers.CharField(max_length=4096)


class PasswordConfirmSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})


class DeleteAccountSerializer(serializers.Serializer):
    """The password, or — for accounts created with Google that never set one — the username typed out."""

    password = serializers.CharField(max_length=128, trim_whitespace=False, required=False, allow_blank=True)
    confirm_username = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if user.has_usable_password():
            if not user.check_password(attrs.get("password", "")):
                raise serializers.ValidationError({"password": ["Contraseña incorrecta."]})
        elif attrs.get("confirm_username", "").strip().lower() != user.username.lower():
            raise serializers.ValidationError({"confirm_username": ["Escribí tu nombre de usuario para confirmar."]})
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    # Optional only for accounts without a usable password (created with Google).
    current_password = serializers.CharField(max_length=128, trim_whitespace=False, required=False, allow_blank=True)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user
        if user.has_usable_password() and not user.check_password(attrs.get("current_password", "")):
            raise serializers.ValidationError({"current_password": ["La contraseña actual es incorrecta."]})
        try:
            password_validation.validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        return attrs


class SocialIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialIdentity
        fields = ["provider", "email_at_link", "created_at"]
