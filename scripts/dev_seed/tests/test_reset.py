import argparse
import copy
import unittest

from tests import _fakes as F  # noqa: E402
import create_dev_users as cdu  # noqa: E402
import safety  # noqa: E402
import seed_dev_from_prod as seed  # noqa: E402

PROD = safety.PROD_STACK_NAME
DEV = safety.DEV_STACK_NAME
ACC, OTHER = "acc1", "acc2"
PERSONA_ID = "sub-dev.admin@example.test"


def p(key):
    return f"{PROD}-{key}"


def d(key):
    return f"{DEV}-{key}"


def args(**kw):
    base = dict(region="us-west-2", prod_stack=PROD, dev_stack=DEV, bucket="b", tables=None, limit=None,
                confirm=False, profile=None, account_id=ACC, reset=False,
                i_understand_this_deletes=False, force_large_delete=False)
    base.update(kw)
    return argparse.Namespace(**base)


def prod_data():
    def prod_item(i):
        return {"pk": f"PRODUCT#p{i}", "sk": "PRODUCT", "account_id": ACC, "name": f"Prod {i}",
                "cover_url": f"prod/accounts/{ACC}/products/p{i}/c.png", "images": []}
    return {
        p("account"): [{"id": ACC, "name": "Club"}, {"id": OTHER, "name": "Otro"}],
        p("workspace"): [{"id": "w1", "account_id": ACC, "name": "W"}, {"id": "w2", "account_id": OTHER, "name": "W2"}],
        p("memberships"): [
            {"PK": "USER#u1", "SK": f"ACCOUNT#{ACC}#WORKSPACE#w1", "ACCOUNT_ID": ACC, "WORKSPACE_ID": "w1", "USER_ID": "u1", "role": "admin"},
            {"PK": "USER#u2", "SK": f"ACCOUNT#{ACC}#WORKSPACE#w1", "ACCOUNT_ID": ACC, "WORKSPACE_ID": "w1", "USER_ID": "u2", "role": "user"},
            {"PK": "USER#u3", "SK": f"ACCOUNT#{OTHER}#WORKSPACE#w2", "ACCOUNT_ID": OTHER, "WORKSPACE_ID": "w2", "USER_ID": "u3", "role": "user"}],
        p("user"): [{"id": "u1", "user_name": "REAL1", "email": "r1@gmail.com"},
                    {"id": "u2", "user_name": "REAL2", "email": "r2@gmail.com"},
                    {"id": "u3", "user_name": "REAL3", "email": "r3@gmail.com"}],
        p("product"): [prod_item(1), prod_item(2), prod_item(3),
                       {"pk": "PRODUCT#px", "sk": "PRODUCT", "account_id": OTHER, "name": "X"}],
        p("order"): [{"id": "o1", "account_id": ACC, "customer": {"id": "u1", "name": "REAL1", "email": "r1@gmail.com"}},
                     {"id": "o2", "account_id": OTHER, "customer": {"id": "u3", "name": "REAL3", "email": "r3@gmail.com"}}],
        p("tournament"): [{"id": "t1", "account_id": ACC, "name": "T1"}, {"id": "t2", "account_id": OTHER, "name": "T2"}],
        p("tournament_team"): [{"id": "tm1", "tournament_id": "t1", "name": "A", "manager_name": "REAL1"},
                               {"id": "tm2", "tournament_id": "t2", "name": "B"}],
        p("tournament_match"): [{"id": "m1", "tournament_id": "t1", "date": "2026-01-01"},
                                {"id": "m2", "tournament_id": "t2", "date": "2026-01-02"}],
        p("tournament_match_event"): [{"id": "e1", "match_id": "m1"}, {"id": "e2", "match_id": "m2"}],
        p("notification"): [{"id": "n1", "user_email": "r1@gmail.com", "title": "REAL1"},
                            {"id": "n2", "user_email": "r3@gmail.com", "title": "REAL3"}],
        p("donation"): [{"id": "d1", "donor_name": "REAL1"}],
    }


def persona_rows():
    return {
        d("user"): [{"id": PERSONA_ID, "user_name": "Dev Admin", "email": "dev.admin@example.test"}],
        d("memberships"): [{"PK": f"USER#{PERSONA_ID}", "SK": f"ACCOUNT#{ACC}#WORKSPACE#w1", "ACCOUNT_ID": ACC,
                            "WORKSPACE_ID": "w1", "USER_ID": PERSONA_ID, "role": "admin", "status": "active"}],
    }


def sync(ddb, **kw):
    return seed.run(args(confirm=True, **kw), F.FakeSession(F.FakeCF(), ddb))


