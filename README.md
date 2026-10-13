# GymboAPI — backend de Gymbo

API REST de [Gymbo](../Gymbo) (app móvil en React Native + Expo) construida con Django, Django REST Framework y PostgreSQL. **Todo corre en Docker:** no hace falta instalar Python, Django ni PostgreSQL en tu máquina.

La app móvil nunca se conecta a PostgreSQL: solo habla con esta API.

## Estado

| Área | Estado |
|---|---|
| Infraestructura Docker (dev y prod), healthchecks, backups | ✅ Implementado y probado |
| Modelos de cuentas y entrenamiento, migraciones | ✅ Implementado y probado |
| Registro, login (usuario o email), tokens, logout, revocación | ✅ Implementado y probado |
| Google Sign-In (verificación OIDC, vincular y desvincular) | ✅ Implementado y probado. ⚠️ Requiere cargar los Client IDs (`GOOGLE_OAUTH_CLIENT_IDS`) |
| Perfil propio (`/users/me/`), cambio de contraseña | ✅ Implementado y probado |
| Ejercicios, rutinas y versiones, sesiones, mediciones, preferencias y progreso (API) | ✅ Implementado y probado |
| Sincronización y migración invitado → cuenta (en la app, contra esta API) | ✅ Implementado y probado |
| Entrenadores y alumnos (invitaciones, permisos, grupos, asignaciones con actualización automática) | ✅ Implementado y probado |
| Compartir (personas y enlaces), catálogo comunitario, copias, denuncias | ✅ Implementado y probado |
| Exportación de datos (JSON, CSV, ZIP) y eliminación de cuenta | ✅ Implementado y probado |

El plan completo y las decisiones tomadas están en `Gymbo/docs/ETAPA-1-AUDITORIA.md`.

## Stack y versiones

| Componente | Versión |
|---|---|
| Python | 3.12.15 (imagen `python:3.12.15-slim-bookworm`) |
| Django | 5.2 LTS |
| Django REST Framework | 3.18 |
| SimpleJWT | 5.5 |
| drf-spectacular (OpenAPI) | 0.30 |
| psycopg | 3.3 |
| Gunicorn | 26.2 |
| WhiteNoise | 6.12 |
| google-auth | 2.61 |
| PostgreSQL | 17 (`postgres:17-alpine`) |

Las versiones exactas de todas las dependencias, incluidas las transitivas y con hashes, están en `uv.lock`. La imagen las instala con `uv sync --frozen`, así que cada build es reproducible.

## Estructura

```
config/            settings (base, development, test, production), urls, wsgi/asgi
apps/
  common/          modelos base, errores, paginación, healthchecks, config, logging
  accounts/        usuario, identidades Google, preferencias, autenticación y tokens
  training/        ejercicios, rutinas y versiones, sesiones, series, mediciones
  coaching/        entrenadores, alumnos, permisos, asignaciones
  sharing/         compartir con personas y por enlace, comunidad, denuncias
  exports/         exportación de datos (formato gymbo-export v1)
docker/            entrypoint, healthcheck, gunicorn, init de PostgreSQL (rol de mínimo privilegio)
scripts/           backup.sh, restore.sh
compose.yaml       desarrollo
compose.prod.yaml  producción
compose.db-port.yaml  (opcional) expone PostgreSQL en 127.0.0.1 para herramientas gráficas
```

La sincronización no necesita una app propia: usa los endpoints de `training` con `updated_since` y `base_version`.

---

## Desarrollo

### Primera ejecución

```bash
cp .env.example .env
# Editá .env: cambiá DJANGO_SECRET_KEY, POSTGRES_PASSWORD y POSTGRES_SUPERUSER_PASSWORD.
docker compose up --build -d
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

- API: http://localhost:8000/api/v1/
- Documentación (Swagger): http://localhost:8000/api/v1/docs/ y esquema OpenAPI en `/api/v1/schema/`
- Salud: http://localhost:8000/health/live/ y http://localhost:8000/health/ready/
- Admin: http://localhost:8000/admin/
- Estado de los contenedores: `docker compose ps` (ambos deben decir `healthy`)

**Desde la app:**
- **Emulador de Android:** `EXPO_PUBLIC_API_URL=http://10.0.2.2:8000`.
- **Teléfono físico en tu red:**
  1. Poné `API_BIND=0.0.0.0` en `.env`.
  2. Agregá tu IP de LAN a `DJANGO_ALLOWED_HOSTS`.
  3. Usá `http://<tu-ip>:8000` como URL de la API.

### Día a día

