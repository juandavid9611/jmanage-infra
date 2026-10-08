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


def run(args: argparse.Namespace, session: Any) -> dict[str, Any]:
    safety.assert_region(args.region)
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
    p.add_argument("--confirm", action="store_true", help="Escribir en dev. Sin esto es dry-run.")
    return p


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Modo: {summary['mode']}")
    for key, info in summary["tables"].items():
        print(f"  {key}: leidos={info['read']} escritos={info['written']}")
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
        summary = run(args, session)
    except safety.SafetyError as exc:
        print(f"RECHAZADO: {exc}", file=sys.stderr)
        return 2
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
