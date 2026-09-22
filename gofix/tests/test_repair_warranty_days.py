# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""What warranty the customer actually gets on a repair.

Two defects lived here, and both were invisible because the number they
produced looked plausible.

**A 90-day repair was granting 30 days.** ``repair_warranty_days`` carried a
docfield default of 30, and completion read
``self.repair_warranty_days or self.resolve_repair_warranty_days()`` — so the
default short-circuited the ``or`` and the resolver never ran on any ticket.
Nineteen of the thirty-seven repair solutions are configured at 90 days,
Screen Replacement among them; every one of them was handing the customer a
month. The service invoice prints this number, so the receipt said 30 while
the workshop had promised 90.

**A diagnosis-only ticket was granting 30 days of free rework.** Nine
solutions are deliberately set to zero — the six diagnostics, data backup,
data recovery and FRP unlock — and none of those is warrantable. The resolver
skipped them (correctly, so that a ticket which diagnoses *and* replaces a
screen still warrants the screen), but when every line was zero it fell
through to the site default. A ticket where nothing was repaired came back
covered.

The asymmetry between solutions and parts is deliberate and tested below: a
solution always declares its warranty, so zero means zero; a part declares one
only when the supplier gives one, so blank means "no part-specific term".
"""

from __future__ import annotations

import unittest

import frappe

from gofix.tests.test_service_maturity import _minimal_service_request

# Catalogue fixtures, chosen because they are the real ones the desk uses.
SCREEN = "SCR-REP"      # Screen Replacement — 90 days
DIAGNOSIS = "GEN-DIA"   # Full Device Diagnosis — 0 days, not warrantable
BATTERY = "BAT-REP"     # Battery Replacement — 180 days


def _days(code):
    return frappe.db.get_value("Repair Solution", code, "warranty_days")


def _cat(code):
    return frappe.db.get_value("Repair Solution", code, "issue_category")


class TestRepairWarrantyDays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in (SCREEN, DIAGNOSIS, BATTERY):
            if not frappe.db.exists("Repair Solution", code):
                raise unittest.SkipTest(f"{code} is not in the repair catalogue on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _ticket(self, *solutions):
        sr = _minimal_service_request()
        if not sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        for code in solutions:
            cat = _cat(code)
            sr.append("issue_lines", {"issue_category": cat, "status": "Open"})
            sr.append("solution_lines", {
                "repair_solution": code, "issue_category": cat, "status": "Completed"})
        sr.save(ignore_permissions=True)
        sr.reload()
        return sr

    # ── the 90-becomes-30 defect ────────────────────────────────────────

    def test_a_screen_repair_gives_the_screen_repair_warranty(self):
        """The one the customer was told about, and the invoice prints."""
        sr = self._ticket(SCREEN)
        self.assertEqual(sr.resolve_repair_warranty_days(), _days(SCREEN))

    def test_the_field_no_longer_pre_empts_the_resolver(self):
        """`repair_warranty_days` must arrive blank so `or` can fall through.

        With a docfield default it was never blank, so the resolver never ran
        and every repair on this estate granted the site default.
        """
        sr = self._ticket(SCREEN)
        self.assertFalse(
            sr.repair_warranty_days,
            "repair_warranty_days is pre-filled again — the resolver is dead and "
            "every repair is back to the site default")
        effective = sr.repair_warranty_days or sr.resolve_repair_warranty_days()
        self.assertEqual(effective, 90)

    def test_an_explicit_override_still_wins(self):
        """The field is still an override — it just isn't one by default."""
        sr = self._ticket(SCREEN)
        sr.repair_warranty_days = 45
        effective = sr.repair_warranty_days or sr.resolve_repair_warranty_days()
        self.assertEqual(effective, 45)

    # ── the diagnosis-only defect ───────────────────────────────────────

    def test_a_diagnosis_alone_carries_no_warranty(self):
        """Nothing was repaired, so there is nothing to warrant."""
        sr = self._ticket(DIAGNOSIS)
        self.assertEqual(
            sr.resolve_repair_warranty_days(), 0,
            "a ticket that only diagnosed a fault is granting free rework")

    def test_diagnosing_and_repairing_still_warrants_the_repair(self):
        """The reason zero is skipped rather than pushed into min().

        Taking the shortest across both would let the diagnosis cancel the
        cover on the screen that was actually replaced.
        """
        sr = self._ticket(DIAGNOSIS, SCREEN)
        self.assertEqual(sr.resolve_repair_warranty_days(), _days(SCREEN))

    def test_a_ticket_with_no_work_falls_back_to_the_site_default(self):
        """No lines at all is a missing answer, not a zero one."""
        from gofix.config import get_int_setting

        sr = self._ticket()
        self.assertEqual(
            sr.resolve_repair_warranty_days(),
            get_int_setting("default_repair_warranty_days", 30))

    # ── the shortest-term rule ──────────────────────────────────────────

    def test_cover_is_the_shortest_term_performed(self):
        sr = self._ticket(SCREEN, BATTERY)
        self.assertEqual(
            sr.resolve_repair_warranty_days(), min(_days(SCREEN), _days(BATTERY)))

    def test_a_part_with_no_term_does_not_shorten_cover(self):
        """Parts are the mirror image of solutions: blank means unstated.

        Almost no spare carries `gofix_part_warranty_days` today, so treating
        blank as zero would take every repair on the estate to no cover.
        """
        sr = self._ticket(SCREEN)
        spare = frappe.db.get_value(
            "Item", {"disabled": 0, "is_stock_item": 1,
                     "gofix_part_warranty_days": ("in", (0, None))}, "name")
        if not spare:
            self.skipTest("no spare without a part warranty on this site")
        sr.append("spare_lines", {"spare_item": spare, "qty": 1, "status": "Issued"})
        self.assertEqual(sr.resolve_repair_warranty_days(), _days(SCREEN))
