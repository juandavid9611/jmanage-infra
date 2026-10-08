"""Reglas de scrubbing de PII para copiar datos de prod a dev.

Modulo puro: sin AWS, sin I/O. Determinista (hash con sal) para que las
referencias entre tablas sigan siendo consistentes: el mismo email real siempre
produce el mismo email falso, el mismo user id real el mismo user id falso, etc.

NUNCA se registran ni imprimen valores crudos: solo conteos y nombres de campos.

Como agregar una tabla nueva: ver README.md (seccion "Agregar una tabla").
"""
from __future__ import annotations

import copy
import hashlib
import re
import uuid
from collections import Counter
from decimal import Decimal
from typing import Any

DEFAULT_SALT = "jmanage-dev-seed-v1"
SRC_ENV = "prod"
DST_ENV = "dev"

FIRST_NAMES = [
    "Alex", "Beatriz", "Camilo", "Daniela", "Esteban", "Fabiola", "Gonzalo",
    "Helena", "Ignacio", "Julia", "Kevin", "Laura", "Mateo", "Natalia",
    "Oscar", "Paula", "Rafael", "Sofia", "Tomas", "Valeria",
]
LAST_NAMES = [
    "Prueba", "Demo", "Ejemplo", "Simulado", "Ficticio", "Muestra", "Dummy",
    "Test", "Piloto", "Sandbox", "Borrador", "Modelo", "Tester", "Falso",
    "Sintetico", "Probador", "Fixture", "Mock", "Staging", "Dev",
]

# S3: solo se copian activos publicos no personales (logos, imagenes de producto).
PUBLIC_KEY_PATTERNS = [
    re.compile(r"^accounts/[^/]+/products/[^/]+/[^/]+$"),
    re.compile(r"^accounts/[^/]+/tournaments/[^/]+/[^/]+$"),
    re.compile(r"^accounts/[^/]+/teams/[^/]+/logo/[^/]+$"),
    re.compile(r"^accounts/[^/]+/(branding|logo|logos)/.+$"),
]

# Campos que contienen user ids (sub de Cognito) en cualquier tabla.
USER_ID_KEYS = {
    "user_id", "owner_user_id", "created_by", "created_by_user_id",
    "accepted_by_user_id", "checked_by", "by", "winner_id", "USER_ID",
}
USER_ID_LIST_KEYS = {"manager_user_ids"}

# Campos "persona" (un dict con id/name/email/...). clave -> (tipo, modo_de_semilla)
PERSON_FIELDS: dict[str, tuple[str, str]] = {
    "name": ("name", "id"),
    "user_name": ("name", "id"),
    "email": ("email", "value"),
    "phone_number": ("phone", "id"),
    "phoneNumber": ("phone", "id"),
    "phone": ("phone", "id"),
    "avatar_url": ("drop", ""),
    "avatarUrl": ("drop", ""),
    "ip_address": ("ip", "id"),
    "ipAddress": ("ip", "id"),
    "identity_card_number": ("document", "id"),
    "identityCardNumber": ("document", "id"),
    "address": ("address", "id"),
    "emergency_contact_name": ("name", "id:ec"),
    "emergency_contact_phone_number": ("phone", "id:ec"),
    "emergency_contact_relationship": ("drop", ""),
    "rh": ("drop", ""),
    "eps": ("drop", ""),
    "comment": ("text", ""),
    "attachments": ("drop", ""),
}

# Heuristica de seguridad para campos no cubiertos por reglas explicitas.
_HEURISTICS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ip_?address", re.I), "ip"),
    (re.compile(r"card_?number", re.I), "card"),
    (re.compile(r"e_?mail", re.I), "email"),
    (re.compile(r"phone|telefono|celular|mobile", re.I), "phone"),
    (re.compile(r"address$|direccion", re.I), "address"),
    (re.compile(r"avatar|photo|picture|foto", re.I), "drop"),
    (re.compile(r"identity|id_?number|document_?number|cedula|passport", re.I), "document"),
    (re.compile(r"token|password|secret", re.I), "token"),
    (re.compile(r"^(comment|comments|note|notes|message|bio)$", re.I), "text"),
]

