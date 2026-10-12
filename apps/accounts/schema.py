from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class GymboJWTScheme(SimpleJWTScheme):
    """Documents GymboJWTAuthentication as the standard Bearer JWT scheme in OpenAPI."""

    target_class = "apps.accounts.authentication.GymboJWTAuthentication"
