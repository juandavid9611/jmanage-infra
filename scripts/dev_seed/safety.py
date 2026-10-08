"""Guardas de seguridad compartidas por los scripts de dev_seed.

Todo lo que escribe en AWS debe pasar por estas funciones. Nunca se imprimen
valores de datos: solo nombres de stacks/tablas y conteos.
"""
from __future__ import annotations

DEV_POOL_ID = "us-west-2_CTvMrsxtC"
DEV_REGION = "us-west-2"
DEV_STACK_NAME = "JmanageInfraStack-dev"
PROD_STACK_NAME = "JmanageInfraStack"
SRC_ENV = "prod"
DST_ENV = "dev"
DEFAULT_BUCKET = "jmanage-bucket"


class SafetyError(Exception):
    """Se lanza cuando una operacion no cumple las reglas de seguridad."""


def assert_region(region: str) -> None:
    if region != DEV_REGION:
        raise SafetyError(f"Region no permitida: {region!r} (solo {DEV_REGION}).")


def assert_dev_pool(pool_id: str) -> None:
    if pool_id != DEV_POOL_ID:
        raise SafetyError(
            f"El User Pool {pool_id!r} no es el de dev ({DEV_POOL_ID}). Se rechaza."
        )


def assert_dev_stack(stack_name: str, outputs: dict[str, str]) -> None:
    """El destino debe ser el stack dev: nombre, EnvUsed y User Pool coinciden."""
    if stack_name != DEV_STACK_NAME:
        raise SafetyError(f"El stack destino {stack_name!r} no es {DEV_STACK_NAME!r}.")
    if outputs.get("EnvUsed") != DST_ENV:
        raise SafetyError("El output EnvUsed del stack destino no es 'dev'.")
    assert_dev_pool(outputs.get("UserPoolId", ""))


def assert_prod_source(stack_name: str, outputs: dict[str, str]) -> None:
    """La fuente debe ser prod y distinta de dev."""
    if stack_name == DEV_STACK_NAME:
        raise SafetyError("El stack fuente no puede ser el stack dev.")
    if outputs.get("EnvUsed") != SRC_ENV:
        raise SafetyError("El output EnvUsed del stack fuente no es 'prod'.")
    if outputs.get("UserPoolId") == DEV_POOL_ID:
        raise SafetyError("El stack fuente usa el User Pool de dev.")


def is_dev_table_name(name: str) -> bool:
    return name.startswith(f"{DEV_STACK_NAME}-")


def assert_dev_tables(dev_tables: dict[str, str], prod_tables: dict[str, str]) -> None:
    prod_names = set(prod_tables.values())
    for key, name in dev_tables.items():
        if not is_dev_table_name(name):
            raise SafetyError(f"La tabla destino de {key!r} no pertenece al stack dev.")
        if name in prod_names:
            raise SafetyError(f"La tabla destino de {key!r} coincide con una de prod.")
    for key, name in prod_tables.items():
        if is_dev_table_name(name):
            raise SafetyError(f"La tabla fuente de {key!r} pertenece al stack dev.")
