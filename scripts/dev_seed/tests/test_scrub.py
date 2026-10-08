import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scrub  # noqa: E402
from scrub import RULES, Scrubber, key_attrs  # noqa: E402

UID = "11111111-aaaa-bbbb-cccc-000000000001"
UID2 = "22222222-aaaa-bbbb-cccc-000000000002"
RAW = ["REALNAME", "real.person@gmail.com", "3001234567", "CC99887766", "Calle Real 123",
       "REALCOMPANY", "4111111111111111", "10.1.2.3"]


def scrubber(**kw):
    return Scrubber(known_user_ids={UID, UID2}, **kw)


def dumped(obj):
    return json.dumps(obj, default=str)


def assert_no_raw(tc, obj):
    text = dumped(obj)
    for r in RAW:
        tc.assertNotIn(r, text)
    tc.assertNotIn(UID, text)
    tc.assertNotIn(UID2, text)


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_output_across_instances(self):
        a, b = scrubber(), scrubber()
        self.assertEqual(a.fake_email("Real.Person@gmail.com"), b.fake_email("real.person@gmail.com"))
        self.assertEqual(a.fake_user_id(UID), b.fake_user_id(UID))
        self.assertNotEqual(a.fake_user_id(UID), a.fake_user_id(UID2))

    def test_email_format(self):
        self.assertRegex(scrubber().fake_email("x@y.com"), r"^user\d+@example\.test$")

    def test_salt_changes_output(self):
        self.assertNotEqual(Scrubber(salt="a").fake_email("x@y.com"), Scrubber(salt="b").fake_email("x@y.com"))

    def test_input_not_mutated(self):
        item = {"id": UID, "user_name": "REALNAME", "email": "real.person@gmail.com"}
        before = dumped(item)
        scrubber().scrub_item("user", item)
        self.assertEqual(before, dumped(item))


class UserTests(unittest.TestCase):
    def setUp(self):
        self.s = scrubber()
        self.item = {
            "id": UID, "user_name": "REALNAME", "email": "real.person@gmail.com",
            "phone_number": "3001234567", "identity_card_number": "CC99887766",
            "address": "Calle Real 123", "city": "Bogota", "rh": "O+", "eps": "Sura",
            "emergency_contact_name": "REALNAME", "emergency_contact_phone_number": "3001234567",
            "emergency_contact_relationship": "madre", "avatar_url": "prod/accounts/a/users/u/profile_photos/x.png",
            "user_status": "active", "shirt_number": "9", "user_group": "g1",
        }

    def test_pii_scrubbed_and_structure_kept(self):
        out = self.s.scrub_item("user", self.item)
        assert_no_raw(self, out)
        self.assertEqual(out["id"], self.s.fake_user_id(UID))
        self.assertRegex(out["email"], r"@example\.test$")
        self.assertEqual(out["avatar_url"], "")
        self.assertEqual(out["rh"], "")
        self.assertEqual(out["user_status"], "active")
        self.assertEqual(out["shirt_number"], "9")
        self.assertEqual(out["user_group"], "g1")

    def test_email_matches_across_tables(self):
        user = self.s.scrub_item("user", self.item)
        notif = self.s.scrub_item("notification", {
            "id": "n1", "user_email": "real.person@gmail.com", "title": "REALNAME te escribio",
            "content": "REALNAME", "action_url": "https://x/invite/secret", "sent_at": 1})
        self.assertEqual(user["email"], notif["user_email"])
        self.assertEqual(notif["title"], "Contenido de prueba")
        self.assertEqual(notif["action_url"], "")

    def test_name_consistent_between_user_and_customer(self):
        user = self.s.scrub_item("user", self.item)
        order = self.s.scrub_item("order", {"id": "o1", "customer": {
            "id": UID, "name": "REALNAME", "email": "real.person@gmail.com",
            "phone_number": "3001234567", "avatar_url": "prod/x", "ip_address": "10.1.2.3"}})
        self.assertEqual(user["user_name"], order["customer"]["name"])
        self.assertEqual(user["email"], order["customer"]["email"])
        self.assertEqual(order["customer"]["id"], user["id"])


class OrderTests(unittest.TestCase):
    def test_order_scrub(self):
        s = scrubber()
        item = {
            "id": "o1", "account_id": "acc", "workspace_id": "ws", "total_amount": 100,
            "customer": {"id": UID, "name": "REALNAME", "email": "real.person@gmail.com",
                         "phone_number": "3001234567", "ip_address": "10.1.2.3", "avatar_url": "x"},
            "shipping_address": {"full_address": "Calle Real 123", "address_type": "Home", "company": "REALCOMPANY"},
            "payment": {"payment": "card", "card_type": "visa", "card_number": "4111111111111111"},
            "items": [{"id": "p1", "cover_url": "prod/accounts/acc/products/p1/a.png", "name": "Camiseta"},
                      {"id": "p2", "cover_url": "prod/accounts/acc/users/u/profile_photos/a.png", "name": "X"}],
            "history": [{"type": "x", "meta": {"by": UID2, "note": "REALNAME"}}],
            "provider_check": {"checked": True, "checked_by": UID, "note": "REALNAME"},
        }
        out = s.scrub_item("order", item)
        assert_no_raw(self, out)
        self.assertEqual(out["shipping_address"]["address_type"], "Home")
        self.assertEqual(out["payment"]["card_type"], "visa")
        self.assertEqual(out["items"][0]["cover_url"], "dev/accounts/acc/products/p1/a.png")
        self.assertEqual(out["items"][1]["cover_url"], "")
        self.assertEqual(out["total_amount"], 100)
        self.assertIn(("prod/accounts/acc/products/p1/a.png", "dev/accounts/acc/products/p1/a.png"), s.assets)
        self.assertEqual(len(s.assets), 1)


