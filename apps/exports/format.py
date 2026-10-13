"""
The Gymbo export format ("gymbo-export", version 1), independent of where the
data comes from. The app builds the very same document from a guest's local
data (src/features/export/services/ in the Gymbo repo) and turns it into the
same CSV tables with the same rules, so a file looks identical whether it was
produced on the phone or by this server. Change both sides together.

Everything here is pure: document in, text/bytes out.
"""
import io
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

FORMAT = "gymbo-export"
VERSION = 1
LB_PER_KG = 2.20462262185
LOGGED_STATUSES = ("completed", "partial")

ABOUT = {
    "descripcion": "Exportación de tus datos de Gymbo. Incluye solo tus datos: nunca contraseñas, tokens ni datos de otras personas.",
    "fechas": "Las fechas de calendario (scheduled_date, date, start_date, week_start) son locales, tal como las viviste, en formato AAAA-MM-DD. Los instantes (started_at, finished_at, generated_at...) están en UTC, formato ISO 8601 (AAAA-MM-DDTHH:MM:SSZ).",
    "unidades": "Cada sesión indica la unidad (kg o lb) en la que se cargaron sus pesos. El progreso semanal está convertido a la unidad de tus preferencias (1 kg = 2.20462262185 lb). Los descansos y duraciones están en segundos.",
    "vacios": "Un campo vacío (o null en JSON) significa 'no registrado', que no es lo mismo que 0.",
    "series": "Una serie cuenta para volumen y progreso solo si está completada y tiene peso y repeticiones. Volumen = peso × repeticiones.",
    "sesiones": "Solo las sesiones completas o parciales cuentan en el progreso semanal. Una semana empieza el lunes.",
    "rutinas": "plan es la versión actual de cada rutina. versions es su historial de versiones (solo en exportaciones de una cuenta; en el dispositivo no hay historial).",
    "csv": "UTF-8, separador coma, punto decimal, booleanos true/false. Cada CSV es una tabla de datos.json aplanada.",
    "archivos": {
        "datos.json": "Todo, en un solo documento.",
        "sesiones.csv": "Una fila por sesión.",
        "series.csv": "Una fila por serie (peso, repeticiones, RIR, descanso).",
        "rutinas.csv": "Una fila por ejercicio planificado de cada rutina.",
        "ejercicios.csv": "Tus ejercicios personalizados.",
        "mediciones.csv": "Peso corporal y otras mediciones.",
        "progreso_semanal.csv": "Resumen por semana.",
    },
}

TABLES = ("sesiones", "series", "rutinas", "ejercicios", "mediciones", "progreso_semanal")


# ── Value formatting (identical rules in the app) ───────────────────────────

