#!/usr/bin/env python3
"""Crea datos de prueba SINTETICOS en DEV para una cuenta ya sincronizada.

Todo lo creado lleva el prefijo de id `td-` (idempotente: mismos ids, put con overwrite). Un reset borra SOLO
items `td-` de esa cuenta y objetos S3 `td-` bajo dev/. No toca prod de ninguna forma (solo lee el stack dev).

Las formas de los items replican las rutas de codigo de jmanage-api (rama fix/shop): ProductRepo.create,
OrderService.create_order, ClubTournamentService, TournamentService/Team/Match/Event (ver README).

Uso:
  python seed_test_data.py --profile P --account-id vittoriacd                 # dry-run
  python seed_test_data.py --profile P --account-id vittoriacd --confirm       # escribe
  python seed_test_data.py --profile P --account-id vittoriacd --kind shop --reset --confirm --i-understand-this-deletes
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
import zlib
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import safety  # noqa: E402
from seed_dev_from_prod import resolve_tables, stack_outputs  # noqa: E402

PREFIX = "td-"
DEFAULT_BRACKET_ACCOUNT = "sportsmanagedev"
S3_ENV = "dev"
KINDS = ("shop", "club", "bracket", "all")
CENT = Decimal("0.01")
TABLES = ("product", "order", "club_tournament", "club_roster", "club_match", "tournament",
          "tournament_team", "tournament_player", "tournament_match", "tournament_match_event")
READ_TABLES = ("account", "user", "memberships")
KEY_ATTRS = {"product": ["pk", "sk"]}


class TestDataError(Exception):
    pass


# ---- utilidades -------------------------------------------------------------------------------------
def money(v: Any) -> Decimal:
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def clean_segment(segment: str) -> str:
    """Igual que s3_keys._clean."""
    s = segment.replace("/", "").replace("\\", "")
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return s or "file"


def product_image_key(account_id: str, product_id: str, filename: str) -> str:
    """Igual que KeyBuilder(env='dev').product_image: dev/accounts/<acc>/products/<pid>/<file>."""
    return "/".join([S3_ENV, "accounts", clean_segment(account_id), "products",
                     clean_segment(product_id), clean_segment(filename)])


def products_prefix(account_id: str) -> str:
    return f"{S3_ENV}/accounts/{clean_segment(account_id)}/products/{PREFIX}"


def public_url(bucket: str, region: str, key: str) -> str:
    """Igual que s3_adapter.public_url_for."""
    return f"https://{bucket}.s3.{region}.amazonaws.com/{quote(key, safe='/')}"


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """PNG RGB de color solido, valido, en Python puro."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


PALETTE = [(220, 53, 69), (13, 110, 253), (25, 135, 84), (255, 193, 7), (111, 66, 193),
           (253, 126, 20), (32, 201, 151), (214, 51, 132), (108, 117, 125), (13, 202, 240)]


def effective_price(price: Decimal, sale: Decimal | None) -> Decimal:
    """Igual que product_rules.effective_price: sale valido (0 < sale < price) o price."""
    if sale is not None and 0 < sale < price:
        return sale
    return price


def build_gsi_attrs(item: dict) -> dict:
    """Igual que product_repo_ddb._build_gsi_attrs."""
    account_id = item.get("account_id") or "_NO_ACCOUNT"
    category = item.get("category") or "_UNCAT"
    created_at = item["created_at"]
    neg_total_sold = -Decimal(item.get("total_sold", 0) or 0)
    price = effective_price(item["price"], item.get("price_sale"))
    first_tag = (item.get("tags") or item.get("genders") or ["_NO_TAG"])[0]
    return {
        "gsi1_pk": f"ACCOUNT#{account_id}#CAT#{category}", "gsi1_sk": created_at,
        "gsi2_pk": f"ACCOUNT#{account_id}#FEATURED", "gsi2_sk": neg_total_sold,
        "gsi3_pk": f"ACCOUNT#{account_id}#PRICE", "gsi3_sk": price,
        "gsi5_pk": f"ACCOUNT#{account_id}#TAG#{str(first_tag).lower()}", "gsi5_sk": created_at,
        "neg_total_sold": neg_total_sold,
    }


