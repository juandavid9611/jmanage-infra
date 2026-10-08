import copy
import os
import unittest

from tests import _fakes as F  # noqa: E402
from tests import test_reset as R  # noqa: E402
import create_dev_users as cdu  # noqa: E402
import safety  # noqa: E402
import scrub  # noqa: E402
import seed_dev_from_prod as seed  # noqa: E402

OWNER = "jd_rodrigueza@javeriana.edu.co"
OWNER_SUB = f"sub-{OWNER}"
GOOD_PW = "Abcdef123456!"


class OwnerPersonaTests(unittest.TestCase):
    def test_default_constant_and_persona(self):
        self.assertEqual(safety.DEV_OWNER_EMAIL_DEFAULT, OWNER)
        owner = next(p for p in cdu.PERSONAS if p["key"] == "owner")
        self.assertEqual((owner["email"], owner["role"], owner["account"]), (OWNER, "admin", "club"))
        self.assertEqual(cdu.EXTRA_MEMBERSHIPS["owner"], [("tournament", "admin")])
        self.assertIn(OWNER, safety.persona_emails())

    def test_env_override_and_validation(self):
        os.environ[safety.DEV_OWNER_EMAIL_ENV] = "Otro@Ejemplo.com"
        try:
            self.assertEqual(safety.owner_email(), "otro@ejemplo.com")
            os.environ[safety.DEV_OWNER_EMAIL_ENV] = "sin-arroba"
            with self.assertRaises(safety.SafetyError):
                safety.owner_email()
        finally:
            del os.environ[safety.DEV_OWNER_EMAIL_ENV]

    def test_create_rerun_idempotent_suppress_and_dev_pool(self):
        ddb = F.FakeDynamo(R.dev_data() if hasattr(R, "dev_data") else {})
        ddb.data = {f"{safety.DEV_STACK_NAME}-account": [{"id": "club1", "settings": {"default_workspace": "ws1", "account_type": "club"}}]}
        cog = F.FakeCognito()
        sent = []
        orig = cog.admin_create_user
        cog.admin_create_user = lambda **kw: (sent.append(kw["MessageAction"]), orig(**kw))[1]
        from tests.test_create_users import args as uargs
        cdu.run(uargs(confirm=True), F.FakeSession(F.FakeCF(), ddb, cognito=cog), GOOD_PW)
        self.assertIn(OWNER, cog.created)
        self.assertEqual(set(sent), {"SUPPRESS"})
        self.assertEqual(set(cog.pools), {safety.DEV_POOL_ID})
        rows = lambda: (len(ddb.data[f"{safety.DEV_STACK_NAME}-user"]), len(ddb.data[f"{safety.DEV_STACK_NAME}-memberships"]))
        before = rows()
        users = [u for u in ddb.data[f"{safety.DEV_STACK_NAME}-user"] if u["email"] == OWNER]
        self.assertEqual(len(users), 1)
        memb = [m for m in ddb.data[f"{safety.DEV_STACK_NAME}-memberships"] if m["USER_ID"] == users[0]["id"]]
        self.assertEqual({(m["ACCOUNT_ID"], m["role"]) for m in memb}, {("club1", "admin"), (cdu.TOURNAMENT_ACCOUNT_ID, "admin")})
        n_created = len(cog.created)
        cdu.run(uargs(confirm=True), F.FakeSession(F.FakeCF(), ddb, cognito=cog), GOOD_PW)
        self.assertEqual(len(cog.created), n_created)
        self.assertEqual(rows(), before)

    def test_dry_run_output_has_no_emails(self):
        ddb = F.FakeDynamo({f"{safety.DEV_STACK_NAME}-account": [{"id": "club1", "settings": {"default_workspace": "ws1"}}]})
        from tests.test_create_users import args as uargs
        summary = cdu.run(uargs(), F.FakeSession(F.FakeCF(), ddb, cognito=F.FakeCognito()), None)
        text = cdu.format_summary(summary)
        self.assertIn("owner", text)
        self.assertNotIn("@", text)
        self.assertNotIn("javeriana", text)

    def test_owner_refuses_non_dev_pool(self):
        from tests.test_create_users import args as uargs
        with self.assertRaises(safety.SafetyError):
            cdu.run(uargs(confirm=True, pool_id="us-west-2_PROD"),
                    F.FakeSession(F.FakeCF(), F.FakeDynamo({}), cognito=F.FakeCognito()), GOOD_PW)

    def test_scrubber_still_scrubs_owner_email(self):
        s = scrub.Scrubber(known_user_ids={"u1"})
        out = s.scrub_item("user", {"id": "u1", "user_name": "X", "email": OWNER})
        self.assertNotIn(OWNER, str(out))
        self.assertRegex(out["email"], r"@example\.test$")
        notif = s.scrub_item("notification", {"id": "n", "user_email": OWNER})
        self.assertNotIn(OWNER, str(notif))


class OwnerResetTests(unittest.TestCase):
    def test_reset_preserves_owner_rows(self):
        ddb = F.FakeDynamo(copy.deepcopy(R.prod_data()))
        ddb.data.update(R.persona_rows())
        R.sync(ddb)
        d = R.d
        ddb.data[d("user")].append({"id": OWNER_SUB, "user_name": "Dev Owner", "email": OWNER})
        ddb.data[d("memberships")].append({"PK": f"USER#{OWNER_SUB}", "SK": f"ACCOUNT#{R.ACC}#WORKSPACE#wown",
                                           "ACCOUNT_ID": R.ACC, "WORKSPACE_ID": "wown", "USER_ID": OWNER_SUB, "role": "admin"})
        ddb.data[d("workspace")].append({"id": "wown", "account_id": R.ACC, "name": "propio"})
        ddb.data[d("workspace")].append({"id": "wjunk", "account_id": R.ACC, "name": "basura"})
        R.seed.run(R.args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(F.FakeCF(), ddb))
        self.assertIn(OWNER_SUB, [u["id"] for u in ddb.data[d("user")]])
        self.assertTrue(any(m["USER_ID"] == OWNER_SUB for m in ddb.data[d("memberships")]))
        ids = {w["id"] for w in ddb.data[d("workspace")]}
        self.assertIn("wown", ids)
        self.assertNotIn("wjunk", ids)

    def test_reset_preserves_overridden_owner_email(self):
        os.environ[safety.DEV_OWNER_EMAIL_ENV] = "otro@ejemplo.com"
        try:
            ddb = F.FakeDynamo(copy.deepcopy(R.prod_data()))
            R.sync(ddb)
            d = R.d
            ddb.data[d("user")].append({"id": "own2", "user_name": "O", "email": "otro@ejemplo.com"})
            ddb.data[d("memberships")].append({"PK": "USER#own2", "SK": f"ACCOUNT#{R.ACC}#WORKSPACE#w1", "ACCOUNT_ID": R.ACC,
                                               "WORKSPACE_ID": "w1", "USER_ID": "own2", "role": "admin"})
            R.seed.run(R.args(reset=True, confirm=True, i_understand_this_deletes=True), F.FakeSession(F.FakeCF(), ddb))
            self.assertTrue(any(m["USER_ID"] == "own2" for m in ddb.data[d("memberships")]))
        finally:
            del os.environ[safety.DEV_OWNER_EMAIL_ENV]


if __name__ == "__main__":
    unittest.main()
