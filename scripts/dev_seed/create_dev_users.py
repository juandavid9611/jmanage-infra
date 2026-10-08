#!/usr/bin/env python3
"""Crea usuarios de prueba fijos en el User Pool de DEV y sus registros en las tablas dev.

Personas (todas con emails @example.test, password desde env/prompt, nunca en el codigo):
  admin, coach, user          -> cuenta club
  team_owner, torneos_admin   -> cuenta tipo torneo ("dev-torneos", se crea si no existe)
  admin ademas es admin de la cuenta de torneos.

Seguridad:
  * Dry-run por defecto; solo escribe con --confirm.
  * Rechaza cualquier User Pool distinto de us-west-2_CTvMrsxtC y cualquier stack no-dev.
  * El password solo viene de DEV_USERS_PASSWORD o de un prompt (getpass); no se imprime.
  * Es idempotente: reutiliza usuarios Cognito existentes y no pisa items de User existentes.

Uso:
  DEV_USERS_PASSWORD=... python create_dev_users.py --profile mi-perfil --confirm
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import safety  # noqa: E402
from seed_dev_from_prod import TABLE_OUTPUTS, resolve_tables, stack_outputs  # noqa: E402

PASSWORD_ENV = "DEV_USERS_PASSWORD"
TOURNAMENT_ACCOUNT_ID = safety.PERSONA_ACCOUNT_ID
TOURNAMENT_WORKSPACE_ID = "ws_dev_torneos"

# key, email, nombre, rol de membresia, custom:role en Cognito, cuenta ("club"|"tournament")
PERSONAS: list[dict[str, str]] = [
    {"key": "admin", "email": "dev.admin@example.test", "name": "Dev Admin",
     "role": "admin", "cognito_role": "admin", "account": "club"},
    {"key": "coach", "email": "dev.coach@example.test", "name": "Dev Coach",
     "role": "coach", "cognito_role": "user", "account": "club"},
    {"key": "user", "email": "dev.user@example.test", "name": "Dev User",
     "role": "user", "cognito_role": "user", "account": "club"},
    {"key": "team_owner", "email": "dev.teamowner@example.test", "name": "Dev Team Owner",
     "role": "team_owner", "cognito_role": "user", "account": "tournament"},
    {"key": "torneos_admin", "email": "dev.torneos@example.test", "name": "Dev Torneos Admin",
     "role": "admin", "cognito_role": "admin", "account": "tournament"},
]
# Membresias extra: persona -> [(cuenta, rol)]
EXTRA_MEMBERSHIPS = {"admin": [("tournament", "admin")]}

NEEDED_TABLES = ("user", "account", "workspace", "memberships")


def validate_password(password: str) -> None:
    classes = [any(c.islower() for c in password), any(c.isupper() for c in password),
               any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    if len(password) < 12 or not all(classes):
        raise ValueError("El password debe tener 12+ caracteres con minuscula, mayuscula, numero y simbolo.")


def get_password(confirm: bool) -> str | None:
    if not confirm:
        return None
    password = os.environ.get(PASSWORD_ENV) or getpass.getpass(f"Password para los usuarios dev ({PASSWORD_ENV}): ")
    validate_password(password)
    return password


def _error_code(exc: Exception) -> str:
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


def get_sub(cognito: Any, pool_id: str, email: str) -> str | None:
    try:
        resp = cognito.admin_get_user(UserPoolId=pool_id, Username=email)
    except Exception as exc:
        if _error_code(exc) == "UserNotFoundException":
            return None
        raise
    return next((a["Value"] for a in resp.get("UserAttributes", []) if a["Name"] == "sub"), None)


def ensure_cognito_user(cognito: Any, pool_id: str, persona: dict, password: str | None, confirm: bool) -> str | None:
    """Devuelve el sub (o None en dry-run si el usuario aun no existe)."""
    safety.assert_dev_pool(pool_id)
    sub = get_sub(cognito, pool_id, persona["email"])
    if sub or not confirm:
        return sub
    created = cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=persona["email"],
        UserAttributes=[
            {"Name": "email", "Value": persona["email"]},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "name", "Value": persona["name"]},
            {"Name": "custom:role", "Value": persona["cognito_role"]},
        ],
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=pool_id, Username=persona["email"], Password=password, Permanent=True
    )
    attrs = created.get("User", {}).get("Attributes", [])
    return next((a["Value"] for a in attrs if a["Name"] == "sub"), None) or get_sub(
        cognito, pool_id, persona["email"]
    )


def user_item(sub: str, persona: dict) -> dict[str, Any]:
    """Misma forma que UserService._get_new_user en jmanage-api."""
    return {
        "id": sub,
        "user_name": persona["name"],
        "email": persona["email"],
        "user_status": "active",
        "created_time": int(time.time()),
        "user_metrics": {
            "asistencia_entrenos": 0, "asistencia_partidos": 0, "puntualidad_pagos": 0,
            "llegadas_tarde": 0, "deuda_acumulada": 0, "total": 0,
            "puntaje_asistencia_description": "", "puntaje_asistencia": 3,
            "last_update": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "shirt_number": "0",
    }


def membership_item(sub: str, account_id: str, workspace_id: str, role: str) -> dict[str, Any]:
    """Misma forma que MembershipRepo.create en jmanage-api."""
    return {
        "PK": f"USER#{sub}",
        "SK": f"ACCOUNT#{account_id}#WORKSPACE#{workspace_id}",
        "ACCOUNT_ID": account_id,
        "WORKSPACE_ID": workspace_id,
        "USER_ID": sub,
        "role": role,
        "status": "active",
    }


def tournament_account_item() -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "id": TOURNAMENT_ACCOUNT_ID,
        "name": "Dev Torneos",
        "created_at": now,
        "updated_at": now,
        "settings": {"default_workspace": TOURNAMENT_WORKSPACE_ID, "timezone": "America/Bogota",
                     "language": "es", "currency": "COP", "account_type": "tournament"},
        "subscription": {"plan": "free", "status": "active", "trial_end": None},
        "branding": {},
    }


def detect_club_account(account_table: Any) -> str:
    items: list[dict] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = account_table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    clubs = [i["id"] for i in items
             if (i.get("settings") or {}).get("account_type", "club") != "tournament"]
    if len(clubs) != 1:
        raise safety.SafetyError(
            f"Se esperaba 1 cuenta club en dev y hay {len(clubs)}; usa --club-account-id."
        )
    return clubs[0]


def put_if_absent(table: Any, item: dict, key_attr: str) -> bool:
    try:
        table.put_item(Item=item, ConditionExpression=f"attribute_not_exists({key_attr})")
        return True
    except Exception as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            return False
        raise


def run(args: argparse.Namespace, session: Any, password: str | None) -> dict[str, Any]:
    safety.assert_region(args.region)
    safety.assert_dev_pool(args.pool_id)
    cf = session.client("cloudformation", region_name=args.region)
    dev_out = stack_outputs(cf, args.dev_stack)
    safety.assert_dev_stack(args.dev_stack, dev_out)
    if dev_out.get("UserPoolId") != args.pool_id:
        raise safety.SafetyError("--pool-id no coincide con el User Pool del stack dev.")

    tables = resolve_tables(cf, args.dev_stack, dev_out)
    dev_tables = {k: v for k, v in tables.items() if k in NEEDED_TABLES}
    safety.assert_dev_tables(dev_tables, {})
    ddb = session.resource("dynamodb", region_name=args.region)
    t = {k: ddb.Table(v) for k, v in dev_tables.items()}
    cognito = session.client("cognito-idp", region_name=args.region)

    club_account = args.club_account_id or detect_club_account(t["account"])
    club_acc_item = t["account"].get_item(Key={"id": club_account}).get("Item")
    if not club_acc_item:
        raise safety.SafetyError("La cuenta club indicada no existe en las tablas dev.")
    club_ws = (club_acc_item.get("settings") or {}).get("default_workspace")
    if not club_ws:
        raise safety.SafetyError("La cuenta club no tiene default_workspace.")
    accounts = {"club": (club_account, club_ws),
                "tournament": (TOURNAMENT_ACCOUNT_ID, TOURNAMENT_WORKSPACE_ID)}

    summary: dict[str, Any] = {"mode": "confirm" if args.confirm else "dry-run",
                               "cognito_created": 0, "cognito_existing": 0, "users_written": 0,
                               "memberships_written": 0, "accounts_created": 0, "workspaces_created": 0}

    # Cuenta de torneos + workspaces (solo se crean si faltan).
    need_tournament = not t["account"].get_item(Key={"id": TOURNAMENT_ACCOUNT_ID}).get("Item")
    summary["tournament_account_missing"] = need_tournament
    if args.confirm:
        if need_tournament and put_if_absent(t["account"], tournament_account_item(), "id"):
            summary["accounts_created"] += 1
        for acc_id, ws_id in accounts.values():
            ws = {"id": ws_id, "account_id": acc_id,
                  "name": "Principal" if acc_id == club_account else "Dev Torneos",
                  "logo": None, "plan": None}
            if put_if_absent(t["workspace"], ws, "id"):
                summary["workspaces_created"] += 1

    for persona in PERSONAS:
        existed = get_sub(cognito, args.pool_id, persona["email"]) is not None
        sub = ensure_cognito_user(cognito, args.pool_id, persona, password, args.confirm)
        summary["cognito_existing" if existed else "cognito_created"] += 1
        if not args.confirm:
            continue
        if not sub:
            raise safety.SafetyError(f"No se obtuvo el sub de la persona {persona['key']}.")
        if put_if_absent(t["user"], user_item(sub, persona), "id"):
            summary["users_written"] += 1
        memberships = [(persona["account"], persona["role"])] + EXTRA_MEMBERSHIPS.get(persona["key"], [])
        for acc_kind, role in memberships:
            acc_id, ws_id = accounts[acc_kind]
            t["memberships"].put_item(Item=membership_item(sub, acc_id, ws_id, role))
            summary["memberships_written"] += 1
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="Perfil de AWS con escritura en dev")
    p.add_argument("--region", default=safety.DEV_REGION)
    p.add_argument("--dev-stack", default=safety.DEV_STACK_NAME)
    p.add_argument("--pool-id", default=safety.DEV_POOL_ID, help="Solo se acepta el pool de dev")
    p.add_argument("--club-account-id", help="Cuenta club de dev (si hay mas de una)")
    p.add_argument("--confirm", action="store_true", help="Crear usuarios. Sin esto es dry-run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import boto3

    try:
        safety.assert_dev_pool(args.pool_id)
        password = get_password(args.confirm)
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        summary = run(args, session, password)
    except (safety.SafetyError, ValueError) as exc:
        print(f"RECHAZADO: {exc}", file=sys.stderr)
        return 2
    print(f"Modo: {summary['mode']}")
    for k, v in summary.items():
        if k != "mode":
            print(f"  {k}: {v}")
    if summary["mode"] == "dry-run":
        print("DRY-RUN: no se creo nada. Usa --confirm para crear los usuarios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
