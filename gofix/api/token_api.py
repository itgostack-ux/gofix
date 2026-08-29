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
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, get_datetime, now_datetime, nowdate, time_diff_in_seconds

from gofix.config import get_int_setting, has_role_setting, is_privileged_user
from gofix.security import assert_service_request_access, get_user_service_scope
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
	resolve_store_code)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------


def _company_is_gofix_enabled(company: str | None) -> bool:
	"""Return True only when the Company is explicitly enabled for GoFix Token."""

	if not company:
		return False
	if not frappe.db.has_column("Company", "gofix_enabled"):
		return False
	return bool(frappe.db.get_value("Company", company, "gofix_enabled"))


def _resolve_store(identifier: str | None) -> dict | None:
	"""Resolve a store identifier to ``{warehouse, company, store_code, store_name}``.

	Accepts (in priority order): CH Store name, CH Store store_code,
	CH Store store_name, POS Profile name, Warehouse name. Returns ``None``
	if nothing matches OR the resolved company is not GoFix-enabled — so
	the caller can surface a single user-friendly "Store not configured for
	GoFix" message either way, without leaking whether the store exists on
	a non-GoFix company.
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
			as_dict=True)
		if row and row[0].get("warehouse") and _company_is_gofix_enabled(row[0].get("company")):
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
		as_dict=True)
	if profile and profile.get("warehouse") and _company_is_gofix_enabled(profile.get("company")):
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
		as_dict=True)
	if (
		wh
		and not wh.get("is_group")
		and not wh.get("disabled")
		and _company_is_gofix_enabled(wh.get("company"))
	):
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
	"""Atomic Redis fixed-window limiter for guest store operations.

	Skipped for authenticated users so FDE testing / smoke tests aren't
	throttled.
	"""

	if frappe.session.user and frappe.session.user != "Guest":
		return
	window_seconds = get_int_setting("guest_rate_window_seconds", 3600)
	request_limit = get_int_setting("guest_rate_limit", 30)
	request = getattr(frappe.local, "request", None)
	client_ip = getattr(request, "remote_addr", None) or "unknown"
	cache_key = f"gofix_token_rate::{client_ip}::{bucket}"
	redis_key = frappe.cache.make_key(cache_key)
	frappe.cache.set(redis_key, 0, nx=True, ex=window_seconds)
	hits = cint(frappe.cache.incrby(redis_key, 1))
	if frappe.cache.ttl(redis_key) < 0:
		frappe.cache.expire(redis_key, window_seconds)
	if hits > request_limit:
		frappe.throw(
			_("Too many requests from this store. Please slow down."),
			frappe.RateLimitExceededError,
			title=_("Rate Limit"))


# ---------------------------------------------------------------------------
# Guest: Tablet config
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=300, ip_based=True)
def get_tablet_config(store: str) -> dict:
	"""Return everything the customer tablet needs to render the wizard."""

	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))

	_rate_limit_guest(f"config::{resolved['warehouse']}")
	queue_limit = min(get_int_setting("token_queue_limit", 200), 2000)

	device_types = frappe.get_all(
		"GoFix Device Type",
		filters={"disabled": 0},
		fields=["device_type", "icon", "display_order"],
		order_by="display_order asc, device_type asc",
		limit_page_length=queue_limit)
	visit_reasons = frappe.get_all(
		"GoFix Visit Reason",
		filters={"disabled": 0},
		fields=["reason_name", "is_repair", "display_order"],
		order_by="display_order asc, reason_name asc",
		limit_page_length=get_int_setting("token_queue_limit", 200))
	brand_rows = frappe.get_all(
		"GoFix Brand Option",
		filters={"disabled": 0},
		fields=["device_type", "brand_name", "display_order"],
		order_by="device_type asc, display_order asc, brand_name asc",
		limit_page_length=get_int_setting("token_queue_limit", 200))
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
		limit_page_length=get_int_setting("token_queue_limit", 200))

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
			"max_issues": get_int_setting("max_selected_issues", 3),
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=300, methods=["POST"], ip_based=True)
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
	customer_language: str | None = None) -> dict:
	"""Create a GoFix Token from the customer tablet.

	Returns ``{token_number, name, queue_position, whatsapp_status}``. All
	validation errors bubble as ``frappe.ValidationError`` and are shown to
	the customer inline.
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
		as_dict=True)
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
			fields=["name", "symptom_name", "device_type", "is_expert_check", "is_other"])
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
	previous_capability = frappe.flags.get("gofix_guest_token_creation")
	frappe.flags.gofix_guest_token_creation = True
	try:
		doc.insert()
	finally:
		if previous_capability is None:
			frappe.flags.pop("gofix_guest_token_creation", None)
		else:
			frappe.flags.gofix_guest_token_creation = previous_capability

	# Whatsapp send is fire-and-forget; failure must not block the token.
	try:
		_enqueue_whatsapp_confirmation(doc.name)
	except Exception:
		frappe.log_error(
			title="gofix_token: whatsapp enqueue failed",
			message=frappe.get_traceback())

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
	"""Enqueue the customer-facing WhatsApp confirmation for a new token.

	The actual send lives in
	``gofix.gofix_services.whatsapp_notifications.send_token_confirmation``
	so the API layer stays thin. Runs on the short queue — the tablet flow
	never blocks on WhatsApp.
	"""

	if not token_name:
		return
	frappe.enqueue(
		"gofix.gofix_services.whatsapp_notifications.send_token_confirmation",
		queue="short",
		token_name=token_name,
		enqueue_after_commit=True)


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
		(warehouse, str(business_date), STATUS_WAITING, token_name))[0][0]
	return int(ahead or 0) + 1


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=300, ip_based=True)
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
		as_dict=True)
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
	if not has_role_setting("token_transition_roles"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _apply_authorized_token_scope(filters: dict[str, Any]) -> dict[str, Any]:
	"""Apply both company and store scope to token queries; empty scope matches none."""
	scope = get_user_service_scope()
	if scope is None:
		return filters
	companies = sorted(scope.get("companies") or ())
	warehouses = sorted(scope.get("warehouses") or ())
	if not companies or not warehouses:
		filters["name"] = ("in", ["__no_gofix_token_scope__"])
		return filters
	filters["company"] = ("in", companies)
	filters["store"] = ("in", warehouses)
	return filters


def _assert_resolved_store_scope(resolved: dict) -> None:
	scope = get_user_service_scope()
	if scope is None:
		return
	if (
		resolved.get("company") not in set(scope.get("companies") or ())
		or resolved.get("warehouse") not in set(scope.get("warehouses") or ())
	):
		frappe.throw(_("This store is outside your assigned GoFix scope."), frappe.PermissionError)


def _assert_token_scope(doc) -> None:
	scope = get_user_service_scope()
	if scope is None:
		return
	if (
		doc.get("company") not in set(scope.get("companies") or ())
		or doc.get("store") not in set(scope.get("warehouses") or ())
	):
		frappe.throw(_("This token is outside your assigned GoFix scope."), frappe.PermissionError)


def _assert_token_assignee(user: str, doc) -> None:
	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not row or not row.enabled or row.user_type != "System User":
		frappe.throw(_("The selected assignee is not an active System User."), frappe.PermissionError)
	if not is_privileged_user(user) and not has_role_setting(
		"token_transition_roles", user=user
	):
		frappe.throw(_("The selected user is not configured to operate GoFix tokens."), frappe.PermissionError)
	assignee_scope = get_user_service_scope(user)
	if assignee_scope is not None and (
		doc.company not in set(assignee_scope.get("companies") or ())
		or doc.store not in set(assignee_scope.get("warehouses") or ())
	):
		frappe.throw(_("The selected user is not assigned to this store."), frappe.PermissionError)


@frappe.whitelist()
def get_fde_stores() -> list[dict]:
	"""Return the GoFix-enabled stores this FDE can operate.

	Used by the token queue page to populate the store picker. Falls back to
	POS Profile / Warehouse names when a CH Store row is missing so the
	picker still works during rollout.
	"""

	_ensure_fde()
	scope = get_user_service_scope()
	if scope is not None and (
		not scope.get("companies") or not scope.get("warehouses")
	):
		return []
	companies = frappe.get_all(
		"Company",
		filters={
			"gofix_enabled": 1,
			**(
				{"name": ("in", sorted(scope["companies"]))}
				if scope is not None else {}
			),
		},
		pluck="name",
		limit_page_length=get_int_setting("token_queue_limit", 200))
	if not companies:
		return []

	seen: dict[str, dict] = {}

	# CH Store rows first — they carry the human-friendly store_name/store_code.
	if frappe.db.table_exists("CH Store"):
		rows = frappe.get_all(
			"CH Store",
			filters={"company": ("in", companies), "disabled": 0},
			fields=["name", "store_code", "store_name", "warehouse", "company"],
			order_by="store_name asc, store_code asc",
			limit_page_length=get_int_setting("token_queue_limit", 200))
		for r in rows:
			if not r.get("warehouse"):
				continue
			if scope is not None and r["warehouse"] not in scope["warehouses"]:
				continue
			seen[r["warehouse"]] = {
				"warehouse": r["warehouse"],
				"company": r["company"],
				"store_code": (r.get("store_code") or "").strip().upper(),
				"store_name": r.get("store_name") or r.get("name") or r["warehouse"],
			}

	# Fall back to warehouses only when CH Store has not been set up at all.
	# Once CH Store exists, it is the store master; showing every warehouse
	# here pollutes the FDE queue with Buyback/Damaged/Demo/WIP locations.
	if not seen:
		wh_rows = frappe.get_all(
			"Warehouse",
			filters={"company": ("in", companies), "is_group": 0, "disabled": 0},
			fields=["name", "warehouse_name", "company"],
			order_by="warehouse_name asc",
			limit_page_length=get_int_setting("token_queue_limit", 200))
		for w in wh_rows:
			if scope is not None and w["name"] not in scope["warehouses"]:
				continue
			code, name = resolve_store_code(w["name"])
			seen[w["name"]] = {
				"warehouse": w["name"],
				"company": w["company"],
				"store_code": code,
				"store_name": name or w["warehouse_name"] or w["name"],
			}

	return list(seen.values())


@frappe.whitelist()
def list_active_tokens(store: str | None = None, statuses: Any = None) -> list[dict]:
	"""Return active tokens for the FDE queue view."""

	_ensure_fde()
	if statuses:
		if isinstance(statuses, str):
			import json

			try:
				statuses = json.loads(statuses)
			except (ValueError, TypeError):
				statuses = [s.strip() for s in statuses.split(",") if s.strip()]
	else:
		statuses = list(ACTIVE_STATUSES)

	filters: dict[str, Any] = {
		"business_date": nowdate(),
		"status": ["in", statuses],
	}
	if store and store != "__all__":
		resolved = _resolve_store(store)
		if not resolved:
			frappe.throw(_("Store {0} is not configured for GoFix.").format(store))
		_assert_resolved_store_scope(resolved)
		filters["store"] = resolved["warehouse"]
		filters["company"] = resolved["company"]
	else:
		_apply_authorized_token_scope(filters)

	queue_limit = get_int_setting("token_queue_limit", 200)
	rows = frappe.get_all(
		"GoFix Token",
		filters=filters,
		fields=[
			"name",
			"token_number",
			"status",
			"company",
			"store",
			"store_code",
			"store_name",
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
		limit_page_length=queue_limit)

	issue_map: dict[str, list[str]] = {r["name"]: [] for r in rows}
	if issue_map:
		for issue in frappe.get_all(
			"GoFix Token Issue",
			filters={"parent": ("in", list(issue_map))},
				fields=["parent", "symptom_name"],
				order_by="parent asc, idx asc",
				limit_page_length=get_int_setting("token_queue_limit", 200) * get_int_setting("max_selected_issues", 3)):
			issue_map.setdefault(issue.parent, []).append(issue.symptom_name)
	now = now_datetime()
	for r in rows:
		r["waiting_seconds"] = int(time_diff_in_seconds(now, r["creation"]))
		r["symptoms"] = issue_map.get(r["name"], [])
	return rows


# ---------------------------------------------------------------------------
# FDE: Transition
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def transition_token(
	name: str,
	to_status: str,
	reason: str | None = None,
	notes: str | None = None,
	assigned_fde: str | None = None) -> dict:
	"""Move a token between statuses.

	Delegates all rule checks to :class:`GoFixToken` — this function just
	patches fields and saves so the validation matrix stays single-source.
	"""

	_ensure_fde()
	doc = _load_token(name, for_update=True)
	doc.status = to_status
	if to_status in {STATUS_CANCELLED, STATUS_LEFT}:
		if reason:
			doc.cancellation_reason = reason
		if notes is not None:
			doc.cancellation_notes = notes
	if assigned_fde:
		_assert_token_assignee(assigned_fde, doc)
		doc.assigned_fde = assigned_fde
	doc.save()
	return {
		"name": doc.name,
		"status": doc.status,
		"token_number": doc.token_number,
		"assigned_fde": doc.assigned_fde,
	}


# ---------------------------------------------------------------------------
# FDE: Link Service Request (Phase 6 will fill in the auto-linking hook)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def link_service_request(name: str, service_request: str) -> dict:
	"""Attach an existing Service Request to a token."""

	_ensure_fde()
	doc = _load_token(name, for_update=True)
	sr = assert_service_request_access(service_request, permission_type="write")
	if sr.company != doc.company:
		frappe.throw(_("Token and Service Request must belong to the same company."), frappe.PermissionError)
	sr_locations = {
		value for value in (
			sr.get("source_warehouse"), sr.get("current_location"),
			sr.get("current_processing_location"), sr.get("transferred_to_store")) if value
	}
	if doc.store not in sr_locations:
		frappe.throw(_("Token and Service Request must belong to the same store."), frappe.PermissionError)
	doc.service_request = service_request
	# Automatically transition to Job Card Created if still Attending — this
	# is the natural signal the FDE has handed off to the technician queue.
	if doc.status == STATUS_ATTENDING:
		doc.status = STATUS_JOB_CARD
	doc.save()
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
		limit_page_length=get_int_setting("token_queue_limit", 200))


# ---------------------------------------------------------------------------
# Management Dashboard endpoints (mirrors the ch_pos.api.token_api contract
# so the shared /queue-mgmt page can drive both systems by URL-toggling
# ``?system=gofix``)
# ---------------------------------------------------------------------------


# Map GoFix statuses onto the four buckets the shared dashboard understands.
# ``in_progress`` = anything actively being handled by an FDE.
_STATUS_BUCKET = {
	STATUS_WAITING:   "waiting",
	STATUS_CALLED:    "in_progress",
	STATUS_ATTENDING: "in_progress",
	STATUS_JOB_CARD:  "in_progress",
	STATUS_COMPLETED: "completed",
	STATUS_CANCELLED: "cancelled",
	STATUS_LEFT:      "dropped",
}


def _date_filter_bounds(date_filter: str) -> tuple[str | None, str | None]:
	"""Return ``(start, end)`` datetimes for the shared date-filter tokens."""

	today = frappe.utils.today()
	if date_filter == "today":
		return today + " 00:00:00", None
	if date_filter == "yesterday":
		yesterday = frappe.utils.add_days(today, -1)
		return yesterday + " 00:00:00", yesterday + " 23:59:59"
	if date_filter == "this_week":
		return frappe.utils.add_days(today, -6) + " 00:00:00", None
	return None, None


def _apply_scope(filters: dict, store: str | None) -> dict | None:
	"""Attach a ``store`` filter for a single-store view; ``None`` = all stores.

	Returns the resolved store dict (or ``None`` for all-stores) so the caller
	can echo store_code / store_name back to the UI.
	"""

	if not store:
		_apply_authorized_token_scope(filters)
		return None
	resolved = _resolve_store(store)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(store))
	_assert_resolved_store_scope(resolved)
	filters["store"] = resolved["warehouse"]
	filters["company"] = resolved["company"]
	return resolved


