# dev_seed: datos de prod en dev (sin PII) + usuarios de prueba

Herramientas para que el ambiente `dev` se parezca a `prod` sin exponer datos personales,
y para tener usuarios de prueba fijos en el User Pool de dev.

| Archivo | Para que sirve |
|---|---|
| `scrub.py` | Reglas de scrubbing de PII (puro, sin AWS). Determinista por hash. |
| `seed_dev_from_prod.py` | Copia todas las tablas de prod a dev, scrubbeadas, y copia solo activos publicos de S3. |
| `create_dev_users.py` | Crea usuarios Cognito fijos en el pool de dev y sus membresias en las tablas dev. |
| `safety.py` | Guardas compartidas (solo dev, region, pool, tablas distintas). |
| `tests/` | Pruebas unitarias con fixtures y boto3 simulado. No llaman a AWS. |

## Reglas de seguridad (resumen)

- **Dry-run por defecto.** Nada se escribe sin `--confirm`.
- Se **rechaza** cualquier destino que no sea el stack `JmanageInfraStack-dev` con `EnvUsed=dev` y el
  User Pool `us-west-2_CTvMrsxtC`. Tambien se rechaza si una tabla destino coincide con una de prod.
- Prod solo se lee (`describe_stacks`, `list_stack_resources`, `scan`). Las tablas de prod se envuelven en un
  objeto sin metodos de escritura.
- Los scripts **nunca imprimen valores**: solo nombres de tablas y conteos.
- Passwords: solo desde la variable `DEV_USERS_PASSWORD` o un prompt. Nunca en el codigo ni en logs.
- Region fija: `us-west-2`.

## Que necesitas

1. Python con boto3 (por ejemplo el venv de la API: `jmanage-api/.venv/bin/python`; el venv de infra no trae boto3).
2. Un perfil de AWS (`--profile`) con:
   - lectura en prod: `cloudformation:DescribeStacks`, `cloudformation:ListStackResources`, `dynamodb:Scan` sobre las tablas de prod, `s3:GetObject` sobre `prod/*` del bucket;
   - escritura en dev: `dynamodb:BatchWriteItem`/`PutItem`/`GetItem`/`Scan` sobre las tablas dev, `s3:PutObject` sobre `dev/*`, y `cognito-idp:AdminCreateUser`, `AdminSetUserPassword`, `AdminGetUser` sobre el pool dev.
3. El stack dev desplegado (`JmanageInfraStack-dev`) con sus outputs.

## Orden de operaciones

Todo desde `jmanage-infra/scripts/dev_seed/`:

```bash
PY=../../../jmanage-api/.venv/bin/python

# 0. Pruebas locales (no tocan AWS)
PYTHONPATH=. $PY -m unittest discover -s tests -t .

# 1. Dry-run del seed: lee prod, scrubbea en memoria, muestra conteos. No escribe.
$PY seed_dev_from_prod.py --profile MI_PERFIL

# 2. Revisa la salida. Si aparece "Campos scrubbeados por heuristica", agrega reglas explicitas (ver abajo).
#    Opcional: probar con pocas filas  ->  --limit 20  o  --tables user product

# 3. Seed real (escribe SOLO en tablas dev y en el prefijo dev/ del bucket)
$PY seed_dev_from_prod.py --profile MI_PERFIL --confirm

# 4. Usuarios de prueba: primero dry-run, luego real
$PY create_dev_users.py --profile MI_PERFIL
DEV_USERS_PASSWORD='...' $PY create_dev_users.py --profile MI_PERFIL --confirm
```

Si en dev hay mas de una cuenta club, pasa `--club-account-id <id>` a `create_dev_users.py`.
El seed debe correrse **antes** que los usuarios (los usuarios necesitan la cuenta club ya copiada).

## Como volver a correrlo

- El seed es idempotente por clave primaria: reescribe los mismos items (los ids y emails falsos son
  deterministas, asi que dos corridas producen lo mismo). **No borra** items que ya esten en dev; si quieres
  una copia limpia, vacia las tablas dev manualmente antes (cuidado: solo dev).
- `create_dev_users.py` reutiliza los usuarios Cognito que ya existen y no pisa items de `User` existentes.
- La sal del hash es `DEV_SEED_SALT` (variable de entorno, opcional). Cambiarla cambia todos los datos falsos.

## Que se scrubbea

Todos los valores son falsos y deterministas (mismo valor real -> mismo valor falso en todas las tablas).

| Tabla | Campos |
|---|---|
| User | `id` (se remapea a otro UUID), `user_name`, `email` (`user<N>@example.test`), `phone_number`, `identity_card_number`, `address`, `emergency_contact_*`, `avatar_url` (se vacia), `rh`, `eps` (se vacian) |
| Memberships | `PK` (`USER#...`), `USER_ID` remapeados; roles, cuentas y workspaces se conservan |
| PaymentRequest | `payment_request_to` (nombre, email, telefono, avatar...), `user_id`, `description` (vacia), `images` (comprobantes: se vacian), `reference` |
| Order | `customer.*` (nombre, email, telefono, avatar, IP, id), `shipping_address.full_address/company`, `payment.card_number`, notas de `history`/`provider_check`/`delivery_check`, `items[].cover_url` |
| Product | `reviews[]` (nombre, comentario, avatar, adjuntos); `cover_url`/`images` se reescriben `prod/` -> `dev/` |
| Notification | `user_email`, `title`/`content` (texto de relleno), `action_url` (vacia) |
| Donation | `donor_name`, `message`, `created_by_user_id` |
| File | `name` (se anonimiza, conserva la extension), `url` y `tags` (se vacian) |
| Tournament / TournamentTeam / TournamentPlayer | `logo_url` (se reescribe); equipo: `manager_name`, `contact_email`, `contact_phone`, `documents` (se vacia), `manager_user_ids`/`owner_user_id`; jugador: `name`, `id_number`, `avatar_url` |
| TournamentInvitation | `email`, `token` (los tokens reales son secretos vivos: se reemplazan), `accepted_by_user_id` |
| TournamentMatch | `notes` |
| Votation | `candidates[]` (nombre, avatar, id), `votes` (ids), `created_by`, `winner_id` |
| Calendar | `description`, `participants` (ids y nombres) |
| Tour | `bookers` (nombre, avatar, ids), `images` (se vacian), `content`, `tour_guides` |
| ClubRoster | `guest_name`, `user_id` |
| Account / Workspace | `branding.logo_url` / `logo` (solo si es un activo publico; se reescribe a `dev/`) |
| TrainingSession, ClubTournament, ClubMatch, TournamentMatchEvent | sin PII directa; pasan por el barrido generico |