```bash
docker compose up -d                 # levantar
docker compose logs -f api           # ver logs
docker compose exec api pytest       # tests
docker compose down                  # bajar (los datos se conservan)
```

El código se monta en el contenedor y `runserver` se recarga solo cuando editás un archivo. Las dependencias viven en `/opt/venv` dentro de la imagen, así que el montaje no las tapa.

**Para agregar una dependencia:**

```bash
docker compose run --rm --no-deps api uv add <paquete>
docker compose up --build -d
```

### Atajos opcionales (`make`)

`make up | build | down | logs | migrate | makemigrations | test | check | shell | dbshell | createsuperuser | backup | restore FILE=...`

El Makefile es opcional: cada objetivo es un comando de `docker compose` de los que figuran en este README.

### Detener, eliminar y borrar datos

| Comando | Qué hace | ¿Se pierden datos? |
|---|---|---|
| `docker compose stop` | Detiene los contenedores sin eliminarlos | No |
| `docker compose down` | Elimina contenedores y red | **No:** el volumen `pgdata` se conserva |
| `docker compose up --build` | Reconstruye la imagen y recrea contenedores | No |
| `docker compose down -v` | Elimina contenedores **y volúmenes** | ⚠️ **Sí: borra la base de datos entera.** Hacé un backup antes. |

Comprobado: un usuario creado antes de `down` + `up` sigue existiendo después.

### Comandos administrativos

```bash
docker compose exec api python manage.py check
docker compose exec api python manage.py shell
docker compose exec api python manage.py showmigrations
docker compose exec api python manage.py load_builtin_exercises   # idempotente, nunca borra
docker compose exec db sh -c 'psql -U "$APP_DB_USER" -d "$POSTGRES_DB"'     # psql como rol de la app
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'   # psql como superusuario (administración)
```

**PostgreSQL desde una herramienta gráfica** (solo en desarrollo, a pedido): `docker compose -f compose.yaml -f compose.db-port.yaml up -d` lo publica en `127.0.0.1:5434`.

### Tests

```bash
docker compose exec api pytest
```

- **Aislamiento:** los tests usan `config.settings.test`, forzado con `--ds` en `pyproject.toml`, y una base separada `test_<POSTGRES_DB>` que pytest-django crea y destruye. No tocan los datos de desarrollo; un test verifica las dos cosas.
- **PostgreSQL real:** varias invariantes dependen del motor, como la unicidad sin distinguir mayúsculas y los índices únicos parciales.
- **Google:** la verificación de tokens se prueba con firmas RS256 reales generadas en el test, sin simular la verificación.

### Ejercicios integrados

`apps/training/data/builtin_exercises.json` se **genera** a partir de la librería de la app (`Gymbo/src/constants/exerciseLibrary.ts`), que es la fuente de verdad. Desde la carpeta `Gymbo`:

```bash
node scripts/export-exercise-library.js --check   # ¿están sincronizados?
node scripts/export-exercise-library.js           # regenera el JSON en ../GymboAPI
```

Después, `docker compose exec api python manage.py load_builtin_exercises`.

---

## Configuración

Todas las variables están documentadas en `.env.example`. Resumen:

- **Settings:** `DJANGO_SETTINGS_MODULE` elige el entorno: `config.settings.development` o `config.settings.production`. Los tests usan siempre `config.settings.test`.
- **Base de datos:** se configura con `DATABASE_URL` **o** con `POSTGRES_HOST`/`POSTGRES_*`, nunca con las dos. Si están las dos, el backend no arranca, para evitar apuntar a la base equivocada. En Compose el host es `db`, el nombre del servicio, nunca `localhost`.
- **Variables faltantes:** el arranque falla con un mensaje que dice qué falta.
- **`EXPO_PUBLIC_API_URL`:** es una variable de la app, y su contenido es **público** porque queda dentro de la app compilada. Nunca pongas ahí valores del backend.

### PostgreSQL en Compose: roles

| Rol | Uso |
|---|---|
| `POSTGRES_SUPERUSER` | Superusuario del contenedor. Solo para administración (backups, `psql`). **La API nunca lo recibe:** `compose.yaml` le pasa a la API una lista explícita de variables, no el `.env` completo. |
| `POSTGRES_USER` (`gymbo_app`) | Rol con el que conecta la API. Es dueño de la base, para poder migrar, pero **no** es superusuario. Lo crea `docker/postgres/initdb/01-create-app-role.sh` la primera vez que se inicializa el volumen. |

En desarrollo, `POSTGRES_APP_CREATEDB=true` le permite crear la base de tests. En producción siempre es `false`.

