"""Every spare is inspected before it goes into a customer's device.

Covers the gate itself, both terminal outcomes, and the thing the whole
feature exists to answer: whether a part arrived broken or we broke it.
"""

import unittest

import frappe
from frappe.utils import nowdate

from gofix.gofix_services import spare_qc


def _minimal_service_request():
    """The smallest Service Request the doctype will accept."""
    company = frappe.db.get_value("Company", {}, "name")
    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
    customer = frappe.db.get_value("Customer", {}, "name")
    model = frappe.db.get_value("CH Model", {}, ["name", "brand", "sub_category"], as_dict=True)
    if not (company and warehouse and customer and model):
        return None
    category = None
    if model.sub_category:
        category = frappe.db.get_value("CH Sub Category", model.sub_category, "category")
    category = category or frappe.db.get_value("CH Category", {}, "name")
    if not category:
        return None

    sr = frappe.new_doc("Service Request")
    sr.customer = customer
    sr.contact_number = "9000000000"
    sr.company = company
    sr.source_warehouse = warehouse
    sr.issue_description = "Spare QC contract test"
    sr.decision = "Draft"
    sr.service_date = nowdate()
    sr.priority = "Medium"
    sr.data_backup_disclaimer = 1
    sr.device_category = category
    sr.device_brand = model.brand
    sr.device_model = model.name
    sr.flags.ignore_permissions = True
    sr.flags.ignore_mandatory = True
    sr.insert(ignore_permissions=True)
    return sr


class TestSpareFitGate(unittest.TestCase):
    """The rule that decides whether a spare may be fitted.

    Pure dict-in / verdict-out, so it runs on any site: these are the exact
    rows the server gate and the Ops Hub both ask about.
    """

    def test_a_spare_that_has_not_arrived_cannot_be_fitted(self):
        for status in ("Awaiting Procurement", "Pending"):
            verdict = spare_qc.fit_permission({"status": status, "qc_status": "Pending QC"})
            self.assertFalse(verdict["allowed"], status)
            self.assertIn("not arrived", verdict["reason"])

    def test_an_uninspected_spare_cannot_be_fitted(self):
        verdict = spare_qc.fit_permission({"status": "Reserved", "qc_status": "Pending QC"})
        self.assertFalse(verdict["allowed"])
        self.assertIn("not passed QC", verdict["reason"])

    def test_a_blank_qc_status_is_treated_as_uninspected(self):
        """Fail closed: an empty value must never read as 'passed'."""
        for blank in (None, ""):
            verdict = spare_qc.fit_permission({"status": "Reserved", "qc_status": blank})
            self.assertFalse(verdict["allowed"], repr(blank))

    def test_a_failed_spare_cannot_be_fitted_and_says_to_replace_it(self):
        verdict = spare_qc.fit_permission({"status": "Damaged", "qc_status": "Failed"})
        self.assertFalse(verdict["allowed"])
        self.assertIn("replacement", verdict["reason"])

    def test_a_passed_spare_may_be_fitted(self):
        verdict = spare_qc.fit_permission({"status": "Reserved", "qc_status": "Passed"})
        self.assertTrue(verdict["allowed"])
        self.assertIsNone(verdict["reason"])

    def test_assert_fit_allowed_raises_rather_than_returning(self):
        """The gate must stop a save, not hand back a value a caller can ignore."""
        with self.assertRaises(frappe.ValidationError):
            spare_qc.assert_fit_allowed({"status": "Reserved", "qc_status": "Pending QC"})


class TestQcVocabulary(unittest.TestCase):
    def test_qc_defects_exclude_anything_that_needs_the_part_fitted(self):
        """Damage caused while fitting is not a QC defect.

        If "Installation Damage" or "Technician Damage" were offered at
        incoming inspection, the two questions this feature exists to separate
        -- did it arrive broken, did we break it -- would collapse into one
        dropdown and the register could no longer split them.
        """
        for forbidden in ("Installation Damage", "Technician Damage"):
            self.assertNotIn(forbidden, spare_qc.QC_DEFECT_TYPES)

    def test_the_stored_options_match_the_code(self):
        """A Select the code can never write is a silent dead end."""
        options = [
            o for o in
            (frappe.get_meta("SR Spare Line").get_field("qc_defect_type").options or "").split("\n")
            if o
        ]
        self.assertEqual(sorted(options), sorted(spare_qc.QC_DEFECT_TYPES))

    def test_every_event_the_code_writes_is_a_valid_option(self):
        options = set(
            (frappe.get_meta("GoFix Spare Event Log").get_field("event_type").options or "").split("\n")
        )
        for event in ("Requested", "Awaiting Procurement", "QC Passed", "QC Failed",
                      "Moved to Damaged Bin", "Replacement Raised",
                      "Damaged by Technician", "Returned", "Recovered"):
            self.assertIn(event, options, event)


class TestSpareHistoryIsDurable(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer / model to build a ticket on")

    def tearDown(self):
        frappe.db.rollback()

    def test_an_event_is_written_and_readable(self):
        name = spare_qc.log_event(
            self.sr.name, "QC Failed", spare_line="row-1", spare_item=None,
            qty=1, defect_type="Transit Damage", remarks="screen cracked in the box",
        )
        self.assertTrue(name, "log_event returned nothing")
        rows = spare_qc.history(self.sr.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "QC Failed")
        self.assertEqual(rows[0]["defect_type"], "Transit Damage")
        self.assertEqual(rows[0]["actor"], "Administrator")

    def test_history_cannot_be_rewritten(self):
        """An edited history is not evidence."""
        name = spare_qc.log_event(self.sr.name, "QC Passed", spare_line="row-1", qty=1)
        doc = frappe.get_doc("GoFix Spare Event Log", name)
        doc.remarks = "actually it was fine"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_a_logging_failure_never_breaks_the_caller(self):
        """Losing the note is bad; losing the stock movement is worse."""
        self.assertIsNone(spare_qc.log_event(self.sr.name, "Not A Real Event Type", qty=1))

    def test_qc_failure_and_technician_damage_are_separable(self):
        """The question the floor actually asks, answered from one table."""
        spare_qc.log_event(self.sr.name, "QC Failed", spare_line="a", qty=1,
                           defect_type="DOA (Dead on Arrival)")
        spare_qc.log_event(self.sr.name, "Damaged by Technician", spare_line="b", qty=1,
                           defect_type="Technician Damage")
        rows = spare_qc.history(self.sr.name)
        kinds = {r["event_type"] for r in rows}
        self.assertEqual(kinds, {"QC Failed", "Damaged by Technician"})
        arrived_broken = [r for r in rows if r["event_type"] == "QC Failed"]
        we_broke_it = [r for r in rows if r["event_type"] == "Damaged by Technician"]
        self.assertEqual(len(arrived_broken), 1)
        self.assertEqual(len(we_broke_it), 1)
