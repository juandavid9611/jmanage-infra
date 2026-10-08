import argparse
import copy
import struct
import unittest
import zlib
from datetime import date, datetime, timezone
from decimal import Decimal

from tests import _fakes as F  # noqa: E402
import safety  # noqa: E402
import seed_test_data as td  # noqa: E402

DEV = safety.DEV_STACK_NAME
ACC = "vittoriacd"
BR = "sportsmanagedev"
OWNER = safety.owner_email()
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 6, 1)
BUCKET = "jmanage-bucket"


def d(key):
    return f"{DEV}-{key}"


def args(**kw):
    base = dict(region="us-west-2", dev_stack=DEV, bucket=BUCKET, account_id=ACC, kind="all",
                bracket_account_id=BR, reset=False, i_understand_this_deletes=False, confirm=False, profile=None)
    base.update(kw)
    return argparse.Namespace(**base)


def dev_data():
    users = [{"id": "owner-sub", "user_name": "Dueno", "email": OWNER},
             {"id": "persona-sub", "user_name": "Dev User", "email": "dev.user@example.test"}]
    users += [{"id": f"u{i:02d}", "user_name": f"Fake {i}", "email": f"user{i}@example.test"} for i in range(14)]
    mem = [{"PK": f"USER#{u['id']}", "SK": f"ACCOUNT#{ACC}#WORKSPACE#ws1", "ACCOUNT_ID": ACC, "USER_ID": u["id"]}
           for u in users]
    return {
        d("account"): [{"id": ACC, "settings": {"default_workspace": "ws1", "account_type": "club"}},
                       {"id": BR, "settings": {"default_workspace": "wsb", "account_type": "tournament"}}],
        d("user"): users, d("memberships"): mem,
    }


class RecordingCF(F.FakeCF):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.stacks = []

    def describe_stacks(self, StackName):
        self.stacks.append(StackName)
        return super().describe_stacks(StackName)


def session(data=None, s3=None, cf=None):
    ddb = F.FakeDynamo(copy.deepcopy(data if data is not None else dev_data()))
    return F.FakeSession(cf or RecordingCF(), ddb, s3 or F.FakeS3()), ddb


def execute(sess, **kw):
    return td.run(args(**kw), sess, today=TODAY, now=NOW)


class PngTests(unittest.TestCase):
    def test_png_is_valid(self):
        png = td.make_png(32, 32, (10, 20, 30))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos, chunks, idat = 8, [], b""
        while pos < len(png):
            (n,) = struct.unpack(">I", png[pos:pos + 4])
            tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
            (crc,) = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])
            self.assertEqual(crc, zlib.crc32(tag + data) & 0xFFFFFFFF)
            chunks.append(tag)
            if tag == b"IDAT":
                idat += data
            if tag == b"IHDR":
                self.assertEqual(struct.unpack(">II", data[:8]), (32, 32))
            pos += 12 + n
        self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(len(zlib.decompress(idat)), 32 * (1 + 3 * 32))


# Atributos que ProductRepo.create / _build_gsi_attrs (fix/shop) escriben.
PRODUCT_KEYS = {"pk", "sk", "id", "account_id", "created_at", "name", "category", "price", "publish", "available",
                "quantity", "taxes", "price_sale", "inventory_type", "code", "sku", "description_html",
                "sub_description", "cover_url", "genders", "tags", "images", "colors", "sizes", "ratings_buckets",
                "reviews", "total_ratings", "total_sold", "total_reviews", "new_label",
                "gsi1_pk", "gsi1_sk", "gsi2_pk", "gsi2_sk", "gsi3_pk", "gsi3_sk", "gsi5_pk", "gsi5_sk", "neg_total_sold"}
# Atributos que OrderService.create_order escribe.
ORDER_KEYS = {"id", "account_id", "workspace_id", "order_number", "created_at", "taxes", "items", "history",
              "subtotal", "shipping", "discount", "total_amount", "customer", "delivery", "total_quantity",
              "shipping_address", "payment", "status", "payment_request_id", "provider_check", "delivery_check"}
ORDER_ITEM_KEYS = {"id", "sku", "quantity", "name", "cover_url", "price", "available", "colors", "size"}
CUSTOMER_KEYS = {"id", "name", "email", "phone_number", "avatar_url"}


