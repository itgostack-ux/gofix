# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""Loaners and customer feedback, which nothing exercised until now.

``gofix/service_maturity.py`` carries four capabilities the repair desk was
believed to be missing — courtesy devices, CSAT/NPS capture, bench capacity and
certification expiry. All four are implemented and whitelisted. None of them is
called from any screen, and none had a single test.

That combination is the dangerous one. Wiring an untested module to buttons is
how a quiet module becomes a loud one, so these come first: they pin the rules
that stop a loaner going missing, and the scoring contract that decides what a
CSAT number actually means.

The scoring contract is the subtle one. ``csat_score`` is a Frappe ``Rating``
field, and Frappe stores a rating as a 0–1 fraction rather than a star count —
so a customer's 4 out of 5 is persisted as 0.8. Anything reading that column and
showing it as a score would report every rating as a fraction of a star. The
test below pins the conversion so a later change cannot quietly redefine what a
4 means.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, nowdate

from gofix import service_maturity as sm

LOANER_SERIAL = "_CHTEST-LOANER-0001"


def _device_taxonomy():
    """Category / Brand / Model — a ticket will not book in without all three."""
    model = frappe.db.get_value(
        "CH Model", {"brand": ("is", "set")}, ["name", "brand", "sub_category"], as_dict=True
    )
    if not model:
        return None
    category = None
    if model.sub_category:
        category = frappe.db.get_value("CH Sub Category", model.sub_category, "category")
    category = category or frappe.db.get_value("CH Category", {}, "name")
    if not category:
        return None
    return {"device_category": category, "device_brand": model.brand, "device_model": model.name}


def _minimal_service_request():
    """The smallest Service Request the doctype will accept."""
    company = frappe.db.get_value("Company", {}, "name")
    warehouse = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0}, "name"
    )
    customer = frappe.db.get_value("Customer", {}, "name")
    device = _device_taxonomy()
    if not (company and warehouse and customer and device):
        return None

    sr = frappe.new_doc("Service Request")
    sr.customer = customer
    sr.contact_number = "9000000000"
    sr.company = company
    sr.source_warehouse = warehouse
    sr.issue_description = "Loaner and feedback contract test"
    sr.decision = "Draft"
    sr.service_date = nowdate()
    sr.priority = "Medium"
    sr.data_backup_disclaimer = 1
    sr.update(device)
    sr.flags.ignore_permissions = True
    sr.flags.ignore_mandatory = True
    sr.insert(ignore_permissions=True)
    return sr


class TestLoanerCustody(unittest.TestCase):
    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")

    def tearDown(self):
        frappe.db.rollback()

    def test_a_loaner_goes_out_and_is_recorded(self):
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        self.sr.reload()
        self.assertEqual(self.sr.loaner_status, "Issued")
        self.assertEqual(self.sr.loaner_serial_no, LOANER_SERIAL)
        self.assertTrue(self.sr.loaner_issued_at, "the issue time must be stamped")

    def test_a_second_loaner_on_the_same_ticket_is_refused(self):
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(self.sr.name, "_CHTEST-LOANER-0002")

    def test_the_same_device_cannot_be_out_twice(self):
        """The rule that stops one handset being lent to two customers."""
        other = _minimal_service_request()
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(other.name, LOANER_SERIAL)

    def test_a_blank_serial_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(self.sr.name, "   ")

    def test_returning_books_it_back_in(self):
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        sm.return_loaner(self.sr.name, condition="Good")
        self.sr.reload()
        self.assertEqual(self.sr.loaner_status, "Returned")
        self.assertTrue(self.sr.loaner_returned_at, "the return time must be stamped")

    def test_returning_nothing_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            sm.return_loaner(self.sr.name)

    def test_a_ticket_cannot_close_with_our_device_still_out(self):
        """The guard that turns a lost loaner into a blocked ticket."""
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        self.sr.reload()
        with self.assertRaises(frappe.ValidationError):
            sm.assert_loaner_returned(self.sr, "deliver")

        sm.return_loaner(self.sr.name)
        self.sr.reload()
        sm.assert_loaner_returned(self.sr, "deliver")  # must not raise

    def test_outstanding_lists_what_is_still_out(self):
        sm.issue_loaner(self.sr.name, LOANER_SERIAL)
        names = [r["name"] for r in sm.outstanding_loaners(company=self.sr.company)]
        self.assertIn(self.sr.name, names)

        sm.return_loaner(self.sr.name)
        names = [r["name"] for r in sm.outstanding_loaners(company=self.sr.company)]
        self.assertNotIn(self.sr.name, names, "a returned loaner must drop off the list")