def _mgmt_status(row_status: str) -> str:
	"""Collapse the GoFix status set into the four UI labels the shared
	dashboard already renders badges for: ``Waiting`` / ``In Progress`` /
	``Completed`` / ``Cancelled`` / ``Dropped``.
	"""

	bucket = _STATUS_BUCKET.get(row_status, "waiting")
	return {
		"waiting":     "Waiting",
		"in_progress": "In Progress",
		"completed":   "Completed",
		"cancelled":   "Cancelled",
		"dropped":     "Dropped",
	}[bucket]


@frappe.whitelist()
def get_pos_profiles() -> list[dict]:
	"""GoFix mirror of ``ch_pos.api.token_api.get_pos_profiles``.

	Returns ``[{name, company, warehouse}]`` for the stores the caller can
	operate. ``name`` is the CH Store name (or the warehouse if the store
	row is missing) so URLs remain stable across the two backends.
	"""

	_ensure_fde()
	stores = get_fde_stores()  # already scoped to gofix_enabled companies
	profiles: list[dict] = []
	for s in stores:
		# Prefer the CH Store name for URL-round-tripping; fall back to warehouse.
		name = s.get("store_code") or s.get("warehouse")
		if frappe.db.table_exists("CH Store"):
			ch = frappe.db.get_value(
				"CH Store",
				{"warehouse": s["warehouse"]},
				"name")
			if ch:
				name = ch
		profiles.append(
			{
				"name": name,
				"company": s.get("company"),
				"warehouse": s.get("warehouse"),
			}
		)
	return profiles