class ShopShapeTests(unittest.TestCase):
    def setUp(self):
        users = dev_data()[d("user")]
        self.shop = td.build_shop(ACC, BUCKET, "us-west-2", NOW, users[0], users[1], "ws1")
        self.products = {p["id"]: p for p in self.shop["product"]}

    def test_product_key_attributes_and_gsi(self):
        self.assertEqual(len(self.products), 8)
        for p in self.products.values():
            self.assertTrue(PRODUCT_KEYS <= set(p), PRODUCT_KEYS - set(p))
            self.assertEqual(p["pk"], f"PRODUCT#{p['id']}")
            self.assertEqual(p["sk"], "PRODUCT")
            self.assertTrue(p["id"].startswith("td-"))
            self.assertEqual(p["gsi1_pk"], f"ACCOUNT#{ACC}#CAT#{p['category']}")
            self.assertEqual(p["gsi2_sk"], -p["total_sold"])
            self.assertEqual(p["gsi3_sk"], td.effective_price(p["price"], p["price_sale"]))
            self.assertEqual(p["gsi5_pk"], f"ACCOUNT#{ACC}#TAG#{p['tags'][0].lower()}")

    def test_scenarios(self):
        ps = list(self.products.values())
        sales = [p for p in ps if p["price_sale"] is not None]
        self.assertEqual(len(sales), 2)
        for p in sales:
            self.assertTrue(0 < p["price_sale"] < p["price"])
        self.assertEqual(sorted(p["available"] for p in ps)[:2], [0, 2])
        self.assertEqual([p["publish"] for p in ps].count("draft"), 1)
        self.assertGreaterEqual(len({p["category"] for p in ps}), 4)
        for p in ps:
            self.assertTrue(45000 <= p["price"] <= 250000 or p["price"] == Decimal("22000"))
            self.assertEqual(p["quantity"] - p["available"], p["total_sold"])

    def test_images_layout_and_cover(self):
        keys = {k for k, _ in self.shop["images"]}
        self.assertEqual(len(keys), 16)
        for p in self.products.values():
            self.assertEqual(p["cover_url"], p["images"][0])
            for k in p["images"]:
                self.assertIn(k, keys)
                self.assertTrue(k.startswith(f"dev/accounts/{ACC}/products/{p['id']}/td-"))
        self.assertEqual(self.products["td-prod-01"]["images"][0], "dev/accounts/vittoriacd/products/td-prod-01/td-01-1.png")

    def test_orders_shape_and_totals(self):
        orders = self.shop["order"]
        self.assertEqual([o["status"] for o in orders], ["pending", "paid", "completed", "cancelled"])
        for o in orders:
            self.assertTrue(ORDER_KEYS <= set(o), ORDER_KEYS - set(o))
            self.assertTrue(CUSTOMER_KEYS <= set(o["customer"]))
            for it in o["items"]:
                self.assertTrue(ORDER_ITEM_KEYS <= set(it))
                p = self.products[it["id"]]
                self.assertEqual(p["publish"], "published")
                self.assertTrue(it["cover_url"].startswith(f"https://{BUCKET}.s3.us-west-2.amazonaws.com/dev/"))
                self.assertEqual(it["price"], td.money(td.effective_price(p["price"], p["price_sale"])))
            sub = sum(it["price"] * it["quantity"] for it in o["items"])
            self.assertEqual(o["subtotal"], td.money(sub))
            self.assertEqual(o["total_amount"], o["subtotal"] - o["discount"] + o["shipping"])
            self.assertEqual(o["total_quantity"], sum(i["quantity"] for i in o["items"]))
            self.assertEqual(o["delivery"]["shipment_amount"], o["shipping"])
        paid = orders[1]
        self.assertEqual(paid["subtotal"], Decimal("199999.50") + 2 * Decimal("59900.00"))  # centavos preservados
        done = orders[2]
        self.assertTrue(done["provider_check"]["checked"] and done["delivery_check"]["checked"])
        self.assertEqual(done["customer"]["email"], "dev.user@example.test")
        self.assertEqual(orders[0]["customer"]["email"], OWNER)
        self.assertIsNone(orders[0]["provider_check"])

    def test_cancelled_order_not_counted_as_sold(self):
        p1 = self.products["td-prod-01"]
        self.assertEqual(p1["total_sold"], 2)  # pending x2; la cancelada (x1) no cuenta


