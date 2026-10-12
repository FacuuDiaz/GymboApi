from django.contrib import admin

from .models import ContentReport, RoutineShare, ShareLink


@admin.register(ContentReport)
class ContentReportAdmin(admin.ModelAdmin):
    """Moderation queue. Hiding/restoring content is done on the routine or exercise itself (moderation_status)."""

    list_display = ("kind", "object_id", "reason", "status", "created_at")
    list_filter = ("status", "kind", "reason")
    actions = ["mark_resolved", "mark_dismissed"]

    @admin.action(description="Marcar como resueltas")
    def mark_resolved(self, request, queryset):
        queryset.update(status=ContentReport.Status.RESOLVED)

    @admin.action(description="Descartar")
    def mark_dismissed(self, request, queryset):
        queryset.update(status=ContentReport.Status.DISMISSED)


@admin.register(ShareLink)
class ShareLinkAdmin(admin.ModelAdmin):
    list_display = ("routine", "token_hint", "role", "expires_at", "revoked_at", "uses")
    readonly_fields = ("token_hash", "token_hint")


admin.site.register(RoutineShare)
