"""A counter user can read what the Service Intake form asks them to pick.

The intake renders pickers over the GoFix catalogues — accessories, symptoms,
visit reason, referral source, repair solutions — and looks the typed IMEI up
against Serial No. Every one of those was readable by System Manager, GoFix
Floor Manager and GoFix Technician only.

Nobody saw it while every cashier held System Manager. Once that role was
removed the form died on the first picker it rendered: "Insufficient Permission
for GoFix Accessory". These tests pin the grant, and pin that it stays read-only
— the counter chooses from a catalogue, it does not maintain one.
"""

import unittest

import frappe

from gofix.setup.permissions import (
    COUNTER_CATALOGUES,
    COUNTER_READ_ONLY,
    COUNTER_ROLE,
    ensure_counter_catalogue_read,
)


def _counter_user():
    """Somebody who actually works a till and bypasses nothing.

    A privileged user passes every check here and would prove nothing.
    """
    from ch_pos.config import is_privileged_user

    rows = frappe.db.sql(
        """
        SELECT DISTINCT e.user
          FROM `tabPOS Executive` e
          JOIN `tabUser` u ON u.name = e.user AND u.enabled = 1
         WHERE e.is_active = 1
         LIMIT 200
        """,
        pluck="user")
    for user in rows:
        if is_privileged_user(user):
            continue
        if COUNTER_ROLE in frappe.get_roles(user):
            return user
    return None


class TestCounterCatalogueRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_counter_catalogue_read()

    def tearDown(self):
        frappe.db.rollback()

    def test_the_role_is_one_every_till_profile_grants(self):
        """If POS User ever stops being universal, this grant covers nobody."""
        profiles = frappe.get_all(
            "User Role Profile", filters={"role_profile": ("is", "set")},
            pluck="role_profile", distinct=True)
        till_profiles = [
            p for p in profiles
            if frappe.db.exists("Has Role", {
                "parent": p, "parenttype": "Role Profile", "role": "POS User"})
        ]
        self.assertTrue(till_profiles, "no role profile grants POS User any more")

    def test_a_counter_user_can_read_every_catalogue(self):
        user = _counter_user()
        if not user:
            self.skipTest("no non-privileged POS Executive holding POS User")
        original = frappe.session.user
        try:
            frappe.set_user(user)
            for doctype in COUNTER_CATALOGUES + COUNTER_READ_ONLY:
                if not frappe.db.exists("DocType", doctype):
                    continue
                self.assertTrue(
                    frappe.has_permission(doctype, "read"),
                    f"{doctype} is unreadable — the intake form will throw on it")
        finally:
            frappe.set_user(original)

    def test_the_pickers_themselves_resolve(self):
        """has_permission is necessary but not sufficient — the Link search is
        what the form actually calls, and it applies its own gate."""
        from frappe.desk.search import search_link

        user = _counter_user()
        if not user:
            self.skipTest("no non-privileged POS Executive holding POS User")
        original = frappe.session.user
        try:
            frappe.set_user(user)
            for doctype in COUNTER_CATALOGUES:
                if not frappe.db.exists("DocType", doctype):
                    continue
                search_link(doctype, "", page_length=1)  # must not raise
        finally:
            frappe.set_user(original)

    def test_the_counter_cannot_edit_a_catalogue(self):
        """Upkeep stays with GoFix Floor Manager."""
        user = _counter_user()
        if not user:
            self.skipTest("no non-privileged POS Executive holding POS User")
        original = frappe.session.user
        try:
            frappe.set_user(user)
            for doctype in COUNTER_CATALOGUES + COUNTER_READ_ONLY:
                if not frappe.db.exists("DocType", doctype):
                    continue
                for ptype in ("write", "create", "delete"):
                    self.assertFalse(
                        frappe.has_permission(doctype, ptype),
                        f"{doctype}: the counter must not be able to {ptype}")
        finally:
            frappe.set_user(original)

    def test_running_it_again_creates_nothing(self):
        self.assertEqual(ensure_counter_catalogue_read(), 0)