class TestCustomerFeedback(unittest.TestCase):
    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")

    def tearDown(self):
        frappe.db.rollback()

    def test_csat_is_stored_as_a_fraction_not_a_star_count(self):
        """4 out of 5 persists as 0.8 — Frappe's Rating convention.

        Pinned because anything that reads this column and prints it as a
        score would report every rating as a fraction of one star.
        """
        sm.record_feedback(self.sr.name, csat=4)
        self.sr.reload()
        self.assertAlmostEqual(float(self.sr.csat_score), 0.8, places=4)

    def test_nps_is_stored_as_given(self):
        sm.record_feedback(self.sr.name, nps=9)
        self.sr.reload()
        self.assertEqual(int(self.sr.nps_score), 9)

    def test_a_comment_alone_is_still_feedback(self):
        """Someone who writes a sentence and skips both scores has told you something."""
        sm.record_feedback(self.sr.name, comment="Fixed fast, staff were helpful")
        self.sr.reload()
        self.assertEqual(self.sr.feedback_comment, "Fixed fast, staff were helpful")
        self.assertTrue(self.sr.feedback_received_at)

    def test_scores_outside_their_scale_are_refused(self):
        for bad_csat in (0, 6):
            with self.subTest(csat=bad_csat), self.assertRaises(frappe.ValidationError):
                sm.record_feedback(self.sr.name, csat=bad_csat)
        for bad_nps in (-1, 11):
            with self.subTest(nps=bad_nps), self.assertRaises(frappe.ValidationError):
                sm.record_feedback(self.sr.name, nps=bad_nps)

    def test_summary_counts_the_response(self):
        sm.record_feedback(self.sr.name, csat=5, nps=10)
        summary = sm.feedback_summary(company=self.sr.company)
        self.assertGreaterEqual(summary.get("responses", 0), 1)
        for key in ("responses", "csat_avg", "nps", "promoters", "detractors"):
            self.assertIn(key, summary, f"summary lost its {key} key")


class TestBenchCapacity(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_capacity_answers_for_a_real_warehouse(self):
        company = frappe.db.get_value("Company", {}, "name")
        warehouse = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 0}, "name"
        )
        if not warehouse:
            raise unittest.SkipTest("no non-group warehouse on this site")
        out = sm.bench_capacity(warehouse)
        self.assertEqual(out.get("warehouse"), warehouse)
        self.assertIn("date", out, "capacity must say which day it is answering for")