class AssetTests(unittest.TestCase):
    def test_rewrite_rules(self):
        s = scrubber()
        self.assertEqual(s.rewrite_asset("prod/accounts/a/teams/t/logo/l.png"), "dev/accounts/a/teams/t/logo/l.png")
        self.assertEqual(s.rewrite_asset("prod/accounts/a/tournaments/t/l.png"), "dev/accounts/a/tournaments/t/l.png")
        self.assertEqual(s.rewrite_asset("prod/accounts/a/teams/t/docs/dni/d.pdf"), "")
        self.assertEqual(s.rewrite_asset("prod/accounts/a/users/u/invoices/p/r.png"), "")
        self.assertEqual(s.rewrite_asset("prod/accounts/a/players/p/a.png"), "")
        self.assertEqual(s.rewrite_asset("https://b.s3.amazonaws.com/x?X-Amz-Signature=abc"), "")
        self.assertEqual(s.rewrite_asset("https://cdn.example.com/logo.png"), "https://cdn.example.com/logo.png")
        self.assertEqual(s.rewrite_asset("somefile.png"), "")
        self.assertEqual(s.rewrite_asset(None), None)

    def test_dst_keys_never_reach_prod_prefix(self):
        s = scrubber()
        s.rewrite_asset("prod/accounts/a/products/p/x.png")
        for src, dst in s.assets:
            self.assertTrue(src.startswith("prod/"))
            self.assertTrue(dst.startswith("dev/"))

    def test_product_images_list_filters_dropped(self):
        s = scrubber()
        out = s.scrub_item("product", {
            "pk": "PRODUCT#p1", "sk": "PRODUCT", "name": "Camiseta",
            "cover_url": "prod/accounts/a/products/p1/c.png",
            "images": ["prod/accounts/a/products/p1/c.png", "prod/accounts/a/users/u/x.png", "https://cdn.x/y.png"],
            "reviews": [{"id": "r1", "name": "REALNAME", "comment": "REALNAME dice", "avatar_url": "prod/a", "attachments": ["prod/x"]}],
        })
        self.assertEqual(out["images"], ["dev/accounts/a/products/p1/c.png", "https://cdn.x/y.png"])
        self.assertEqual(out["reviews"][0]["comment"], "")
        self.assertEqual(out["reviews"][0]["attachments"], [])
        assert_no_raw(self, out)
        self.assertEqual(out["pk"], "PRODUCT#p1")