@frappe.whitelist()
def get_dashboard_stats(pos_profile: str | None = None, date_filter: str = "today") -> dict:
	"""Aggregate KPI card metrics for the management dashboard.

	``pos_profile`` — the store identifier chosen in the sidebar (may be
	blank for the all-stores rollup). ``date_filter`` — today / yesterday /
	this_week / all.
	"""

	_ensure_fde()
	filters: dict[str, Any] = {}
	_apply_scope(filters, pos_profile)

	start, end = _date_filter_bounds(date_filter)
	if start and end:
		filters["creation"] = ["between", [start, end]]
	elif start:
		filters["creation"] = [">=", start]

	analytics_limit = get_int_setting("token_analytics_row_limit", 5000)
	rows = frappe.get_all(
		"GoFix Token",
		filters=filters,
		fields=["status", "creation", "completed_at", "store", "store_name"],
		limit_page_length=analytics_limit)

	total = len(rows)
	waiting = sum(1 for r in rows if _STATUS_BUCKET.get(r["status"]) == "waiting")
	in_progress = sum(1 for r in rows if _STATUS_BUCKET.get(r["status"]) == "in_progress")
	completed = sum(1 for r in rows if _STATUS_BUCKET.get(r["status"]) == "completed")
	cancelled = sum(1 for r in rows if _STATUS_BUCKET.get(r["status"]) == "cancelled")
	dropped = sum(1 for r in rows if _STATUS_BUCKET.get(r["status"]) == "dropped")

	serviceable = completed + waiting + in_progress
	completion_rate = round(completed / serviceable * 100) if serviceable else 0

	completed_rows = [r for r in rows if _STATUS_BUCKET.get(r["status"]) == "completed" and r.get("completed_at")]
	if completed_rows:
		total_mins = sum(
			int((get_datetime(r["completed_at"]) - get_datetime(r["creation"])).total_seconds() / 60)
			for r in completed_rows
		)
		avg_wait = round(total_mins / len(completed_rows))
	else:
		avg_wait = 0

	store_breakdown: dict[str, dict] = {}
	for r in rows:
		key = r.get("store") or "Unknown"
		label = r.get("store_name") or key
		bucket = store_breakdown.setdefault(
			key,
			{"store": label, "total": 0, "waiting": 0, "in_progress": 0, "completed": 0})
		bucket["total"] += 1
		b = _STATUS_BUCKET.get(r["status"])
		if b in bucket:
			bucket[b] += 1

	return {
		"total": total,
		"waiting": waiting,
		"in_progress": in_progress,
		"completed": completed,
		"cancelled": cancelled,
		"dropped": dropped,
		"avg_wait_minutes": avg_wait,
		"completion_rate": completion_rate,
		"store_breakdown": list(store_breakdown.values()),
		"is_truncated": len(rows) >= analytics_limit,
		"row_limit": analytics_limit,
	}


