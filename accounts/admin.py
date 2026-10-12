from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import SocialIdentity, TrainingPreferences, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("Datos opcionales", {"fields": ("phone", "gender", "birth_date")}),)
    list_display = ("username", "email", "is_active", "is_staff", "date_joined")


@admin.register(SocialIdentity)
class SocialIdentityAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "created_at")
    # The subject is an identifier, not a secret, but there is no reason to edit it by hand.
    readonly_fields = ("provider", "subject", "email_at_link")


admin.site.register(TrainingPreferences)