class OtherTableTests(unittest.TestCase):
    def setUp(self):
        self.s = scrubber()

    def test_memberships_remap(self):
        out = self.s.scrub_item("memberships", {"PK": f"USER#{UID}", "SK": "ACCOUNT#a#WORKSPACE#w",
                                                "USER_ID": UID, "ACCOUNT_ID": "a", "WORKSPACE_ID": "w", "role": "admin"})
        self.assertEqual(out["PK"], "USER#" + self.s.fake_user_id(UID))
        self.assertEqual(out["USER_ID"], self.s.fake_user_id(UID))
        self.assertEqual(out["SK"], "ACCOUNT#a#WORKSPACE#w")
        self.assertEqual(out["role"], "admin")
        self.assertNotIn(UID, dumped(out))

    def test_payment_request(self):
        out = self.s.scrub_item("payment_request", {
            "id": "pr", "user_id": UID, "description": "REALNAME debe", "concept": "Mensualidad",
            "payment_request_to": {"id": UID, "name": "REALNAME", "email": "real.person@gmail.com", "phone_number": "3001234567"},
            "images": ["prod/accounts/a/users/u/invoices/p/r.png"], "reference": "CC99887766", "user_price": 5000})
        assert_no_raw(self, out)
        self.assertEqual(out["concept"], "Mensualidad")
        self.assertEqual(out["images"], [])
        self.assertEqual(out["user_price"], 5000)

    def test_invitation_token_replaced(self):
        out = self.s.scrub_item("tournament_invitation", {
            "id": "i", "email": "real.person@gmail.com", "token": "REALSECRETTOKEN", "accepted_by_user_id": UID})
        self.assertNotIn("REALSECRETTOKEN", dumped(out))
        self.assertTrue(out["token"].startswith("tok_"))
        self.assertEqual(out["accepted_by_user_id"], self.s.fake_user_id(UID))

    def test_team_and_player(self):
        team = self.s.scrub_item("tournament_team", {
            "id": "t", "name": "Los Rojos", "manager_name": "REALNAME", "contact_email": "real.person@gmail.com",
            "contact_phone": "3001234567", "documents": {"dni": [{"key": "prod/accounts/a/teams/t/docs/dni/x.pdf"}]},
            "logo_url": "prod/accounts/a/teams/t/logo/l.png", "manager_user_ids": [UID], "owner_user_id": UID2})
        assert_no_raw(self, team)
        self.assertEqual(team["name"], "Los Rojos")
        self.assertEqual(team["documents"], {})
        self.assertEqual(team["logo_url"], "dev/accounts/a/teams/t/logo/l.png")
        player = self.s.scrub_item("tournament_player", {
            "id": "p", "name": "REALNAME", "id_number": "CC99887766", "avatar_url": "prod/accounts/a/players/p/a.png", "number": 9})
        assert_no_raw(self, player)
        self.assertEqual(player["number"], 9)
        self.assertEqual(player["avatar_url"], "")

    def test_tour_calendar_votation(self):
        tour = self.s.scrub_item("tour", {
            "id": "tr", "bookers": {UID: {"id": UID, "name": "REALNAME", "avatarUrl": "https://b.s3.amazonaws.com/x?X-Amz-Signature=1", "goals": 2}},
            "images": ["prod/accounts/a/tours/t/i.png"], "tour_guides": [UID2, "REALNAME"], "content": "REALNAME"})
        assert_no_raw(self, tour)
        self.assertEqual(list(tour["bookers"]), [self.s.fake_user_id(UID)])
        self.assertEqual(tour["bookers"][self.s.fake_user_id(UID)]["goals"], 2)
        cal = self.s.scrub_item("calendar", {"id": "c", "participants": {UID: "REALNAME"}, "description": "REALNAME", "title": "Entreno"})
        assert_no_raw(self, cal)
        self.assertEqual(cal["title"], "Entreno")
        self.assertEqual(list(cal["participants"]), [self.s.fake_user_id(UID)])
        self.assertEqual(list(cal["participants"].values()), [tour["bookers"][self.s.fake_user_id(UID)]["name"]])
        vot = self.s.scrub_item("votation", {
            "id": "v", "candidates": [{"id": UID, "name": "REALNAME", "avatar_url": "x", "goals": 1}],
            "votes": {UID2: UID}, "winner_id": UID, "created_by": UID2})
        assert_no_raw(self, vot)

    def test_donation_file_roster(self):
        don = self.s.scrub_item("donation", {"id": "d", "donor_name": "REALNAME", "message": "REALNAME", "amount_cop": 1000,
                                             "created_by_user_id": UID, "anonymous": False})
        assert_no_raw(self, don)
        self.assertEqual(don["amount_cop"], 1000)
        f = self.s.scrub_item("file", {"id": "f", "name": "REALNAME-cedula.pdf", "url": "prod/accounts/a/files/f/c.pdf", "tags": ["REALNAME"], "size": 3})
        assert_no_raw(self, f)
        self.assertTrue(f["name"].endswith(".pdf"))
        self.assertEqual(f["url"], "")
        r = self.s.scrub_item("club_roster", {"id": "r", "guest_name": "REALNAME", "user_id": UID, "number": 4})
        assert_no_raw(self, r)

    def test_account_workspace_logo(self):
        acc = self.s.scrub_item("account", {"id": "a", "name": "Club", "branding": {"logo_url": "prod/accounts/a/branding/logo.png", "primary_color": "#fff"}})
        self.assertEqual(acc["branding"]["logo_url"], "dev/accounts/a/branding/logo.png")
        ws = self.s.scrub_item("workspace", {"id": "w", "name": "Main", "logo": "prod/accounts/a/users/u/x.png"})
        self.assertEqual(ws["logo"], "")


class SafetyNetTests(unittest.TestCase):
    def test_unknown_pii_fields_caught_by_heuristic(self):
        s = scrubber()
        out = s.scrub_item("training_session", {
            "id": "t", "title": "Entreno", "coach_email": "real.person@gmail.com", "mobile": "3001234567",
            "photo_url": "https://x/y.png", "extra": {"home_address": "Calle Real 123", "note": "REALNAME"},
            "leak": "prod/accounts/a/users/u/secret.png"})
        assert_no_raw(self, out)
        self.assertEqual(out["title"], "Entreno")
        self.assertEqual(out["leak"], "")
        report = scrub.pii_report(s)
        self.assertIn("coach_email", report["heuristic_fields"]["training_session"])

    def test_report_has_no_values(self):
        s = scrubber()
        s.scrub_item("user", {"id": UID, "user_name": "REALNAME", "email": "real.person@gmail.com"})
        text = dumped(scrub.pii_report(s))
        for r in RAW + [UID]:
            self.assertNotIn(r, text)

    def test_every_table_has_rules_and_keys(self):
        for key in RULES:
            self.assertTrue(key_attrs(key))
        self.assertEqual(key_attrs("memberships"), ["PK", "SK"])
        self.assertEqual(key_attrs("product"), ["pk", "sk"])

    def test_unknown_table_rejected(self):
        with self.assertRaises(KeyError):
            scrubber().scrub_item("nope", {})


if __name__ == "__main__":
    unittest.main()
