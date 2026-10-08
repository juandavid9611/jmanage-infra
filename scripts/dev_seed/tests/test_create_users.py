import argparse
import unittest

from tests import _fakes as F  # noqa: E402
import create_dev_users as cdu  # noqa: E402
import safety  # noqa: E402

DEV = safety.DEV_STACK_NAME


def args(**kw):
    base = dict(region="us-west-2", dev_stack=DEV, pool_id=safety.DEV_POOL_ID,
                club_account_id=None, confirm=False, profile=None)
    base.update(kw)
    return argparse.Namespace(**base)


def dev_data():
    return {
        f"{DEV}-account": [{"id": "club1", "name": "Club", "settings": {"default_workspace": "ws1", "account_type": "club"}}],
        f"{DEV}-workspace": [],
    }


GOOD_PW = "Abcdef123456!"


class CreateUsersTests(unittest.TestCase):
    def test_dry_run_creates_nothing(self):
        ddb, cog = F.FakeDynamo(dev_data()), F.FakeCognito()
        summary = cdu.run(args(), F.FakeSession(F.FakeCF(), ddb, cognito=cog), None)
        self.assertEqual(cog.created, [])
        self.assertEqual(cog.passwords, 0)
        self.assertEqual(ddb.puts, [])
        self.assertEqual(summary["mode"], "dry-run")
        self.assertTrue(summary["tournament_account_missing"])

    def test_confirm_creates_personas_and_memberships(self):
        ddb, cog = F.FakeDynamo(dev_data()), F.FakeCognito()
        summary = cdu.run(args(confirm=True), F.FakeSession(F.FakeCF(), ddb, cognito=cog), GOOD_PW)
        self.assertEqual(len(cog.created), len(cdu.PERSONAS))
        self.assertEqual(cog.passwords, len(cdu.PERSONAS))
        self.assertEqual(set(cog.pools), {safety.DEV_POOL_ID})
        self.assertEqual(summary["accounts_created"], 1)
        memberships = [i for t, i in ddb.puts if t.endswith("-memberships")]
        self.assertEqual(len(memberships), len(cdu.PERSONAS) + 1)
        admin_sub = "sub-dev.admin@example.test"
        roles = {(m["ACCOUNT_ID"], m["role"]) for m in memberships if m["USER_ID"] == admin_sub}
        self.assertEqual(roles, {("club1", "admin"), (cdu.TOURNAMENT_ACCOUNT_ID, "admin")})
        owner = [m for m in memberships if m["role"] == "team_owner"][0]
        self.assertEqual(owner["SK"], f"ACCOUNT#{cdu.TOURNAMENT_ACCOUNT_ID}#WORKSPACE#{cdu.TOURNAMENT_WORKSPACE_ID}")
        self.assertEqual(owner["PK"], f"USER#{owner['USER_ID']}")
        tables = {t for t, _ in ddb.puts}
        self.assertTrue(all(t.startswith(f"{DEV}-") for t in tables))

    def test_idempotent_rerun(self):
        ddb, cog = F.FakeDynamo(dev_data()), F.FakeCognito(existing=[p["email"] for p in cdu.PERSONAS])
        summary = cdu.run(args(confirm=True), F.FakeSession(F.FakeCF(), ddb, cognito=cog), GOOD_PW)
        self.assertEqual(cog.created, [])
        self.assertEqual(summary["cognito_existing"], len(cdu.PERSONAS))

    def test_refuses_other_pool(self):
        cog = F.FakeCognito()
        with self.assertRaises(safety.SafetyError):
            cdu.run(args(confirm=True, pool_id="us-west-2_PRODPOOL"), F.FakeSession(F.FakeCF(), F.FakeDynamo(dev_data()), cognito=cog), GOOD_PW)
        self.assertEqual(cog.created, [])
        self.assertEqual(cog.pools, [])

    def test_refuses_stack_pool_mismatch(self):
        cf = F.FakeCF(dev_outputs={"UserPoolId": "us-west-2_OTHER"})
        with self.assertRaises(safety.SafetyError):
            cdu.run(args(confirm=True), F.FakeSession(cf, F.FakeDynamo(dev_data())), GOOD_PW)

    def test_refuses_prod_stack(self):
        with self.assertRaises(safety.SafetyError):
            cdu.run(args(confirm=True, dev_stack=safety.PROD_STACK_NAME), F.FakeSession(F.FakeCF(), F.FakeDynamo(dev_data())), GOOD_PW)

    def test_requires_single_club_account_or_flag(self):
        data = dev_data()
        data[f"{DEV}-account"].append({"id": "club2", "settings": {"default_workspace": "w2"}})
        with self.assertRaises(safety.SafetyError):
            cdu.run(args(), F.FakeSession(F.FakeCF(), F.FakeDynamo(data)), None)
        cdu.run(args(club_account_id="club2"), F.FakeSession(F.FakeCF(), F.FakeDynamo(data)), None)

    def test_password_rules_and_sources(self):
        for bad in ("short1!A", "alllowercase123!", "ALLUPPERCASE123!", "NoDigitsHere!!!!", "NoSymbolsHere123"):
            with self.assertRaises(ValueError):
                cdu.validate_password(bad)
        cdu.validate_password(GOOD_PW)
        self.assertIsNone(cdu.get_password(False))

    def test_no_password_hardcoded_in_source(self):
        import inspect
        src = inspect.getsource(cdu)
        self.assertNotIn("Password=\"", src)
        self.assertEqual([p["email"] for p in cdu.PERSONAS if not p["email"].endswith("@example.test")], [])


if __name__ == "__main__":
    unittest.main()
