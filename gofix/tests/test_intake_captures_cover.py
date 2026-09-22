# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""The cover the counter found has to end up on the ticket.

The intake screen already looked the IMEI up and showed what it found — a VAS
plan, our own prior workmanship, a fitted part still in window — and the chips
were advisory. Nothing was written down. ``_classify_coverage`` picks
In-Warranty / VAS Claim / Non-Warranty from ``warranty_status`` and
``active_warranty_plan``, and no one ever set the second, so a device with a
live plan was filed as Non-Warranty.

That is the defect the old system's board pack is about: of 32 protected
devices that came in for repair, 22 were billed as ordinary paid repairs and
eight of those had a damage plan in force. The plan was knowable at the
counter both times. It just was not recorded, so the claim could never be
traced back to the policy that should have paid for it.

These tests assert the field lands and the bucket changes with it. Asserting
only that the API returns a plan would have passed before the change too.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, nowdate

from gofix.tests.test_service_maturity import _minimal_service_request


class TestCoverageCategoryFollowsTheCapturedPlan(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def _ticket(self, **fields):
        sr = _minimal_service_request()
        if not sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        if fields:
            sr.update(fields)
            sr.flags.ignore_permissions = True
            sr.save(ignore_permissions=True)
            sr.reload()
        return sr

    def test_without_a_plan_a_covered_device_is_filed_as_non_warranty(self):
        """The old behaviour, kept as the thing the fix has to change."""
        sr = self._ticket()
        self.assertEqual(sr.get("coverage_category"), "Non-Warranty")

    def test_capturing_a_plan_moves_the_ticket_to_vas_claim(self):
        """The whole point of A: a recorded plan changes how the repair is filed."""
        plan = frappe.db.get_value("Active VAS Plans", {"docstatus": 1, "status": "Active"}, "name")
        if not plan:
            self.skipTest("no active VAS plan on this site to capture")
        sr = self._ticket(active_warranty_plan=plan)
        self.assertEqual(
            sr.get("coverage_category"), "VAS Claim",
            "a ticket with a live plan on it is still being filed as Non-Warranty")

    def test_our_own_warranty_outranks_a_plan(self):
        """A rework is on our tab; a plan is recovered through claims.

        Order matters because the estimate engine only zeroes In-Warranty.

        Classified in memory rather than saved: the Service Request refuses
        "Under Warranty" on a ticket with no IMEI behind it — correctly, since
        that is the counter guessing — and the rule under test here is the
        precedence, not the evidence guard.
        """
        from ch_erp15.warranty import UNDER_WARRANTY

        sr = self._ticket()
        sr.warranty_status = UNDER_WARRANTY
        sr.active_warranty_plan = "any-live-plan"
        self.assertEqual(sr._classify_coverage(), "In-Warranty")

    def test_a_plan_alone_classifies_as_a_claim(self):
        sr = self._ticket()
        sr.warranty_status = ""
        sr.active_warranty_plan = "any-live-plan"
        self.assertEqual(sr._classify_coverage(), "VAS Claim")

    def test_neither_is_the_customer_paying(self):
        sr = self._ticket()
        sr.warranty_status = ""
        sr.active_warranty_plan = None
        self.assertEqual(sr._classify_coverage(), "Non-Warranty")


class TestIntakeAcceptsTheCaptureFields(unittest.TestCase):
    """The POS counter must be able to send what it found.

    The fields existed on the Service Request all along; the intake API simply
    did not copy them across, so anything the screen sent was dropped on the
    floor without a word — the same silent-drop that lost advance_amount.
    """

    def tearDown(self):
        frappe.db.rollback()

    def test_the_intake_copies_the_captured_cover(self):
        from ch_pos.api import repair

        source = open(repair.__file__).read()
        for field in ("active_warranty_plan", "previous_service_request",
                      "is_repeat_complaint"):
            self.assertIn(
                f'"{field}"', source,
                f"{field} is not copied by the intake, so the counter's selection "
                "never reaches the ticket")

    def test_a_prior_repair_can_be_linked_at_intake(self):
        """`warranty_rework_context` only zeroes a rework when this is set."""
        previous = _minimal_service_request()
        if not previous:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        sr = _minimal_service_request()
        sr.previous_service_request = previous.name
        sr.is_repeat_complaint = 1
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()
        self.assertEqual(sr.previous_service_request, previous.name)
        self.assertTrue(sr.is_repeat_complaint)


class TestTheCounterChoiceIsHonouredButChecked(unittest.TestCase):
    """Which policy a repair is claimed against is a decision, not a ranking.

    The lookup ranks a device's live plans and returns its pick. That is right
    when nobody has said otherwise, and wrong when they have: a device can
    carry an extended warranty and a damage plan at once, and which one to
    claim against is decided at the counter with the customer there. Letting
    the ranking overwrite that would have made the selection on screen
    decorative — visible, and then discarded on save.

    The check matters as much as the honouring. Without it the field is a way
    to attach an unrelated policy to your own repair.
    """

    def tearDown(self):
        frappe.db.rollback()

    def _sr(self):
        sr = _minimal_service_request()
        if not sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        return sr

    def test_a_valid_choice_beats_the_ranking(self):
        sr = self._sr()
        sr.active_warranty_plan = "PLAN-CHOSEN"
        result = {
            "covering_plan": {"name": "PLAN-RANKED", "warranty_plan": "WP-R"},
            "all_plans": [
                {"name": "PLAN-RANKED", "warranty_plan": "WP-R", "is_valid": True},
                {"name": "PLAN-CHOSEN", "warranty_plan": "WP-C", "is_valid": True},
            ],
        }
        self.assertEqual(sr._choose_covering_plan(result).get("name"), "PLAN-CHOSEN")

    def test_a_plan_that_is_not_on_this_device_is_ignored(self):
        """The guard: you cannot claim against someone else's policy."""
        sr = self._sr()
        sr.active_warranty_plan = "PLAN-SOMEBODY-ELSES"
        result = {
            "covering_plan": {"name": "PLAN-RANKED"},
            "all_plans": [{"name": "PLAN-RANKED", "is_valid": True}],
        }
        self.assertEqual(sr._choose_covering_plan(result).get("name"), "PLAN-RANKED")

    def test_an_expired_plan_cannot_be_chosen(self):
        sr = self._sr()
        sr.active_warranty_plan = "PLAN-LAPSED"
        result = {
            "covering_plan": {"name": "PLAN-RANKED"},
            "all_plans": [
                {"name": "PLAN-RANKED", "is_valid": True},
                {"name": "PLAN-LAPSED", "is_valid": False},
            ],
        }
        self.assertEqual(sr._choose_covering_plan(result).get("name"), "PLAN-RANKED")

    def test_with_no_choice_the_ranking_stands(self):
        sr = self._sr()
        sr.active_warranty_plan = None
        result = {
            "covering_plan": {"name": "PLAN-RANKED"},
            "all_plans": [{"name": "PLAN-RANKED", "is_valid": True}],
        }
        self.assertEqual(sr._choose_covering_plan(result).get("name"), "PLAN-RANKED")
