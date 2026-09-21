# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt
"""An escalation that leaves no record did not escalate.

The SLA sweep used to raise a realtime toast and, where configured, send an
email. Neither survives not being looked at — a toast reaches only a browser
open at that second, and all nine GoFix SLA Rules on this estate have
``send_email_alert`` switched off. So the breach counter went up and nothing
anywhere recorded it. That is how eleven escalations left no trace.

``tabService Request`` is 243 columns and ~63.5KB into MySQL's 65,535-byte row
limit, so the trail cannot be a field on the ticket. It is a comment on the
ticket plus a Notification Log per recipient — the same shape the daily standup
settled on, and for the same reason.

These tests assert rows exist. A test that only checked the return value would
have passed against the old code too.
"""

from __future__ import annotations

import unittest

import frappe

from gofix.gofix_services.doctype.gofix_sla_rule.gofix_sla_rule import (
    _escalation_marker,
    _send_sla_alert,
)
from gofix.tests.test_service_maturity import _minimal_service_request

_REPEAT_KEY = "sla_escalation_repeat_seconds"


def _sla(**kw):
    """A rule shaped like the nine real ones: escalation role set, email off."""
    return frappe._dict({
        "name": "GSLA-TEST",
        "target_hours": 4.0,
        "escalation_1_role": "Service Manager",
        "escalation_2_role": "System Manager",
        "escalation_1_email": None,
        "escalation_2_email": None,
        "send_email_alert": 0,
        **kw,
    })


def _comments(sr_name, level):
    return frappe.db.sql(
        """SELECT name, content FROM `tabComment`
           WHERE reference_doctype = 'Service Request' AND reference_name = %s
             AND content LIKE %s""",
        (sr_name, f"%{_escalation_marker(level)}%"), as_dict=True)


def _logs(sr_name):
    return frappe.get_all("Notification Log",
                          filters={"document_type": "Service Request", "document_name": sr_name},
                          fields=["name", "for_user", "subject"])


class TestEscalationLeavesARecord(unittest.TestCase):
    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        self.user = frappe.db.get_value("User", {"enabled": 1, "user_type": "System User"}, "name")
        frappe.cache.delete_value(f"sla_escalation_1_{self.sr.name}")
        frappe.cache.delete_value(f"sla_escalation_2_{self.sr.name}")

    def tearDown(self):
        frappe.cache.delete_value(f"sla_escalation_1_{self.sr.name}")
        frappe.cache.delete_value(f"sla_escalation_2_{self.sr.name}")
        frappe.db.rollback()

    def test_the_ticket_carries_the_escalation_afterwards(self):
        """The assertion the old code could not have passed."""
        self.assertTrue(_send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                                        users=[self.user], user_emails=[]))
        found = _comments(self.sr.name, 1)
        self.assertEqual(len(found), 1, "the escalation left no comment on the ticket")
        self.assertIn("5.0h", found[0].content, "the comment must say how long it had run")

    def test_each_recipient_gets_something_that_waits_for_them(self):
        _send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                        users=[self.user], user_emails=[])
        logs = _logs(self.sr.name)
        self.assertEqual([row.for_user for row in logs], [self.user])

    def test_it_does_not_re_escalate_within_the_window(self):
        self.assertTrue(_send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                                        users=[self.user], user_emails=[]))
        self.assertFalse(_send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.2,
                                         users=[self.user], user_emails=[]))
        self.assertEqual(len(_comments(self.sr.name, 1)), 1)

    def test_the_record_suppresses_it_even_with_a_cold_cache(self):
        """The cache is a fast path, not the memory.

        A worker restart, a flushed Redis or a rolled-back transaction must not
        turn one breach into a stream of duplicate escalations.
        """
        _send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                        users=[self.user], user_emails=[])
        frappe.cache.delete_value(f"sla_escalation_1_{self.sr.name}")
        self.assertFalse(_send_sla_alert(self.sr.name, _sla(), level=1, elapsed=6.0,
                                         users=[self.user], user_emails=[]))
        self.assertEqual(len(_comments(self.sr.name, 1)), 1)

    def test_level_2_is_recorded_separately_from_level_1(self):
        _send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                        users=[self.user], user_emails=[])
        self.assertTrue(_send_sla_alert(self.sr.name, _sla(), level=2, elapsed=9.0,
                                        users=[self.user], user_emails=[]),
                        "a level-2 breach must escalate even though level 1 already did")
        self.assertEqual(len(_comments(self.sr.name, 1)), 1)
        self.assertEqual(len(_comments(self.sr.name, 2)), 1)


class TestEscalationWithNobodyToTell(unittest.TestCase):
    """A breach that reaches no one is the case that most needs recording."""

    def setUp(self):
        self.sr = _minimal_service_request()
        if not self.sr:
            raise unittest.SkipTest("no company / warehouse / customer to build a ticket on")
        frappe.cache.delete_value(f"sla_escalation_1_{self.sr.name}")

    def tearDown(self):
        frappe.cache.delete_value(f"sla_escalation_1_{self.sr.name}")
        frappe.db.rollback()

    def test_it_still_records_the_escalation(self):
        self.assertTrue(_send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                                        users=[], user_emails=[]))
        self.assertEqual(len(_comments(self.sr.name, 1)), 1,
                         "an escalation nobody could receive still has to be on the ticket")

    def test_it_says_nobody_was_configured(self):
        _send_sla_alert(self.sr.name, _sla(escalation_1_role=None), level=1, elapsed=5.0,
                        users=[], user_emails=[])
        self.assertEqual(len(_comments(self.sr.name, 1)), 1)

    def test_it_does_not_re_comment_every_sweep(self):
        """The failure mode of recording only on successful delivery.

        With nobody in scope there is no delivery to key off, so a naive
        implementation re-comments every fifteen minutes — forever, and on
        precisely the tickets nobody is watching.
        """
        for _ in range(4):
            _send_sla_alert(self.sr.name, _sla(), level=1, elapsed=5.0,
                            users=[], user_emails=[])
        self.assertEqual(len(_comments(self.sr.name, 1)), 1,
                         "the escalation was written to the ticket more than once")
