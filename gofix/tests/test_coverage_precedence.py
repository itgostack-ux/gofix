# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""Which pocket pays is worked out, not typed in.

``coverage_category`` bifurcates every repair three ways — In-Warranty (ours to
carry), VAS Claim (a policy pays, through the claims flow), Non-Warranty (the
customer pays). Step A put the evidence on the ticket: ``active_warranty_plan``
for a live policy, ``previous_service_request`` for our own prior repair. This
step makes the label follow that evidence in the right order.

Three things were wrong, and each of them is a way the label lies:

**Our own repair warranty was not in the ranking at all.** ``_classify_coverage``
read ``warranty_status`` and ``active_warranty_plan`` and nothing else, so a
handset back on the bench six weeks after we replaced its screen — inside the
workmanship warranty we granted — came out "VAS Claim" the moment the customer
also happened to hold a protection plan. The rule the business states is the
opposite: within the repair warranty it is ours; after it, and with a plan, it
is a claim.

**The repeat-complaint detector ran twelve steps too late.** ``validate()``
called ``fetch_warranty_from_serial`` (which classifies) at line 430 and
``_detect_repeat_complaint`` (which sets ``previous_service_request``) at line
442. The system's own finding could not reach its own decision.

**A claim ticket's authority lived in a flag, not a field.** CH Warranty Claim
creates the GoFix ticket with ``flags.skip_warranty_fetch``, which exists for
exactly one ``insert()``. The first time a GoFix staffer opened that ticket and
saved it, the lookup ran, found no live device warranty, and quietly downgraded
an approved claim to "No Warranty" — with a popup telling them no cover was
found. It also never set ``coverage_category`` at all, so the tickets that are
the clearest VAS claims on the site carried a blank bucket.
"""

from __future__ import annotations

import unittest

import frappe
from ch_erp15.warranty import UNDER_WARRANTY
from frappe.utils import add_days, nowdate

from gofix.tests.test_service_maturity import _device_taxonomy, _minimal_service_request

SERIAL = "_CHTEST-COVERAGE-0001"


def _registered_serial(expiry=None):
    """A Serial No we have sold, whose device warranty ran out yesterday.

    The expired device warranty is the point: it is what ``_fallback_warranty_``
    ``from_serial`` overwrites the ticket with when nothing stops it.
    """
    if frappe.db.exists("Serial No", SERIAL):
        frappe.db.set_value("Serial No", SERIAL, "warranty_expiry_date",
                            expiry or add_days(nowdate(), -1))
        return SERIAL
    item = frappe.db.sql(
        """SELECT name FROM `tabItem`
           WHERE IFNULL(disabled, 0) = 0 AND has_serial_no = 1
             AND IFNULL(has_variants, 0) = 0
           ORDER BY name LIMIT 1""",
        pluck=True,
    )
    if not item:
        return None
    doc = frappe.new_doc("Serial No")
    doc.serial_no = SERIAL
    doc.item_code = item[0]
    doc.warranty_expiry_date = expiry or add_days(nowdate(), -1)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    return SERIAL


def _new_ticket_fields():
    """An unsaved Service Request, so a caller can insert it itself.

    ``_minimal_service_request`` inserts, and the claim's authority flag only
    means anything on the insert — so a test of that path has to own it.
    """
    company = frappe.db.get_value("Company", {}, "name")
    warehouse = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0}, "name")
    customer = frappe.db.get_value("Customer", {}, "name")
    device = _device_taxonomy()
    if not (company and warehouse and customer and device):
        return None
    sr = frappe.new_doc("Service Request")
    sr.customer = customer
    sr.contact_number = "9000000000"
    sr.company = company
    sr.source_warehouse = warehouse
    sr.issue_description = "Coverage precedence fixture"
    sr.decision = "Draft"
    sr.service_date = nowdate()
    sr.priority = "Medium"
    sr.data_backup_disclaimer = 1
    sr.update(device)
    return sr


def _a_repair_we_warranted(days_left=60, **extra):
    """A completed repair of ours whose workmanship warranty is still open."""
    previous = _minimal_service_request()
    if not previous:
        return None
    frappe.db.set_value(
        "Service Request", previous.name,
        {
            "decision": "Completed",
            "repair_warranty_days": 90,
            "repair_warranty_expiry": add_days(nowdate(), days_left),
            **extra,
        },
        update_modified=False,
    )
    previous.reload()
    return previous


class TestOurOwnRepairWarrantyOutranksAPlan(unittest.TestCase):
    """The precedence the business states, in the order it states it.

    Classified in memory: the evidence guard on ``warranty_status`` and the
    Link validation on ``active_warranty_plan`` are separate rules with their
    own tests, and the question here is only which bucket wins.
    """

    def tearDown(self):
        frappe.db.rollback()

    def _ticket(self):
        sr = _minimal_service_request()
        if not sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        return sr

    def test_a_live_repair_warranty_beats_a_live_plan(self):
        """Six weeks after our screen job, with a plan in force: ours to carry."""
        previous = _a_repair_we_warranted(days_left=60)
        if not previous:
            raise unittest.SkipTest("no ticket could be built")
        sr = self._ticket()
        sr.previous_service_request = previous.name
        sr.active_warranty_plan = "a-live-plan"
        self.assertEqual(
            sr._classify_coverage(), "In-Warranty",
            "a return visit inside our own workmanship warranty is being filed "
            "as a VAS claim, which bills the customer's policy for work we owe "
            "them free")

    def test_a_live_repair_warranty_alone_is_in_warranty(self):
        previous = _a_repair_we_warranted(days_left=5)
        if not previous:
            raise unittest.SkipTest("no ticket could be built")
        sr = self._ticket()
        sr.previous_service_request = previous.name
        self.assertEqual(sr._classify_coverage(), "In-Warranty")

    def test_an_expired_repair_warranty_gives_the_plan_its_turn(self):
        """After three months, with a plan: exactly what the business asked for."""
        previous = _a_repair_we_warranted(days_left=-1)
        if not previous:
            raise unittest.SkipTest("no ticket could be built")
        sr = self._ticket()
        sr.previous_service_request = previous.name
        sr.active_warranty_plan = "a-live-plan"
        self.assertEqual(sr._classify_coverage(), "VAS Claim")

    def test_an_expired_repair_warranty_with_no_plan_is_the_customer_paying(self):
        previous = _a_repair_we_warranted(days_left=-1)
        if not previous:
            raise unittest.SkipTest("no ticket could be built")
        sr = self._ticket()
        sr.previous_service_request = previous.name
        self.assertEqual(sr._classify_coverage(), "Non-Warranty")

    def test_a_previous_repair_that_carried_no_warranty_is_not_cover(self):
        """Blank expiry means no term was granted — never an open-ended one."""
        previous = _minimal_service_request()
        if not previous:
            raise unittest.SkipTest("no ticket could be built")
        frappe.db.set_value("Service Request", previous.name,
                            {"repair_warranty_expiry": None}, update_modified=False)
        sr = self._ticket()
        sr.previous_service_request = previous.name
        self.assertEqual(sr._classify_coverage(), "Non-Warranty")

    def test_a_link_to_a_repair_that_no_longer_exists_is_not_cover(self):
        sr = self._ticket()
        sr.previous_service_request = "SR-DOES-NOT-EXIST"
        self.assertEqual(sr._classify_coverage(), "Non-Warranty")

    def test_a_device_warranty_still_outranks_everything(self):
        sr = self._ticket()
        sr.warranty_status = UNDER_WARRANTY
        sr.active_warranty_plan = "a-live-plan"
        self.assertEqual(sr._classify_coverage(), "In-Warranty")


class TestTheRepeatIsKnownBeforeTheTicketIsClassified(unittest.TestCase):
    """The detector's finding has to reach the decision that uses it.

    This is the ordering test, and it is deliberately driven through a real
    ``save()`` rather than by calling the two methods by hand — the bug was
    entirely in the order ``validate()`` calls them, so calling them in the
    right order by hand would have passed all along.
    """

    def tearDown(self):
        frappe.db.rollback()

    def test_an_auto_detected_repeat_lands_in_the_bucket(self):
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        category = frappe.db.get_value("Issue Category", {}, "name")
        if not category:
            raise unittest.SkipTest("no issue category to match a repeat on")

        previous = _a_repair_we_warranted(
            days_left=45, serial_no=serial, issue_category=category,
            service_date=nowdate(),
        )
        if not previous:
            raise unittest.SkipTest("no ticket could be built")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.issue_category = category
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()

        self.assertEqual(
            sr.previous_service_request, previous.name,
            "the repeat detector did not link the prior repair")
        self.assertEqual(
            sr.coverage_category, "In-Warranty",
            "the ticket was classified before the repeat was detected, so the "
            "system's own finding could not reach its own decision")


class TestAClaimTicketKeepsItsAuthority(unittest.TestCase):
    """An approved claim must not be downgraded by the next person to save it."""

    def tearDown(self):
        frappe.db.rollback()

    def _claim(self, coverage_type="vas_plan"):
        company = frappe.db.get_value("Company", {}, "name")
        customer = frappe.db.get_value("Customer", {}, "name")
        item = frappe.db.get_value("Item", {"disabled": 0, "has_variants": 0}, "name")
        if not (company and customer and item):
            return None
        claim = frappe.new_doc("CH Warranty Claim")
        claim.claim_date = nowdate()
        claim.claim_channel = "Store"
        claim.company = company
        claim.reported_at_company = company
        claim.customer = customer
        claim.item_code = item
        claim.serial_no = SERIAL
        claim.issue_description = "Coverage precedence fixture"
        claim.coverage_type = coverage_type
        claim.flags.ignore_permissions = True
        claim.flags.ignore_mandatory = True
        claim.flags.ignore_validate = True
        claim.insert(ignore_permissions=True)
        return claim

    def test_saving_a_claim_ticket_does_not_strip_its_warranty(self):
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        claim = self._claim()
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.warranty_status = UNDER_WARRANTY
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()

        self.assertEqual(
            sr.warranty_status, UNDER_WARRANTY,
            "an approved claim was downgraded to out-of-warranty the first time "
            "the ticket was saved, because its authority lived in a flag that "
            "only survived the insert")

    def test_a_claim_ticket_is_never_left_without_a_bucket(self):
        """Inserted the way the claim inserts it — flag and all.

        This is the path that produced the blank bucket, and it only produces
        it on the insert: the lookup returned early on the flag without ever
        classifying, so the tickets that are the clearest VAS claims on the
        site were the ones with no coverage category at all.
        """
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        claim = self._claim()
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _new_ticket_fields()
        if not sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        sr.serial_no = serial
        sr.warranty_status = UNDER_WARRANTY
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        sr.flags.ignore_mandatory = True
        sr.flags.skip_warranty_fetch = True
        sr.insert(ignore_permissions=True)
        sr.reload()

        self.assertEqual(
            sr.coverage_category, "VAS Claim",
            "the ticket an approved VAS claim created carries no coverage "
            "bucket at all")

    def test_a_vas_plan_claim_is_filed_as_a_claim_not_as_our_own_cost(self):
        """The claim's own coverage_type says who pays; nothing else does.

        Left as In-Warranty these tickets inflate the one number the label
        exists to produce — what the business carried on its own tab.
        """
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        claim = self._claim(coverage_type="vas_plan")
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.warranty_status = UNDER_WARRANTY
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()
        self.assertEqual(sr.coverage_category, "VAS Claim")

    def test_a_repair_warranty_claim_stays_on_our_own_tab(self):
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        claim = self._claim(coverage_type="repair_warranty")
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.warranty_status = UNDER_WARRANTY
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()
        self.assertEqual(sr.coverage_category, "In-Warranty")


class TestAClaimCannotBeBorrowed(unittest.TestCase):
    """The link that now grants cover has to be the claim's own.

    Before this step ``warranty_claim`` was decorative — nothing wrote it and
    nothing much read it. It now decides whether the warranty lookup runs at
    all and which bucket the repair is filed in, which is precisely the power
    that made a hand-typed "Under Warranty" worth guarding. So it gets the same
    treatment: the claim has to agree that this is its ticket and its device.
    """

    def tearDown(self):
        frappe.db.rollback()

    def _claim_for(self, serial, service_request=None):
        company = frappe.db.get_value("Company", {}, "name")
        customer = frappe.db.get_value("Customer", {}, "name")
        item = frappe.db.get_value("Item", {"disabled": 0, "has_variants": 0}, "name")
        if not (company and customer and item):
            return None
        claim = frappe.new_doc("CH Warranty Claim")
        claim.claim_date = nowdate()
        claim.claim_channel = "Store"
        claim.company = company
        claim.reported_at_company = company
        claim.customer = customer
        claim.item_code = item
        claim.serial_no = serial
        claim.issue_description = "Borrowed-claim fixture"
        claim.coverage_type = "vas_plan"
        if service_request:
            claim.service_request = service_request
        claim.flags.ignore_permissions = True
        claim.flags.ignore_mandatory = True
        claim.flags.ignore_validate = True
        claim.flags.ignore_links = True
        claim.insert(ignore_permissions=True)
        return claim

    def test_a_claim_raised_for_another_ticket_is_refused(self):
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        other = _minimal_service_request()
        if not other:
            raise unittest.SkipTest("no ticket could be built")
        claim = self._claim_for(serial, service_request=other.name)
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            sr.save(ignore_permissions=True)

    def test_a_claim_raised_for_another_device_is_refused(self):
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        claim = self._claim_for("_CHTEST-SOME-OTHER-IMEI")
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr = _minimal_service_request()
        sr.serial_no = serial
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError):
            sr.save(ignore_permissions=True)

    def test_the_claim_that_created_this_ticket_is_accepted(self):
        """The claim stamps its own `service_request` right after insert."""
        serial = _registered_serial()
        if not serial:
            raise unittest.SkipTest("no serialised item on this site")
        sr = _minimal_service_request()
        if not sr:
            raise unittest.SkipTest("no ticket could be built")
        claim = self._claim_for(serial, service_request=sr.name)
        if not claim:
            raise unittest.SkipTest("no company / customer / item to raise a claim on")

        sr.serial_no = serial
        sr.warranty_status = UNDER_WARRANTY
        sr.warranty_claim = claim.name
        sr.flags.ignore_permissions = True
        sr.save(ignore_permissions=True)
        sr.reload()
        self.assertEqual(sr.coverage_category, "VAS Claim")


class TestTheClaimStampsTheTicketItCreates(unittest.TestCase):
    """The link back to the claim has to be a field, not a comment.

    ``ch_warranty_claim`` wrote ``custom_warranty_claim`` behind a
    ``has_column`` guard — a column that does not exist on this bench, so the
    write was a silent no-op — while ``Service Request.warranty_claim``, the
    real field the doctype ships, was left empty by every path. Two guards read
    it: the evidence check that lets an approved claim assert cover, and the
    double-count guard on ``claims_used``. Both were dead.
    """

    def tearDown(self):
        frappe.db.rollback()

    def test_the_real_link_field_is_populated(self):
        import inspect

        from ch_item_master.ch_item_master.doctype.ch_warranty_claim import ch_warranty_claim

        source = inspect.getsource(ch_warranty_claim)
        self.assertIn(
            "sr.warranty_claim = self.name", source,
            "the claim never stamps Service Request.warranty_claim, so the "
            "ticket has no link back to the claim that authorised it")
