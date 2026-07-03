# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""GoFix Token API.

Public (guest) endpoints power the customer-facing tablet at ``/gofix-token``:

* :func:`get_tablet_config` — device types, brands, symptoms, visit reasons,
  UX rules and store metadata resolved from a single ``store`` query param.
* :func:`create_token` — creates a queued ``GoFix Token`` from tablet input.
* :func:`get_queue_position` — polled by the confirmation screen.

Authenticated endpoints power the FDE queue and job-linking workflows:

* :func:`list_active_tokens` — active queue for a store.
* :func:`transition_token` — move token between statuses.
* :func:`link_service_request` — attach a downstream Service Request.

All guest endpoints are rate-limited per IP/store so a compromised tablet
cannot spam the queue.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, nowdate, time_diff_in_seconds

from gofix.gofix_services.doctype.gofix_token.gofix_token import (
	ACTIVE_STATUSES,
	STATUS_ATTENDING,
	STATUS_CALLED,
	STATUS_CANCELLED,
	STATUS_COMPLETED,
	STATUS_JOB_CARD,
	STATUS_LEFT,
	STATUS_WAITING,
	normalize_phone,
	resolve_store_code,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ISSUES = 3
_FDE_ROLES = {"System Manager", "Service Manager", "Store Manager", "Store Executive"}
_GUEST_RATE_LIMIT = 30  # per store per hour
_GUEST_RATE_WINDOW = 3600


# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------


def _resolve_store(identifier: str | None) -> dict | None:
	"""Resolve a store identifier to ``{warehouse, company, store_code, store_name}``.

	Accepts (in priority order): CH Store name, CH Store store_code,
	CH Store store_name, POS Profile name, Warehouse name. Returns ``None``
	if nothing matches so the caller can raise a user-friendly error.
	"""

	if not identifier:
		return None
	identifier = identifier.strip()

	# 1) CH Store lookup
	if frappe.db.table_exists("CH Store"):
		row = frappe.db.sql(
			"""
			SELECT name, store_code, store_name, warehouse, company
			FROM `tabCH Store`
			WHERE disabled = 0
			  AND (name = %s OR store_code = %s OR store_name = %s
			       OR pos_profile = %s OR warehouse = %s)
			LIMIT 1
			""",
			(identifier, identifier, identifier, identifier, identifier),
			as_dict=True,
		)
		if row and row[0].get("warehouse"):
			r = row[0]
			return {
				"warehouse": r["warehouse"],
				"company": r["company"],
				"store_code": (r.get("store_code") or "").strip().upper(),
				"store_name": r.get("store_name") or "",
			}

	# 2) POS Profile fallback (some tablets embed the profile name)
	profile = frappe.db.get_value(
		"POS Profile",
		identifier,
		("name", "warehouse", "company"),
		as_dict=True,
	)
	if profile and profile.get("warehouse"):
		code, name = resolve_store_code(profile["warehouse"])
		return {
			"warehouse": profile["warehouse"],
			"company": profile["company"],
			"store_code": code,
			"store_name": name,
		}

	# 3) Warehouse direct
	wh = frappe.db.get_value(
		"Warehouse",
		identifier,
		("name", "warehouse_name", "company", "is_group", "disabled"),
		as_dict=True,
	)
	if wh and not wh.get("is_group") and not wh.get("disabled"):
		code, name = resolve_store_code(wh["name"])
		return {
			"warehouse": wh["name"],
			"company": wh["company"],
			"store_code": code,
			"store_name": name,
		}

	return None


# ---------------------------------------------------------------------------
# Rate-limit helper (guest endpoints)
# ---------------------------------------------------------------------------


def _rate_limit_guest(bucket: str) -> None:
	"""Simple Redis rolling-window limiter — mirrors ``ch_pos.api.token_api``.

	Skipped for authenticated users so FDE testing / smoke tests aren't
	throttled.
	"""

	if frappe.session.user and frappe.session.user != "Guest":
		return
	import time

	now = time.time()
	window_start = now - _GUEST_RATE_WINDOW
	cache_key = f"gofix_token_rate::{bucket}"
	hits = frappe.cache().get_value(cache_key) or []
	hits = [t for t in hits if t > window_start]
	if len(hits) >= _GUEST_RATE_LIMIT:
		frappe.throw(
			_("Too many requests from this store. Please slow down."),
			frappe.RateLimitExceededError,
			title=_("Rate Limit"),
		)
	hits.append(now)
	frappe.cache().set_value(cache_key, hits, expires_in_sec=_GUEST_RATE_WINDOW)


# ---------------------------------------------------------------------------
# Guest: Tablet config
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
def get_tablet_config(store: str) -> dict:
	"""Return everything the customer tablet needs to render the wizard."""

	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))

	_rate_limit_guest(f"config::{resolved['warehouse']}")

	device_types = frappe.get_all(
		"GoFix Device Type",
		filters={"disabled": 0},
		fields=["device_type", "icon", "display_order"],
		order_by="display_order asc, device_type asc",
	)
	visit_reasons = frappe.get_all(
		"GoFix Visit Reason",
		filters={"disabled": 0},
		fields=["reason_name", "is_repair", "display_order"],
		order_by="display_order asc, reason_name asc",
	)
	brand_rows = frappe.get_all(
		"GoFix Brand Option",
		filters={"disabled": 0},
		fields=["device_type", "brand_name", "display_order"],
		order_by="device_type asc, display_order asc, brand_name asc",
	)
	symptom_rows = frappe.get_all(
		"GoFix Symptom",
		filters={"disabled": 0},
		fields=[
			"device_type",
			"symptom_name",
			"is_expert_check",
			"is_other",
			"display_order",
		],
		order_by="device_type asc, display_order asc, symptom_name asc",
	)

	brands_by_device: dict[str, list[dict]] = {}
	for r in brand_rows:
		brands_by_device.setdefault(r["device_type"], []).append(
			{"name": r["brand_name"], "display_order": r["display_order"]}
		)

	symptoms_by_device: dict[str, list[dict]] = {}
	for r in symptom_rows:
		symptoms_by_device.setdefault(r["device_type"], []).append(
			{
				"name": r["symptom_name"],
				"is_expert_check": bool(r["is_expert_check"]),
				"is_other": bool(r["is_other"]),
				"display_order": r["display_order"],
			}
		)

	return {
		"store": {
			"code": resolved["store_code"],
			"name": resolved["store_name"],
			"company": resolved["company"],
			"warehouse": resolved["warehouse"],
		},
		"device_types": [
			{"name": d["device_type"], "icon": d.get("icon") or "", "display_order": d["display_order"]}
			for d in device_types
		],
		"visit_reasons": [
			{
				"name": v["reason_name"],
				"is_repair": bool(v["is_repair"]),
				"display_order": v["display_order"],
			}
			for v in visit_reasons
		],
		"brands_by_device": brands_by_device,
		"symptoms_by_device": symptoms_by_device,
		"rules": {
			"max_issues": _MAX_ISSUES,
			"expert_check_exclusive": True,
			"other_notes_required": False,
			"phone_country_code": "+91",
			"phone_digits": 10,
		},
	}