**Barrido generico** (red de seguridad, corre sobre todas las tablas): cualquier campo cuyo nombre parezca
email, telefono, direccion, avatar/foto, documento/cedula, token/password/secret, IP o tarjeta se scrubbea aunque
no tenga regla; los ids de usuario conocidos se remapean donde aparezcan (valores, claves de dict, `USER#...`);
cualquier string que empiece con `prod/` o sea una URL presignada de S3 se vacia. El dry-run lista los
**nombres** de campo atrapados por la heuristica para que les agregues una regla explicita.

## Que NO se copia (y por que)

- **Usuarios de Cognito**: no se pueden copiar (hashes de password y subs no son exportables). Los usuarios
  scrubbeados de la tabla `User` aparecen en dev como "pendientes" y no pueden iniciar sesion. Para entrar
  usa los usuarios de `create_dev_users.py`.
- **Objetos S3 privados**: fotos de perfil, comprobantes/facturas (`users/.../invoices`), avatares de jugadores,
  documentos de equipo, archivos (`files/`) e imagenes de tours. Contienen PII o pueden contenerla; las
  referencias se vacian.
- **URLs presignadas** guardadas en datos (ej. `avatarUrl` de bookers): estan vencidas y atan a prod; se vacian.
- **Texto libre**: descripciones de pagos, comentarios, notas, mensajes de donacion se vacian o se reemplazan.
- **Datos de salud**: `rh` y `eps` se vacian.
- **Si se copian** (publicos, sin PII): logos de cuentas/torneos/equipos e imagenes de producto, server-side
  de `prod/accounts/...` a `dev/accounts/...` dentro del mismo bucket (`jmanage-bucket`).

## Usuarios de prueba (`create_dev_users.py`)

Emails fijos `@example.test` (no reciben correo; el pool los crea con `MessageAction=SUPPRESS` y password permanente):

| Persona | Email | Rol | Cuenta |
|---|---|---|---|
| admin | `dev.admin@example.test` | admin | club (y admin de la cuenta de torneos) |
| coach | `dev.coach@example.test` | coach | club |
| user | `dev.user@example.test` | user | club |
| team_owner | `dev.teamowner@example.test` | team_owner | `dev-torneos` (tipo torneo) |
| torneos_admin | `dev.torneos@example.test` | admin | `dev-torneos` |

La cuenta `dev-torneos` (con `account_type=tournament`) y los workspaces faltantes se crean si no existen.
El password debe tener 12+ caracteres con minuscula, mayuscula, numero y simbolo.

## Agregar una tabla nueva

1. Agrega el `CfnOutput` de la tabla en el stack (o su ID logico se resuelve por `list_stack_resources`, como `Donation`).
2. En `seed_dev_from_prod.py`, agrega la clave en `TABLE_OUTPUTS` (hay una asercion que exige que coincida con `RULES`).
3. En `scrub.py`, agrega la entrada en `RULES` con lista de `(ruta, tipo)`. Rutas: `a.b`, `lista.*` (cada elemento),
   `""` (el item completo). Tipos de valor: `email name phone document address company ip card token reference text
   text_placeholder filename drop user_id user_pk s3_public id_or_name`. Tipos de contenedor: `person map_keys
   name_by_key map_ids`. Si la tabla tiene clave primaria distinta de `id`, agregala a `KEY_SCHEMA`.
4. Agrega un fixture en `tests/test_scrub.py` con valores centinela y verifica que no aparezcan en la salida.
5. Corre las pruebas y un dry-run; revisa la lista de campos heuristicos.

## Riesgos abiertos

- Los usuarios `User` copiados no tienen cuenta Cognito: listas de usuarios muestran "pendiente".
- Si prod agrega campos con PII con nombres atipicos (ej. `celu`), la heuristica podria no verlos: revisa el
  dry-run cada vez que cambie el esquema, y las reglas por tabla se derivaron del codigo de `jmanage-api`
  (no de datos reales).
- Nombres de equipos, cuentas, productos y contenido de entrenamientos se copian tal cual (son datos de negocio,
  no personales). Si algun nombre de equipo incluye un nombre de persona, no se detecta.
- El prefijo `dev/` del bucket compartido se escribe con la credencial del perfil; el bucket es el mismo para
  prod y dev (`jmanage-bucket`), solo cambia el prefijo.
- La tabla `Donation` no tiene `CfnOutput`; se resuelve por recursos del stack (si cambia el ID logico, falla con un mensaje claro).
