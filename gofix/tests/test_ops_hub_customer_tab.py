# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""The Ops Hub's Customer tab, from the server's side.

``service_maturity.py`` was implemented, guarded and whitelisted, and no screen
called any of it — a courtesy device could go out with nothing on the ticket to
say so, and nobody was ever asked how the repair went. The Customer tab is the
caller, and ``get_ticket_detail`` is the contract between them.

The conversion is the part worth pinning. ``csat_score`` is a Frappe ``Rating``,
so the four stars a customer gave are stored as 0.8. The tab must show 4. If
that translation is ever dropped, every rating on every ticket silently becomes
a fifth of what the customer said, and nothing throws.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, nowdate

from gofix import service_maturity as sm
from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import get_ticket_detail
from gofix.tests.test_service_maturity import (
    LOANER_SERIAL,
    _minimal_service_request,
    stock_the_loaner_shelf,
)


class TestCustomerTabPayload(unittest.TestCase):
    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        # A loaner must now be a real device on this store's Demo shelf.
        self._lendable = stock_the_loaner_shelf(self.sr)

    def tearDown(self):
        frappe.db.rollback()

    def _care(self):
        return get_ticket_detail(self.sr.name).get("care") or {}

    def test_the_tab_is_given_something_to_render(self):
        care = self._care()
        for key in ("loaner_status", "loaner_serial_no", "appointment_datetime",
                    "csat_score", "nps_score", "feedback_comment", "feedback_received_at"):
            self.assertIn(key, care, f"the Customer tab reads care.{key}")

    def test_a_loaner_shows_up_on_the_ticket(self):
        if not self._lendable:
            raise unittest.SkipTest("this store has no Demo bin to lend from")
        """The whole point: issuing one must be visible to whoever opens it."""
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        care = self._care()
        self.assertEqual(care["loaner_status"], "Issued")
        self.assertEqual(care["loaner_serial_no"], LOANER_SERIAL)
        self.assertTrue(care["loaner_issued_at"])

    def test_returning_it_shows_up_too(self):
        if not self._lendable:
            raise unittest.SkipTest("this store has no Demo bin to lend from")
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        sm.return_loaner(self.sr.name, condition="Good")
        care = self._care()
        self.assertEqual(care["loaner_status"], "Returned")
        self.assertTrue(care["loaner_returned_at"])

    def test_four_stars_reach_the_screen_as_four(self):
        """Stored as 0.8, shown as 4. Drop the conversion and every rating
        becomes a fifth of what the customer said, with nothing thrown."""
        sm.record_feedback(self.sr.name, csat=4)
        self.assertEqual(self._care()["csat_score"], 4.0)

    def test_five_stars_are_not_rounded_away(self):
        sm.record_feedback(self.sr.name, csat=5)
        self.assertEqual(self._care()["csat_score"], 5.0)

    def test_one_star_survives_the_round_trip(self):
        sm.record_feedback(self.sr.name, csat=1)
        self.assertEqual(self._care()["csat_score"], 1.0)

    def test_nps_is_passed_through_unscaled(self):
        """NPS is an Int, not a Rating — converting it too would be the
        mirror-image bug."""
        sm.record_feedback(self.sr.name, nps=9)
        self.assertEqual(int(self._care()["nps_score"]), 9)

    def test_no_rating_reads_as_absent_not_as_zero(self):
        """A customer who never answered has not scored the repair 0."""
        sm.record_feedback(self.sr.name, comment="Said nothing about the score")
        self.assertIsNone(self._care()["csat_score"])

    def test_an_appointment_reaches_the_screen(self):
        slot = f"{add_days(nowdate(), 2)} 15:30:00"
        sm.book_appointment(self.sr.name, slot)
        care = self._care()
        self.assertTrue(care["appointment_datetime"].startswith(add_days(nowdate(), 2)))