# ---------------------------------------------------------------------------
# Guest: Create token
# ---------------------------------------------------------------------------


def _parse_issues(payload: Any, resolved_symptoms: dict[str, dict]) -> list[dict]:
	"""Normalize the tablet's issue payload into child-row dicts.

	Accepts either a JSON string or a list. Each item may be a plain string
	(matched against seeded symptoms) or a dict with ``name`` and optional
	``is_expert_check`` / ``is_other`` overrides. Unknown names are still
	accepted and stored as-is so ops can add symptoms without pushing code.
	"""

	if not payload:
		return []
	if isinstance(payload, str):
		try:
			import json

			payload = json.loads(payload)
		except (ValueError, TypeError):
			payload = [s.strip() for s in payload.split(",") if s.strip()]

	rows: list[dict] = []
	for item in payload:
		if isinstance(item, dict):
			name = (item.get("name") or item.get("symptom_name") or "").strip()
			overrides = item
		else:
			name = str(item).strip()
			overrides = {}
		if not name:
			continue
		match = resolved_symptoms.get(name)
		rows.append(
			{
				"symptom_name": name,
				"device_type": (match and match.get("device_type")) or overrides.get("device_type"),
				"is_expert_check": (
					1 if (overrides.get("is_expert_check") or (match and match.get("is_expert_check"))) else 0
				),
				"is_other": (
					1 if (overrides.get("is_other") or (match and match.get("is_other"))) else 0
				),
				"symptom_ref": match.get("name") if match else None,
			}
		)
	return rows