def _token_row_for_ui(r: dict, now, user_names: dict[str, str] | None = None) -> dict:
	"""Shape a GoFix Token row for the shared queue-mgmt table."""

	created = get_datetime(r["creation"])
	end_time = get_datetime(r["completed_at"]) if r.get("completed_at") else now
	wait_minutes = int((end_time - created).total_seconds() / 60)

	device_parts = [r.get("device_type") or "", r.get("device_brand") or "", r.get("device_model") or ""]
	device_label = " ".join(p for p in device_parts[1:] if p).strip()

	tech = r.get("assigned_fde")
	tech_name = (user_names or {}).get(tech) or (tech or None)

	return {
		"name": r["name"],
		"token_display": r.get("token_number") or r["name"],
		"creation": r["creation"],
		"status": _mgmt_status(r["status"]),
		"raw_status": r["status"],
		"customer_name": r.get("customer_name") or "",
		"customer_phone": r.get("customer_phone") or "",
		"device_type": r.get("device_type") or "",
		"device": device_label,
		"issue_category": r.get("visit_reason") or "",
		"technician": tech,
		"technician_name": tech_name,
		"wait_minutes": wait_minutes,
		"pos_profile": r.get("store"),
		"company": r.get("company"),
	}


@frappe.whitelist()
def get_queue(pos_profile: str | None = None, status: str | None = None, date_filter: str = "today") -> list[dict]:
	"""Return the queue rows for the management dashboard.

	``status`` accepts either the collapsed UI label (``Waiting`` /
	``In Progress`` / ``Completed`` / ``Cancelled`` / ``Dropped``) or the
	raw GoFix status; both are normalised.
	"""

	_ensure_fde()
	filters: dict[str, Any] = {}
	_apply_scope(filters, pos_profile)

	start, end = _date_filter_bounds(date_filter)
	if start and end:
		filters["creation"] = ["between", [start, end]]
	elif start:
		filters["creation"] = [">=", start]

	if status and status != "All":
		# Reverse-map collapsed labels to the raw statuses.
		reverse = {v: [] for v in {"Waiting", "In Progress", "Completed", "Cancelled", "Dropped"}}
		for raw, bucket in _STATUS_BUCKET.items():
			label = _mgmt_status(raw)
			reverse.setdefault(label, []).append(raw)
		if status in reverse:
			filters["status"] = ["in", reverse[status]]
		else:
			filters["status"] = status

	queue_limit = get_int_setting("token_queue_limit", 200)
	rows = frappe.get_all(
		"GoFix Token",
		filters=filters,
		fields=[
			"name", "token_number", "status", "creation", "completed_at",
			"customer_name", "customer_phone",
			"device_type", "device_brand", "device_model",
			"visit_reason", "assigned_fde", "store", "company",
		],
		order_by="creation desc",
		limit_page_length=queue_limit + 1)
	if len(rows) > queue_limit:
		frappe.throw(
			_("The token queue exceeds the configured limit of {0} rows. Narrow the filters.").format(
				queue_limit
			),
			frappe.ValidationError)

	tech_users = {r.get("assigned_fde") for r in rows if r.get("assigned_fde")}
	user_names = {
		row.name: row.full_name or row.name
		for row in frappe.get_all(
			"User",
			filters={"name": ("in", list(tech_users))},
			fields=["name", "full_name"])
	} if tech_users else {}
	now = now_datetime()
	return [_token_row_for_ui(r, now, user_names) for r in rows]