# ---- SHOP -----------------------------------------------------------------------------------------------
# final_available = stock final (despues de las ordenes no canceladas). sale: precio con descuento valido.
PRODUCT_SPECS = [
    dict(n=1, name="Camiseta Oficial Test", category="T-shirts", price="89900", sale="69900", final=40,
         sizes=["S", "M", "L", "XL"], colors=["#FF4842", "#1890FF"], gender=["Men"], tags=["camiseta", "oficial"]),
    dict(n=2, name="Pantaloneta de Juego Test", category="Apparel", price="59900", sale=None, final=25,
         sizes=["S", "M", "L"], colors=["#000000", "#FFFFFF"], gender=["Men"], tags=["pantaloneta"]),
    dict(n=3, name="Guayos Pro Test", category="Shoes", price="250000", sale="199999.50", final=12,
         sizes=["39", "40", "41", "42", "43"], colors=["#000000"], gender=["Men", "Women"], tags=["guayos"]),
    dict(n=4, name="Morral Deportivo Test", category="Backpacks and bags", price="120000", sale=None, final=0,
         sizes=[], colors=["#00AB55"], gender=["Kids"], tags=["morral"]),          # agotado
    dict(n=5, name="Balon Entrenamiento Test", category="Accessories", price="45000", sale=None, final=2,
         sizes=[], colors=["#FFC107"], gender=["Men", "Women"], tags=["balon"]),   # poco stock
    dict(n=6, name="Chaqueta Impermeable Test", category="Clothing", price="185000", sale=None, final=8,
         sizes=["M", "L", "XL"], colors=["#1890FF"], gender=["Women"], tags=["chaqueta"]),
    dict(n=7, name="Medias Tecnicas Test", category="Accessories", price="22000", sale=None, final=60,
         sizes=["S", "M"], colors=["#FFFFFF", "#000000"], gender=["Kids"], tags=["medias"]),
    dict(n=8, name="Camiseta Edicion Limitada Test (borrador)", category="T-shirts", price="95000", sale=None,
         final=15, sizes=["M", "L"], colors=["#7F00FF"], gender=["Men"], tags=["limitada"], publish="draft"),
]

# Ordenes: (n, estado, persona, [(n_producto, cantidad, color_idx, size_idx)], shipping, discount)
ORDER_SPECS = [
    dict(n=1, status="pending", who="owner", lines=[(1, 2, 0, 1), (5, 1, 0, None)], shipping=8000, discount=0),
    dict(n=2, status="paid", who="owner", lines=[(3, 1, 0, 2), (2, 2, 0, 1)], shipping=12000, discount=5000),
    dict(n=3, status="completed", who="persona", lines=[(6, 1, 0, 1), (7, 3, 0, 0)], shipping=8000, discount=0),
    dict(n=4, status="cancelled", who="owner", lines=[(1, 1, 1, 2)], shipping=8000, discount=0),
]
SOLD_STATUSES = ("pending", "processing", "paid", "completed")  # no restauran stock


def order_sold_qty(n_product: int) -> int:
    return sum(q for o in ORDER_SPECS if o["status"] in SOLD_STATUSES for (p, q, _c, _s) in o["lines"] if p == n_product)


def build_shop(account_id: str, bucket: str, region: str, now: datetime, owner: dict | None, persona: dict | None,
               workspace_id: str | None) -> dict[str, Any]:
    products: list[dict] = []
    images: list[tuple[str, bytes]] = []
    by_n: dict[int, dict] = {}
    for spec in PRODUCT_SPECS:
        n = spec["n"]
        pid = f"{PREFIX}prod-{n:02d}"
        sold = order_sold_qty(n)
        price = money(spec["price"])
        sale = money(spec["sale"]) if spec["sale"] else None
        if sale is not None and not (0 < sale < price):
            raise TestDataError("priceSale invalido en la especificacion")
        keys = [product_image_key(account_id, pid, f"{PREFIX}{n:02d}-{i}.png") for i in (1, 2)]
        color = PALETTE[(n - 1) % len(PALETTE)]
        for i, key in enumerate(keys):
            shade = tuple(min(255, c + 40 * i) for c in color)
            images.append((key, make_png(32, 32, shade)))
        available = int(spec["final"])
        item = {
            "pk": f"PRODUCT#{pid}", "sk": "PRODUCT", "id": pid, "account_id": account_id,
            "created_at": iso(now - timedelta(hours=n)),
            "name": spec["name"], "category": spec["category"], "price": price,
            "publish": spec.get("publish", "published"),
            "available": available, "quantity": available + sold,
            "taxes": Decimal("0"), "price_sale": sale,
            "inventory_type": "out of stock" if available == 0 else "low stock" if available <= 5 else "in stock",
            "code": f"TD-{n:02d}", "sku": f"TD-SKU-{n:02d}",
            "description_html": f"<p>Producto sintetico de prueba #{n}. No es un articulo real.</p>",
            "sub_description": "Dato de prueba (td-)",
            "cover_url": keys[0], "genders": spec["gender"], "tags": spec["tags"], "images": keys,
            "colors": spec["colors"], "sizes": spec["sizes"], "ratings_buckets": [], "reviews": [],
            "total_ratings": Decimal("0"), "total_sold": sold, "total_reviews": 0,
            "new_label": {"enabled": True, "content": "NEW"},
        }
        item.update(build_gsi_attrs(item))
        products.append(item)
        by_n[n] = item

    orders: list[dict] = []
    warnings: list[str] = []
    for spec in ORDER_SPECS:
        person = owner if spec["who"] == "owner" else (persona or owner)
        if not person or not workspace_id:
            warnings.append(f"orden {spec['n']} omitida: falta usuario dev o workspace")
            continue
        orders.append(_build_order(spec, account_id, workspace_id, bucket, region, now, person, by_n))
    return {"product": products, "order": orders, "images": images, "warnings": warnings}