def owned_snapshot(ddb):
    """Filas de dev de la cuenta ACC (por regla de propiedad), ordenadas, para comparar."""
    out = {}
    for key in seed.TABLE_OUTPUTS:
        rows = ddb.data.get(d(key), [])
        out[key] = sorted(repr(sorted(r.items(), key=lambda kv: kv[0])) for r in rows
                          if r.get("account_id") == ACC or r.get("ACCOUNT_ID") == ACC
                          or r.get("tournament_id") == "t1" or r.get("match_id") == "m1"
                          or (key == "account" and r.get("id") == ACC))
    return out


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.ddb = F.FakeDynamo(copy.deepcopy(prod_data()))
        self.ddb.data.update(persona_rows())

    def test_account_sync_copies_only_that_account(self):
        summary = sync(self.ddb)
        self.assertEqual(summary["tables"]["product"]["written"], 3)
        self.assertEqual(summary["tables"]["user"]["written"], 2)  # u1,u2; no u3
        self.assertEqual(summary["tables"]["tournament_team"]["written"], 1)
        self.assertEqual(summary["tables"]["tournament_match_event"]["written"], 1)
        self.assertEqual(summary["tables"]["notification"]["written"], 1)
        self.assertEqual(summary["skipped"], {"donation": "campana global sin atributo de cuenta"})
        self.assertNotIn("donation", summary["tables"])
        self.assertEqual(len(self.ddb.data[d("account")]), 1)
        for table, item in self.ddb.puts:
            self.assertTrue(table.startswith(f"{DEV}-"))

    def test_determinism_two_syncs_identical(self):
        other = F.FakeDynamo(copy.deepcopy(prod_data()))
        other.data.update(persona_rows())
        sync(self.ddb)
        sync(other)
        sync(other)  # re-sync sobre lo ya sincronizado
        for key in seed.TABLE_OUTPUTS:
            a = sorted(map(repr, (sorted(r.items()) for r in self.ddb.data.get(d(key), []))))
            b = sorted(map(repr, (sorted(r.items()) for r in other.data.get(d(key), []))))
            self.assertEqual(a, b, key)

    def test_second_sync_is_noop(self):
        sync(self.ddb)
        summary = seed.run(args(), F.FakeSession(F.FakeCF(), self.ddb))
        for info in summary["tables"].values():
            self.assertEqual((info["upsert"], info["delete"]), (0, 0))

    def test_sync_mutate_reset_restores_snapshot(self):
        sync(self.ddb)
        baseline = owned_snapshot(self.ddb)
        # dev ajeno que no debe tocarse y fila de usuario no referenciada
        self.ddb.data[d("product")].append({"pk": "PRODUCT#other", "sk": "PRODUCT", "account_id": OTHER, "name": "ajeno"})
        self.ddb.data[d("user")].append({"id": "stray", "user_name": "x", "email": "x@example.test"})
        baseline = owned_snapshot(self.ddb)
        other_before = [r for r in self.ddb.data[d("product")] if r["account_id"] == OTHER]
        # mutar: modificar, borrar, agregar
        products = self.ddb.data[d("product")]
        next(r for r in products if r["pk"] == "PRODUCT#p1")["name"] = "EDITADO"
        self.ddb.data[d("order")][:] = [r for r in self.ddb.data[d("order")] if r["id"] != "o1"]
        products.append({"pk": "PRODUCT#nuevo", "sk": "PRODUCT", "account_id": ACC, "name": "agregado"})
        self.ddb.data[d("tournament_team")].append({"id": "tm_new", "tournament_id": "t1", "name": "nuevo"})
        self.ddb.data[d("tournament_match_event")].append({"id": "e_new", "match_id": "m1"})
        mutated = owned_snapshot(self.ddb)
        self.assertNotEqual(baseline, mutated)

        # dry-run del reset: cuenta, no escribe
        puts, dels = len(self.ddb.puts), len(self.ddb.deletes)
        dry = seed.run(args(reset=True), F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual((len(self.ddb.puts), len(self.ddb.deletes)), (puts, dels))
        self.assertEqual(dry["tables"]["product"]["upsert"], 1)
        self.assertEqual(dry["tables"]["product"]["delete"], 1)
        self.assertEqual(dry["tables"]["order"]["upsert"], 1)
        self.assertEqual(dry["tables"]["tournament_team"]["delete"], 1)
        self.assertEqual(dry["tables"]["tournament_match_event"]["delete"], 1)

        seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual(owned_snapshot(self.ddb), baseline)
        # personas intactas
        self.assertIn(PERSONA_ID, [u["id"] for u in self.ddb.data[d("user")]])
        self.assertTrue(any(m["USER_ID"] == PERSONA_ID for m in self.ddb.data[d("memberships")]))
        # otras cuentas y usuarios no referenciados intactos
        self.assertEqual([r for r in self.ddb.data[d("product")] if r["account_id"] == OTHER], other_before)
        self.assertIn("stray", [u["id"] for u in self.ddb.data[d("user")]])
        # nunca se borra en user/account
        self.assertFalse([t for t, _ in self.ddb.deletes if t in (d("user"), d("account"))])
        # y un segundo reset es idempotente
        again = seed.run(args(reset=True), F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual(sum(i["upsert"] + i["delete"] for i in again["tables"].values()), 0)

    def test_reset_restores_deleted_table_rows_with_tables_filter(self):
        sync(self.ddb)
        self.ddb.data[d("product")][:] = []
        self.ddb.data[d("order")][:] = []
        seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True, tables=["product"]),
                 F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual(len(self.ddb.data[d("product")]), 3)
        self.assertEqual(self.ddb.data[d("order")], [])  # otra tabla: no se toca

    def test_persona_workspace_and_membership_preserved(self):
        sync(self.ddb)
        self.ddb.data[d("workspace")].append({"id": "ws_extra", "account_id": ACC, "name": "extra"})
        seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(F.FakeCF(), self.ddb))
        ids = {w["id"] for w in self.ddb.data[d("workspace")]}
        self.assertIn("w1", ids)       # referenciado por persona y por el origen
        self.assertNotIn("ws_extra", ids)

    def test_large_delete_cap(self):
        sync(self.ddb)
        for i in range(30):
            self.ddb.data[d("product")].append({"pk": f"PRODUCT#junk{i}", "sk": "PRODUCT", "account_id": ACC})
        dels = len(self.ddb.deletes)
        with self.assertRaises(safety.SafetyError):
            seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual(len(self.ddb.deletes), dels)
        seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True, force_large_delete=True),
                 F.FakeSession(F.FakeCF(), self.ddb))
        self.assertEqual(len(self.ddb.data[d("product")]), 3)

    def test_deletion_recheck_refuses_foreign_rows(self):
        sync(self.ddb)
        planned = seed.plan_account(args(reset=True), self.ddb, {k: p(k) for k in seed.TABLE_OUTPUTS},
                                    {k: d(k) for k in seed.TABLE_OUTPUTS}, list(seed.TABLE_OUTPUTS),
                                    seed.Scrubber(known_user_ids={"u1", "u2", "u3"}), self.ddb.data[p("user")])
        planned["plan"]["product"]["deletes"].append({"pk": "PRODUCT#other", "sk": "PRODUCT", "account_id": OTHER})
        writer = seed.DevWriter(self.ddb, {k: d(k) for k in seed.TABLE_OUTPUTS}, {k: p(k) for k in seed.TABLE_OUTPUTS})
        with self.assertRaises(safety.SafetyError):
            seed.apply_account_plan(args(reset=True, confirm=True), writer, planned)


