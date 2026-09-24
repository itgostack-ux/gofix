"""A courtesy device must be a real device on this store's shelf.

The field was free text with no lookup, and issue_loaner checked only that the
string was non-empty -- so "1" was accepted as a loaner IMEI and the ticket
recorded a device nobody could ever chase. The pool is the store's own Demo
bin, which is already what a shelf of lendable devices is.
"""

import unittest

import frappe

from gofix import service_maturity as sm


def _a_submitted_ticket():
    return frappe.db.get_value("Service Request", {"docstatus": 1}, "name")


class TestLoanerPool(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "loaner_pool_test"
        frappe.db.savepoint(self.sp)
        name = _a_submitted_ticket()
        if not name:
            raise unittest.SkipTest("no submitted Service Request on this site")
        self.sr = frappe.get_doc("Service Request", name)

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def test_the_pool_is_the_stores_own_demo_bin(self):
        pool = sm.loaner_pool_warehouse(self.sr)
        if not pool:
            raise unittest.SkipTest("this ticket's store has no Demo bin")
        self.assertEqual(
            frappe.db.get_value("Warehouse", pool, "ch_bin_type"), "Demo")
        # Same store as the ticket, not somebody else's shelf.
        self.assertEqual(
            frappe.db.get_value("Warehouse", pool, "parent_warehouse"),
            frappe.db.get_value("Warehouse", self.sr.source_warehouse, "parent_warehouse"))

    def test_a_serial_that_does_not_exist_is_refused(self):
        """The reported bug: typing 1 was accepted."""
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(self.sr.name, "1")

    def test_an_empty_serial_is_still_refused(self):
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(self.sr.name, "   ")

    def test_sellable_stock_cannot_be_lent(self):
        """Lending stock off the sale shelf takes it off the count it belongs to."""
        if not sm.loaner_pool_warehouse(self.sr):
            raise unittest.SkipTest("this ticket's store has no Demo bin")
        sellable = frappe.db.sql(
            """SELECT sn.name FROM `tabSerial No` sn
               JOIN `tabWarehouse` w ON w.name = sn.warehouse
               WHERE sn.status = 'Active' AND w.ch_bin_type = 'Sellable' LIMIT 1""",
            pluck=True,
        )
        if not sellable:
            raise unittest.SkipTest("no active serial in a sellable bin")
        with self.assertRaises(frappe.ValidationError):
            sm.issue_loaner(self.sr.name, sellable[0])

    def _stock_the_pool(self):
        pool = sm.loaner_pool_warehouse(self.sr)
        serial = frappe.db.get_value("Serial No", {"status": "Active"}, "name")
        if not (pool and serial):
            raise unittest.SkipTest("no Demo bin or no active serial to move into it")
        frappe.db.set_value("Serial No", serial, "warehouse", pool)
        return serial

    def test_a_device_on_the_shelf_is_offered_and_can_be_lent(self):
        serial = self._stock_the_pool()
        offered = [r["serial_no"] for r in sm.search_loaner_devices(self.sr.name)]
        self.assertIn(serial, offered)
        sm.issue_loaner(self.sr.name, serial)
        self.sr.reload()
        self.assertEqual(self.sr.loaner_status, "Issued")
        self.assertEqual(self.sr.loaner_serial_no, serial)

    def test_a_device_already_out_stops_being_offered(self):
        """A pool that offers a phone somebody is carrying is worse than none."""
        serial = self._stock_the_pool()
        sm.issue_loaner(self.sr.name, serial)
        self.assertNotIn(serial,
                         [r["serial_no"] for r in sm.search_loaner_devices(self.sr.name)])

    def test_the_search_is_scoped_to_this_stores_shelf(self):
        """A counter can only lend what its own store holds."""
        pool = sm.loaner_pool_warehouse(self.sr)
        if not pool:
            raise unittest.SkipTest("this ticket's store has no Demo bin")
        for row in sm.search_loaner_devices(self.sr.name):
            self.assertEqual(row["warehouse"], pool)
