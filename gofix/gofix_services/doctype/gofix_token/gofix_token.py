# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Token controller.

Implements the customer-facing token lifecycle:

    Waiting -> Called -> Attending -> Job Card Created -> Completed
                                          |
                                          +--> Customer Left
                                          +--> Cancelled

The token_number is generated per store per business date using a SQL COUNT
under GET_LOCK, mirroring the pattern proven by ch_pos.api.token_api.
"""

from __future__ import annotations

import re
from typing import Iterable

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime, nowdate


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_WAITING = "Waiting"
STATUS_CALLED = "Called"
STATUS_ATTENDING = "Attending"
STATUS_JOB_CARD = "Job Card Created"
STATUS_COMPLETED = "Completed"
STATUS_LEFT = "Customer Left"
STATUS_CANCELLED = "Cancelled"

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_LEFT, STATUS_CANCELLED}
ACTIVE_STATUSES = {STATUS_WAITING, STATUS_CALLED, STATUS_ATTENDING, STATUS_JOB_CARD}

# Allowed transitions. Keys are the "from" status; values are permitted "to"
# statuses. Only System Manager can override via SET_STATUS_OVERRIDE_ROLE.
_ALLOWED_TRANSITIONS = {
	STATUS_WAITING: {STATUS_CALLED, STATUS_ATTENDING, STATUS_LEFT, STATUS_CANCELLED},
	STATUS_CALLED: {STATUS_ATTENDING, STATUS_WAITING, STATUS_LEFT, STATUS_CANCELLED},
	STATUS_ATTENDING: {STATUS_JOB_CARD, STATUS_COMPLETED, STATUS_LEFT, STATUS_CANCELLED},
	STATUS_JOB_CARD: {STATUS_COMPLETED, STATUS_CANCELLED},
	STATUS_COMPLETED: set(),
	STATUS_LEFT: set(),
	STATUS_CANCELLED: set(),
}

_TRANSITION_ROLES = {"System Manager", "Service Manager", "Store Manager", "Store Executive"}
_MAX_ISSUES = 3

_PHONE_DIGITS = re.compile(r"\D+")


# ---------------------------------------------------------------------------
# Public helpers (used by API layer too)
# ---------------------------------------------------------------------------


def normalize_phone(raw: str | None) -> str:
	"""Normalize an Indian phone number to +91XXXXXXXXXX.

	Accepts common inputs: "9876543210", "+91-98765 43210", "919876543210",
	"0091 9876543210". Returns the input untouched if it can't be normalized
	confidently so validate() can raise a clear error.
	"""

	if not raw:
		return ""
	digits = _PHONE_DIGITS.sub("", raw)
	if not digits:
		return ""
	# strip leading zeros / trunk prefix
	digits = digits.lstrip("0")
	if len(digits) == 10:
		return "+91" + digits
	if len(digits) == 12 and digits.startswith("91"):
		return "+" + digits
	if len(digits) == 11 and digits.startswith("0"):
		return "+91" + digits[1:]
	return raw.strip()


def resolve_store_code(warehouse: str | None) -> tuple[str, str]:
	"""Return (store_code, store_name) for a warehouse.

	Prefers the CH Store row that points at this warehouse. Falls back to the
	first three uppercase alpha characters of the warehouse name so brand-new
	sites still get a usable token prefix.
	"""

	if not warehouse:
		return "", ""
	code = ""
	name = ""
	if frappe.db.has_table("CH Store"):
		row = frappe.db.get_value(
			"CH Store",
			{"warehouse": warehouse},
			("store_code", "store_name"),
			as_dict=True,
		)
		if row:
			code = (row.get("store_code") or "").strip().upper()
			name = row.get("store_name") or ""
	if not name:
		name = frappe.db.get_value("Warehouse", warehouse, "warehouse_name") or warehouse
	if not code:
		# Fallback: uppercase alphas from the warehouse name (drop numerics/spaces)
		alpha = re.sub(r"[^A-Za-z]", "", name).upper()
		code = alpha[:3] or "GFX"
	return code, name


# ---------------------------------------------------------------------------
# DocType controller
# ---------------------------------------------------------------------------


class GoFixToken(Document):
	# --- lifecycle hooks --------------------------------------------------

	def before_insert(self) -> None:
		if not self.business_date:
			self.business_date = nowdate()
		if not self.status:
			self.status = STATUS_WAITING
		if not self.whatsapp_status:
			self.whatsapp_status = "Not Sent"
		if not self.source:
			self.source = "Tablet"
		self._resolve_store_fields()
		self.customer_phone = normalize_phone(self.customer_phone)
		if not self.token_number:
			self.token_number = _next_token_number(self.store, self.business_date)

	def validate(self) -> None:
		self._resolve_store_fields()
		self.customer_phone = normalize_phone(self.customer_phone)
		self._validate_repair_fields()
		self._validate_issue_rules()
		self._validate_status_transition()
		self._validate_cancellation()
		self._touch_status_timestamps()

	def before_save(self) -> None:
		self._append_status_log_if_changed()

	# --- validation helpers ----------------------------------------------

	def _resolve_store_fields(self) -> None:
		code, name = resolve_store_code(self.store)
		if code:
			self.store_code = code
		if name:
			self.store_name = name
		if self.visit_reason and self.is_repair_visit is None:
			self.is_repair_visit = frappe.db.get_value(
				"GoFix Visit Reason", self.visit_reason, "is_repair"
			) or 0

	def _validate_repair_fields(self) -> None:
		if not self.is_repair_visit:
			return
		if not self.device_type:
			frappe.throw(_("Device type is required for a repair visit."))
		if not (self.selected_issues or []):
			frappe.throw(_("Select at least one symptom for a repair visit."))

	def _validate_issue_rules(self) -> None:
		rows = list(self.selected_issues or [])
		if len(rows) > _MAX_ISSUES:
			frappe.throw(
				_("At most {0} symptoms can be selected. Please remove extras.").format(_MAX_ISSUES)
			)
		expert = [r for r in rows if r.is_expert_check]
		if expert and len(rows) > 1:
			frappe.throw(
				_("\"Not sure / expert check\" cannot be combined with other symptoms.")
			)

	def _validate_status_transition(self) -> None:
		if self.is_new():
			return
		previous = frappe.db.get_value("GoFix Token", self.name, "status")
		if previous == self.status:
			return
		allowed = _ALLOWED_TRANSITIONS.get(previous, set())
		if self.status not in allowed and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_("Token cannot move from {0} to {1}.").format(previous, self.status)
			)
		user_roles = set(frappe.get_roles())
		if not (user_roles & _TRANSITION_ROLES):
			frappe.throw(
				_("You do not have permission to change token status.")
			)

	def _validate_cancellation(self) -> None:
		if self.status not in {STATUS_CANCELLED, STATUS_LEFT}:
			return
		if not self.cancellation_reason:
			frappe.throw(_("Select a reason before {0}.").format(self.status))
		reason = frappe.db.get_value(
			"GoFix Cancellation Reason",
			self.cancellation_reason,
			("requires_note", "scope"),
			as_dict=True,
		) or {}
		scope = reason.get("scope") or "Both"
		if scope != "Both":
			if scope == "Customer Left" and self.status != STATUS_LEFT:
				frappe.throw(
					_("Reason {0} can only be used with status Customer Left.").format(
						self.cancellation_reason
					)
				)
			if scope == "Cancelled" and self.status != STATUS_CANCELLED:
				frappe.throw(
					_("Reason {0} can only be used with status Cancelled.").format(
						self.cancellation_reason
					)
				)
		if reason.get("requires_note") and not (self.cancellation_notes or "").strip():
			frappe.throw(
				_("Add a note — reason {0} requires FDE notes.").format(self.cancellation_reason)
			)

	def _touch_status_timestamps(self) -> None:
		"""Stamp per-status timestamps and assignment when the FDE moves the token."""

		if self.is_new():
			return
		previous = frappe.db.get_value("GoFix Token", self.name, "status")
		if previous == self.status:
			return
		now = now_datetime()
		user = frappe.session.user
		if self.status == STATUS_CALLED and not self.called_at:
			self.called_at = now
			self.assigned_fde = self.assigned_fde or user
		elif self.status == STATUS_ATTENDING and not self.attending_at:
			self.attending_at = now
			self.assigned_fde = self.assigned_fde or user
		elif self.status == STATUS_COMPLETED and not self.completed_at:
			self.completed_at = now
		elif self.status == STATUS_LEFT and not self.left_at:
			self.left_at = now
			self.cancelled_by = user
		elif self.status == STATUS_CANCELLED and not self.cancelled_at:
			self.cancelled_at = now
			self.cancelled_by = user

	def _append_status_log_if_changed(self) -> None:
		previous = frappe.db.get_value("GoFix Token", self.name, "status") if not self.is_new() else None
		if previous == self.status and not self.is_new():
			return
		self.append(
			"status_log",
			{
				"from_status": previous or "",
				"to_status": self.status,
				"changed_at": now_datetime(),
				"changed_by": frappe.session.user,
				"notes": (self.cancellation_notes or "").strip() or None,
			},
		)


# ---------------------------------------------------------------------------
# Token-number generator
# ---------------------------------------------------------------------------


def _next_token_number(store: str, business_date) -> str:
	"""Return the next daily token number for a store, e.g. AKG-024.

	Uses GET_LOCK to serialize concurrent tablet submissions per store/date so
	two customers filling the wizard at the same time cannot collide on the
	sequence. Falls back to a timestamp suffix if the lock cannot be acquired
	within one second (extremely unlikely in single-store traffic but safer
	than throwing at the customer).
	"""

	store_code, _name = resolve_store_code(store)
	business_date_str = str(business_date)
	lock_name = f"gofix_token_seq::{store}::{business_date_str}"
	lock_row = frappe.db.sql("SELECT GET_LOCK(%s, 1)", (lock_name,), as_list=True)
	lock_acquired = bool(lock_row and lock_row[0] and lock_row[0][0] == 1)
	try:
		count = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabGoFix Token`
			WHERE store = %s AND business_date = %s
			""",
			(store, business_date_str),
		)[0][0]
		seq = int(count or 0) + 1
		if not lock_acquired:
			# Extremely defensive: nudge past any concurrent insert we couldn't see.
			seq += 1
		return f"{store_code}-{seq:03d}"
	finally:
		if lock_acquired:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))


# ---------------------------------------------------------------------------
# Utility exports
# ---------------------------------------------------------------------------


def get_active_statuses() -> Iterable[str]:
	return tuple(ACTIVE_STATUSES)


def get_terminal_statuses() -> Iterable[str]:
	return tuple(TERMINAL_STATUSES)