def _build_order(spec: dict, account_id: str, workspace_id: str, bucket: str, region: str, now: datetime,
                 person: dict, by_n: dict[int, dict]) -> dict:
    oid = f"{PREFIX}ord-{spec['n']:02d}"
    created = now - timedelta(days=10 - spec["n"], hours=2)
    items, priced = [], []
    for (pn, qty, ci, si) in spec["lines"]:
        p = by_n[pn]
        if p["publish"] != "published":
            raise TestDataError("una orden no puede referenciar un producto borrador")
        unit = effective_price(p["price"], p.get("price_sale"))
        items.append({
            "id": p["id"], "sku": p["sku"], "quantity": qty, "name": p["name"],
            "cover_url": public_url(bucket, region, p["cover_url"]), "price": money(unit),
            "available": int(p["available"]),
            "colors": [p["colors"][ci]] if p["colors"] else [],
            "size": p["sizes"][si] if (p["sizes"] and si is not None) else "",
        })
        priced.append((unit, qty))
    subtotal = money(sum((u * q for u, q in priced), Decimal("0")))
    shipping, discount = money(spec["shipping"]), money(spec["discount"])
    if discount > subtotal:
        raise TestDataError("descuento mayor al subtotal")
    total = money(subtotal - discount + shipping)

    def ev(kind: str, title: str, minutes: int, meta: dict | None = None) -> dict:
        e = {"type": kind, "title": title, "time": iso(created + timedelta(minutes=minutes))}
        if meta:
            e["meta"] = meta
        return e

    history = [ev("order_created", "Orden creada", 0)]
    status = spec["status"]
    provider = delivery = None
    if status in ("paid", "completed"):
        history.append(ev("payment_paid", "Pago confirmado", 30))
        history.append(ev("order_status_changed", "Orden: paid", 31, {"from": "pending", "to": "paid"}))
    if status == "completed":
        provider = {"checked": True, "checked_at": iso(created + timedelta(hours=5)), "checked_by": person["id"], "note": None}
        delivery = {"checked": True, "checked_at": iso(created + timedelta(days=1)), "checked_by": person["id"], "note": None}
        history.append(ev("provider_check_on", "Pedido al proveedor confirmado", 300, {"by": person["id"]}))
        history.append(ev("delivery_check_on", "Entrega confirmada", 1440, {"by": person["id"]}))
        history.append(ev("order_status_changed", "Orden: completed", 1441, {"from": "paid", "to": "completed"}))
    if status == "cancelled":
        history.append(ev("order_status_changed", "Orden: cancelled", 20,
                          {"from": "pending", "to": "cancelled", "reason": "test_data"}))
    return {
        "id": oid, "account_id": account_id, "workspace_id": workspace_id,
        "order_number": f"#{created.strftime('%y%m%d')}-TD{spec['n']:06d}",
        "created_at": iso(created), "taxes": Decimal("0"), "items": items, "history": history,
        "subtotal": subtotal, "shipping": shipping, "discount": discount, "total_amount": total,
        "customer": {"id": person["id"], "name": person.get("user_name") or person["email"],
                     "email": person["email"], "phone_number": "", "avatar_url": None},
        "delivery": {"shipment_amount": shipping, "delivery_type": "standard"},
        "total_quantity": sum(q for _u, q in priced),
        "shipping_address": {"full_address": "Calle 1 # 2-3, Ciudad de Prueba", "address_type": "Home", "company": ""},
        "payment": {"payment": "bank_transfer", "card_type": None, "card_number": None},
        "status": status, "payment_request_id": None,
        "provider_check": provider, "delivery_check": delivery,
    }


