# Shared by production.py and collectstatic.py so the build produces exactly
# the hashed, compressed files that production serves.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
