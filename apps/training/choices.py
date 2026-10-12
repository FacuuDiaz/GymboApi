"""String choices mirroring src/types/enums.ts in the mobile app. Keep both in sync."""
from django.db import models


class MuscleGroup(models.TextChoices):
    CHEST = "chest", "Pecho"
    BACK = "back", "Espalda"
    SHOULDERS = "shoulders", "Hombros"
    BICEPS = "biceps", "Bíceps"
    TRICEPS = "triceps", "Tríceps"
    QUADS = "quads", "Cuádriceps"
    HAMSTRINGS = "hamstrings", "Femorales"
    GLUTES = "glutes", "Glúteos"
    CALVES = "calves", "Gemelos"
    ABS = "abs", "Abdominales"
    FOREARMS = "forearms", "Antebrazos"
    FULL_BODY = "full_body", "Cuerpo completo"


class EquipmentType(models.TextChoices):
    BARBELL = "barbell", "Barra"
    DUMBBELL = "dumbbell", "Mancuernas"
    MACHINE = "machine", "Máquina"
    CABLE = "cable", "Polea"
    BODYWEIGHT = "bodyweight", "Peso corporal"
    KETTLEBELL = "kettlebell", "Kettlebell"
    BAND = "band", "Bandas"


class ExerciseCategory(models.TextChoices):
    COMPOUND = "compound", "Compuesto"
    ISOLATION = "isolation", "Aislamiento"


class ExerciseModality(models.TextChoices):
    FREE_WEIGHT = "free_weight", "Peso libre"
    MACHINE = "machine", "Máquina"
    BODYWEIGHT = "bodyweight", "Peso corporal"


class TrainingLevel(models.TextChoices):
    BEGINNER = "beginner", "Principiante"
    INTERMEDIATE = "intermediate", "Intermedio"
    ADVANCED = "advanced", "Avanzado"


class RoutineGoal(models.TextChoices):
    HYPERTROPHY = "hypertrophy", "Hipertrofia"
    STRENGTH = "strength", "Fuerza"


class WeightUnit(models.TextChoices):
    KG = "kg", "kg"
    LB = "lb", "lb"


class ThemeName(models.TextChoices):
    DARK = "dark", "Oscuro"
    LIGHT = "light", "Claro"


class PlanningMode(models.TextChoices):
    REPEAT = "repeat", "Repetir semana"
    INDIVIDUAL = "individual", "Semanas individuales"


class SessionStatus(models.TextChoices):
    ACTIVE = "active", "En curso"
    COMPLETED = "completed", "Completada"
    PARTIAL = "partial", "Parcial"
    SKIPPED = "skipped", "Omitida"


class Visibility(models.TextChoices):
    PRIVATE = "private", "Privada"
    SHARED = "shared", "Compartida con usuarios"
    LINK = "link", "Accesible por enlace"
    PUBLIC = "public", "Pública"


class ExerciseVisibility(models.TextChoices):
    PRIVATE = "private", "Privado"
    PUBLIC = "public", "Público"


class MeasurementKind(models.TextChoices):
    BODY_WEIGHT = "body_weight", "Peso corporal"


class MeasurementUnit(models.TextChoices):
    KG = "kg", "kg"
    LB = "lb", "lb"


class VersionOrigin(models.TextChoices):
    OWNER_EDIT = "owner_edit", "Edición del propietario"
    COACH_UPDATE = "coach_update", "Actualización del entrenador"
    COPY = "copy", "Copia"
    SHARED_EDIT = "shared_edit", "Edición de una persona invitada"
    MIGRATION = "migration", "Migración desde el dispositivo"