@frappe.whitelist()
def get_technician_tokens(technician: str | None = None) -> list[dict]:
	""""My Tokens" list — tokens assigned to the current FDE today."""

	_ensure_fde()
	user = technician or frappe.session.user
	if user != frappe.session.user and not has_role_setting(
		"service_manager_roles"
	):
		frappe.throw(_("You can only view your own assigned tokens."), frappe.PermissionError)
	today = frappe.utils.today()
	filters = {
		"assigned_fde": user,
		"creation": [">=", today + " 00:00:00"],
	}
	_apply_authorized_token_scope(filters)
	rows = frappe.get_all(
		"GoFix Token",
		filters=filters,
		fields=[
			"name", "token_number", "status", "creation", "completed_at",
			"customer_name", "customer_phone",
			"device_type", "device_brand", "device_model",
			"visit_reason", "assigned_fde", "store", "company",
		],
		order_by="creation desc",
		limit_page_length=get_int_setting("token_queue_limit", 200))
	now = now_datetime()
	user_names = {user: frappe.db.get_value("User", user, "full_name") or user}
	return [_token_row_for_ui(r, now, user_names) for r in rows]


@frappe.whitelist()
def get_store_users(pos_profile: str | None = None, role: str | None = None) -> list[dict]:
	"""FDE picker for the "Assign" dialog. Returns store-mapped users when
	available, else falls back to enabled System Users with any of the
	transition roles.
	"""

	_ensure_fde()
	users: list[dict] = []
	if not pos_profile:
		return users
	resolved = _resolve_store(pos_profile)
	if not resolved:
		frappe.throw(_("Store {0} is not configured for GoFix.").format(pos_profile))
	_assert_resolved_store_scope(resolved)
	if frappe.db.table_exists("CH Store"):
		store_name = frappe.db.get_value(
			"CH Store",
			{"warehouse": resolved["warehouse"], "disabled": 0},
			"name")
		if store_name:
			# CH Store User retired into CH User Scope (ch_erp15 patch v34).
			from ch_erp15.ch_erp15.scope import get_store_users

			rows = [
				frappe._dict({
					"user": _r.get("user"),
					"full_name": _r.get("full_name"),
					"role": _r.get("role_profile") or _r.get("scope_role"),
				})
				for _r in get_store_users(
					store_name, role=role, limit=get_int_setting("token_queue_limit", 200)
				)
			]
			missing_names = {row.user for row in rows if row.user and not row.full_name}
			full_names = {
				row.name: row.full_name or row.name
				for row in frappe.get_all(
					"User",
					filters={"name": ("in", tuple(missing_names))},
					fields=["name", "full_name"],
					limit_page_length=len(missing_names))
			} if missing_names else {}
			for row in rows:
				if not row.full_name:
					row.full_name = full_names.get(row.user) or row.user
			users = rows

	return users