# ---- CLUB -----------------------------------------------------------------------------------------------
POSITIONS = ["Portero", "Defensa", "Defensa", "Defensa", "Defensa", "Mediocampista", "Mediocampista",
             "Mediocampista", "Delantero", "Delantero", "Delantero", "Defensa", "Mediocampista", "Delantero"]
CLUB_TOURNAMENTS = [("a", "Copa Test A", "Sub-20"), ("b", "Liga Test B", "Libre")]
MATCH_OFFSETS = [-35, -21, -9, 6, 20]  # dias respecto a hoy: 3 pasados, 2 futuros
RIVALS = ["Rival Test FC", "Atletico Prueba", "Deportivo Demo", "Union Ficticia", "Club Sintetico"]


def build_club(account_id: str, workspace_id: str, user_ids: list[str], now: datetime, today: date) -> dict[str, Any]:
    tournaments, roster, matches = [], [], []
    warnings: list[str] = []
    for ti, (tk, name, category) in enumerate(CLUB_TOURNAMENTS):
        tid = f"{PREFIX}ct-{tk}"
        tournaments.append({
            "id": tid, "account_id": account_id, "workspace_id": workspace_id, "name": name, "category": category,
            "created_at": iso(now - timedelta(days=60 - ti)), "updated_at": iso(now - timedelta(days=60 - ti)),
        })
        pool = user_ids[ti * 2:] + user_ids[:ti * 2]  # rotacion deterministica distinta por torneo
        chosen = pool[:12]
        if len(chosen) < 12:
            warnings.append(f"{name}: solo {len(chosen)} usuarios reales en la cuenta (se pidieron 12)")
        entries = []
        for i, uid in enumerate(chosen):
            entries.append({"id": f"{PREFIX}cr-{tk}-{i + 1:02d}", "user_id": uid, "number": i + 1})
        for g in range(2):
            entries.append({"id": f"{PREFIX}cr-{tk}-g{g + 1}", "guest_name": f"Invitado Test {tk.upper()}{g + 1}",
                            "number": 20 + g})
        for idx, e in enumerate(entries):
            row = {"id": e["id"], "account_id": account_id, "workspace_id": workspace_id, "tournament_id": tid,
                   "number": e["number"], "position": POSITIONS[idx % len(POSITIONS)],
                   "created_at": iso(now - timedelta(days=59)), "updated_at": iso(now - timedelta(days=59))}
            if "user_id" in e:                      # user_id usa un GSI disperso: los invitados NO llevan el atributo
                row["user_id"] = e["user_id"]
            else:
                row["guest_name"] = e["guest_name"]
            roster.append(row)
        for mi, off in enumerate(MATCH_OFFSETS):
            d = today + timedelta(days=off + ti)
            lineup = None
            if off < 0:
                lineup_entries = []
                for idx, e in enumerate(entries):
                    if idx < 11:
                        st, called, minutes = "titular", True, 90 if idx % 4 else 65
                    elif idx < 13:
                        st, called, minutes = "suplente", True, 25 if idx == 11 else 0
                    else:
                        st, called, minutes = "", False, 0
                    lineup_entries.append({"roster_entry_id": e["id"], "called_up": called, "status": st, "minutes": minutes})
                lineup = {"entries": lineup_entries, "saved_at": iso(datetime.combine(d, datetime.min.time(), timezone.utc) + timedelta(hours=22))}
            matches.append({
                "id": f"{PREFIX}cm-{tk}-{mi + 1}", "account_id": account_id, "workspace_id": workspace_id,
                "tournament_id": tid, "date": d.isoformat(), "rival": RIVALS[(mi + ti) % len(RIVALS)],
                "calendar_event_id": None, "lineup": lineup,
                "created_at": iso(now - timedelta(days=58)), "updated_at": iso(now - timedelta(days=58)),
            })
    return {"club_tournament": tournaments, "club_roster": roster, "club_match": matches, "warnings": warnings}


