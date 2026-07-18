import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gofix.gofix_services.doctype.job_assignment.job_assignment import (
	JobAssignment,
	_reconstruct_in_progress_periods,
)


class TestJobAssignmentTimeTracking(unittest.TestCase):
	def test_reconstructs_only_active_periods_across_holds(self):
		transitions = [
			(datetime(2026, 7, 18, 14, 35, 33), "Open", "In Progress"),
			(datetime(2026, 7, 18, 15, 27, 44), "In Progress", "On Hold"),
			(datetime(2026, 7, 18, 15, 28, 6), "On Hold", "In Progress"),
			(datetime(2026, 7, 18, 15, 28, 10), "In Progress", "On Hold"),
			(datetime(2026, 7, 18, 16, 6, 17), "On Hold", "In Progress"),
			(datetime(2026, 7, 18, 16, 17, 12), "In Progress", "On Hold"),
			(datetime(2026, 7, 18, 16, 17, 21), "On Hold", "Completed"),
		]

		periods, open_start = _reconstruct_in_progress_periods(transitions)

		self.assertIsNone(open_start)
		self.assertEqual(len(periods), 3)
		total_seconds = sum((ended - started).total_seconds() for started, ended in periods)
		self.assertEqual(total_seconds, 3790)

	def test_returns_open_start_for_live_release_recovery(self):
		transitions = [
			(datetime(2026, 7, 18, 10, 0), "Open", "In Progress"),
		]

		periods, open_start = _reconstruct_in_progress_periods(transitions)

		self.assertEqual(periods, [])
		self.assertEqual(open_start, datetime(2026, 7, 18, 10, 0))

	def test_terminal_end_closes_unreleased_legacy_period(self):
		transitions = [
			(datetime(2026, 7, 18, 10, 0), "Open", "In Progress"),
		]

		periods, open_start = _reconstruct_in_progress_periods(
			transitions,
			terminal_end=datetime(2026, 7, 18, 10, 45),
		)

		self.assertIsNone(open_start)
		self.assertEqual(
			periods,
			[(datetime(2026, 7, 18, 10, 0), datetime(2026, 7, 18, 10, 45))],
		)

	def test_live_release_recovers_missing_open_custody_row(self):
		started_at = datetime(2026, 7, 18, 10, 0)
		ended_at = datetime(2026, 7, 18, 10, 30)
		assignment = SimpleNamespace(
			name="JA-TEST-RECOVERY",
			service_request="SR-TEST-RECOVERY",
			service_order="SO-TEST-RECOVERY",
			service_engineer="EMP-TEST",
			start_datetime=None,
			actual_hours=0,
			_custody_event="release",
			db_set=Mock(),
		)

		with (
			patch(
				"gofix.gofix_services.doctype.job_assignment.job_assignment."
				"frappe.db.exists",
				side_effect=[True, False],
			),
			patch(
				"gofix.gofix_services.doctype.job_assignment.job_assignment."
				"frappe.db.get_value",
				return_value=None,
			),
			patch(
				"gofix.gofix_services.doctype.job_assignment.job_assignment."
				"_job_assignment_status_transitions",
				return_value=[(started_at, "Open", "In Progress")],
			),
			patch(
				"gofix.gofix_services.doctype.job_assignment.job_assignment."
				"_insert_custody_period",
				return_value=0.5,
			) as insert_period,
			patch(
				"gofix.gofix_services.doctype.job_assignment.job_assignment."
				"now_datetime",
				return_value=ended_at,
			),
		):
			JobAssignment.record_custody_event(assignment)

		insert_period.assert_called_once_with(
			assignment,
			started_at,
			ended_at,
			"Recovered from Job Assignment status history.",
		)
		assignment.db_set.assert_any_call(
			"start_datetime",
			started_at,
			update_modified=False,
		)
		assignment.db_set.assert_any_call(
			"actual_hours",
			0.5,
			update_modified=False,
		)


if __name__ == "__main__":
	unittest.main()