def _load_token(token_name: str, *, for_update: bool = False):
	if not token_name or not frappe.db.exists("GoFix Token", token_name):
		frappe.throw(_("Token {0} not found.").format(token_name or ""))
	if for_update:
		frappe.db.get_value("GoFix Token", token_name, "name", for_update=True)
	doc = frappe.get_doc("GoFix Token", token_name)
	_assert_token_scope(doc)
	return doc


@frappe.whitelist(methods=["POST"])
def assign_token(token_name: str, technician: str) -> dict:
	"""Set ``assigned_fde``. Moves Waiting → Called when appropriate."""

	_ensure_fde()
	doc = _load_token(token_name, for_update=True)
	_assert_token_assignee(technician, doc)
	doc.assigned_fde = technician
	if doc.status == STATUS_WAITING:
		doc.status = STATUS_CALLED
	doc.save()
	return {"status": "ok", "token_status": doc.status}


@frappe.whitelist(methods=["POST"])
def start_token(token_name: str) -> dict:
	"""Manager-dashboard "Start" — moves Waiting/Called → Attending."""

	_ensure_fde()
	doc = _load_token(token_name, for_update=True)
	if doc.status in {STATUS_WAITING, STATUS_CALLED}:
		doc.status = STATUS_ATTENDING
		doc.save()
	return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def complete_token(token_name: str) -> dict:
	"""Manager-dashboard "Complete" — closes the token."""

	_ensure_fde()
	doc = _load_token(token_name, for_update=True)
	if doc.status in {STATUS_ATTENDING, STATUS_JOB_CARD}:
		doc.status = STATUS_COMPLETED
		doc.save()
	return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def cancel_token(
	token_name: str,
	drop_reason: str | None = None,
	drop_sub_reason: str | None = None,
	drop_remarks: str | None = None) -> dict:
	"""Manager-dashboard "Cancel". Uses ``drop_reason`` if supplied else
	first available Cancelled-scope reason (kept for API parity with the
	ch_pos endpoint which passes optional drop metadata).
	"""

	_ensure_fde()
	doc = _load_token(token_name, for_update=True)
	reason = drop_reason
	if not reason:
		fallback = frappe.db.get_value(
			"GoFix Cancellation Reason",
			{"scope": ["in", ["Cancelled", "Both"]], "disabled": 0},
			"name",
			order_by="display_order asc")
		reason = fallback
	if not reason:
		frappe.throw(_("No GoFix Cancellation Reason configured."))
	doc.cancellation_reason = reason
	if drop_remarks:
		doc.cancellation_notes = drop_remarks
	doc.status = STATUS_CANCELLED
	doc.save()
	return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def drop_token(
	token_name: str,
	drop_reason: str | None = None,
	drop_sub_reason: str | None = None,
	drop_remarks: str | None = None) -> dict:
	"""Manager-dashboard "Drop" — customer walked away without service."""

	_ensure_fde()
	doc = _load_token(token_name, for_update=True)
	reason = drop_reason
	if not reason:
		fallback = frappe.db.get_value(
			"GoFix Cancellation Reason",
			{"scope": ["in", ["Customer Left", "Both"]], "disabled": 0},
			"name",
			order_by="display_order asc")
		reason = fallback
	if not reason:
		frappe.throw(_("No GoFix Cancellation Reason configured."))
	doc.cancellation_reason = reason
	if drop_remarks:
		doc.cancellation_notes = drop_remarks
	doc.status = STATUS_LEFT
	doc.save()
	return {"status": "ok"}


