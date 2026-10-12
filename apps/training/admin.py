from django.contrib import admin

from .models import BodyMeasurement, Exercise, Routine, RoutineVersion, WorkoutSession


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "is_builtin", "owner", "visibility", "moderation_status", "deleted_at")
    list_filter = ("is_builtin", "visibility", "moderation_status", "primary_muscle")
    search_fields = ("name", "slug")


class RoutineVersionInline(admin.TabularInline):
    model = RoutineVersion
    fields = ("number", "origin", "created_by", "created_at")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Routine)
class RoutineAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_active", "visibility", "moderation_status", "updated_at")
    list_filter = ("visibility", "moderation_status", "is_active")
    inlines = [RoutineVersionInline]


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ("owner", "scheduled_date", "status")
    list_filter = ("status",)


admin.site.register(BodyMeasurement)
