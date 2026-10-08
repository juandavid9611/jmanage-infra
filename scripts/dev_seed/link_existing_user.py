"""Da a un usuario que YA existe en dev membresias en una cuenta sembrada.

Uso (dry-run por defecto):
  python link_existing_user.py --email you@example.com --account-id vittoriacd
  python link_existing_user.py --email you@example.com --account-id vittoriacd --confirm

- Solo escribe en la tabla Memberships de DEV (se niega si el nombre no es de dev).
- Crea las filas nuevas sin sobreescribir ninguna existente.
- Copia la forma de una membresia existente del usuario (mismos atributos).
- Los workspaces se leen de la tabla Workspace de dev para esa cuenta.
- No toca Cognito ni la contrasena del usuario.
"""

import argparse
import re
import sys

import boto3

DEV_PREFIX = "JmanageInfraStack-dev-"
REGION = "us-west-2"


def short(name):
    return re.sub(r"[0-9A-F]{8}-.*$", "", name.replace(DEV_PREFIX, ""))


def scan(client, table):
    items, kw = [], {}
    while True:
        resp = client.scan(TableName=table, **kw)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True)
    p.add_argument("--account-id", required=True)
    p.add_argument("--role", default="admin", choices=["admin", "coach", "user", "team_owner"])
    p.add_argument("--profile")
    p.add_argument("--confirm", action="store_true", help="Escribir en dev. Sin esto es dry-run.")
    args = p.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    client = session.client("dynamodb")
    names = []
    for page in client.get_paginator("list_tables").paginate():
        names += page["TableNames"]
    dev = {short(n): n for n in names if n.startswith(DEV_PREFIX)}
    for key in ("User", "Memberships", "Workspace"):
        if key not in dev:
            sys.exit(f"No encontre la tabla dev '{key}'. Abortando.")
    memberships_table = dev["Memberships"]
    if not memberships_table.startswith(DEV_PREFIX):
        sys.exit("La tabla Memberships no es de dev. Abortando.")

    users = [u for u in scan(client, dev["User"]) if u.get("email", {}).get("S", "").lower() == args.email.lower()]
    if len(users) != 1:
        sys.exit(f"Se esperaba 1 usuario dev con ese email y hay {len(users)}. Abortando.")
    user_id = users[0]["id"]["S"]

    workspaces = [w["id"]["S"] for w in scan(client, dev["Workspace"]) if w.get("account_id", {}).get("S") == args.account_id]
    if not workspaces:
        sys.exit(f"La cuenta {args.account_id} no tiene workspaces en dev. Siembrala primero.")

    existing = [
        m for m in scan(client, memberships_table) if m.get("PK", {}).get("S") == f"USER#{user_id}"
    ]
    if not existing:
        sys.exit("El usuario no tiene ninguna membresia de referencia para copiar la forma. Abortando.")
    template = existing[0]

    print(f"Modo: {'confirm' if args.confirm else 'dry-run'}  cuenta={args.account_id}  rol={args.role}")
    for ws in sorted(workspaces):
        item = dict(template)
        item["ACCOUNT_ID"] = {"S": args.account_id}
        item["WORKSPACE_ID"] = {"S": ws}
        item["SK"] = {"S": f"ACCOUNT#{args.account_id}#WORKSPACE#{ws}"}
        item["role"] = {"S": args.role}
        item["status"] = {"S": "active"}
        if not args.confirm:
            print(f"  crearia {item['SK']['S']}")
            continue
        try:
            client.put_item(
                TableName=memberships_table,
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            print(f"  creada {item['SK']['S']}")
        except client.exceptions.ConditionalCheckFailedException:
            print(f"  ya existia {item['SK']['S']}")
    if not args.confirm:
        print("DRY-RUN: no se escribio nada. Usa --confirm para escribir en dev.")


if __name__ == "__main__":
    main()