@frappe.whitelist()
def get_reports(pos_profile: str | None = None, days: int = 7) -> dict:
	"""Daily-breakdown and FDE-performance data for the Reports tab."""

	_ensure_fde()
	days = min(max(1, cint(days) or 7), get_int_setting("token_report_max_days", 90))
	today = frappe.utils.today()
	start_date = frappe.utils.add_days(today, -(days - 1))
	filters: dict[str, Any] = {"creation": [">=", start_date + " 00:00:00"]}
	_apply_scope(filters, pos_profile)

	analytics_limit = get_int_setting("token_analytics_row_limit", 5000)
	raw_rows = frappe.get_all(
		"GoFix Token",
		filters=filters,
		fields=["name", "status", "creation", "completed_at", "assigned_fde"],
		limit_page_length=analytics_limit + 1)
	is_truncated = len(raw_rows) > analytics_limit
	rows = raw_rows[:analytics_limit]

	daily_map: dict[str, dict] = {}
	for r in rows:
		day = str(get_datetime(r["creation"]).date())
		bucket = daily_map.setdefault(
			day,
			{"date": day, "created": 0, "completed": 0, "cancelled": 0, "wait_sum": 0, "wait_count": 0})
		bucket["created"] += 1
		mgmt = _STATUS_BUCKET.get(r["status"])
		if mgmt == "completed":
			bucket["completed"] += 1
			if r.get("completed_at"):
				mins = int((get_datetime(r["completed_at"]) - get_datetime(r["creation"])).total_seconds() / 60)
				bucket["wait_sum"] += mins
				bucket["wait_count"] += 1
		elif mgmt in {"cancelled", "dropped"}:
			bucket["cancelled"] += 1

	daily_breakdown = []
	for day, data in sorted(daily_map.items(), reverse=True):
		avg = round(data["wait_sum"] / data["wait_count"]) if data["wait_count"] else 0
		daily_breakdown.append(
			{
				"date": data["date"],
				"created": data["created"],
				"completed": data["completed"],
				"cancelled": data["cancelled"],
				"avg_wait": avg,
			}
		)

	tech_users = {r.get("assigned_fde") for r in rows if r.get("assigned_fde")}
	user_names = {
		row.name: row.full_name or row.name
		for row in frappe.get_all(
			"User",
			filters={"name": ("in", list(tech_users))},
			fields=["name", "full_name"])
	} if tech_users else {}
	tech_map: dict[str, dict] = {}
	for r in rows:
		tech = r.get("assigned_fde")
		if not tech:
			continue
		bucket = tech_map.setdefault(
			tech,
			{
				"technician": tech,
					"name": user_names.get(tech) or tech,
				"total": 0, "completed": 0, "time_sum": 0, "time_count": 0,
			})
		bucket["total"] += 1
		if _STATUS_BUCKET.get(r["status"]) == "completed":
			bucket["completed"] += 1
			if r.get("completed_at"):
				mins = int((get_datetime(r["completed_at"]) - get_datetime(r["creation"])).total_seconds() / 60)
				bucket["time_sum"] += mins
				bucket["time_count"] += 1

	tech_performance = []
	for data in sorted(tech_map.values(), key=lambda x: x["completed"], reverse=True):
		avg = round(data["time_sum"] / data["time_count"]) if data["time_count"] else 0
		tech_performance.append(
			{
				"technician": data["technician"],
				"name": data["name"],
				"total": data["total"],
				"completed": data["completed"],
				"avg_time": avg,
			}
		)

	return {
		"daily_breakdown": daily_breakdown,
		"tech_performance": tech_performance,
		"is_truncated": is_truncated,
		"row_limit": analytics_limit,
	}