# ---- BRACKET (torneo de liga) -----------------------------------------------------------------------------
TEAM_NAMES = ["Equipo Test Alfa", "Equipo Test Beta", "Equipo Test Gamma", "Equipo Test Delta"]
TEAM_SHORT = ["TAL", "TBE", "TGA", "TDE"]
TEAM_COLORS = ["#d32f2f", "#1976d2", "#388e3c", "#fbc02d"]
PLAYER_POSITIONS = ["Goalkeeper", "Defender", "Defender", "Defender", "Midfielder", "Forward"]
# Todas las parejas una vez (liga a una vuelta, 3 fechas). Indices de equipo (local, visitante).
FIXTURES = [[(0, 3), (1, 2)], [(0, 2), (3, 1)], [(0, 1), (2, 3)]]
FINISHED_WEEKS = 2
# Eventos por partido finalizado (en orden de fechas): (tipo, minuto, equipo 0=local/1=visitante, jugador, asistente|None)
MATCH_EVENTS = {
    (0, 0): [("goal", 12, 0, 5, 4), ("goal", 55, 1, 5, None), ("yellow_card", 60, 1, 2, None), ("goal", 80, 0, 4, None)],
    (0, 1): [("goal", 30, 0, 5, None), ("penalty_scored", 70, 1, 5, None), ("red_card", 85, 0, 3, None),
             ("yellow_card", 40, 1, 1, None)],
    (1, 0): [("goal", 20, 0, 5, 4), ("goal", 25, 0, 4, None), ("goal", 88, 1, 5, None), ("yellow_card", 50, 0, 2, None)],
    (1, 1): [("own_goal", 15, 0, 1, None), ("goal", 44, 1, 4, 5), ("second_yellow", 77, 0, 2, None)],
}

RULES = {"points_per_win": 3, "points_per_draw": 1, "points_per_loss": 0, "total_matchweeks": 3, "legs": 1,
         "yellow_cards_for_suspension": 5, "extra_time_allowed": True, "penalties_allowed": True,
         "max_substitutions": 5, "yellow_card_fee": 0, "red_card_fee": 0}
GOAL_TYPES = ("goal", "penalty_scored")
RED_TYPES = ("red_card", "second_yellow")


def _zero_team() -> dict:
    return {"played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0,
            "goal_difference": 0, "points": 0, "yellow_cards": 0, "red_cards": 0, "form": []}


def _zero_player() -> dict:
    return {"appearances": 0, "goals": 0, "penalties": 0, "own_goals": 0, "assists": 0,
            "yellow_cards": 0, "red_cards": 0}


def compute_stats(matches: list[dict], events: list[dict], team_ids: list[str], player_ids: list[str]):
    """Misma semantica que tournament_aggregator.recompute_tournament (liga, fase de grupos)."""
    team = {t: _zero_team() for t in team_ids}
    player = {p: _zero_player() for p in player_ids}
    tour = {"total_goals": 0, "total_yellow_cards": 0, "total_red_cards": 0,
            "total_matches": len(matches), "matches_played": 0}
    for m in sorted(matches, key=lambda x: x["date"]):
        if m["status"] != "finished":
            continue
        sh, sa = int(m["score_home"]), int(m["score_away"])
        tour["matches_played"] += 1
        h, a = team[m["home_team_id"]], team[m["away_team_id"]]
        for t, gf, ga in ((h, sh, sa), (a, sa, sh)):
            t["played"] += 1
            t["goals_for"] += gf
            t["goals_against"] += ga
            t["goal_difference"] = t["goals_for"] - t["goals_against"]
        if sh > sa:
            h["won"] += 1; h["points"] += 3; a["lost"] += 1; hr, ar = "W", "L"
        elif sh < sa:
            a["won"] += 1; a["points"] += 3; h["lost"] += 1; hr, ar = "L", "W"
        else:
            h["drawn"] += 1; a["drawn"] += 1; h["points"] += 1; a["points"] += 1; hr = ar = "D"
        h["form"] = (h["form"] + [{"match_id": m["id"], "result": hr}])[-5:]
        a["form"] = (a["form"] + [{"match_id": m["id"], "result": ar}])[-5:]
    for ev in events:
        et = ev["type"]
        p = player.get(ev["player_id"])
        t = team.get(ev["team_id"])
        if et in GOAL_TYPES:
            p["goals"] += 1
            if et == "penalty_scored":
                p["penalties"] += 1
            tour["total_goals"] += 1
            if ev.get("assist_player_id"):
                player[ev["assist_player_id"]]["assists"] += 1
        elif et == "own_goal":
            p["own_goals"] += 1
            tour["total_goals"] += 1
        elif et == "yellow_card":
            p["yellow_cards"] += 1; t["yellow_cards"] += 1; tour["total_yellow_cards"] += 1
        elif et in RED_TYPES:
            p["red_cards"] += 1; t["red_cards"] += 1; tour["total_red_cards"] += 1
    return team, player, tour


def score_from_events(events: list[dict], home: str, away: str) -> tuple[int, int]:
    """Igual que TournamentMatchService._compute_score."""
    sh = sa = 0
    for ev in events:
        et, tid = ev["type"], ev["team_id"]
        if et in GOAL_TYPES:
            sh += tid == home
            sa += tid == away
        elif et == "own_goal":
            sa += tid == home
            sh += tid == away
    return sh, sa