# Reglas explicitas por tabla: (ruta, tipo). Ruta "" = el item completo.
# "*" recorre elementos de lista o valores de dict.
# Las claves de tabla son las de TABLE_OUTPUTS en seed_dev_from_prod.py.
RULES: dict[str, list[tuple[str, str]]] = {
    "user": [("", "person"), ("id", "user_id")],
    "account": [("branding.logo_url", "s3_public")],
    "workspace": [("logo", "s3_public")],
    "memberships": [("PK", "user_pk"), ("USER_ID", "user_id")],
    "payment_request": [
        ("payment_request_to", "person"),
        ("payment_request_to.id", "user_id"),
        ("user_id", "user_id"),
        ("description", "text"),
        ("images", "drop"),
        ("reference", "reference"),
    ],
    "order": [
        ("customer", "person"),
        ("customer.id", "user_id"),
        ("shipping_address.full_address", "address"),
        ("shipping_address.company", "company"),
        ("payment.card_number", "card"),
        ("items.*.cover_url", "s3_public"),
        ("history.*.meta.note", "text"),
        ("history.*.meta.by", "user_id"),
        ("provider_check.note", "text"),
        ("provider_check.checked_by", "user_id"),
        ("delivery_check.note", "text"),
        ("delivery_check.checked_by", "user_id"),
    ],
    "product": [
        ("cover_url", "s3_public"),
        ("images.*", "s3_public"),
        ("reviews.*", "person"),
    ],
    "file": [("name", "filename"), ("url", "drop"), ("tags", "drop")],
    "notification": [
        ("user_email", "email"),
        ("title", "text_placeholder"),
        ("content", "text_placeholder"),
        ("action_url", "drop"),
    ],
    "donation": [
        ("donor_name", "name"),
        ("message", "text"),
        ("created_by_user_id", "user_id"),
    ],
    "tournament": [("logo_url", "s3_public")],
    "tournament_team": [
        ("manager_name", "name"),
        ("contact_email", "email"),
        ("contact_phone", "phone"),
        ("documents", "drop"),
        ("logo_url", "s3_public"),
    ],
    "tournament_player": [
        ("name", "name"),
        ("id_number", "document"),
        ("avatar_url", "drop"),
    ],
    "tournament_match": [("notes", "text")],
    "tournament_match_event": [],
    "tournament_invitation": [("email", "email"), ("token", "token")],
    "votation": [
        ("candidates.*", "person"),
        ("candidates.*.id", "user_id"),
        ("votes", "map_ids"),
    ],
    "calendar": [
        ("description", "text"),
        ("participants", "name_by_key"),
        ("participants", "map_keys"),
    ],
    "tour": [
        ("bookers.*", "person"),
        ("bookers.*.id", "user_id"),
        ("bookers", "map_keys"),
        ("images", "drop"),
        ("content", "text_placeholder"),
        ("tour_guides.*", "id_or_name"),
    ],
    "training_session": [],
    "club_tournament": [],
    "club_roster": [("guest_name", "name")],
    "club_match": [],
}

# Esquema de claves primarias (para overwrite_by_pkeys y validacion).
KEY_SCHEMA: dict[str, list[str]] = {
    "memberships": ["PK", "SK"],
    "product": ["pk", "sk"],
}
DEFAULT_KEY = ["id"]


def key_attrs(table_key: str) -> list[str]:
    return KEY_SCHEMA.get(table_key, DEFAULT_KEY)