class FlagTests(unittest.TestCase):
    def run_flags(self, **kw):
        return seed.run(args(**kw), F.FakeSession(F.FakeCF(), F.FakeDynamo(copy.deepcopy(prod_data()))))

    def test_reset_requires_account(self):
        with self.assertRaises(safety.SafetyError):
            self.run_flags(account_id=None, reset=True)

    def test_reset_confirm_requires_understand_flag(self):
        with self.assertRaises(safety.SafetyError):
            self.run_flags(reset=True, confirm=True)

    def test_persona_account_refused(self):
        with self.assertRaises(safety.SafetyError):
            self.run_flags(account_id=safety.PERSONA_ACCOUNT_ID)

    def test_limit_incompatible(self):
        with self.assertRaises(safety.SafetyError):
            self.run_flags(limit=5)

    def test_reset_refuses_when_dev_table_is_prod_table(self):
        cf = F.FakeCF(dev_outputs={"ProductTableName": p("product")})
        ddb = F.FakeDynamo(copy.deepcopy(prod_data()))
        with self.assertRaises(safety.SafetyError):
            seed.run(args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(cf, ddb))
        self.assertEqual(ddb.deletes, [])

    def test_persona_emails_match_create_dev_users(self):
        self.assertEqual({x["email"] for x in cdu.PERSONAS}, set(safety.PERSONA_EMAILS))


if __name__ == "__main__":
    unittest.main()