> El script de inicialización solo corre cuando el volumen está vacío. Si cambiás `POSTGRES_USER` o `POSTGRES_PASSWORD` después, tenés que modificar el rol a mano con `ALTER ROLE`; recrear el contenedor no alcanza.

---

## API de autenticación

Todas bajo `/api/v1/`. Los errores siempre tienen la forma `{"error": {"code", "message", "fields"?}}`.

| Endpoint | Auth | Descripción |
|---|---|---|
| `POST auth/register/` | — | `username`, `email` y `password` obligatorios. Opcionales: `first_name`, `last_name`, `phone`, `gender`, `birth_date`. Devuelve `{access, refresh, user}`. |
| `POST auth/login/` | — | `{login, password}`; `login` puede ser el username o el email. Ante un fallo, la respuesta es la misma sea cual sea la causa (`invalid_credentials`). |
| `POST auth/refresh/` | — | `{refresh}` devuelve un par nuevo. El refresh usado queda revocado. |
| `POST auth/logout/` | — | `{refresh}`: revoca la sesión de este dispositivo. Tener el refresh token alcanza como autorización (como la revocación de OAuth, RFC 7009), así que no hace falta un access token. Es idempotente. |
| `POST auth/logout-all/` | Bearer | Revoca **todas** las sesiones, y los access tokens quedan inválidos en el momento. |
| `POST auth/google/` | — | `{id_token}` de Google. Responde 201 si crea una cuenta y 200 si ya existía. |
| `POST auth/google/link/` | Bearer | Vincula Google a la cuenta actual. |
| `POST auth/google/unlink/` | Bearer | `{password}`: desvincula Google. |
| `GET/PATCH users/me/` | Bearer | Cuenta propia. `username` y `email` son de solo lectura por ahora. |
| `POST users/me/password/` | Bearer | `{current_password, new_password}`: cierra las demás sesiones y devuelve un par nuevo. |

### Ciclo de vida de la sesión

1. **Inicio de sesión:** login, registro o Google devuelven un **access token** (10 min) y un **refresh token** (30 días).
2. **Guardado en la app:** los dos van en **SecureStore**, nunca en AsyncStorage. El access se manda como `Authorization: Bearer <access>`.
3. **Access vencido:** la API responde 401 y la app llama a `auth/refresh/`. La respuesta trae un **par nuevo**, que hay que guardar entero porque el refresh anterior deja de servir (rotación).
4. **Reuso detectado:** si un refresh ya rotado se vuelve a presentar, la API asume que fue copiado y **revoca todas las sesiones** de la cuenta (`token_reused`). Consecuencia práctica: si la app reintenta un refresh cuya respuesta se perdió por la red, el usuario tiene que volver a iniciar sesión. Es el precio de detectar robos.
5. **Logout:** revoca el refresh. El access de ese dispositivo sigue siendo válido hasta 10 minutos más, salvo que se use `logout-all` o se cambie la contraseña, que lo invalidan en el acto. La precisión de esa invalidación es de 1 segundo.
6. **Desactivación:** una cuenta desactivada no puede iniciar sesión ni renovar tokens.

### Google Sign-In

- **Flujo:** la app obtiene un *ID token* con el SDK nativo de Google y lo manda a `auth/google/`.
- **Verificación:** la API comprueba la firma RS256 contra las claves públicas de Google, el emisor (`accounts.google.com`), la audiencia (uno de **nuestros** client IDs), el vencimiento y `email_verified`.
- **Identificación:** las cuentas se identifican por el `sub` de Google, que es estable aunque el usuario cambie de email.
- **Sin vinculación automática:** si ya existe una cuenta con contraseña y el mismo email, Google **no** se vincula solo (responde 409 `google_email_in_use`). El dueño tiene que iniciar sesión y usar `auth/google/link/`.
- **Desvincular:** no se permite si dejaría la cuenta sin forma de entrar. Primero hay que definir una contraseña.
- **Sin secretos en la app:** el flujo móvil solo necesita *client IDs*, que son públicos.

**Configuración:**
1. En Google Cloud Console, creá los OAuth Client IDs para Android (package `com.anonymous.gymbo` más el SHA-1 del certificado de firma), iOS y web.
2. Cargalos separados por comas en `GOOGLE_OAUTH_CLIENT_IDS`.
3. Mientras esa variable esté vacía, `auth/google/` responde 503 `google_not_configured`.

### Protección contra fuerza bruta

