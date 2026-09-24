"""The timeline must account for time the ticket is still spending.

Dwell is recorded when a stage is LEFT, so the stage a ticket is sitting in
had no row and its hours were charged to nothing: a ticket parked in Repair
for three days showed Repair nowhere, and "stages touched" was always one
short. And because the operations stage does not change when a repair is
paused, the hours a job spent waiting for a part were charged to the bench --
a board reading that cannot tell a slow repair from a stalled one.
"""

import unittest

import frappe
from frappe.utils import add_to_date, now_datetime

from gofix.gofix_services.page.gofix_ops_hub import gofix_ops_hub as hub


def _a_ticket_with_history():
    row = frappe.db.sql(
        """SELECT parent FROM `tabGoFix Status Log`
           WHERE parenttype = 'Service Request' AND event_type = 'Operations Stage'
           GROUP BY parent ORDER BY COUNT(*) DESC LIMIT 1""",
        pluck=True,
    )
    return row[0] if row else None


class TestOpenStageIsCounted(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "open_stage_test"
        frappe.db.savepoint(self.sp)
        self.sr = _a_ticket_with_history()
        if not self.sr:
            raise unittest.SkipTest("no ticket with a status history on this site")

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def test_the_stage_the_ticket_is_in_gets_a_row(self):
        tl = hub._build_status_timeline(frappe.get_doc("Service Request", self.sr))
        open_rows = [e for e in tl if e.get("open")]
        self.assertTrue(open_rows, "no open stage row - current time is charged to nothing")

    def test_the_open_row_names_the_stage_and_carries_no_destination(self):
        """It is where the ticket IS, not a move it has made."""
        tl = hub._build_status_timeline(frappe.get_doc("Service Request", self.sr))
        for e in (x for x in tl if x.get("open")):
            self.assertTrue(e["from_status"], "open row has no stage")
            self.assertIsNone(e["to_status"], "an open stage has not been left")

    def test_the_open_row_carries_the_server_clock(self):
        """So a workstation with a wrong clock cannot tick it wrongly."""
        tl = hub._build_status_timeline(frappe.get_doc("Service Request", self.sr))
        for e in (x for x in tl if x.get("open")):
            self.assertTrue(e.get("server_now"), "open row cannot be ticked safely")

    def test_open_time_is_positive_for_a_ticket_that_has_been_sitting(self):
        tl = hub._build_status_timeline(frappe.get_doc("Service Request", self.sr))
        ops = [e for e in tl if e.get("open") and e["track"] == "Operations"]
        if not ops:
            raise unittest.SkipTest("no open Operations row on this ticket")
        self.assertGreaterEqual(ops[0]["hours_in_prev"], 0)


class TestHeldTimeIsItsOwnTrack(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "hold_track_test"
        frappe.db.savepoint(self.sp)

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def test_hold_is_an_allowed_event_type(self):
        options = (frappe.get_meta("GoFix Status Log").get_field("event_type").options or "").split("\n")
        self.assertIn("Hold", options)

    def test_the_builder_knows_the_hold_track(self):
        self.assertIn("Hold", hub.TIMELINE_TRACKS)

    def test_going_on_hold_and_resuming_each_write_one_row(self):
        sr = _a_ticket_with_history()
        line = frappe.db.get_value(
            "SR Solution Line", {"parent": sr, "parenttype": "Service Request"},
            ["name", "status", "repair_solution"], as_dict=True) if sr else None
        if not line:
            raise unittest.SkipTest("no solution line to hold")
        reason = frappe.db.get_value("GoFix Pause Reason", {"is_active": 1}, "name")
        if not reason:
            raise unittest.SkipTest("no active pause reason seeded")

        before = frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"})
        # Force a known starting point so the transitions are real ones.
        frappe.db.set_value("SR Solution Line", line.name, "status", "In Progress")
        hub._log_hold_event(sr, line, "In Progress", "On Hold", reason, "waiting on a part")
        hub._log_hold_event(sr, line, "On Hold", "In Progress", None, "part arrived")
        after = frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"})
        self.assertEqual(after - before, 2)

    def test_a_status_move_that_is_not_a_hold_writes_nothing(self):
        """Every other transition is already on the Operations track."""
        sr = _a_ticket_with_history()
        line = frappe.db.get_value(
            "SR Solution Line", {"parent": sr, "parenttype": "Service Request"},
            ["name", "status", "repair_solution"], as_dict=True) if sr else None
        if not line:
            raise unittest.SkipTest("no solution line")
        before = frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"})
        hub._log_hold_event(sr, line, "In Progress", "Completed", None, "done")
        hub._log_hold_event(sr, line, "Planned", "In Progress", None, "started")
        self.assertEqual(frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"}), before)

    def test_holding_something_already_held_does_not_double_log(self):
        sr = _a_ticket_with_history()
        line = frappe.db.get_value(
            "SR Solution Line", {"parent": sr, "parenttype": "Service Request"},
            ["name", "status", "repair_solution"], as_dict=True) if sr else None
        if not line:
            raise unittest.SkipTest("no solution line")
        reason = frappe.db.get_value("GoFix Pause Reason", {"is_active": 1}, "name")
        before = frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"})
        hub._log_hold_event(sr, line, "On Hold", "On Hold", reason, "still waiting")
        self.assertEqual(frappe.db.count("GoFix Status Log", {"parent": sr, "event_type": "Hold"}), before)

    def test_the_first_hold_row_is_not_charged_from_intake(self):
        """A hold is an interruption with a start, not a stage held since intake.

        Anchoring the track at creation reported a ten-minute hold on an
        already-held ticket as having lasted 4.56 hours.
        """
        sr = _a_ticket_with_history()
        line = frappe.db.get_value(
            "SR Solution Line", {"parent": sr, "parenttype": "Service Request"},
            ["name", "status", "repair_solution"], as_dict=True) if sr else None
        reason = frappe.db.get_value("GoFix Pause Reason", {"is_active": 1}, "name")
        if not (line and reason):
            raise unittest.SkipTest("no solution line or pause reason")
        frappe.db.set_value("SR Solution Line", line.name, "status", "In Progress")
        hub._log_hold_event(sr, line, "In Progress", "On Hold", reason, "waiting")
        tl = hub._build_status_timeline(frappe.get_doc("Service Request", sr))
        holds = [e for e in tl if e["track"] == "Hold" and not e.get("open")]
        self.assertTrue(holds)
        self.assertEqual(holds[0]["hours_in_prev"], 0.0)
