import logging
import secrets

from django.contrib.auth import authenticate
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.tokens import RefreshToken

from . import google
from .models import SocialIdentity, User
from .serializers import (
    AuthResponseSerializer,
    ChangePasswordSerializer,
    GoogleAuthResponseSerializer,
    GoogleTokenSerializer,
    LoginSerializer,
    PasswordConfirmSerializer,
    RefreshSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    UserSerializer,
)
from .throttles import AuthIPThrottle, LoginIdentifierThrottle
from .tokens import handle_refresh_reuse, is_blacklisted, issue_token_pair, revoke_all_sessions

logger = logging.getLogger("gymbo.auth")


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class ServiceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "service_unavailable"


def _auth_response(user, **extra) -> dict:
    return {**issue_token_pair(user), "user": UserSerializer(user).data, **extra}


class PublicAuthView(APIView):
    """Unauthenticated auth endpoint: throttled per IP; failures answer 401 (not 403)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthIPThrottle]

    def get_authenticate_header(self, request):
        return 'Bearer realm="api"'


# ── Registration and password login ─────────────────────────────────────────

class RegisterView(PublicAuthView):
    @extend_schema(request=RegisterSerializer, responses={201: AuthResponseSerializer}, tags=["auth"])
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError:
            # Lost a race against a concurrent registration with the same username/email.
            raise Conflict("Ese nombre de usuario o email ya está en uso.", code="account_exists")
        logger.info("user_registered user_id=%s", user.pk)
        return Response(_auth_response(user), status=status.HTTP_201_CREATED)


class LoginView(PublicAuthView):
    throttle_classes = [AuthIPThrottle, LoginIdentifierThrottle]

    @extend_schema(
        request=LoginSerializer,
        responses={200: AuthResponseSerializer, 401: OpenApiResponse(description="invalid_credentials")},
        tags=["auth"],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login = serializer.validated_data["login"].strip()
        password = serializer.validated_data["password"]

        username = login
        if "@" in login:
            match = User.objects.filter(email__iexact=login).only("username").first()
            if match is None:
                # Spend the same hashing time as a real check, so response time
                # does not reveal whether the email is registered.
                User().set_password(password)
                raise AuthenticationFailed("Usuario/email o contraseña incorrectos.", code="invalid_credentials")
            username = match.username

        user = authenticate(request, username=username, password=password)
        if user is None:  # wrong password, unknown username or inactive account: same answer
            raise AuthenticationFailed("Usuario/email o contraseña incorrectos.", code="invalid_credentials")
        return Response(_auth_response(user))


# ── Token refresh / logout ──────────────────────────────────────────────────

class RefreshView(PublicAuthView):
    @extend_schema(
        request=RefreshSerializer,
        responses={200: TokenPairSerializer, 401: OpenApiResponse(description="token_invalid | token_reused")},
        tags=["auth"],
    )
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except (TokenError, InvalidToken, AuthenticationFailed, ObjectDoesNotExist):
            self._detect_reuse(request.data.get("refresh"))
            raise AuthenticationFailed("Sesión inválida o vencida. Iniciá sesión de nuevo.", code="token_invalid")
        return Response(serializer.validated_data)

    @staticmethod
    def _detect_reuse(raw):
        if not isinstance(raw, str) or not raw:
            return
        try:
            # Valid signature and not expired, but rejected: if it is because it
            # was already rotated, someone is replaying a copied token.
            payload = token_backend.decode(raw, verify=True)
        except Exception:
            return
        if payload.get(jwt_settings.TOKEN_TYPE_CLAIM) == "refresh" and is_blacklisted(payload.get("jti", "")):
            handle_refresh_reuse(payload.get(jwt_settings.USER_ID_CLAIM))
            raise AuthenticationFailed(
                "Detectamos un uso indebido de tu sesión y la cerramos en todos tus dispositivos.", code="token_reused"
            )


class LogoutView(APIView):
    """Revokes one refresh token (this device). Idempotent."""

    @extend_schema(request=RefreshSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["refresh"]
        try:
            payload = token_backend.decode(raw, verify=True)
        except Exception:
            raise AuthenticationFailed("Token de sesión inválido.", code="token_invalid")
        if str(payload.get(jwt_settings.USER_ID_CLAIM)) != str(request.user.pk):
            raise PermissionDenied("Ese token no pertenece a tu sesión.")
        try:
            RefreshToken(raw).blacklist()
        except TokenError:
            pass  # already revoked: logging out twice is fine
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    """Revokes every session of the account, on every device, immediately."""

    @extend_schema(request=None, responses={204: None}, tags=["auth"])
    def post(self, request):
        revoke_all_sessions(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Google Sign-In ──────────────────────────────────────────────────────────

def _verify_google(raw_token: str) -> google.GoogleClaims:
    try:
        return google.verify_google_id_token(raw_token)
    except google.GoogleNotConfigured:
        raise ServiceUnavailable("El inicio de sesión con Google no está configurado.", code="google_not_configured")
    except google.GoogleUnavailable:
        raise ServiceUnavailable("No pudimos contactar a Google. Probá de nuevo.", code="google_unavailable")
    except google.InvalidGoogleToken:
        raise AuthenticationFailed("El token de Google no es válido.", code="invalid_google_token")


def _unique_username_from(email: str) -> str:
    base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch in "._")[:20] or "usuario"
    if len(base) < 3:
        base = f"{base}_gym"
    candidate = base
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}{secrets.randbelow(10000):04d}"
    return candidate


class GoogleLoginView(PublicAuthView):
    """
    Signs in with a Google ID token. Creates an account on first use, unless a
    password account already uses that email: it is never linked automatically
    (the owner must sign in and link Google from their profile).
    """

    @extend_schema(
        request=GoogleTokenSerializer,
        responses={
            200: GoogleAuthResponseSerializer,
            201: GoogleAuthResponseSerializer,
            401: OpenApiResponse(description="invalid_google_token"),
            409: OpenApiResponse(description="google_email_in_use"),
            503: OpenApiResponse(description="google_not_configured | google_unavailable"),
        },
        tags=["auth"],
    )
    def post(self, request):
        serializer = GoogleTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claims = _verify_google(serializer.validated_data["id_token"])

        identity = SocialIdentity.objects.select_related("user").filter(
            provider=SocialIdentity.Provider.GOOGLE, subject=claims.subject
        ).first()
        if identity is not None:
            if not identity.user.is_active:
                raise AuthenticationFailed("Esta cuenta está desactivada.", code="account_inactive")
            return Response(_auth_response(identity.user, created=False))

        if User.objects.filter(email__iexact=claims.email).exists():
            raise Conflict(
                "Ya existe una cuenta con ese email. Iniciá sesión con tu contraseña y vinculá Google desde tu perfil.",
                code="google_email_in_use",
            )
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=_unique_username_from(claims.email),
                    email=claims.email,
                    password=None,  # unusable password: Google is the only way in until one is set
                    first_name=claims.given_name,
                    last_name=claims.family_name,
                )
                SocialIdentity.objects.create(
                    user=user, provider=SocialIdentity.Provider.GOOGLE, subject=claims.subject, email_at_link=claims.email
                )
        except IntegrityError:
            raise Conflict("La cuenta se está creando en otra solicitud. Probá de nuevo.", code="account_exists")
        logger.info("user_registered_google user_id=%s", user.pk)
        return Response(_auth_response(user, created=True), status=status.HTTP_201_CREATED)


class GoogleLinkView(APIView):
    @extend_schema(request=GoogleTokenSerializer, responses={200: UserSerializer}, tags=["auth"])
    def post(self, request):
        serializer = GoogleTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claims = _verify_google(serializer.validated_data["id_token"])
        user = request.user

        existing = SocialIdentity.objects.filter(provider=SocialIdentity.Provider.GOOGLE, subject=claims.subject).first()
        if existing is not None:
            if existing.user_id != user.pk:
                raise Conflict("Esa cuenta de Google ya está vinculada a otro usuario.", code="google_identity_in_use")
            return Response(UserSerializer(user).data)  # already linked to me: idempotent
        if user.social_identities.filter(provider=SocialIdentity.Provider.GOOGLE).exists():
            raise Conflict("Ya tenés otra cuenta de Google vinculada. Desvinculala primero.", code="google_already_linked")
        try:
            SocialIdentity.objects.create(
                user=user, provider=SocialIdentity.Provider.GOOGLE, subject=claims.subject, email_at_link=claims.email
            )
        except IntegrityError:
            raise Conflict("Esa cuenta de Google ya está vinculada.", code="google_identity_in_use")
        return Response(UserSerializer(user).data)


class GoogleUnlinkView(APIView):
    """Requires the account password, and refuses if that would leave the account with no way to sign in."""

    @extend_schema(request=PasswordConfirmSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        user = request.user
        if not user.has_usable_password():
            raise PermissionDenied(
                "Definí una contraseña antes de desvincular Google, o no podrías volver a entrar.", code="password_required"
            )
        serializer = PasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not user.check_password(serializer.validated_data["password"]):
            raise PermissionDenied("Contraseña incorrecta.", code="invalid_password")
        deleted, _ = user.social_identities.filter(provider=SocialIdentity.Provider.GOOGLE).delete()
        if not deleted:
            return Response({"error": {"code": "not_linked", "message": "No hay una cuenta de Google vinculada."}}, status=404)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── The authenticated user's own account ────────────────────────────────────

class MeView(generics.RetrieveUpdateAPIView):
    """Only ever the requester's own account — there is no user id in the URL to tamper with."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    @extend_schema(tags=["users"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(tags=["users"])
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)


class ChangePasswordView(APIView):
    """Sets a new password and closes every other session; returns a fresh token pair for this device."""

    @extend_schema(request=ChangePasswordSerializer, responses={200: TokenPairSerializer}, tags=["users"])
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        revoke_all_sessions(user)
        return Response(issue_token_pair(user))