- **Por IP:** `THROTTLE_AUTH` (10/min) en todos los endpoints públicos de autenticación.
- **Por cuenta:** `THROTTLE_LOGIN_IDENTIFIER` (5/min) por username o email intentado, aunque los intentos vengan de IPs distintas. La clave del caché es un hash, así que no guarda emails.
- **Contadores compartidos:** se guardan en la caché de PostgreSQL, que comparten todos los workers de Gunicorn, sin agregar Redis.
- **Detrás de un proxy:** configurá `DJANGO_NUM_PROXIES` para que la IP del cliente sea la real.

---

## API de entrenamiento

Todas bajo `/api/v1/` y con `Authorization: Bearer <access>`. Cada endpoint solo ve los datos **del usuario autenticado**: el registro de otra persona responde **404**, igual que uno que no existe.

| Endpoint | Métodos | Notas |
|---|---|---|
| `exercises/` | GET, POST | GET incluye los integrados, los propios y los publicados. Filtros: `mine`, `q`, `muscle`, `equipment`. |
| `exercises/{id}/` | GET, PATCH, DELETE | Solo los propios; los integrados responden 403. |
| `routines/` | GET, POST | Incluyen el `plan` vigente (`plan_version`). |
| `routines/{id}/` | GET, PATCH, DELETE | Si el plan cambia, se crea una **versión nueva e inmutable**. |
| `routines/{id}/versions/` | GET | Todas las versiones del plan, de la más antigua a la más nueva. |
| `workouts/` | GET, POST | Sesión con ejercicios y series anidados. Filtros: `from`, `to` (fecha local), `status`, `routine`. |
| `workouts/{id}/` | GET, PATCH, DELETE | Para guardar el avance de una sesión en curso o corregir una ya terminada. |
| `measurements/` | GET, POST | POST con una fecha que ya tiene valor **lo reemplaza**, igual que en la app. |
| `measurements/{id}/` | GET, PATCH, DELETE | |
| `users/me/preferences/` | GET, PATCH | Unidad, objetivo, incrementos, tema, etc. Si hay conflicto, gana la última escritura de cada campo. |
| `progress/weekly/` | GET | `from`, `to`, `unit`. Semanas de lunes a domingo: sesiones, series, volumen y duración. |
| `progress/exercises/{exercise_id}/` | GET | Un punto por sesión: peso máximo, 1RM estimado, volumen. |

### Convenciones

- **IDs del cliente e idempotencia:** la app genera los UUID y los envía en `id`. Repetir un POST con el mismo `id` devuelve **200** con lo ya guardado, sin duplicar. Un `id` que pertenece a otra cuenta responde **409 `id_conflict`**, sin revelar de quién es.
- **Concurrencia optimista:** todo PATCH exige `base_version`, que es la `version` sobre la que el cliente hizo su cambio. Si no coincide, la respuesta es **409 `version_conflict`** e incluye en `error.current` el estado actual del servidor para poder combinar.
- **Borrados:** son *tombstones* (`deleted_at`). Los listados no los muestran, salvo con `?updated_since=<ISO>`, que devuelve todo lo modificado desde ese momento, incluidos los borrados (para sincronizar).
- **Historia inmutable:**
  - Cada sesión apunta a la versión del plan con la que se entrenó y guarda una copia del nombre, el músculo y el equipo de cada ejercicio, junto con lo planificado (series, reps y peso objetivo).
  - Ni editar la rutina ni borrar un ejercicio cambia una sesión pasada.
  - Una sesión puede referenciar un ejercicio que el servidor no conoce solo si trae su `exercise_name`, por ejemplo al migrar historial local antiguo.
- **Referencias a ejercicios:** se usa el *slug* para los integrados (`chest-bench-press`) o el UUID para los personalizados, tal como los guarda la app.
- **Fechas:** `scheduled_date` es la fecha **local** que vivió el usuario, y el servidor nunca la recalcula. `timezone` es el nombre IANA de la zona.
- **`null` no es 0:**
  - Un peso o unas reps sin registrar valen `null`.
  - Una semana sin sesiones informa `sessions: 0` y todas sus métricas en `null`.
  - Una sesión en curso tiene `duration_seconds: null`.
- **Unidades:**
  - Cada sesión guarda la unidad de sus pesos (`kg` o `lb`). Si está vacía, se asume la unidad preferida del usuario.
  - El progreso convierte todo a la unidad pedida.
  - Las fórmulas son las mismas que en la app: 1RM de Epley con un tope de 15 reps; solo cuentan las series completadas con peso y reps.

## Entrenadores y alumnos

Una misma cuenta puede ser alumna de varios entrenadores y entrenar a muchos alumnos. Los roles salen de **relaciones explícitas**, no de un campo del usuario.