def build_bracket(account_id: str, now: datetime, today: date) -> dict[str, Any]:
    tid = f"{PREFIX}trn-1"
    team_ids = [f"{PREFIX}ttm-{i + 1}" for i in range(4)]
    teams, players = [], []
    player_ids: dict[tuple[int, int], str] = {}
    for ti in range(4):
        for pi in range(6):
            pid = f"{PREFIX}tpl-{ti + 1}-{pi + 1:02d}"
            player_ids[(ti, pi)] = pid
            players.append({"id": pid, "tournament_id": tid, "team_id": team_ids[ti],
                            "name": f"Jugador Test {TEAM_SHORT[ti]}-{pi + 1:02d}", "position": PLAYER_POSITIONS[pi],
                            "number": pi + 1, "id_number": f"TD{ti + 1}{pi + 1:04d}", "avatar_url": "",
                            "created_at": iso(now - timedelta(days=30))})
    matches, events = [], []
    for wi, week in enumerate(FIXTURES):
        for mi, (hi, ai) in enumerate(week):
            mid = f"{PREFIX}mtc-{wi + 1}-{mi + 1}"
            d = today + timedelta(days=7 * (wi - FINISHED_WEEKS) + 1)
            finished = wi < FINISHED_WEEKS
            match = {"id": mid, "tournament_id": tid, "home_team_id": team_ids[hi], "away_team_id": team_ids[ai],
                     "date": f"{d.isoformat()}T18:00:00", "venue": "Cancha de Prueba", "matchweek": wi + 1,
                     "round": "", "group_id": "", "status": "finished" if finished else "scheduled",
                     "score_home": 0, "score_away": 0, "created_at": iso(now - timedelta(days=29))}
            if finished:
                mevs = []
                for ei, (et, minute, side, pl, asst) in enumerate(MATCH_EVENTS[(wi, mi)], start=1):
                    team_idx = hi if side == 0 else ai
                    mevs.append({"id": f"{PREFIX}mev-{wi + 1}-{mi + 1}-{ei:02d}", "match_id": mid, "type": et,
                                 "minute": minute, "stoppage_time": None,
                                 "player_id": player_ids[(team_idx, pl)],
                                 "assist_player_id": player_ids[(team_idx, asst)] if asst is not None else None,
                                 "team_id": team_ids[team_idx], "event_index": ei,
                                 "created_at": iso(now - timedelta(days=28))})
                match["score_home"], match["score_away"] = score_from_events(mevs, team_ids[hi], team_ids[ai])
                events.extend(mevs)
            matches.append(match)
    team_stats, player_stats, tour_stats = compute_stats(matches, events, team_ids, [p["id"] for p in players])
    for ti in range(4):
        teams.append({"id": team_ids[ti], "tournament_id": tid, "name": TEAM_NAMES[ti], "short_name": TEAM_SHORT[ti],
                      "logo_url": "", "seed": ti + 1, "manager_name": "", "contact_email": "", "contact_phone": "",
                      "primary_color": TEAM_COLORS[ti], "rules_accepted": True, "documents": {},
                      "manager_user_ids": [], "created_at": iso(now - timedelta(days=30)),
                      "stats": team_stats[team_ids[ti]]})
    for p in players:
        p["stats"] = player_stats[p["id"]]
    unfinished = [m["matchweek"] for m in matches if m["status"] != "finished"]
    tournament = {
        "id": tid, "account_id": account_id, "name": "Copa Test Torneo", "season": "2026-TEST", "type": "league",
        "status": "active", "is_public": True, "current_matchweek": min(unfinished) if unfinished else 3,
        "rules": dict(RULES), "groups": [], "bracket": {}, "sport": "futbol", "teams_per_group": None,
        "num_teams": 4, "tiebreaker_order": [], "options": {}, "description": "Torneo sintetico de prueba (td-)",
        "logo_url": "", "start_date": (today - timedelta(days=14)).isoformat(), "end_date": (today + timedelta(days=14)).isoformat(),
        "location": "Ciudad de Prueba", "created_at": iso(now - timedelta(days=30)), "payments_enabled": False,
        "team_count": 4, "stats": tour_stats,
    }
    return {"tournament": [tournament], "tournament_team": teams, "tournament_player": players,
            "tournament_match": matches, "tournament_match_event": events, "warnings": []}


# ---- ejecucion ---------------------------------------------------------------------------------------------
def item_key(table_key: str, item: dict) -> dict:
    return {a: item[a] for a in KEY_ATTRS.get(table_key, ["id"])}


