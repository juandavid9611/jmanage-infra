#!/usr/bin/env python3
"""Copia datos de prod a dev con PII scrubbeada.

Seguridad (ver README.md):
  * Dry-run por defecto. Solo escribe con --confirm.
  * Prod se lee SOLO con llamadas de lectura (describe_stacks, list_stack_resources,
    scan). Las tablas de prod se envuelven en ReadOnlyTable, que no expone escrituras.
  * Se rechaza cualquier destino que no sea el stack dev (nombre, EnvUsed y User Pool).
  * Nunca se imprimen valores de datos: solo nombres de tablas y conteos.

Uso:
  python seed_dev_from_prod.py --profile mi-perfil                # dry-run
  python seed_dev_from_prod.py --profile mi-perfil --confirm      # escribe en dev
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Iterator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import safety  # noqa: E402
from scrub import DEFAULT_SALT, RULES, Scrubber, key_attrs, pii_report  # noqa: E402

# Clave de tabla -> nombre del CfnOutput en el stack. La tabla Donation no tiene output;
# se resuelve por list_stack_resources (ver resolve_tables).
TABLE_OUTPUTS: dict[str, str] = {
    "user": "UserTableName",
    "account": "AccountTableName",
    "workspace": "WorkspaceTableName",
    "memberships": "MembershipsTableName",
    "payment_request": "PaymentRequestTableName",
    "calendar": "CalendarTableName",
    "tour": "TourTableName",
    "product": "ProductTableName",
    "order": "OrderTableName",
    "file": "FileTableName",
    "tournament": "TournamentTableName",
    "tournament_team": "TournamentTeamTableName",
    "tournament_player": "TournamentPlayerTableName",
    "tournament_match": "TournamentMatchTableName",
    "tournament_match_event": "TournamentMatchEventTableName",
    "tournament_invitation": "TournamentInvitationTableName",
    "votation": "VotationTableName",
    "training_session": "TrainingSessionTableName",
    "club_tournament": "ClubTournamentTableName",
    "club_roster": "ClubRosterEntryTableName",
    "club_match": "ClubMatchTableName",
    "notification": "NotificationTableName",
    "donation": "",  # sin output: se busca por ID logico "Donation"
}
LOGICAL_IDS = {"donation": "Donation"}

assert set(TABLE_OUTPUTS) == set(RULES), "RULES y TABLE_OUTPUTS deben cubrir las mismas tablas"


class ReadOnlyTable:
    """Envoltorio de una tabla de prod: solo expone lectura (scan paginado)."""

    def __init__(self, table: Any):
        self._table = table

    def scan_items(self, limit: int | None = None) -> Iterator[dict]:
        kwargs: dict[str, Any] = {}
        n = 0
        while True:
            resp = self._table.scan(**kwargs)
            for item in resp.get("Items", []):
                yield item
                n += 1
                if limit is not None and n >= limit:
                    return
            if not resp.get("LastEvaluatedKey"):
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


class DevWriter:
    """Unico punto de escritura. Solo acepta las tablas dev ya validadas."""

    def __init__(self, dynamodb: Any, dev_tables: dict[str, str], prod_tables: dict[str, str]):
        safety.assert_dev_tables(dev_tables, prod_tables)
        self._dynamodb = dynamodb
        self._dev_tables = dict(dev_tables)

    def put_many(self, table_key: str, items: list[dict]) -> int:
        name = self._dev_tables[table_key]
        safety.assert_dev_tables({table_key: name}, {})
        table = self._dynamodb.Table(name)
        with table.batch_writer(overwrite_by_pkeys=key_attrs(table_key)) as batch:
            for item in items:
                batch.put_item(Item=item)
        return len(items)

    def delete_many(self, table_key: str, keys: list[dict]) -> int:
        name = self._dev_tables[table_key]
        safety.assert_dev_tables({table_key: name}, {})
        table = self._dynamodb.Table(name)
        with table.batch_writer(overwrite_by_pkeys=key_attrs(table_key)) as batch:
            for key in keys:
                batch.delete_item(Key=key)
        return len(keys)


def stack_outputs(cf: Any, stack_name: str) -> dict[str, str]:
    stacks = cf.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def resolve_tables(cf: Any, stack_name: str, outputs: dict[str, str]) -> dict[str, str]:
    """Nombres de tabla por clave: CfnOutputs, con fallback a recursos del stack."""
    tables: dict[str, str] = {}
    missing = []
    for key, out in TABLE_OUTPUTS.items():
        if out and out in outputs:
            tables[key] = outputs[out]
        else:
            missing.append(key)
    if missing:
        by_logical: dict[str, str] = {}
        paginator = cf.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for res in page["StackResourceSummaries"]:
                if res["ResourceType"] != "AWS::DynamoDB::Table":
                    continue
                base = re.sub(r"[0-9A-F]{8}$", "", res["LogicalResourceId"])
                by_logical[base] = res["PhysicalResourceId"]
        for key in missing:
            name = by_logical.get(LOGICAL_IDS.get(key, ""))
            if name:
                tables[key] = name
    unresolved = sorted(set(TABLE_OUTPUTS) - set(tables))
    if unresolved:
        raise safety.SafetyError(
            f"No se pudo resolver la tabla de {unresolved} en el stack {stack_name}."
        )
    return tables


def copy_assets(s3: Any, bucket: str, assets: set[tuple[str, str]], confirm: bool) -> dict[str, int]:
    """Copia server-side prod/... -> dev/... dentro del mismo bucket (solo con --confirm)."""
    result = {"planned": len(assets), "copied": 0, "missing": 0}
    if not confirm:
        return result
    for src, dst in sorted(assets):
        if not src.startswith(f"{safety.SRC_ENV}/") or not dst.startswith(f"{safety.DST_ENV}/"):
            raise safety.SafetyError("Clave S3 fuera de los prefijos prod/ -> dev/.")
        try:
            s3.copy_object(CopySource={"Bucket": bucket, "Key": src}, Bucket=bucket, Key=dst)
            result["copied"] += 1
        except Exception as exc:  # objeto inexistente u otro error: se cuenta, no se imprime la clave
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                result["missing"] += 1
            else:
                raise
    return result


# ---- Sincronizacion de UNA cuenta (--account-id / --reset) ---------------------------------
# Como se determina a que cuenta pertenece una fila (ver README):
DIRECT_ATTR: dict[str, str] = {
    "account": "id", "memberships": "ACCOUNT_ID",
    **{k: "account_id" for k in (
        "workspace", "payment_request", "calendar", "tour", "product", "order", "file",
        "tournament", "votation", "tournament_invitation", "training_session",
        "club_tournament", "club_roster", "club_match")},
}
BY_TOURNAMENT = {"tournament_team", "tournament_player", "tournament_match"}  # via tournament_id
BY_MATCH = {"tournament_match_event"}                                         # via match_id
# Sin atributo de cuenta ni padre: se omiten con aviso.
SKIPPED_TABLES = {"donation": "campana global sin atributo de cuenta"}
# Tablas donde un reset NUNCA borra (se comparten entre cuentas o son la cuenta misma).
NO_DELETE = {"user", "account"}
MAX_DELETE_FRACTION = 0.5


def item_key(table_key: str, item: dict) -> tuple:
    return tuple(item.get(a) for a in key_attrs(table_key))


def key_dict(table_key: str, item: dict) -> dict:
    return {a: item[a] for a in key_attrs(table_key)}


def validate_flags(args: argparse.Namespace) -> None:
    account_id = getattr(args, "account_id", None)
    reset = getattr(args, "reset", False)
    if account_id:
        safety.assert_syncable_account(account_id)
        if getattr(args, "limit", None):
            raise safety.SafetyError("--limit no es compatible con --account-id (rompe la copia exacta).")
    if reset and not account_id:
        raise safety.SafetyError("--reset requiere --account-id.")
    if reset and args.confirm and not getattr(args, "i_understand_this_deletes", False):
        raise safety.SafetyError("--reset --confirm borra datos de dev: agrega --i-understand-this-deletes.")
    if getattr(args, "force_large_delete", False) and not reset:
        raise safety.SafetyError("--force-large-delete solo aplica con --reset.")


class AccountScope:
    """Resuelve que filas pertenecen a la cuenta, en prod (crudas) y en dev (scrubbeadas)."""

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.tournaments: set = set()
        self.matches: set = set()
        self.emails: set = set()
        self.member_user_ids: set = set()

    def owns(self, table_key: str, item: dict) -> bool:
        attr = DIRECT_ATTR.get(table_key)
        if attr:
            return item.get(attr) == self.account_id
        if table_key in BY_TOURNAMENT:
            return item.get("tournament_id") in self.tournaments
        if table_key in BY_MATCH:
            return item.get("match_id") in self.matches
        if table_key == "notification":
            return item.get("user_email") in self.emails
        if table_key == "user":
            return item.get("id") in self.member_user_ids
        return False


def _scan(ddb: Any, name: str) -> list[dict]:
    return list(ReadOnlyTable(ddb.Table(name)).scan_items())


class _Cache:
    def __init__(self, ddb: Any, tables: dict[str, str]):
        self._ddb, self._tables, self._c = ddb, tables, {}

    def get(self, key: str) -> list[dict]:
        if key not in self._c:
            self._c[key] = _scan(self._ddb, self._tables[key])
        return self._c[key]


def _derive_ownership(scope: AccountScope, cache: _Cache) -> None:
    """Rellena torneos/partidos (ids) a partir de las filas directas de la cuenta."""
    scope.tournaments = {t["id"] for t in cache.get("tournament") if t.get("account_id") == scope.account_id}
    scope.matches = {m["id"] for m in cache.get("tournament_match") if m.get("tournament_id") in scope.tournaments}


def plan_account(args: argparse.Namespace, ddb: Any, prod_tables: dict[str, str], dev_tables: dict[str, str],
                 selected: list[str], scrubber: Scrubber, user_items: list[dict]) -> dict[str, Any]:
    """Calcula (sin escribir) upserts/borrados por tabla para que dev == snapshot de prod."""
    acc = args.account_id
    prod, dev = _Cache(ddb, prod_tables), _Cache(ddb, dev_tables)

    # --- origen (prod, crudo) ---
    src = AccountScope(acc)
    src.member_user_ids = {m["USER_ID"] for m in prod.get("memberships") if m.get("ACCOUNT_ID") == acc and m.get("USER_ID")}
    _derive_ownership(src, prod)
    src.emails = {u["email"] for u in user_items if u.get("id") in src.member_user_ids and u.get("email")}

    source_set: dict[str, dict[tuple, dict]] = {}
    for key in selected:
        if key in SKIPPED_TABLES:
            continue
        rows = user_items if key == "user" else prod.get(key)
        out: dict[tuple, dict] = {}
        for item in rows:
            if not src.owns(key, item):
                continue
            scrubbed = scrubber.scrub_item(key, item)
            missing = [a for a in key_attrs(key) if not scrubbed.get(a)]
            if missing:
                raise safety.SafetyError(f"Item sin clave primaria tras el scrub en {key}: faltan {missing}.")
            out[item_key(key, scrubbed)] = scrubbed
        source_set[key] = out

    # --- destino (dev, ya scrubbeado) ---
    dst = AccountScope(acc)
    dev_users = dev.get("user")
    persona_ids = {u["id"] for u in dev_users if u.get("email") in safety.PERSONA_EMAILS}
    dev_members = [m for m in dev.get("memberships") if m.get("ACCOUNT_ID") == acc]
    persona_ws = {m.get("WORKSPACE_ID") for m in dev_members if m.get("USER_ID") in persona_ids}
    _derive_ownership(dst, dev)
    dst.member_user_ids = {m["USER_ID"] for m in dev_members if m.get("USER_ID") and m["USER_ID"] not in persona_ids}
    src_user_scrubbed = [scrubber.scrub_item("user", u) for u in user_items if u.get("id") in src.member_user_ids]
    dst.emails = ({u["email"] for u in dev_users if u.get("id") in dst.member_user_ids and u.get("email")}
                  | {u["email"] for u in src_user_scrubbed if u.get("email")}) - safety.PERSONA_EMAILS

    def preserved(key: str, item: dict) -> bool:
        if key == "memberships":
            return item.get("USER_ID") in persona_ids
        if key == "workspace":
            return item.get("id") in persona_ws
        return False

    plan: dict[str, dict[str, Any]] = {}
    owned_total = deletes_total = 0
    for key in selected:
        if key in SKIPPED_TABLES:
            continue
        source = source_set[key]
        dev_rows = {item_key(key, i): i for i in dev.get(key)}
        upserts = [it for k, it in source.items() if dev_rows.get(k) != it]
        unchanged = len(source) - len(upserts)
        deletes: list[dict] = []
        if args.reset and key not in NO_DELETE:
            owned = [i for i in dev_rows.values() if dst.owns(key, i) and not preserved(key, i)]
            owned_total += len(owned)
            deletes = [i for i in owned if item_key(key, i) not in source]
            deletes_total += len(deletes)
        plan[key] = {"source": len(source), "upserts": upserts, "unchanged": unchanged, "deletes": deletes}

    if args.reset and deletes_total and deletes_total > owned_total * MAX_DELETE_FRACTION \
            and not getattr(args, "force_large_delete", False):
        raise safety.SafetyError(
            f"El reset borraria {deletes_total} de {owned_total} filas de la cuenta en dev (>50%). "
            "Verifica la cuenta o usa --force-large-delete."
        )
    return {"plan": plan, "dst": dst}


def apply_account_plan(args: argparse.Namespace, writer: DevWriter, planned: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dst: AccountScope = planned["dst"]
    for key, p in planned["plan"].items():
        written = deleted = 0
        if args.confirm:
            written = writer.put_many(key, p["upserts"])
            for item in p["deletes"]:  # re-chequeo sobre el item de dev justo antes de borrar
                if key in NO_DELETE or not dst.owns(key, item):
                    raise safety.SafetyError(f"Borrado rechazado en {key}: la fila no pertenece a la cuenta.")
            deleted = writer.delete_many(key, [key_dict(key, i) for i in p["deletes"]])
        result[key] = {"read": p["source"], "upsert": len(p["upserts"]), "unchanged": p["unchanged"],
                       "delete": len(p["deletes"]), "written": written, "deleted": deleted}
    return result


def run(args: argparse.Namespace, session: Any) -> dict[str, Any]:
    safety.assert_region(args.region)
    validate_flags(args)
    cf = session.client("cloudformation", region_name=args.region)

    prod_out = stack_outputs(cf, args.prod_stack)
    dev_out = stack_outputs(cf, args.dev_stack)
    safety.assert_prod_source(args.prod_stack, prod_out)
    safety.assert_dev_stack(args.dev_stack, dev_out)

    prod_tables = resolve_tables(cf, args.prod_stack, prod_out)
    dev_tables = resolve_tables(cf, args.dev_stack, dev_out)
    safety.assert_dev_tables(dev_tables, prod_tables)

    ddb = session.resource("dynamodb", region_name=args.region)
    writer = DevWriter(ddb, dev_tables, prod_tables)
    selected = [k for k in TABLE_OUTPUTS if not args.tables or k in args.tables]
    unknown = sorted(set(args.tables or []) - set(TABLE_OUTPUTS))
    if unknown:
        raise safety.SafetyError(f"Tablas desconocidas en --tables: {unknown}")

    salt = os.environ.get("DEV_SEED_SALT", DEFAULT_SALT)

    # Pre-pase: ids de usuarios de prod (para remapearlos de forma consistente).
    user_ro = ReadOnlyTable(ddb.Table(prod_tables["user"]))
    user_items = list(user_ro.scan_items(args.limit))
    known_ids = {u["id"] for u in user_items if "id" in u}
    scrubber = Scrubber(salt=salt, known_user_ids=known_ids)

    summary: dict[str, Any] = {"mode": "confirm" if args.confirm else "dry-run", "tables": {}}
    account_id = getattr(args, "account_id", None)
    if account_id:
        planned = plan_account(args, ddb, prod_tables, dev_tables, selected, scrubber, user_items)
        summary["account_id_set"] = True
        summary["reset"] = bool(args.reset)
        summary["tables"] = apply_account_plan(args, writer, planned)
        summary["skipped"] = {k: v for k, v in SKIPPED_TABLES.items() if k in selected}
        s3 = session.client("s3", region_name=args.region)
        summary["assets"] = copy_assets(s3, args.bucket, scrubber.assets, args.confirm)
        summary["pii"] = pii_report(scrubber)
        return summary
    for key in selected:
        if key == "user":
            source = iter(user_items)
        else:
            source = ReadOnlyTable(ddb.Table(prod_tables[key])).scan_items(args.limit)
        scrubbed = []
        for item in source:
            out = scrubber.scrub_item(key, item)
            missing_keys = [a for a in key_attrs(key) if not out.get(a)]
            if missing_keys:
                raise safety.SafetyError(
                    f"Item sin clave primaria tras el scrub en {key}: faltan {missing_keys}."
                )
            scrubbed.append(out)
        written = writer.put_many(key, scrubbed) if args.confirm else 0
        summary["tables"][key] = {"read": len(scrubbed), "written": written}

    s3 = session.client("s3", region_name=args.region)
    summary["assets"] = copy_assets(s3, args.bucket, scrubber.assets, args.confirm)
    summary["pii"] = pii_report(scrubber)
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", help="Perfil de AWS (prod lectura + dev escritura)")
    p.add_argument("--region", default=safety.DEV_REGION)
    p.add_argument("--prod-stack", default=safety.PROD_STACK_NAME)
    p.add_argument("--dev-stack", default=safety.DEV_STACK_NAME)
    p.add_argument("--bucket", default=safety.DEFAULT_BUCKET)
    p.add_argument("--tables", nargs="*", help="Limitar a estas claves de tabla (ej. user product)")
    p.add_argument("--limit", type=int, help="Maximo de items por tabla (muestreo)")
    p.add_argument("--account-id", help="Copiar solo los datos de esta cuenta de prod")
    p.add_argument("--reset", action="store_true",
                   help="Con --account-id: ademas borra de dev las filas de esa cuenta que no existen en prod")
    p.add_argument("--i-understand-this-deletes", dest="i_understand_this_deletes", action="store_true",
                   help="Requerido con --reset --confirm")
    p.add_argument("--force-large-delete", action="store_true",
                   help="Permite borrar mas del 50%% de las filas de la cuenta en dev")
    p.add_argument("--confirm", action="store_true", help="Escribir en dev. Sin esto es dry-run.")
    return p


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Modo: {summary['mode']}")
    for key, info in summary["tables"].items():
        if "upsert" in info:
            print(f"  {key}: origen={info['read']} upsert={info['upsert']} sin_cambios={info['unchanged']} "
                  f"borrar={info['delete']} (escritos={info['written']} borrados={info['deleted']})")
        else:
            print(f"  {key}: leidos={info['read']} escritos={info['written']}")
    for key, why in summary.get("skipped", {}).items():
        print(f"  AVISO {key}: omitida ({why})")
    a = summary["assets"]
    print(f"S3: planificados={a['planned']} copiados={a['copied']} faltantes={a['missing']}")
    pii = summary["pii"]
    print(f"Activos descartados (no publicos): {pii['assets_dropped']}")
    if pii["heuristic_fields"]:
        print("Campos scrubbeados por heuristica (revisar y agregar regla explicita):")
        print(json.dumps(pii["heuristic_fields"], indent=2, sort_keys=True))
    if summary["mode"] == "dry-run":
        print("DRY-RUN: no se escribio nada. Usa --confirm para escribir en dev.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        validate_flags(args)
        summary = run(args, session)
    except safety.SafetyError as exc:
        print(f"RECHAZADO: {exc}", file=sys.stderr)
        return 2
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