@frappe.whitelist(allow_guest=True)
def create_token(
	store: str,
	customer_name: str,
	customer_phone: str,
	visit_reason: str,
	device_type: str | None = None,
	device_brand: str | None = None,
	device_model: str | None = None,
	other_device_hint: str | None = None,
	selected_issues: Any = None,
	additional_notes: str | None = None,
	customer_language: str | None = None,
) -> dict:
	"""Create a GoFix Token from the customer tablet.

	Returns ``{token_number, name, queue_position, whatsapp_status}``. All
	validation errors bubble as ``frappe.ValidationError`` and are shown to
	the customer inline. Runs with ``ignore_permissions`` because the guest
	tablet has no Desk role.
	"""

	if not (customer_name or "").strip():
		frappe.throw(_("Please enter your name."))
	if not (customer_phone or "").strip():
		frappe.throw(_("Please enter your mobile number."))
	if not (visit_reason or "").strip():
		frappe.throw(_("Please choose a reason for your visit."))

	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))

	_rate_limit_guest(f"create::{resolved['warehouse']}")

	# Validate visit_reason and pull is_repair flag so downstream validation
	# knows whether to demand device + symptoms.
	visit_row = frappe.db.get_value(
		"GoFix Visit Reason",
		visit_reason,
		("name", "is_repair", "disabled"),
		as_dict=True,
	)
	if not visit_row or visit_row.get("disabled"):
		frappe.throw(_("Visit reason {0} is not available.").format(visit_reason))
	is_repair = bool(visit_row.get("is_repair"))

	# Build a lookup of seeded symptoms for the chosen device_type so
	# unknown labels can still be persisted without losing the flags.
	symptom_lookup: dict[str, dict] = {}
	if is_repair and device_type:
		rows = frappe.get_all(
			"GoFix Symptom",
			filters={"device_type": device_type, "disabled": 0},
			fields=["name", "symptom_name", "device_type", "is_expert_check", "is_other"],
		)
		symptom_lookup = {r["symptom_name"]: r for r in rows}

	issues = _parse_issues(selected_issues, symptom_lookup)

	doc = frappe.new_doc("GoFix Token")
	doc.company = resolved["company"]
	doc.store = resolved["warehouse"]
	doc.source = "Tablet"
	doc.customer_name = customer_name.strip()[:140]
	doc.customer_phone = normalize_phone(customer_phone)
	doc.customer_language = customer_language or "English"
	doc.visit_reason = visit_reason
	doc.is_repair_visit = 1 if is_repair else 0
	if is_repair:
		doc.device_type = (device_type or "").strip() or None
		doc.device_brand = (device_brand or "").strip() or None
		doc.device_model = (device_model or "").strip() or None
		doc.other_device_hint = (other_device_hint or "").strip() or None
		for row in issues:
			doc.append("selected_issues", row)
	doc.additional_notes = (additional_notes or "").strip() or None
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Whatsapp send is fire-and-forget; failure must not block the token.
	try:
		_enqueue_whatsapp_confirmation(doc.name)
	except Exception:
		frappe.log_error(
			title="gofix_token: whatsapp enqueue failed",
			message=frappe.get_traceback(),
		)

	position = _queue_position(doc.name, resolved["warehouse"], doc.business_date)
	return {
		"name": doc.name,
		"token_number": doc.token_number,
		"queue_position": position,
		"status": doc.status,
		"whatsapp_status": doc.whatsapp_status,
		"store_code": doc.store_code,
		"store_name": doc.store_name,
		"business_date": str(doc.business_date),
	}


def _enqueue_whatsapp_confirmation(token_name: str) -> None:
	"""Phase 7 hook — no-op stub for now.

	The actual sender lands in ``gofix.gofix_services.whatsapp_notifications``
	so this function stays trivial and the API contract is stable.
	"""

	# TODO(phase-7): frappe.enqueue("gofix.gofix_services.whatsapp_notifications.send_token_confirmation", token=token_name)
	return None


# ---------------------------------------------------------------------------
# Guest: Queue position
# ---------------------------------------------------------------------------


def _queue_position(token_name: str, warehouse: str, business_date) -> int:
	"""Return the 1-based waiting-position of a token in its store today.

	Returns ``0`` for tokens that are no longer Waiting (Called/Attending
	etc.) so the tablet can just show "You're up".
	"""

	status = frappe.db.get_value("GoFix Token", token_name, "status")
	if status != STATUS_WAITING:
		return 0
	ahead = frappe.db.sql(
		"""
		SELECT COUNT(*) FROM `tabGoFix Token`
		WHERE store = %s AND business_date = %s AND status = %s
		  AND creation < (SELECT creation FROM `tabGoFix Token` WHERE name = %s)
		""",
		(warehouse, str(business_date), STATUS_WAITING, token_name),
	)[0][0]
	return int(ahead or 0) + 1