| Endpoint | Quién | Qué hace |
|---|---|---|
| `POST coaching/relationships/invite/` | Entrenador | `{username, message?}` crea una invitación pendiente (vence a los 14 días) |
| `GET coaching/relationships/` | Ambos | Filtros `role=coach\|student` y `status`. Cada relación indica tu `role` |
| `POST coaching/relationships/{id}/accept/` | Alumno | `{progress, sessions, measurements}`: qué autoriza a ver. Por defecto, **nada** |
| `POST …/reject/` | Alumno | Rechaza la invitación |
| `POST …/end/` | Ambos | Termina la relación (o cancela una invitación) |
| `PATCH coaching/relationships/{id}/` | Alumno: `permissions`; entrenador: `group` | |
| `GET …/progress/weekly/`, `…/progress/exercises/{id}/` | Entrenador | Solo con permiso `progress` |
| `GET …/sessions/` | Entrenador | Solo con permiso `sessions` |
| `GET …/measurements/` | Entrenador | Solo con permiso `measurements` |
| `coaching/groups/` | Entrenador | Grupos propios (horario, gimnasio…). El alumno no los ve |
| `POST coaching/assignments/` | Entrenador | `{relationship, routine, can_edit_plan?}` |
| `PATCH coaching/assignments/{id}/` | Entrenador | `{can_edit_plan}` |
| `POST coaching/assignments/{id}/end/` | Ambos | Finaliza la asignación |

**Reglas:**
- **Privacidad:**
  - Antes de aceptar, el entrenador no ve nada.
  - Después ve solo lo que el alumno autorizó, y solo el alumno puede cambiar esos permisos.
  - El entrenador nunca recibe el email ni otros datos personales del alumno: solo su username y su nombre.
- **Asignar** le crea al alumno **su propia** rutina (`attribution: "Asignada por @…"`; `assignment` en la representación de la rutina).
- **Ejercicios privados del entrenador:** si la rutina los usa, se copian una vez a la cuenta del alumno, para que pueda verlos y registrar entrenamientos con ellos.
- **Actualización automática (D5):**
  - Cada cambio del entrenador en su rutina se aplica a todas sus asignaciones activas, como versión nueva con `origin: coach_update`.
  - Gana el entrenador; lo que había editado el alumno queda en el historial de versiones.
  - Las sesiones pasadas no cambian: siguen apuntando a la versión con la que se entrenaron.
- **Sin `can_edit_plan`:**
  - El alumno entrena con la rutina, la activa o archiva y mueve días, pero no puede cambiar el plan, el nombre ni el objetivo (403 `plan_locked`).
  - Tampoco puede borrarla mientras la asignación siga activa.
- **Al terminar la relación o la asignación:**
  - Los permisos caen en el momento.
  - El alumno conserva la rutina como propia (desbloqueada) y todo su historial.
  - Los cambios posteriores del entrenador ya no le llegan.

## Compartir y comunidad

| Endpoint | Quién | Qué hace |
|---|---|---|
| `POST routines/{id}/shares/` | Dueño | `{username, role}` con rol `viewer` (ver), `copier` (ver y copiar) o `editor` (ver, copiar y editar). La invitación queda pendiente hasta que el destinatario acepta |
| `GET routines/{id}/shares/` | Dueño | Lista los accesos de esa rutina |
| `GET routine-shares/?direction=received\|sent` | Ambos | Compartidas conmigo o por mí |
| `POST routine-shares/{id}/accept/` · `…/decline/` | Destinatario | Acepta o rechaza el acceso |
| `PATCH routine-shares/{id}/` · `DELETE …` | Dueño | Cambia el rol o revoca el acceso |
| `GET / PATCH shared/routines/{id}/` · `POST …/copy/` | Destinatario | Según su rol: ver, editar (solo plan, nombre, objetivo y duración) o copiar |
| `POST routines/{id}/links/` | Dueño | `{role: viewer\|copier, expires_in_days?}`. Devuelve el token **una sola vez** |
| `GET routines/{id}/links/` · `DELETE share-links/{id}/` | Dueño | Lista los enlaces (sin el token) o revoca uno |
| `POST shared-links/resolve/` | Cualquiera | `{token}`: ver la rutina, sin necesidad de cuenta |
| `POST shared-links/copy/` | Con cuenta | `{token}`: copiar, si el enlace es `copier` |
| `POST routines/{id}/publish/` · `unpublish/` | Dueño | Publica o retira la rutina del catálogo |
| `POST exercises/{id}/publish/` · `unpublish/` | Autor | Publica o retira un ejercicio |
| `GET community/routines/` · `community/exercises/` | Cualquiera | Catálogo con filtros `q`, `goal`, `author`, `muscle`, `equipment` |
| `POST community/routines/{id}/copy/` · `community/exercises/{id}/copy/` | Con cuenta | Copia a tu cuenta |
| `POST reports/` | Con cuenta | `{kind, object_id, reason, details?}` |