def timestamp(value: datetime | None) -> str | None:
    """UTC instant with second precision: 2026-10-12T21:05:00Z."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def number(value) -> str:
    """At most 2 decimals, no trailing zeros: 82.5, 100, 0.33. None → ''."""
    if value is None:
        return ""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")  # same as JS toFixed(2)
    return "0" if text in ("", "-0") else text


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return number(value)
    return str(value)


def _quote(text: str) -> str:
    if any(c in text for c in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def to_csv(header: list[str], rows: list[list]) -> str:
    """RFC 4180 with a UTF-8 BOM so spreadsheets detect the encoding."""
    lines = [",".join(_quote(h) for h in header)]
    lines += [",".join(_quote(cell(v)) for v in row) for row in rows]
    return "﻿" + "\r\n".join(lines) + "\r\n"


# ── Derived data ─────────────────────────────────────────────────────────────

def counts(s: dict) -> bool:
    return bool(s["completed"] and s["weight"] and s["reps"])


def convert(weight: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return weight
    return weight * LB_PER_KG if to_unit == "lb" else weight / LB_PER_KG


def weekly_progress(sessions: list[dict], unit: str) -> list[dict]:
    """Only weeks with at least one logged session, oldest first."""
    weeks: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "completed_sets": 0, "volume": 0.0, "duration_seconds": 0})
    for session in sessions:
        if session["status"] not in LOGGED_STATUSES:
            continue
        day = date.fromisoformat(session["scheduled_date"])
        week = weeks[(day - timedelta(days=day.weekday())).isoformat()]
        week["sessions"] += 1
        week["duration_seconds"] += session["duration_seconds"] or 0
        for exercise in session["exercises"]:
            for s in exercise["sets"]:
                if counts(s):
                    week["completed_sets"] += 1
                    week["volume"] += convert(s["weight"], session["unit"], unit) * s["reps"]
    return [
        {"week_start": key, **{**value, "volume": float(number(value["volume"]))}, "unit": unit}
        for key, value in sorted(weeks.items())
    ]


def tables(doc: dict) -> dict[str, str]:
    """Every CSV of the export, keyed by table name."""
    sessions = doc["sessions"]
    out = {}

    rows = []
    for s in sessions:
        counted = [x for e in s["exercises"] for x in e["sets"] if counts(x)]
        rows.append([
            s["id"], s["scheduled_date"], s["original_scheduled_date"], s["status"], s["routine_name"], s["started_at"],
            s["finished_at"], s["duration_seconds"], s["unit"], len(s["exercises"]), len(counted),
            sum(x["weight"] * x["reps"] for x in counted),
        ])
    out["sesiones"] = to_csv(
        ["session_id", "date", "original_date", "status", "routine_name", "started_at", "finished_at", "duration_seconds",
         "unit", "exercises", "completed_sets", "volume"], rows)

    rows = [
        [s["id"], s["scheduled_date"], s["routine_name"], e["order"], e["exercise_id"], e["exercise_name"], x["set_number"],
         x["weight"], s["unit"], x["reps"], x["rir"], x["rest_seconds"], x["completed"]]
        for s in sessions for e in s["exercises"] for x in e["sets"]
    ]
    out["series"] = to_csv(
        ["session_id", "date", "routine_name", "exercise_order", "exercise_id", "exercise_name", "set_number", "weight",
         "unit", "reps", "rir", "rest_seconds", "completed"], rows)

    rows = [
        [r["id"], r["name"], r["goal"], r["is_active"], r["archived_at"], w["index"] + 1, d["weekday"], d["name"],
         d["is_rest_day"], p["order"], p["exercise_id"], p.get("exercise_name", ""), p["sets"], p["reps_min"], p["reps_max"],
         p.get("target_weight")]
        for r in doc["routines"] for w in r["plan"]["weeks"] for d in w["days"] for p in d["exercises"]
    ]
    out["rutinas"] = to_csv(
        ["routine_id", "routine_name", "goal", "is_active", "archived_at", "week", "weekday", "day_name", "rest_day",
         "exercise_order", "exercise_id", "exercise_name", "sets", "reps_min", "reps_max", "target_weight"], rows)

    out["ejercicios"] = to_csv(
        ["exercise_id", "name", "primary_muscle", "secondary_muscles", "equipment", "category", "modality"],
        [[e["id"], e["name"], e["primary_muscle"], "|".join(e["secondary_muscles"]), e["equipment"], e["category"], e["modality"]]
         for e in doc["exercises"]])

    out["mediciones"] = to_csv(["date", "kind", "value", "unit"], [[m["date"], m["kind"], m["value"], m["unit"]] for m in doc["measurements"]])

    out["progreso_semanal"] = to_csv(
        ["week_start", "sessions", "completed_sets", "volume", "unit", "duration_seconds"],
        [[w["week_start"], w["sessions"], w["completed_sets"], w["volume"], w["unit"], w["duration_seconds"]] for w in doc["weekly_progress"]])
    return out


def readme(doc: dict) -> str:
    about = doc["about"]
    lines = ["GYMBO — EXPORTACIÓN DE DATOS", f"Formato {doc['format']} v{doc['version']} · generado {doc['generated_at']} · origen: {doc['source']}", ""]
    for key, value in about.items():
        if key == "archivos":
            lines.append("ARCHIVOS")
            lines += [f"  {name}: {text}" for name, text in value.items()]
        else:
            lines.append(f"{key.upper()}: {value}")
        lines.append("")
    return "\r\n".join(lines)


def to_zip(doc: dict, json_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("datos.json", json_text)
        archive.writestr("LEEME.txt", readme(doc))
        for name, text in tables(doc).items():
            archive.writestr(f"{name}.csv", text)
    return buffer.getvalue()
