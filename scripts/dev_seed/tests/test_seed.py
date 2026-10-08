import argparse
import json
import unittest

from tests import _fakes as F  # noqa: E402
import safety  # noqa: E402
import seed_dev_from_prod as seed  # noqa: E402

PROD = safety.PROD_STACK_NAME
DEV = safety.DEV_STACK_NAME
UID = "11111111-aaaa-bbbb-cccc-000000000001"


def args(**kw):
    base = dict(region="us-west-2", prod_stack=PROD, dev_stack=DEV, bucket="jmanage-bucket",
                tables=None, limit=None, confirm=False, profile=None)
    base.update(kw)
    return argparse.Namespace(**base)


def prod_data():
    return {
        f"{PROD}-user": [{"id": UID, "user_name": "REALNAME", "email": "real@gmail.com"},
                         {"id": "u2", "user_name": "OTHERREAL", "email": "other@gmail.com"}],
        f"{PROD}-product": [{"pk": "PRODUCT#p", "sk": "PRODUCT", "name": "Camisa",
                             "cover_url": "prod/accounts/a/products/p/c.png", "images": []}],
        f"{PROD}-order": [{"id": "o", "customer": {"id": UID, "name": "REALNAME", "email": "real@gmail.com"}}],
        f"{PROD}-memberships": [{"PK": f"USER#{UID}", "SK": "ACCOUNT#a#WORKSPACE#w", "USER_ID": UID}],
    }


class DryRunTests(unittest.TestCase):
    def test_dry_run_reads_but_never_writes(self):
        ddb = F.FakeDynamo(prod_data())
        s3 = F.FakeS3()
        session = F.FakeSession(F.FakeCF(), ddb, s3)
        summary = seed.run(args(), session)
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(ddb.puts, [])
        self.assertEqual(s3.copies, [])
        self.assertEqual(summary["tables"]["user"]["read"], 2)
        self.assertEqual(summary["tables"]["user"]["written"], 0)
        self.assertEqual(summary["assets"]["planned"], 1)
        self.assertEqual(summary["assets"]["copied"], 0)
        self.assertFalse(any(n.startswith(DEV) for n in ddb.tables_requested if False))

    def test_summary_contains_no_raw_values(self):
        session = F.FakeSession(F.FakeCF(), F.FakeDynamo(prod_data()))
        text = json.dumps(seed.run(args(), session), default=str)
        for raw in ("REALNAME", "real@gmail.com", UID, "OTHERREAL"):
            self.assertNotIn(raw, text)


class ConfirmTests(unittest.TestCase):
    def test_confirm_writes_scrubbed_items_to_dev_tables_only(self):
        ddb = F.FakeDynamo(prod_data())
        s3 = F.FakeS3()
        summary = seed.run(args(confirm=True), F.FakeSession(F.FakeCF(), ddb, s3))
        self.assertEqual(summary["mode"], "confirm")
        self.assertTrue(ddb.puts)
        for table, item in ddb.puts:
            self.assertTrue(table.startswith(f"{DEV}-"), table)
            text = json.dumps(item, default=str)
            for raw in ("REALNAME", "real@gmail.com", UID, "OTHERREAL"):
                self.assertNotIn(raw, text)
        self.assertEqual(s3.copies, [("prod/accounts/a/products/p/c.png", "dev/accounts/a/products/p/c.png")])
        self.assertEqual(summary["tables"]["user"]["written"], 2)

    def test_missing_s3_source_counted_not_fatal(self):
        s3 = F.FakeS3(missing={"prod/accounts/a/products/p/c.png"})
        summary = seed.run(args(confirm=True), F.FakeSession(F.FakeCF(), F.FakeDynamo(prod_data()), s3))
        self.assertEqual(summary["assets"]["missing"], 1)

    def test_donation_table_resolved_from_stack_resources(self):
        cf = F.FakeCF()
        seed.run(args(), F.FakeSession(cf, F.FakeDynamo(prod_data())))
        self.assertIn("list_stack_resources", cf.calls)

    def test_tables_filter(self):
        ddb = F.FakeDynamo(prod_data())
        summary = seed.run(args(tables=["product"]), F.FakeSession(F.FakeCF(), ddb))
        self.assertEqual(list(summary["tables"]), ["product"])


class RefusalTests(unittest.TestCase):
    def assert_refused(self, session, **kw):
        ddb = session._ddb
        with self.assertRaises(safety.SafetyError):
            seed.run(args(confirm=True, **kw), session)
        self.assertEqual(ddb.puts, [])

    def test_refuses_non_dev_target_stack_name(self):
        self.assert_refused(F.FakeSession(F.FakeCF(), F.FakeDynamo()), dev_stack=PROD)

    def test_refuses_dev_outputs_env_not_dev(self):
        cf = F.FakeCF(dev_outputs={"EnvUsed": "prod"})
        self.assert_refused(F.FakeSession(cf, F.FakeDynamo(prod_data())))

    def test_refuses_dev_stack_with_other_pool(self):
        cf = F.FakeCF(dev_outputs={"UserPoolId": "us-west-2_OTHER"})
        self.assert_refused(F.FakeSession(cf, F.FakeDynamo(prod_data())))

    def test_refuses_prod_source_that_is_dev(self):
        self.assert_refused(F.FakeSession(F.FakeCF(), F.FakeDynamo()), prod_stack=DEV)

    def test_refuses_prod_source_with_dev_pool(self):
        cf = F.FakeCF(prod_outputs={"UserPoolId": safety.DEV_POOL_ID})
        self.assert_refused(F.FakeSession(cf, F.FakeDynamo(prod_data())))

    def test_refuses_when_dev_table_equals_prod_table(self):
        cf = F.FakeCF(dev_outputs={"UserTableName": f"{PROD}-user"})
        self.assert_refused(F.FakeSession(cf, F.FakeDynamo(prod_data())))

    def test_refuses_other_region(self):
        self.assert_refused(F.FakeSession(F.FakeCF(), F.FakeDynamo()), region="us-east-1")

    def test_cli_returns_2_on_refusal(self):
        self.assertEqual(seed.build_parser().parse_args(["--confirm"]).confirm, True)
        self.assertFalse(seed.build_parser().parse_args([]).confirm)


class ReadOnlyTests(unittest.TestCase):
    def test_read_only_table_has_no_write_methods(self):
        ro = seed.ReadOnlyTable(F.FakeTable("t", F.FakeDynamo()))
        for name in ("put_item", "batch_writer", "update_item", "delete_item"):
            self.assertFalse(hasattr(ro, name))

    def test_scan_paginates_and_limits(self):
        ddb = F.FakeDynamo({"t": [{"id": 1}, {"id": 2}, {"id": 3}]})
        ro = seed.ReadOnlyTable(ddb.Table("t"))
        self.assertEqual(len(list(ro.scan_items())), 3)
        self.assertEqual(len(list(ro.scan_items(limit=1))), 1)

    def test_dev_writer_rejects_prod_names(self):
        ddb = F.FakeDynamo()
        with self.assertRaises(safety.SafetyError):
            seed.DevWriter(ddb, {"user": f"{PROD}-user"}, {"user": f"{PROD}-user"})
        self.assertEqual(ddb.puts, [])

    def test_copy_assets_rejects_bad_prefixes(self):
        with self.assertRaises(safety.SafetyError):
            seed.copy_assets(F.FakeS3(), "b", {("dev/x", "dev/y")}, True)
        with self.assertRaises(safety.SafetyError):
            seed.copy_assets(F.FakeS3(), "b", {("prod/x", "prod/y")}, True)


if __name__ == "__main__":
    unittest.main()