**Reglas:**
- **Privacidad:** publicar o compartir muestra el plan y el **username** del autor. Nunca su id, email, perfil, peso, sesiones ni progreso.
- **Enlaces:**
  - Token de 256 bits; en la base se guarda solo su SHA-256, más los 6 primeros caracteres para distinguirlos.
  - Nunca dan permiso de edición y solo abren esa rutina.
  - Pueden vencer o revocarse. Cualquier enlace inválido responde igual: 404 `link_invalid`.
  - El token viaja en el cuerpo del POST, nunca en la URL, así no queda en los logs.
- **Copias:**
  - Generan una rutina nueva del que copia, inactiva, con `attribution: "Basada en «X» de @autor"`, y sin sesiones ni mediciones del autor.
  - Editar la copia no toca el original, ni al revés.
  - Los ejercicios que no son integrados ni propios se copian a la cuenta (una vez por ejercicio, con `copied_from`). Así la copia funciona sin conexión y no se rompe si el autor edita o borra su ejercicio.
- **Editor:** cambia el plan del dueño. La versión queda registrada con `origin: shared_edit` y su autor. No puede activar, compartir ni borrar. Sus ejercicios privados no se aceptan en el plan, porque el dueño no podría verlos.
- **Rutinas asignadas por un entrenador:** el alumno no puede publicarlas, compartirlas ni crear enlaces (403 `assigned_routine`).
- **Moderación:**
  - Solo se puede denunciar contenido publicado, y no el propio.
  - Con **3 denuncias de personas distintas**, el contenido se oculta del catálogo hasta que lo revise el staff desde el admin.
  - El autor lo conserva, pero no puede volver a publicarlo mientras esté oculto.

## Exportación de datos

`GET /api/v1/exports/?type=json|csv|zip` (con cuenta; `type`, no `format`, porque DRF reserva `?format=`).

| `type` | Archivo |
|---|---|
| `json` (por defecto) | `gymbo-<usuario>-<fecha>.json`: todo en un documento |
| `csv` | Una tabla, elegida con `table=` (`series` por defecto; también `sesiones`, `rutinas`, `ejercicios`, `mediciones`, `progreso_semanal`) |
| `zip` | `datos.json` + todas las tablas CSV + `LEEME.txt` con la explicación de campos, unidades y fechas |

- **Qué incluye:** cuenta (username, email, datos de perfil cargados, proveedores vinculados), preferencias, ejercicios propios, rutinas con su plan actual y **todo su historial de versiones**, sesiones con ejercicios y series (peso, repeticiones, RIR, descanso, completada), duración, mediciones y progreso semanal. Lo borrado no se exporta.
- **Qué no incluye:** contraseñas, hashes, tokens, ni datos de otras personas (entrenadores, alumnos, quienes compartieron rutinas). Las rutinas copiadas o asignadas conservan su texto de atribución.
- **Formato:** `gymbo-export` versión 1, definido en `apps/exports/format.py`. La app genera **el mismo formato** con los datos locales de un invitado; si cambia, cambian los dos lados.
  - Fechas de calendario locales (`AAAA-MM-DD`); instantes en UTC (`AAAA-MM-DDTHH:MM:SSZ`).
  - Cada sesión indica su unidad (kg/lb). El progreso semanal se convierte a la unidad de las preferencias.
  - Vacío/`null` = no registrado (distinto de 0).
  - CSV: UTF-8 con BOM, coma, punto decimal, `true`/`false`.
- **Seguridad:**
  - Solo la propia cuenta: no hay id en la URL.
  - No se guarda ningún archivo en el servidor: no hay URLs públicas, ni archivos que vencer o limpiar.
  - `Cache-Control: no-store`.
  - Límite `THROTTLE_EXPORT` (30/hora por cuenta), porque cada exportación lee la cuenta entera.
- **Para un entrenador:** el archivo se puede mandar a quien quieras; no le da acceso a la cuenta ni credenciales.

## Eliminación de cuenta

`DELETE /api/v1/users/me/` con `{password}` (o `{confirm_username}` en cuentas creadas con Google que nunca definieron contraseña). Limitado por IP como los endpoints de autenticación. Responde 204.

Es **inmediata e irreversible**. Implementación: `apps/accounts/deletion.py`.