class ClubShapeTests(unittest.TestCase):
    def setUp(self):
        self.uids = [f"u{i:02d}" for i in range(14)]
        self.club = td.build_club(ACC, "ws1", self.uids, NOW, TODAY)

    def test_shapes(self):
        self.assertEqual(len(self.club["club_tournament"]), 2)
        for t in self.club["club_tournament"]:
            self.assertTrue({"id", "account_id", "workspace_id", "name", "category", "created_at", "updated_at"} <= set(t))
        for r in self.club["club_roster"]:
            self.assertTrue({"id", "account_id", "workspace_id", "tournament_id", "number", "position", "created_at", "updated_at"} <= set(r))
            self.assertTrue(("user_id" in r) != ("guest_name" in r))  # XOR: GSI disperso
        for tk in ("a", "b"):
            rows = [r for r in self.club["club_roster"] if r["tournament_id"] == f"td-ct-{tk}"]
            self.assertEqual(len(rows), 14)
            self.assertEqual(sum("guest_name" in r for r in rows), 2)
            self.assertEqual(len({r["user_id"] for r in rows if "user_id" in r}), 12)
        self.assertEqual(len(self.club["club_match"]), 10)

    def test_matches_and_lineups(self):
        for m in self.club["club_match"]:
            self.assertTrue({"id", "account_id", "workspace_id", "tournament_id", "date", "rival",
                             "calendar_event_id", "lineup", "created_at", "updated_at"} <= set(m))
            date.fromisoformat(m["date"])
            past = date.fromisoformat(m["date"]) < TODAY
            self.assertEqual(m["lineup"] is not None, past)
            if past:
                roster = {r["id"] for r in self.club["club_roster"] if r["tournament_id"] == m["tournament_id"]}
                ids = [e["roster_entry_id"] for e in m["lineup"]["entries"]]
                self.assertTrue(set(ids) <= roster and len(ids) == len(set(ids)))
                self.assertIn("saved_at", m["lineup"])
                st = {e["status"] for e in m["lineup"]["entries"]}
                self.assertTrue({"titular", "suplente", ""} <= st)
                self.assertTrue(any(not e["called_up"] for e in m["lineup"]["entries"]))
                for e in m["lineup"]["entries"]:
                    self.assertIn(e["status"], ("titular", "suplente", ""))
                    self.assertIsInstance(e["minutes"], int)

    def test_few_users_warns(self):
        club = td.build_club(ACC, "ws1", ["u1", "u2"], NOW, TODAY)
        self.assertTrue(club["warnings"])


class BracketShapeTests(unittest.TestCase):
    def setUp(self):
        self.br = td.build_bracket(BR, NOW, TODAY)

    def test_counts_and_keys(self):
        self.assertEqual(len(self.br["tournament_team"]), 4)
        self.assertEqual(len(self.br["tournament_player"]), 24)
        self.assertEqual(len(self.br["tournament_match"]), 6)
        t = self.br["tournament"][0]
        self.assertTrue({"id", "account_id", "name", "type", "status", "is_public", "current_matchweek", "rules",
                         "groups", "bracket", "created_at", "team_count", "stats"} <= set(t))
        self.assertEqual(t["team_count"], 4)
        for team in self.br["tournament_team"]:
            self.assertTrue({"id", "tournament_id", "name", "short_name", "documents", "manager_user_ids", "stats"} <= set(team))
            self.assertNotIn("group_id", team)
        for m in self.br["tournament_match"]:
            self.assertTrue({"id", "tournament_id", "home_team_id", "away_team_id", "date", "matchweek", "status",
                             "score_home", "score_away", "round", "group_id"} <= set(m))
            self.assertIsInstance(m["date"], str)
        for e in self.br["tournament_match_event"]:
            self.assertTrue({"id", "match_id", "type", "minute", "player_id", "team_id", "event_index"} <= set(e))

    def test_round_robin_complete(self):
        pairs = {frozenset((m["home_team_id"], m["away_team_id"])) for m in self.br["tournament_match"]}
        self.assertEqual(len(pairs), 6)

    def test_scores_and_stats_consistent(self):
        t = self.br["tournament"][0]
        finished = [m for m in self.br["tournament_match"] if m["status"] == "finished"]
        self.assertEqual(len(finished), 4)
        evs = self.br["tournament_match_event"]
        for m in finished:
            mevs = [e for e in evs if e["match_id"] == m["id"]]
            self.assertEqual((m["score_home"], m["score_away"]), td.score_from_events(mevs, m["home_team_id"], m["away_team_id"]))
        self.assertEqual(t["stats"]["matches_played"], 4)
        self.assertEqual(t["stats"]["total_matches"], 6)
        self.assertEqual(t["stats"]["total_goals"], sum(m["score_home"] + m["score_away"] for m in finished))
        teams = self.br["tournament_team"]
        self.assertEqual(sum(x["stats"]["played"] for x in teams), 8)
        self.assertEqual(sum(x["stats"]["goals_for"] for x in teams), sum(x["stats"]["goals_against"] for x in teams))
        self.assertEqual(sum(x["stats"]["won"] for x in teams), sum(x["stats"]["lost"] for x in teams))
        for x in teams:
            s = x["stats"]
            self.assertEqual(s["points"], 3 * s["won"] + s["drawn"])
            self.assertEqual(s["goal_difference"], s["goals_for"] - s["goals_against"])
            self.assertEqual(len(s["form"]), s["played"])
        players = self.br["tournament_player"]
        self.assertEqual(sum(p["stats"]["goals"] for p in players) + sum(p["stats"]["own_goals"] for p in players),
                         t["stats"]["total_goals"])
        self.assertEqual(sum(p["stats"]["yellow_cards"] for p in players), t["stats"]["total_yellow_cards"])
        self.assertEqual(sum(p["stats"]["red_cards"] for p in players), t["stats"]["total_red_cards"])
        self.assertEqual(t["current_matchweek"], 3)


class RunnerTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        sess, ddb = session()
        s3 = sess.client("s3")
        summary = execute(sess)
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(ddb.puts, [])
        self.assertEqual(s3.objects, {})
        self.assertEqual(summary["tables"]["product"]["items"], 8)
        self.assertEqual(summary["tables"]["order"]["items"], 4)
        self.assertEqual(summary["tables"]["tournament_player"]["items"], 24)
        self.assertEqual(summary["s3"]["planned"], 16)
        text = td.format_summary(summary)
        self.assertNotIn(OWNER, text)

    def test_confirm_writes_only_td_in_dev(self):
        s3 = F.FakeS3()
        sess, ddb = session(s3=s3)
        cf = sess.client("cloudformation")
        execute(sess, confirm=True)
        self.assertTrue(ddb.puts)
        for table, item in ddb.puts:
            self.assertTrue(table.startswith(f"{DEV}-"))
            self.assertTrue(str(item["id"]).startswith("td-"))
        self.assertEqual(len(s3.objects), 16)
        for key, (bucket, body, ctype) in s3.objects.items():
            self.assertTrue(key.startswith(f"dev/accounts/{ACC}/products/td-"))
            self.assertEqual((bucket, ctype), (BUCKET, "image/png"))
            self.assertEqual(body[:4], b"\x89PNG")
        self.assertEqual(set(cf.stacks), {DEV})  # nunca se consulta prod

    def test_idempotent_rerun(self):
        sess, ddb = session()
        execute(sess, confirm=True)
        counts = {k: len(v) for k, v in ddb.data.items()}
        execute(sess, confirm=True)
        self.assertEqual({k: len(v) for k, v in ddb.data.items()}, counts)

    def test_kind_filter(self):
        sess, ddb = session()
        summary = execute(sess, kind="club", confirm=True)
        self.assertEqual(set(summary["tables"]), {"club_tournament", "club_roster", "club_match"})
        self.assertFalse(any(t.endswith("-product") for t, _ in ddb.puts))

    def test_missing_owner_warns_and_skips_orders(self):
        data = dev_data()
        data[d("user")] = [u for u in data[d("user")] if u["email"] != OWNER and u["email"] != "dev.user@example.test"]
        sess, _ = session(data)
        summary = execute(sess, kind="shop")
        self.assertEqual(summary["tables"]["order"]["items"], 0)
        self.assertTrue(summary["warnings"])

    def test_bracket_account_must_be_tournament(self):
        data = dev_data()
        data[d("account")][1]["settings"]["account_type"] = "club"
        sess, _ = session(data)
        with self.assertRaises(td.TestDataError):
            execute(sess, kind="bracket")
        summary = execute(sess, kind="all")  # con all se omite con aviso
        self.assertNotIn("tournament", summary["tables"])
        self.assertTrue(any("bracket" in w for w in summary["warnings"]))

    def test_missing_account_refused(self):
        sess, _ = session()
        with self.assertRaises(td.TestDataError):
            execute(sess, account_id="nope")


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.s3 = F.FakeS3()
        self.sess, self.ddb = session(s3=self.s3)
        execute(self.sess, confirm=True)

    def reset(self, **kw):
        return execute(self.sess, reset=True, confirm=True, i_understand_this_deletes=True, **kw)

    def test_flags_required(self):
        with self.assertRaises(safety.SafetyError):
            execute(self.sess, reset=True)
        with self.assertRaises(safety.SafetyError):
            execute(self.sess, reset=True, confirm=True)

    def test_reset_deletes_only_td_items_and_objects(self):
        keep = {
            "product": {"pk": "PRODUCT#real", "sk": "PRODUCT", "id": "real", "account_id": ACC},
            "order": {"id": "real-order", "account_id": ACC},
            "club_tournament": {"id": "real-ct", "account_id": ACC},
            "tournament": {"id": "real-trn", "account_id": BR},
        }
        for k, item in keep.items():
            self.ddb.data[d(k)].append(item)
        other = {"id": "td-ord-99", "account_id": "otra-cuenta"}   # td- pero de otra cuenta
        self.ddb.data[d("order")].append(other)
        self.s3.objects[f"dev/accounts/{ACC}/products/real/photo.png"] = (BUCKET, b"x", "image/png")
        self.s3.objects[f"dev/accounts/otra/products/td-prod-01/x.png"] = (BUCKET, b"x", "image/png")
        summary = self.reset()
        for k, item in keep.items():
            self.assertIn(item, self.ddb.data[d(k)])
        self.assertIn(other, self.ddb.data[d("order")])
        for key in td.tables_for({"shop", "club", "bracket"}):
            for it in self.ddb.data[d(key)]:
                self.assertFalse(td.is_td_item(key, it, BR if key.startswith("tournament") else ACC), key)
        self.assertEqual(set(self.s3.objects), {f"dev/accounts/{ACC}/products/real/photo.png",
                                                "dev/accounts/otra/products/td-prod-01/x.png"})
        for key in self.s3.deleted:
            self.assertTrue(key.startswith(f"dev/accounts/{ACC}/products/td-"))
        self.assertEqual(summary["tables"]["product"]["deleted"], 8)

    def test_reset_kind_limits_scope(self):
        before = len(self.ddb.data[d("club_match")])
        self.reset(kind="shop")
        self.assertEqual(len(self.ddb.data[d("club_match")]), before)
        self.assertEqual(self.ddb.data[d("product")], [])

    def test_reset_does_not_touch_users_or_accounts(self):
        users = list(self.ddb.data[d("user")])
        self.reset()
        self.assertEqual(self.ddb.data[d("user")], users)
        self.assertEqual(len(self.ddb.data[d("account")]), 2)