def assert_td(table_key: str, items: list[dict]) -> None:
    for it in items:
        if not str(it.get("id", "")).startswith(PREFIX):
            raise safety.SafetyError(f"Item sin prefijo {PREFIX} en {table_key}: no se escribe.")


def is_td_item(table_key: str, item: dict, account_id: str) -> bool:
    """Un item es nuestro si su id empieza con td- y (para tablas con cuenta) pertenece a la cuenta."""
    if not str(item.get("id", "")).startswith(PREFIX):
        return False
    if table_key in ("tournament_team", "tournament_player", "tournament_match"):
        return str(item.get("tournament_id", "")).startswith(PREFIX)
    if table_key == "tournament_match_event":
        return str(item.get("match_id", "")).startswith(PREFIX)
    return item.get("account_id") == account_id


def validate_args(args: argparse.Namespace) -> None:
    if not getattr(args, "account_id", None):
        raise safety.SafetyError("--account-id es obligatorio.")
    if args.kind not in KINDS:
        raise safety.SafetyError(f"--kind debe ser uno de {KINDS}.")
    if args.reset and not args.confirm:
        raise safety.SafetyError("--reset requiere --confirm.")
    if args.reset and not args.i_understand_this_deletes:
        raise safety.SafetyError("--reset requiere --i-understand-this-deletes.")


def scan(table: Any) -> list[dict]:
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def tables_for(kinds: set[str]) -> list[str]:
    out = []
    if "shop" in kinds:
        out += ["product", "order"]
    if "club" in kinds:
        out += ["club_tournament", "club_roster", "club_match"]
    if "bracket" in kinds:
        out += ["tournament", "tournament_team", "tournament_player", "tournament_match", "tournament_match_event"]
    return out


def run(args: argparse.Namespace, session: Any, today: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    validate_args(args)
    safety.assert_region(args.region)
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    kinds = {"shop", "club", "bracket"} if args.kind == "all" else {args.kind}

    cf = session.client("cloudformation", region_name=args.region)
    out = stack_outputs(cf, args.dev_stack)
    safety.assert_dev_stack(args.dev_stack, out)           # nombre, EnvUsed=dev y User Pool dev
    names = resolve_tables(cf, args.dev_stack, out)
    safety.assert_dev_tables(names, {})                    # todas deben empezar con JmanageInfraStack-dev-
    bucket = args.bucket
    ddb = session.resource("dynamodb", region_name=args.region)
    t = {k: ddb.Table(v) for k, v in names.items()}

    account = t["account"].get_item(Key={"id": args.account_id}).get("Item")
    if not account:
        raise TestDataError("La cuenta no existe en dev (sincronizala primero con seed_dev_from_prod.py).")
    bracket_skipped = False
    if "bracket" in kinds:
        b = t["account"].get_item(Key={"id": args.bracket_account_id}).get("Item")
        if not b or (b.get("settings") or {}).get("account_type") != "tournament":
            if args.kind != "all":
                raise TestDataError("--bracket-account-id no es una cuenta tipo tournament en dev.")
            kinds.discard("bracket")  # con --kind all se omite el bracket (con aviso) y se siguen shop/club
            bracket_skipped = True

    summary: dict[str, Any] = {"mode": "confirm" if args.confirm else "dry-run", "reset": bool(args.reset),
                               "kinds": sorted(kinds), "tables": {}, "warnings": [], "s3": {}}
    if bracket_skipped:
        summary["warnings"].append("bracket omitido: la cuenta de torneo no existe o no es tipo tournament")
    s3 = session.client("s3", region_name=args.region)

    if args.reset:
        _reset(args, kinds, t, s3, bucket, summary)
        return summary

    users = scan(t["user"])
    by_email = {u.get("email"): u for u in users}
    owner = by_email.get(safety.owner_email())
    persona = next((by_email[e] for e in sorted(safety.PERSONA_EMAILS) if e in by_email), None)
    workspace_id = (account.get("settings") or {}).get("default_workspace")
    data: dict[str, Any] = {"images": [], "warnings": []}

    if "shop" in kinds:
        if not owner:
            summary["warnings"].append("usuario dueno no existe en dev: ordenes omitidas (corre create_dev_users.py)")
        shop = build_shop(args.account_id, bucket, args.region, now, owner, persona, workspace_id)
        data.update({k: shop[k] for k in ("product", "order")})
        data["images"] += shop["images"]
        data["warnings"] += shop["warnings"]
    if "club" in kinds:
        if not workspace_id:
            raise TestDataError("La cuenta no tiene settings.default_workspace.")
        personas = {u["id"] for u in users if u.get("email") in safety.persona_emails()}
        mem = [m for m in scan(t["memberships"]) if m.get("ACCOUNT_ID") == args.account_id]
        user_ids = sorted({m["USER_ID"] for m in mem if m.get("USER_ID") and m["USER_ID"] not in personas
                           and m["USER_ID"] in {u["id"] for u in users}})
        club = build_club(args.account_id, workspace_id, user_ids, now, today)
        data.update({k: club[k] for k in ("club_tournament", "club_roster", "club_match")})
        data["warnings"] += club["warnings"]
    if "bracket" in kinds:
        br = build_bracket(args.bracket_account_id, now, today)
        data.update({k: br[k] for k in tables_for({"bracket"})})

    summary["warnings"] += data["warnings"]
    for key in tables_for(kinds):
        items = data.get(key, [])
        assert_td(key, items)
        summary["tables"][key] = {"items": len(items), "written": 0}
        if args.confirm and items:
            with t[key].batch_writer(overwrite_by_pkeys=list(KEY_ATTRS.get(key, ["id"]))) as bw:
                for it in items:
                    bw.put_item(Item=it)
            summary["tables"][key]["written"] = len(items)
    images = data["images"]
    summary["s3"] = {"planned": len(images), "uploaded": 0}
    for key, _body in images:
        if not key.startswith(f"{S3_ENV}/") or not key.startswith(products_prefix(args.account_id)):
            raise safety.SafetyError("Clave S3 fuera de dev/ o sin prefijo td-.")
    if args.confirm:
        for key, body in images:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="image/png")
        summary["s3"]["uploaded"] = len(images)
    return summary