| Dato | Qué pasa |
|---|---|
| Cuenta, perfil, preferencias, Google vinculado | Se borran |
| Sesiones de login | Se revocan todas al instante (el access token en uso deja de servir) y se borran sus registros |
| Rutinas propias, versiones, enlaces y accesos compartidos | Se borran. Quienes tenían acceso dejan de verlas |
| Publicaciones en la comunidad | Desaparecen del catálogo |
| Copias que otras personas hicieron | **Se conservan**: son de ellas. La atribución pasa a decir «de una cuenta eliminada» |
| Ejercicios propios | Se borran. Excepción: un ejercicio publicado que el plan de otra persona usa directamente queda sin autor y fuera del catálogo, para no romper ese plan |
| Sesiones, series y mediciones | Se borran |
| Relaciones de entrenamiento e invitaciones pendientes | Terminan y se borran. Si la cuenta era de un entrenador, cada alumno **conserva sus rutinas asignadas**, ya desbloqueadas y con todo su historial («Asignada por una cuenta eliminada») |
| Rutinas que te asignó tu entrenador | Se borran con tu cuenta; la rutina original del entrenador no se toca |
| Rutinas compartidas contigo | Se borra tu acceso; la rutina sigue siendo de su autor |
| Denuncias que hiciste | Quedan para moderación, sin el denunciante |
| Exportaciones | No hay nada guardado en el servidor |
| Logs técnicos | Solo método, ruta, código y duración; el borrado registra únicamente el id interno de la cuenta |
| **Backups** | Los datos siguen en los backups hechos antes del borrado hasta que rotan (retención sugerida: 7 diarios + 4 semanales, ≈ 35 días). Si se restaura un backup, hay que volver a aplicar los borrados posteriores |

Lo que está en el teléfono no lo borra el servidor: la app borra los datos locales de esa cuenta al confirmar. Los datos de invitado del dispositivo no se tocan.

## Producción

`compose.prod.yaml` construye la imagen `prod`. Sus características:

- **Ejecución:** Gunicorn, usuario `app` (uid 1000) sin privilegios y sistema de archivos de solo lectura (`read_only`, más `no-new-privileges`).
- **Contenido:** solo dependencias de ejecución (sin `uv` ni `pytest`) y estáticos recolectados en el build con WhiteNoise.
- **Healthcheck:** `/health/ready/` con Python estándar, sin depender de curl.
- **Logs:** en JSON, sin cuerpos, headers, tokens ni query strings.
- **Puerto:** **solo en `127.0.0.1:8000`**, para que lo consuma un proxy inverso en el mismo servidor.

### Arranque

```bash
# En el servidor, con las variables en un archivo de root fuera del repo (chmod 600):
docker compose -f compose.prod.yaml --env-file /etc/gymbo/api.env up -d --build
docker compose -f compose.prod.yaml --env-file /etc/gymbo/api.env exec api python manage.py migrate --plan   # revisar
# backup primero (ver abajo), después:
docker compose -f compose.prod.yaml --env-file /etc/gymbo/api.env exec api python manage.py migrate
```

- **PostgreSQL externo (por defecto):** definí `DATABASE_URL` y `DATABASE_SSLMODE=verify-full` (o `require`).
- **PostgreSQL en el mismo servidor:** agregá `--profile local-db` y las variables `POSTGRES_*`. Sigue sin publicar puertos.

### El backend no arranca si…

- `DJANGO_DEBUG` es `true`;
- `DJANGO_SECRET_KEY` mide menos de 50 caracteres o es un valor de ejemplo;
- `DJANGO_ALLOWED_HOSTS` está vacío o contiene `*`;
- `DATABASE_SSLMODE` no es `require`, `verify-ca` ni `verify-full`;
- `DJANGO_ADMIN_URL` sigue siendo `admin/`.

Todos estos casos están comprobados con la imagen real.

### Dominio y HTTPS con un proxy inverso

El proxy termina TLS y reenvía a `127.0.0.1:8000`. Ejemplo con Caddy, que obtiene y renueva certificados solo:

```
api.tudominio.com {
    reverse_proxy 127.0.0.1:8000
}
```

Con nginx, usá `proxy_pass http://127.0.0.1:8000;` y `proxy_set_header Host $host; X-Forwarded-For $proxy_add_x_forwarded_for; X-Forwarded-Proto $scheme;`.

**Variables:**
- `DJANGO_ALLOWED_HOSTS=api.tudominio.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://api.tudominio.com`
- `DJANGO_NUM_PROXIES=1`

