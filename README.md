# GymboAPI — backend de Gymbo (Django + PostgreSQL)

API REST de [Gymbo](../Gymbo), la app móvil (React Native + Expo). Es un proyecto y un repositorio independientes de la app. La app **nunca** se conecta a PostgreSQL: todo pasa por esta API. El backend y la base de datos se pueden desplegar en servidores distintos.

> **Estado: Etapa 2.** Existen los modelos de cuentas y entrenamiento, las migraciones, el *health check* y la documentación OpenAPI. Los endpoints de autenticación y de datos llegan en las Etapas 3 y 4.

## Versiones

| Componente | Versión |
|---|---|
| Python | 3.12 (`.python-version`) |
| Django | 5.2 LTS (5.2.18) |
| Django REST Framework | 3.18 |
| djangorestframework-simplejwt | 5.5 |
| drf-spectacular | 0.30 |
| psycopg | 3.3 |
| argon2-cffi | 25.1 |
| PostgreSQL | 17 en desarrollo; se requiere ≥ 14 |

Las versiones exactas de todas las dependencias, incluidas las transitivas, están fijadas en `uv.lock`.

## Puesta en marcha en desarrollo

Requisitos: [`uv`](https://docs.astral.sh/uv/) y Docker.

```bash
cp .env.example .env            # y cambiá DJANGO_SECRET_KEY
docker compose up -d            # PostgreSQL de desarrollo en 127.0.0.1:5434
uv sync                         # crea .venv con Python 3.12 e instala dependencias
uv run python manage.py migrate # crea el esquema y carga los 62 ejercicios integrados
uv run python manage.py createsuperuser
uv run python manage.py runserver 0.0.0.0:8000
```

- Health check: `GET http://localhost:8000/api/v1/health/`
- OpenAPI: `/api/v1/schema/` (YAML) y `/api/v1/docs/` (Swagger UI). En producción solo los ve el staff logueado en `/admin/`.
- Admin de Django: `/admin/`

Desde el emulador de Android, el host es `10.0.2.2`. En la app: `EXPO_PUBLIC_API_URL=http://10.0.2.2:8000`.

### Tests

```bash
uv run pytest
```

Los tests corren contra **PostgreSQL real**: pytest-django crea y destruye la base `test_gymbo`. No se usa SQLite porque varias invariantes dependen de PostgreSQL, como la unicidad sin distinguir mayúsculas e índices únicos parciales.

### Ejercicios integrados

`training/data/builtin_exercises.json` se **genera** desde la librería de la app (`Gymbo/src/constants/exerciseLibrary.ts`), que sigue siendo la fuente de verdad. No lo edites a mano. Con ambos repos clonados uno al lado del otro (`Documentos/Gymbo` y `Documentos/GymboAPI`):

```bash
# Desde Gymbo/
node scripts/export-exercise-library.js --check   # ¿están sincronizados? (sale con error si no)
node scripts/export-exercise-library.js           # regenera ../GymboAPI/training/data/builtin_exercises.json
# Desde GymboAPI/
uv run python manage.py load_builtin_exercises     # idempotente, nunca borra
```

Si las carpetas están en otro lugar, pasá la ruta de salida: `node scripts/export-exercise-library.js /ruta/a/GymboAPI/training/data/builtin_exercises.json`.

## Variables de entorno

Ver `.env.example`; están todas documentadas ahí. Al arrancar, el backend valida la configuración:

- **Todos los entornos:** `DATABASE_URL` debe apuntar a PostgreSQL y `DJANGO_ENV` debe ser `development`, `test` o `production`.
- **Producción:** el backend **no arranca** si `DJANGO_DEBUG=true`, si `DJANGO_SECRET_KEY` mide menos de 50 caracteres o es un valor de ejemplo, si `DJANGO_ALLOWED_HOSTS` está vacío o contiene `*`, o si `DATABASE_SSLMODE` no es `require`, `verify-ca` o `verify-full`. Además se activan HTTPS obligatorio, HSTS y cookies seguras.

No se configura CORS porque no hay cliente web (decisión D4). Las apps nativas no lo necesitan.

## PostgreSQL externo (producción)

### Creación con privilegio mínimo

Se usan dos roles: uno **propietario** del esquema, que solo se usa para correr migraciones, y uno de **aplicación** con permisos DML y nada más.

```sql
-- Como administrador del servidor:
CREATE ROLE gymbo_owner LOGIN PASSWORD '<secreto-1>';
CREATE ROLE gymbo_app   LOGIN PASSWORD '<secreto-2>';
CREATE DATABASE gymbo OWNER gymbo_owner;
REVOKE ALL ON DATABASE gymbo FROM PUBLIC;
GRANT CONNECT ON DATABASE gymbo TO gymbo_app;

\c gymbo
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO gymbo_app;
-- Todo lo que cree gymbo_owner (las migraciones) queda accesible para la app:
ALTER DEFAULT PRIVILEGES FOR ROLE gymbo_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gymbo_app;
ALTER DEFAULT PRIVILEGES FOR ROLE gymbo_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO gymbo_app;
```

- El servicio web corre con `DATABASE_URL=postgres://gymbo_app:...@host:5432/gymbo`.
- Las migraciones corren como paso de despliegue con `DATABASE_URL=postgres://gymbo_owner:...@host:5432/gymbo uv run python manage.py migrate`.

### Conectividad y cifrado

- En `pg_hba.conf` (o el firewall del proveedor), permitir conexiones **solo** desde las IPs del backend, con `hostssl` y `scram-sha-256`. Nunca abrir `0.0.0.0/0`.
- `DATABASE_SSLMODE=verify-full` cuando el proveedor entrega certificado de CA (con `sslrootcert` en la URL si hace falta). Como mínimo, `require`.
- Cifrado en reposo: activarlo en el disco o volumen del servidor, o usar el que ofrezca el servicio gestionado.

### Migraciones

- Antes de aplicar en producción: `uv run python manage.py migrate --plan` para revisar, y un **backup previo**.
- Las migraciones de datos de este proyecto no borran datos. Por ejemplo, la reversión de la carga de ejercicios integrados es un *no-op*.
- Comprobado en desarrollo: `migrate training zero` seguido de `migrate` reconstruye el esquema y los datos de referencia sin errores.

### Backups y restauración

```bash
# Backup lógico diario (formato custom, comprimido), con el rol propietario:
pg_dump --format=custom --no-owner --file=gymbo-$(date +%F).dump "$OWNER_DATABASE_URL"

# Restauración de prueba en una base NUEVA (nunca sobre producción):
createdb gymbo_restore_test
pg_restore --no-owner --dbname=gymbo_restore_test gymbo-YYYY-MM-DD.dump
DATABASE_URL=postgres://.../gymbo_restore_test uv run python manage.py migrate --check
```

- Los backups se guardan cifrados (por ejemplo, con `age` o el cifrado del bucket), con acceso restringido y retención definida (sugerido: 7 diarios y 4 semanales).
- **La restauración se prueba al menos una vez por mes.** Un backup que nunca se restauró no cuenta como backup.
- Si el proveedor lo ofrece, conviene sumar PITR (WAL archiving).

### Mantenimiento

Autovacuum activo (es el valor por defecto). Hay que revisar periódicamente `pg_stat_user_tables` y actualizar las versiones menores de PostgreSQL.

## Despliegue del backend (resumen)

El procedimiento completo queda para la Etapa 9. Los puntos clave:

1. Servidor WSGI (gunicorn) detrás de un proxy con TLS (`DJANGO_BEHIND_PROXY=true`).
2. Variables de entorno inyectadas por la plataforma. **No se despliega ningún `.env`.**
3. Pasos de cada despliegue: backup → `migrate` (con el rol propietario) → `collectstatic` → reinicio.
4. Health check del balanceador: `/api/v1/health/`.

## Modelo de datos

Ver `Gymbo/docs/ETAPA-1-AUDITORIA.md` §4 (plan y decisiones, en el repo de la app). Garantías que impone la base de datos (no solo el código):

| Invariante | Mecanismo |
|---|---|
| Username y email únicos sin distinguir mayúsculas | `UniqueConstraint(Lower(...))` |
| Una identidad de Google pertenece a una sola cuenta | único (`provider`, `subject`) |
| Una sola rutina activa por persona | índice único parcial |
| Las versiones de rutina no se modifican | `save()` rechaza actualizaciones; número único por rutina |
| No se puede borrar una versión con sesiones registradas | FK `RESTRICT` |
| Un ejercicio borrado no rompe el historial | FK `SET_NULL` + snapshot en `ExerciseSession` |
| Una medición por (persona, fecha, tipo) | índice único parcial (excluye tombstones) |
| Rangos válidos de series, pesos, RIR y descansos | `CheckConstraint` |
| Una sesión no termina antes de empezar | `CheckConstraint` |

## Qué datos guarda el servidor

- **Cuenta:** username, email y hash Argon2 de la contraseña (nunca la contraseña). Opcionalmente: nombre, teléfono, género y fecha de nacimiento, solo si el usuario los carga.
- **Google:** solo el identificador estable (`sub`) y el email en el momento de vincular.
- **Entrenamiento:** solo lo que el usuario registrado decide sincronizar.
- **Lo que el servidor no recibe:** los datos de los invitados, y los recordatorios y la vibración del temporizador, que quedan solo en el dispositivo.
- **Logs:** no se registran cuerpos de solicitudes, contraseñas ni encabezados `Authorization`.