class TestCertificationAndCapacity(unittest.TestCase):
    """Who counts as bench capacity, and what happens when nobody does.

    ``certification_valid`` fails *open* on purpose: a technician with no
    expiry recorded is valid, because most hold no time-limited authorisation
    and treating a blank as lapsed would empty the roster. That default is
    load-bearing, so it is pinned here — an edit that flips it would silently
    take every store's bench to zero hours with no error anywhere.
    """

    def setUp(self):
        self.company = frappe.db.get_value("Company", {}, "name")
        self.warehouse = frappe.db.get_value(
            "Warehouse", {"company": self.company, "is_group": 0}, "name"
        )
        if not self.warehouse:
            raise unittest.SkipTest("no non-group warehouse on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _technician(self, expiry=None, bench_hours=None):
        emp = frappe.new_doc("Employee")
        emp.first_name = "CHTest"
        emp.gender = frappe.db.get_value("Gender", {}, "name") or "Male"
        emp.date_of_birth = "1990-01-01"
        emp.date_of_joining = "2020-01-01"
        emp.status = "Active"
        emp.company = self.company
        emp.gofix_service_warehouse = self.warehouse
        if expiry:
            emp.gofix_certification_expiry = expiry
        if bench_hours is not None:
            emp.gofix_daily_bench_hours = bench_hours
        emp.flags.ignore_permissions = True
        emp.flags.ignore_mandatory = True
        emp.insert(ignore_permissions=True)
        return emp

    def test_no_expiry_recorded_means_certified(self):
        """The fail-open default. Flipping it would empty every bench."""
        emp = self._technician()
        self.assertTrue(sm.certification_valid(emp.name))

    def test_a_past_expiry_is_lapsed(self):
        emp = self._technician(expiry=add_days(nowdate(), -1))
        self.assertFalse(sm.certification_valid(emp.name))

    def test_todays_expiry_is_still_valid(self):
        """Valid *until* the date, inclusive — a boundary worth being sure of."""
        emp = self._technician(expiry=nowdate())
        self.assertTrue(sm.certification_valid(emp.name))

    def test_nobody_is_not_certified(self):
        self.assertFalse(sm.certification_valid(None))
        self.assertFalse(sm.certification_valid(""))

    def test_a_lapsed_technician_stops_counting_as_capacity(self):
        self._technician(expiry=add_days(nowdate(), -1), bench_hours=8)
        out = sm.bench_capacity(self.warehouse)
        self.assertEqual(out["technicians"], 0, "a lapsed technician was counted as available")
        self.assertEqual(out["available_hours"], 0)
        self.assertEqual(out["lapsed_certifications"], 1,
                         "the lapse must be reported, not just subtracted")

    def test_a_certified_technician_contributes_their_hours(self):
        self._technician(bench_hours=8)
        out = sm.bench_capacity(self.warehouse)
        self.assertEqual(out["technicians"], 1)
        self.assertEqual(out["available_hours"], 8.0)

    def test_an_empty_bench_reports_no_capacity_rather_than_full_capacity(self):
        """The estate's actual state: `tabEmployee` is empty in production.

        With no roster, utilisation is unknowable — and it must come back as
        unknown, not as 0% (which reads as "plenty of room") and not as a
        division by zero.
        """
        out = sm.bench_capacity(self.warehouse)
        self.assertEqual(out["technicians"], 0)
        self.assertEqual(out["available_hours"], 0)
        self.assertIsNone(out["utilisation"], "unknown utilisation must not be reported as 0%")
        self.assertFalse(out["over_capacity"])

    def test_expiring_lists_the_lapsed_technician(self):
        emp = self._technician(expiry=add_days(nowdate(), 5))
        names = [r["name"] for r in sm.expiring_certifications(within_days=30)]
        self.assertIn(emp.name, names)
        self.assertNotIn(
            emp.name, [r["name"] for r in sm.expiring_certifications(within_days=1)],
            "an authorisation valid for another 5 days is not expiring within 1",
        )


class TestAppointments(unittest.TestCase):
    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")

    def tearDown(self):
        frappe.db.rollback()

    def test_booking_records_the_slot(self):
        slot = f"{add_days(nowdate(), 1)} 11:30:00"
        out = sm.book_appointment(self.sr.name, slot)
        self.sr.reload()
        self.assertTrue(self.sr.appointment_datetime)
        self.assertEqual(str(self.sr.appointment_datetime), slot)
        self.assertTrue(out.get("ok"))

    def test_booking_answers_with_that_day_s_capacity(self):
        """The warning is only useful if the slot's own day was measured."""
        slot = f"{add_days(nowdate(), 3)} 09:00:00"
        out = sm.book_appointment(self.sr.name, slot)
        self.assertEqual(out["capacity"].get("date"), add_days(nowdate(), 3))