def _reset(args, kinds, t, s3, bucket, summary) -> None:
    for key in tables_for(kinds):
        acc = args.bracket_account_id if key.startswith("tournament") else args.account_id
        victims = [i for i in scan(t[key]) if is_td_item(key, i, acc)]
        summary["tables"][key] = {"delete": len(victims), "deleted": 0}
        for it in victims:  # re-chequeo por item antes de borrar
            if not is_td_item(key, it, acc):
                raise safety.SafetyError("Borrado rechazado: el item no es td- de la cuenta.")
        if victims:
            with t[key].batch_writer(overwrite_by_pkeys=list(KEY_ATTRS.get(key, ["id"]))) as bw:
                for it in victims:
                    bw.delete_item(Key=item_key(key, it))
            summary["tables"][key]["deleted"] = len(victims)
    if "shop" in kinds:
        prefix = products_prefix(args.account_id)
        keys, token = [], None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in resp.get("Contents", [])]
            token = resp.get("NextContinuationToken")
            if not token:
                break
        for k in keys:
            if not k.startswith(prefix):
                raise safety.SafetyError("Borrado S3 rechazado: clave fuera del prefijo td-.")
        for i in range(0, len(keys), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})
        summary["s3"] = {"deleted": len(keys)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile")
    p.add_argument("--region", default=safety.DEV_REGION)
    p.add_argument("--dev-stack", default=safety.DEV_STACK_NAME)
    p.add_argument("--bucket", default=safety.DEFAULT_BUCKET)
    p.add_argument("--account-id", required=True)
    p.add_argument("--kind", choices=KINDS, default="all")
    p.add_argument("--bracket-account-id", default=DEFAULT_BRACKET_ACCOUNT)
    p.add_argument("--reset", action="store_true", help="Borra SOLO items td- (requiere --confirm)")
    p.add_argument("--i-understand-this-deletes", dest="i_understand_this_deletes", action="store_true")
    p.add_argument("--confirm", action="store_true", help="Escribir en dev. Sin esto es dry-run.")
    return p


def format_summary(summary: dict[str, Any]) -> str:
    lines = [f"Modo: {summary['mode']}{' (reset)' if summary['reset'] else ''}  kinds={','.join(summary['kinds'])}"]
    for k, v in summary["tables"].items():
        lines.append(f"  {k}: " + " ".join(f"{a}={b}" for a, b in v.items()))
    if summary["s3"]:
        lines.append("  s3: " + " ".join(f"{a}={b}" for a, b in summary["s3"].items()))
    lines += [f"  AVISO: {w}" for w in summary["warnings"]]
    if summary["mode"] == "dry-run":
        lines.append("DRY-RUN: no se escribio nada. Usa --confirm.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import boto3

    try:
        validate_args(args)
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        summary = run(args, session)
    except (safety.SafetyError, TestDataError) as exc:
        print(f"RECHAZADO: {exc}", file=sys.stderr)
        return 2
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