class SafetyTests(unittest.TestCase):
    def test_refuses_non_dev_stack(self):
        sess, ddb = session()
        with self.assertRaises(safety.SafetyError):
            execute(sess, dev_stack=safety.PROD_STACK_NAME, confirm=True)
        self.assertEqual(ddb.puts, [])

    def test_refuses_tables_not_in_dev_stack(self):
        cf = RecordingCF(dev_outputs={"ProductTableName": "JmanageInfraStack-Product123"})
        sess, ddb = session(cf=cf)
        with self.assertRaises(safety.SafetyError):
            execute(sess, confirm=True)
        self.assertEqual(ddb.puts, [])

    def test_refuses_dev_stack_with_other_pool(self):
        cf = RecordingCF(dev_outputs={"UserPoolId": "us-west-2_OTHER"})
        sess, ddb = session(cf=cf)
        with self.assertRaises(safety.SafetyError):
            execute(sess, confirm=True)

    def test_refuses_other_region_and_missing_args(self):
        sess, _ = session()
        with self.assertRaises(safety.SafetyError):
            execute(sess, region="us-east-1")
        with self.assertRaises(safety.SafetyError):
            execute(sess, account_id=None)

    def test_non_td_items_are_never_written(self):
        with self.assertRaises(safety.SafetyError):
            td.assert_td("order", [{"id": "real-1"}])

    def test_s3_keys_never_prod(self):
        self.assertTrue(td.product_image_key("a/b", "td-prod-01", "x y.png").startswith("dev/"))
        self.assertNotIn("prod/", td.products_prefix("x"))


if __name__ == "__main__":
    unittest.main()