def _empty_like(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


class Scrubber:
    """Aplica las reglas de PII. Una instancia por corrida (acumula estadisticas)."""

    def __init__(self, salt: str = DEFAULT_SALT, known_user_ids: set[str] | None = None,
                 src_env: str = SRC_ENV, dst_env: str = DST_ENV):
        self.salt = salt
        self.known_user_ids = set(known_user_ids or ())
        self.src_env = src_env
        self.dst_env = dst_env
        self.assets: set[tuple[str, str]] = set()   # (clave origen, clave destino)
        self.stats: Counter[tuple[str, str]] = Counter()  # (tabla, tipo) -> n
        self.heuristic_fields: dict[str, set[str]] = {}   # tabla -> nombres de campo
        self.dropped_assets = 0
        self._table = ""
        self._touched: set[tuple[int, Any]] = set()

    # ---- generadores deterministas -------------------------------------------------
    def _h(self, kind: str, seed: Any) -> int:
        raw = f"{self.salt}|{kind}|{seed}".encode("utf-8")
        return int(hashlib.sha256(raw).hexdigest(), 16)

    def fake_email(self, value: str) -> str:
        return f"user{self._h('email', value.strip().lower()) % 10**8}@example.test"

    def fake_name(self, seed: Any) -> str:
        h = self._h("name", seed)
        return f"{FIRST_NAMES[h % len(FIRST_NAMES)]} {LAST_NAMES[(h // 97) % len(LAST_NAMES)]}"

    def fake_phone(self, seed: Any) -> str:
        return f"+57 300 {self._h('phone', seed) % 10**7:07d}"

    def fake_document(self, seed: Any) -> str:
        return f"9{self._h('document', seed) % 10**9:09d}"

    def fake_address(self, seed: Any) -> str:
        h = self._h("address", seed)
        return f"Calle {h % 150 + 1} # {(h // 7) % 90 + 1}-{(h // 11) % 90 + 1}, Ciudad de Prueba"

    def fake_company(self, seed: Any) -> str:
        return f"Empresa de Prueba {self._h('company', seed) % 1000}"

    def fake_ip(self, seed: Any) -> str:
        return f"192.0.2.{self._h('ip', seed) % 254 + 1}"  # rango TEST-NET-1

    def fake_token(self, seed: Any) -> str:
        return "tok_" + hashlib.sha256(f"{self.salt}|token|{seed}".encode()).hexdigest()[:32]

    def fake_reference(self, seed: Any) -> str:
        return f"REF-{self._h('reference', seed) % 10**8:08d}"

    def fake_user_id(self, value: str) -> str:
        digest = hashlib.sha256(f"{self.salt}|user_id|{value}".encode()).hexdigest()
        return str(uuid.UUID(hex=digest[:32]))

    def map_user_id(self, value: Any) -> Any:
        if isinstance(value, str) and value:
            return self.fake_user_id(value)
        return value

    # ---- activos S3 ------------------------------------------------------------------
    def rewrite_asset(self, value: Any) -> Any:
        """Reescribe una clave publica prod/... a dev/... y la registra para copiarla.

        Todo lo demas (claves privadas, URLs presignadas) se descarta.
        """
        if not isinstance(value, str) or not value:
            return value
        low = value.lower()
        if low.startswith(("http://", "https://")):
            if "amazonaws.com" in low or "x-amz-" in low:
                self.dropped_assets += 1
                return ""
            return value  # URL externa (no es un objeto nuestro)
        key = value.lstrip("/")
        prefix = f"{self.src_env}/"
        if key.startswith(prefix):
            rest = key[len(prefix):]
            if any(p.match(rest) for p in PUBLIC_KEY_PATTERNS):
                new_key = f"{self.dst_env}/{rest}"
                self.assets.add((key, new_key))
                return new_key
        self.dropped_assets += 1
        return ""

    # ---- tipos de valor ----------------------------------------------------------------
    def _value_kind(self, kind: str, value: Any, seed: Any = None) -> Any:
        if kind == "drop":
            return _empty_like(value)
        if value is None or value == "" or value == [] or value == {}:
            return value
        if kind == "s3_public":
            return self.rewrite_asset(value)
        if kind == "user_id":
            return self.map_user_id(value)
        if kind == "user_pk":
            if isinstance(value, str) and value.startswith("USER#"):
                return "USER#" + self.fake_user_id(value[len("USER#"):])
            return value
        if kind == "id_or_name":
            if isinstance(value, str) and value in self.known_user_ids:
                return self.fake_user_id(value)
            return self.fake_name(value)
        if isinstance(value, (list, dict)):
            return _empty_like(value)  # tipo escalar esperado: ante la duda, se vacia
        s = seed if seed is not None else str(value)
        if kind == "email":
            return self.fake_email(str(value))
        if kind == "name":
            return self.fake_name(s)
        if kind == "phone":
            return self.fake_phone(s)
        if kind == "document":
            return self.fake_document(s)
        if kind == "address":
            return self.fake_address(s)
        if kind == "company":
            return self.fake_company(s)
        if kind == "ip":
            return self.fake_ip(s)
        if kind == "card":
            return "**** **** **** 0000"
        if kind == "token":
            return self.fake_token(s)
        if kind == "reference":
            return self.fake_reference(s)
        if kind == "text":
            return "" if isinstance(value, str) else None
        if kind == "text_placeholder":
            return "Contenido de prueba"
        if kind == "filename":
            ext = ""
            if isinstance(value, str) and "." in value:
                ext = "." + re.sub(r"[^A-Za-z0-9]", "", value.rsplit(".", 1)[1])[:8]
            return f"archivo_{self._h('file', value) % 10**8:08d}{ext}"
        raise ValueError(f"Tipo de scrub desconocido: {kind}")

    # ---- tipos de contenedor -------------------------------------------------------------
    def _scrub_person(self, node: dict) -> dict:
        pid = node.get("id") or node.get("user_id")
        for key, (kind, mode) in PERSON_FIELDS.items():
            if key not in node:
                continue
            if mode == "id" and pid:
                seed: Any = str(pid)
            elif mode.startswith("id:") and pid:
                seed = f"{pid}|{mode[3:]}"
            else:
                seed = None
            node[key] = self._value_kind(kind, node[key], seed)
            self._touched.add((id(node), key))
            self.stats[(self._table, f"person.{kind}")] += 1
        return node

    def _map_keys(self, node: dict) -> dict:
        return {self.map_user_id(k) if self._is_user_id(k) else k: v for k, v in node.items()}

    def _is_user_id(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.known_user_ids

    def _container_kind(self, kind: str, node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        if kind == "person":
            return self._scrub_person(node)
        if kind == "map_keys":
            return self._map_keys(node)
        if kind == "name_by_key":
            for k in list(node):
                node[k] = self._value_kind("name", node[k], str(k))
                self._touched.add((id(node), k))
            return node
        if kind == "map_ids":
            return {
                (self.map_user_id(k) if isinstance(k, str) else k):
                (self.map_user_id(v) if isinstance(v, str) else v)
                for k, v in node.items()
            }
        raise ValueError(f"Tipo de contenedor desconocido: {kind}")

    _CONTAINER_KINDS = {"person", "map_keys", "name_by_key", "map_ids"}

    # ---- motor de rutas ---------------------------------------------------------------------
    @staticmethod
    def _slots(node: Any, parts: list[str]) -> list[tuple[Any, Any]]:
        if not parts or not isinstance(node, (dict, list)):
            return []
        head, rest = parts[0], parts[1:]
        if head == "*":
            keys = list(node.keys()) if isinstance(node, dict) else list(range(len(node)))
        elif isinstance(node, dict) and head in node:
            keys = [head]
        else:
            keys = []
        out: list[tuple[Any, Any]] = []
        for k in keys:
            if rest:
                out.extend(Scrubber._slots(node[k], rest))
            else:
                out.append((node, k))
        return out

    def _apply_rule(self, item: dict, path: str, kind: str) -> dict:
        holder: dict[str, Any] = {"root": item}
        slots = self._slots(holder, ["root"] + ([p for p in path.split(".")] if path else []))
        lists_to_compact: list[list] = []
        for container, key in slots:
            new = (self._container_kind(kind, container[key])
                   if kind in self._CONTAINER_KINDS
                   else self._value_kind(kind, container[key]))
            container[key] = new
            self._touched.add((id(container), key))
            self.stats[(self._table, kind)] += 1
            if kind == "s3_public" and isinstance(container, list) and container not in lists_to_compact:
                lists_to_compact.append(container)
        for lst in lists_to_compact:  # descartar activos no publicos tras recorrer todos los slots
            lst[:] = [x for x in lst if x]
        return holder["root"]

    # ---- barrido de seguridad ------------------------------------------------------------------
    def _heuristic_kind(self, key: str) -> str | None:
        for pattern, kind in _HEURISTICS:
            if pattern.search(key):
                return kind
        return None

    def _sweep(self, node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for k, v in node.items():
                nk = self.map_user_id(k) if self._is_user_id(k) else k
                if (id(node), k) in self._touched:
                    out[nk] = v
                    continue
                if isinstance(k, str) and k in USER_ID_KEYS and isinstance(v, str):
                    out[nk] = self.map_user_id(v)
                    continue
                if isinstance(k, str) and k in USER_ID_LIST_KEYS and isinstance(v, list):
                    out[nk] = [self.map_user_id(x) for x in v]
                    continue
                kind = self._heuristic_kind(k) if isinstance(k, str) else None
                scalar = isinstance(v, (str, int, Decimal)) and not isinstance(v, bool) and v != ""
                if kind and scalar:
                    if not (kind == "email" and isinstance(v, str) and v.endswith("@example.test")):
                        out[nk] = self._value_kind(kind, v)
                        self.heuristic_fields.setdefault(self._table, set()).add(k)
                        self.stats[(self._table, f"heuristic.{kind}")] += 1
                        continue
                out[nk] = self._sweep(v)
            return out
        if isinstance(node, list):
            return [self._sweep(x) for x in node]
        if isinstance(node, str):
            return self._sweep_str(node)
        return node

    def _sweep_str(self, s: str) -> str:
        if s in self.known_user_ids:
            return self.fake_user_id(s)
        if s.startswith("USER#") and s[5:] in self.known_user_ids:
            return "USER#" + self.fake_user_id(s[5:])
        low = s.lower()
        if low.startswith(f"{self.src_env}/") or "x-amz-signature" in low:
            self.dropped_assets += 1
            self.stats[(self._table, "sweep.s3_dropped")] += 1
            return ""
        return s

    # ---- API publica --------------------------------------------------------------------------------
    def scrub_item(self, table_key: str, item: dict) -> dict:
        """Devuelve una copia scrubbeada. No modifica el item de entrada."""
        if table_key not in RULES:
            raise KeyError(f"Sin reglas de scrub para la tabla {table_key!r}")
        self._table = table_key
        self._touched = set()
        work = copy.deepcopy(item)
        for path, kind in RULES[table_key]:
            work = self._apply_rule(work, path, kind)
        work = self._sweep(work)
        self._touched = set()
        return work


def pii_report(scrubber: Scrubber) -> dict[str, Any]:
    """Resumen sin valores: conteos por (tabla, tipo) y nombres de campos heuristicos."""
    return {
        "counts": {f"{t}:{k}": n for (t, k), n in sorted(scrubber.stats.items())},
        "heuristic_fields": {t: sorted(v) for t, v in sorted(scrubber.heuristic_fields.items())},
        "assets_to_copy": len(scrubber.assets),
        "assets_dropped": scrubber.dropped_assets,
    }