@frappe.whitelist(allow_guest=True)
def get_queue_position(token_number: str, store: str) -> dict:
	"""Poll endpoint used by the confirmation screen."""

	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))
	_rate_limit_guest(f"position::{resolved['warehouse']}")
	row = frappe.db.get_value(
		"GoFix Token",
		{
			"token_number": token_number,
			"store": resolved["warehouse"],
			"business_date": nowdate(),
		},
		("name", "status", "business_date", "whatsapp_status"),
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Token {0} not found for today.").format(token_number))
	position = _queue_position(row["name"], resolved["warehouse"], row["business_date"])
	return {
		"token_number": token_number,
		"status": row["status"],
		"queue_position": position,
		"whatsapp_status": row["whatsapp_status"],
	}


# ---------------------------------------------------------------------------
# FDE: List active tokens
# ---------------------------------------------------------------------------


def _ensure_fde() -> None:
	if not (set(frappe.get_roles()) & _FDE_ROLES):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def list_active_tokens(store: str, statuses: Any = None) -> list[dict]:
	"""Return active tokens for the FDE queue view."""

	_ensure_fde()
	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))

	if statuses:
		if isinstance(statuses, str):
			import json

			try:
				statuses = json.loads(statuses)
			except (ValueError, TypeError):
				statuses = [s.strip() for s in statuses.split(",") if s.strip()]
	else:
		statuses = list(ACTIVE_STATUSES)

	rows = frappe.get_all(
		"GoFix Token",
		filters={
			"store": resolved["warehouse"],
			"business_date": nowdate(),
			"status": ["in", statuses],
		},
		fields=[
			"name",
			"token_number",
			"status",
			"customer_name",
			"customer_phone",
			"visit_reason",
			"device_type",
			"device_brand",
			"device_model",
			"additional_notes",
			"assigned_fde",
			"service_request",
			"job_assignment",
			"creation",
			"called_at",
			"attending_at",
			"whatsapp_status",
		],
		order_by="creation asc",
	)

	now = now_datetime()
	for r in rows:
		r["waiting_seconds"] = int(time_diff_in_seconds(now, r["creation"]))
		# Fetch symptoms lazily — cheap enough at queue length.
		r["symptoms"] = [
			row.symptom_name
			for row in frappe.get_all(
				"GoFix Token Issue",
				filters={"parent": r["name"]},
				fields=["symptom_name"],
				order_by="idx asc",
			)
		]
	return rows


# ---------------------------------------------------------------------------
# FDE: Transition
# ---------------------------------------------------------------------------


@frappe.whitelist()
def transition_token(
	name: str,
	to_status: str,
	reason: str | None = None,
	notes: str | None = None,
	assigned_fde: str | None = None,
) -> dict:
	"""Move a token between statuses.

	Delegates all rule checks to :class:`GoFixToken` — this function just
	patches fields and saves so the validation matrix stays single-source.
	"""

	_ensure_fde()
	doc = frappe.get_doc("GoFix Token", name)
	doc.status = to_status
	if to_status in {STATUS_CANCELLED, STATUS_LEFT}:
		if reason:
			doc.cancellation_reason = reason
		if notes is not None:
			doc.cancellation_notes = notes
	if assigned_fde:
		doc.assigned_fde = assigned_fde
	doc.save()
	frappe.db.commit()
	return {
		"name": doc.name,
		"status": doc.status,
		"token_number": doc.token_number,
		"assigned_fde": doc.assigned_fde,
	}


# ---------------------------------------------------------------------------
# FDE: Link Service Request (Phase 6 will fill in the auto-linking hook)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def link_service_request(name: str, service_request: str) -> dict:
	"""Attach an existing Service Request to a token."""

	_ensure_fde()
	if not frappe.db.exists("Service Request", service_request):
		frappe.throw(_("Service Request {0} not found.").format(service_request))
	doc = frappe.get_doc("GoFix Token", name)
	doc.service_request = service_request
	# Automatically transition to Job Card Created if still Attending — this
	# is the natural signal the FDE has handed off to the technician queue.
	if doc.status == STATUS_ATTENDING:
		doc.status = STATUS_JOB_CARD
	doc.save()
	frappe.db.commit()
	return {"name": doc.name, "status": doc.status, "service_request": doc.service_request}


# ---------------------------------------------------------------------------
# FDE: Cancellation reason catalog (used by the confirm dialog)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_cancellation_reasons(scope: str | None = None) -> list[dict]:
	"""Return the cancellation-reason catalog filtered by scope.

	Scope is either ``Cancelled`` or ``Customer Left``; ``Both`` reasons
	always appear.
	"""

	_ensure_fde()
	filters: dict[str, Any] = {"disabled": 0}
	if scope in {"Cancelled", "Customer Left"}:
		filters["scope"] = ["in", [scope, "Both"]]
	return frappe.get_all(
		"GoFix Cancellation Reason",
		filters=filters,
		fields=["name", "reason_name", "scope", "requires_note", "display_order"],
		order_by="display_order asc, reason_name asc",
	)