**Comportamiento comprobado:**
- Django redirige HTTP a HTTPS y envía HSTS (1 año, con subdominios). La *preload list* de los navegadores es opcional (`DJANGO_HSTS_PRELOAD=true`): activala solo cuando todos los subdominios sirvan HTTPS para siempre, porque salir de esa lista lleva meses. Mientras tanto, `check --deploy` muestra el aviso W021, y es esperado.
- Un `Host` desconocido recibe 400.
- `/admin/` responde 404, porque el admin vive en `DJANGO_ADMIN_URL`.
- La documentación de la API es solo para staff.

**El firewall del servidor tiene que dejar abiertos solo los puertos 80, 443 y SSH.** Docker Compose por sí solo no protege el servidor.

### PostgreSQL externo con privilegio mínimo

```sql
CREATE ROLE gymbo_owner LOGIN PASSWORD '<secreto-1>';   -- corre las migraciones
CREATE ROLE gymbo_app   LOGIN PASSWORD '<secreto-2>';   -- usa la API
CREATE DATABASE gymbo OWNER gymbo_owner;
REVOKE ALL ON DATABASE gymbo FROM PUBLIC;
GRANT CONNECT ON DATABASE gymbo TO gymbo_app;
\c gymbo
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO gymbo_app;
ALTER DEFAULT PRIVILEGES FOR ROLE gymbo_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gymbo_app;
ALTER DEFAULT PRIVILEGES FOR ROLE gymbo_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO gymbo_app;
```

- **Uso de los roles:** la API corre con `gymbo_app`, y las migraciones se ejecutan con `DATABASE_URL` apuntando a `gymbo_owner`.
- **Red:** en `pg_hba.conf` o en el firewall del proveedor, aceptá conexiones solo desde la IP del backend, con `hostssl` y `scram-sha-256`.
- **Cifrado en reposo:** usá el que ofrezca el disco o el servicio gestionado.

### Backups y restauración

```bash
./scripts/backup.sh                          # o: make backup  → backups/gymbo-<fecha>.dump (chmod 600)
./scripts/restore.sh backups/<archivo>.dump  # restaura en una base NUEVA (<db>_restore), nunca encima
```

Comprobado: backup, restauración en una base nueva y verificación de los datos. `restore.sh` se niega a sobrescribir una base que ya existe.

Para producción con PostgreSQL externo, la herramienta es la misma con la URL del dueño:

```bash
pg_dump --format=custom --no-owner --file=gymbo-$(date +%F).dump "$OWNER_DATABASE_URL"
```

Políticas:
- **Guardado:** los backups van **cifrados y fuera del servidor**.
- **Retención sugerida:** 7 diarios y 4 semanales.
- **Prueba:** la restauración se prueba al menos **una vez por mes**.
- **PITR:** conviene sumarlo si el proveedor lo ofrece.

**Para restaurar sobre la base principal** (es destructivo y se hace a mano):
1. Detené la API.
2. Hacé un backup del estado actual.
3. Restaurá en `<db>_restore` y verificá los datos.
4. Renombrá las bases como superusuario: `ALTER DATABASE gymbo RENAME TO gymbo_old; ALTER DATABASE gymbo_restore RENAME TO gymbo;`.
5. Levantá la API.

### Migraciones en producción

- Están versionadas en el repositorio.
- Nunca se ejecutan `flush`, `dropdb` ni `migrate --fake` de forma automática.
- El entrypoint solo corre `migrate` si se activa `DJANGO_MIGRATE_ON_START=true`. En producción ese valor siempre es `false`: el paso es explícito, con revisión (`--plan`) y un backup previo.

---

## Datos que guarda el servidor

- **Cuenta:**
  - Username, email y el hash Argon2 de la contraseña (nunca la contraseña en sí).
  - Opcionalmente, nombre, teléfono, género y fecha de nacimiento, solo si el usuario los carga.
- **Google:** el `sub` y el email al momento de vincular.
- **Sesiones:** los identificadores (`jti`) de los refresh tokens emitidos, para poder revocarlos.
- **Entrenamiento:** solo lo que el usuario registrado sincroniza desde su cuenta. Lo que se hizo como invitado queda en el teléfono salvo que la persona elija subirlo al crear la cuenta.
- **Coaching y compartir:** relaciones, permisos que el alumno otorgó, asignaciones, accesos compartidos, enlaces (solo el hash del token) y denuncias.
- **Exportaciones:** no se guardan; se generan al pedirlas y se envían en la misma respuesta.
- **No se reciben:** datos de invitados, recordatorios ni la vibración del temporizador.
- **Logs:** método, ruta, código y duración. Sin cuerpos, contraseñas, tokens, headers `Authorization` ni query strings.
