# Copyright (c) 2026, GoFix and contributors
# Repair Orchestration Engine
#
# Centralizes business rules that were previously scattered across controllers:
#   - Estimate versioning & revision approval loop
#   - Auto device transfer when repair location changes
#   - Spare request destination reroute
#   - Billing readiness control (source-store billing)
#   - Repair pause/resume
#   - Repairability decision
#   - QC issue-level rework loop

import json

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, nowdate, today


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ESTIMATE VERSIONING
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def create_estimate_version(service_request, reason=None, send_to_customer=False) -> dict:
	"""Snapshot current issues/solutions/spares into a new Estimate Version.

	This is called:
	  - After initial diagnosis + solution assignment (version 1)
	  - When a new issue is discovered during repair or QC (version 2+)

	If send_to_customer=True, auto-sends WhatsApp for approval.
	Pauses repair until approved.
	"""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	# Calculate costs using pricing engine if available
	labor_cost, spare_cost, estimate_total = _calculate_estimate(sr)

	# Determine version number
	existing_versions = sr.get("estimate_versions") or []
	new_version = len(existing_versions) + 1

	# Supersede previous pending/sent versions
	for ev in existing_versions:
		if ev.status in ("Pending", "Sent to Customer"):
			ev.status = "Superseded"

	# Snapshot of issues + solutions for audit
	snapshot = {
		"issues": [
			{"issue_category": r.issue_category, "description": r.description, "status": r.status}
			for r in (sr.get("issue_lines") or [])
			if r.status not in ("Deleted", "Cancelled")
		],
		"solutions": [
			{"repair_solution": r.repair_solution, "issue_category": r.issue_category,
			 "estimated_minutes": r.estimated_minutes, "status": r.status}
			for r in (sr.get("solution_lines") or [])
			if r.status not in ("Cancelled",)
		],
	}

	sr.append("estimate_versions", {
		"version_number": new_version,
		"estimate_amount": estimate_total,
		"labor_cost": labor_cost,
		"spare_cost": spare_cost,
		"status": "Pending",
		"created_by": frappe.session.user,
		"created_at": now_datetime(),
		"reason_for_revision": reason or ("Initial estimate" if new_version == 1 else "Revised estimate"),
		"issues_snapshot": json.dumps(snapshot),
	})

	sr.set("latest_estimate_version", new_version)
	sr.set("estimated_cost", estimate_total)
	sr.set("estimate_approval_pending", 1)
	sr.set("repair_paused", 1)
	sr.set("repair_pause_reason", "Awaiting Estimate Approval")
	sr.save()

	if send_to_customer:
		_send_estimate_to_customer(sr, new_version)

	return {
		"version": new_version,
		"estimate_amount": estimate_total,
		"labor_cost": labor_cost,
		"spare_cost": spare_cost,
	}


@frappe.whitelist()
def send_estimate_for_approval(service_request, version_number=None) -> dict:
	"""Send the latest (or specified) estimate version to customer via WhatsApp."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)

	version_number = version_number or sr.get("latest_estimate_version")
	_send_estimate_to_customer(sr, version_number)

	return {"message": _("Estimate sent to customer for approval")}


@frappe.whitelist()
def customer_approve_estimate(service_request, version_number=None, remarks=None) -> dict:
	"""Customer approves the estimate — resume repair."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	version_number = int(version_number or sr.get("latest_estimate_version") or 0)

	for ev in (sr.get("estimate_versions") or []):
		if ev.version_number == version_number and ev.status in ("Pending", "Sent to Customer"):
			ev.status = "Customer Approved"
			ev.approved_by = frappe.session.user
			ev.approved_at = now_datetime()
			ev.customer_remarks = remarks
			break
	else:
		frappe.throw(_("Estimate version {0} not found or already processed").format(version_number), title=_("Validation Error"))

	sr.set("estimate_approval_pending", 0)

	# Resume repair if no other blockers
	if not _has_other_blockers(sr):
		sr.set("repair_paused", 0)
		sr.set("repair_pause_reason", "")

	# If this is version 1 and no SO exists, create Service Order now
	if not sr.service_order and sr.get("repairability_status") == "Repairable":
		sr.save()
		sr.create_service_order()
	else:
		sr.save()

	return {"message": _("Estimate approved. Repair may proceed.")}


@frappe.whitelist()
def customer_reject_estimate(service_request, version_number=None, remarks=None) -> dict:
	"""Customer rejects the estimate."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	version_number = int(version_number or sr.get("latest_estimate_version") or 0)

	for ev in (sr.get("estimate_versions") or []):
		if ev.version_number == version_number and ev.status in ("Pending", "Sent to Customer"):
			ev.status = "Customer Rejected"
			ev.approved_by = frappe.session.user
			ev.approved_at = now_datetime()
			ev.customer_remarks = remarks
			break

	sr.set("estimate_approval_pending", 0)
	sr.save()

	return {"message": _("Estimate rejected by customer.")}


def _calculate_estimate(sr):
	"""Calculate estimate from pricing rules or fallback to manual values."""
	try:
		from gofix.gofix_services.doctype.gofix_pricing_rule.gofix_pricing_rule import (
			calculate_estimate_from_rules,
		)
		solutions = [
			{"repair_solution": r.repair_solution, "issue_category": r.issue_category}
			for r in (sr.get("solution_lines") or [])
			if r.status not in ("Cancelled", "Skipped")
		]
		if solutions:
			result = calculate_estimate_from_rules(
				issue_categories=[r.issue_category for r in (sr.get("issue_lines") or [])],
				solutions=solutions,
				brand=sr.brand,
				warranty_status=sr.warranty_status,
				company=sr.company,
				warranty_plan=sr.get("warranty_plan"),
			)
			if result.get("estimate_total"):
				return (
					flt(result["labor_total"]),
					flt(result["spare_total"]),
					flt(result["estimate_total"]),
				)
	except Exception:
		pass

	# Fallback: sum from solution lines + spare lines
	labor = sum(
		flt(r.estimated_minutes or 0) * 5  # ₹5/min default
		for r in (sr.get("solution_lines") or [])
		if r.status not in ("Cancelled", "Skipped")
	)
	spare = sum(
		flt(r.amount or 0)
		for r in (sr.get("spare_lines") or [])
		if r.status not in ("Returned", "Damaged")
	)
	return labor, spare, labor + spare


def _send_estimate_to_customer(sr, version_number):
	"""Send estimate via WhatsApp/SMS to customer."""
	sr.flags.ignore_validate_update_after_submit = True

	for ev in (sr.get("estimate_versions") or []):
		if ev.version_number == int(version_number) and ev.status == "Pending":
			ev.status = "Sent to Customer"

	sr.save()

	# Try WhatsApp first
	try:
		from gofix.gofix_services.whatsapp_notifications import send_whatsapp_template
		estimate_amt = 0
		for ev in sr.get("estimate_versions") or []:
			if ev.version_number == int(version_number):
				estimate_amt = ev.estimate_amount
				break

		template_name = "gofix_estimate_approval" if int(version_number) == 1 else "gofix_revised_estimate"
		send_whatsapp_template(
			template_name=template_name,
			to_number=sr.contact_number,
			params={
				"customer_name": sr.customer_name,
				"sr_name": sr.name,
				"device_name": sr.device_item_name,
				"estimate_amount": frappe.format_value(estimate_amt, {"fieldtype": "Currency"}),
				"version": str(version_number),
			},
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Estimate WhatsApp failed for {sr.name}")


def _has_other_blockers(sr):
	"""Check if repair has other blockers besides estimate approval."""
	# Awaiting device transfer
	transfer_status = sr.get("transfer_status") or ""
	if transfer_status in ("In Transit", "Not Transferred"):
		return True

	# Awaiting spare parts (any pending/reserved spares)
	pending_spares = frappe.db.count("Spare Parts Usage", {
		"service_request": sr.name,
		"part_status": ["in", ["Reserved"]],
		"deleted": 0,
	})
	if pending_spares:
		return True

	return False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REPAIRABILITY DECISION
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def set_repairability(service_request, status, reason=None) -> dict:
	"""Formal repairability decision by technician/manager.

	status: Repairable | Not Repairable | BER | Customer Declined
	"""
	valid = ("Repairable", "Not Repairable", "BER", "Customer Declined")
	if status not in valid:
		frappe.throw(_("Invalid repairability status. Must be one of: {0}").format(", ".join(valid)), title=_("Validation Error"))

	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	sr.set("repairability_status", status)
	sr.set("repairability_reason", reason)
	sr.set("repairability_decided_by", frappe.session.user)
	sr.set("repairability_decided_at", now_datetime())

	if status == "Repairable":
		# If estimate not yet created, auto-create version 1
		if not sr.get("latest_estimate_version"):
			sr.save()
			create_estimate_version(service_request, reason="Initial estimate after diagnosis")
			return {"message": _("Marked as Repairable. Initial estimate created.")}
	elif status in ("Not Repairable", "BER"):
		sr.set("decision", "Rejected")
		sr.set("status", "Rejected")
		sr.set("rejection_reason", reason or f"Device is {status}")

		# Check if there are consumed spares that need recovery
		pending = frappe.get_all("Spare Parts Usage", filters={
			"service_request": service_request,
			"part_status": "Consumed",
			"deleted": 0,
			"status": "Active",
		}, fields=["name", "spare_part_item", "item_name", "qty_used"])
		if pending:
			items_str = ", ".join(f"{p.item_name or p.spare_part_item} (x{p.qty_used})" for p in pending)
			frappe.msgprint(
				_("<b>⚠ {0} consumed spare(s) need recovery before device return:</b><br>{1}<br><br>"
				  "Use the Spare Recovery panel to disposition each part as:<br>"
				  "• Good → Back to Stock<br>"
				  "• Faulty → Supplier Return<br>"
				  "• Damaged by Technician → Damaged Stock").format(len(pending), items_str),
				title=_("Spare Recovery Required"),
				indicator="orange",
			)
	elif status == "Customer Declined":
		sr.set("decision", "Cancelled")
		sr.set("status", "Cancelled")

	sr.save()
	return {"message": _("Repairability set to {0}").format(status)}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LOCATION CHANGE ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def reassign_repair_location(service_request, new_location, location_type=None, reason=None) -> dict:
	"""Move repair from current location to a new one.

	Orchestrates:
	  1) Auto-create device transfer
	  2) Reroute pending spare requests
	  3) Update technician pool
	  4) Pause repair until device received
	"""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	old_location = sr.get("current_processing_location") or sr.source_warehouse
	if new_location == old_location:
		frappe.throw(_("New location is same as current location"), title=_("Validation Error"))

	# 1. Auto-create device transfer
	transfer_name = _auto_create_device_transfer(sr, old_location, new_location, reason)

	# 2. Reroute pending spare requests
	rerouted = _reroute_pending_spare_requests(sr.name, old_location, new_location)

	# 3. Update SR fields
	sr.set("current_processing_location", new_location)
	sr.set("repair_location_type", location_type or "")
	sr.set("last_transfer_reference", transfer_name)
	sr.set("transfer_status", "In Transit")
	sr.set("transferred_to_store", new_location)
	sr.set("transfer_date", nowdate())
	sr.set("transfer_reason", reason)

	# 4. Pause repair until device received
	sr.set("repair_paused", 1)
	sr.set("repair_pause_reason", "Awaiting Device Transfer")
	sr.save()

	# 5. Cancel existing job assignments at old location
	_cancel_location_assignments(sr.name, old_location)

	return {
		"message": _("Repair location changed to {0}").format(new_location),
		"transfer": transfer_name,
		"spares_rerouted": rerouted,
	}


@frappe.whitelist()
def receive_device_at_repair_location(service_request) -> dict:
	"""Mark device as received at the new repair location. Resumes repair."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	sr.set("transfer_status", "Received at Service Center")
	sr.set("transfer_received_date", nowdate())
	sr.set("current_location", sr.get("current_processing_location"))

	# Resume repair if no other blockers
	if not sr.get("estimate_approval_pending"):
		sr.set("repair_paused", 0)
		sr.set("repair_pause_reason", "")

	sr.save()
	return {"message": _("Device received. Repair may proceed.")}


@frappe.whitelist()
def return_device_to_source(service_request, reason=None) -> dict:
	"""Return device to source store (for invoicing). Auto-transfer."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	current = sr.get("current_processing_location") or sr.get("current_location")
	source = sr.source_warehouse

	if current == source:
		frappe.throw(_("Device is already at the source store"), title=_("Validation Error"))

	transfer_name = _auto_create_device_transfer(
		sr, current, source,
		reason or "Return to source store for billing",
	)

	sr.set("transfer_status", "Return In Transit")
	sr.set("last_transfer_reference", transfer_name)
	sr.set("transfer_return_date", nowdate())
	sr.save()

	return {"message": _("Return transfer created: {0}").format(transfer_name)}


@frappe.whitelist()
def confirm_return_at_source(service_request) -> dict:
	"""Confirm device returned to source store. Ready for billing."""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	sr.set("transfer_status", "Returned to Store")
	sr.set("current_location", sr.source_warehouse)
	sr.set("current_processing_location", sr.source_warehouse)
	sr.save()

	return {"message": _("Device returned to source store. Ready for billing.")}


def _auto_create_device_transfer(sr, from_location, to_location, reason=None):
	"""Create a Stock Entry (Material Transfer) to move the device."""
	# Only create stock transfer if there's a serialized device
	serial_no = sr.get("serial_no")
	device_item = sr.device_item

	if not device_item:
		return None

	try:
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Transfer"
		se.company = sr.company
		se.posting_date = nowdate()
		se.remarks = (
			f"GoFix device transfer for {sr.name}: "
			f"{from_location} → {to_location}. {reason or ''}"
		)
		se.append("items", {
			"item_code": device_item,
			"qty": 1,
			"s_warehouse": from_location,
			"t_warehouse": to_location,
			"serial_no": serial_no or "",
		})
		se.insert(ignore_permissions=True)
		se.submit()
		return se.name
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"GoFix auto-transfer failed: {sr.name} {from_location}→{to_location}",
		)
		return None


def _cancel_location_assignments(sr_name, old_location):
	"""Cancel open job assignments at the old location."""
	assignments = frappe.get_all("Job Assignment", filters={
		"service_request": sr_name,
		"assignment_status": ["in", ["Open", "In Progress"]],
	}, pluck="name")

	for ja_name in assignments:
		ja = frappe.get_doc("Job Assignment", ja_name)
		ja.assignment_status = "Cancelled"
		ja.technician_remarks = (ja.technician_remarks or "") + f"\nAuto-cancelled: device moved from {old_location}"
		ja.save(ignore_permissions=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SPARE REQUEST REROUTE
# ═══════════════════════════════════════════════════════════════════════════════

def _reroute_pending_spare_requests(sr_name, old_location, new_location):
	"""Update destination of all pending/reserved spare requests when ticket moves.

	Business rules:
	  - Only reroute if status is Reserved or pending (not Issued/Consumed)
	  - Update the warehouse on the Spare Parts Usage so stock goes to right place
	  - Log the reroute for audit
	"""
	pending = frappe.get_all("Spare Parts Usage", filters={
		"service_request": sr_name,
		"part_status": ["in", ["Reserved"]],
		"deleted": 0,
	}, fields=["name", "warehouse", "spare_part_item", "qty_used"])

	rerouted = 0
	for spu in pending:
		old_wh = spu.warehouse or old_location
		if old_wh == new_location:
			continue

		frappe.db.set_value("Spare Parts Usage", spu.name, {
			"warehouse": new_location,
		}, update_modified=True)

		# Log the reroute
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Spare Parts Usage",
			"reference_name": spu.name,
			"content": (
				f"Spare request rerouted: {spu.spare_part_item} x{spu.qty_used} "
				f"from {old_wh} → {new_location} "
				f"(ticket repair location changed)"
			),
		}).insert(ignore_permissions=True)

		rerouted += 1

	return rerouted


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BILLING READINESS CHECK
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def check_billing_readiness(service_request) -> dict:
	"""Validate all gates before allowing invoice creation.

	Gates:
	  1. QC Passed
	  2. Device at source store (or same store repair)
	  3. Latest estimate approved
	  4. All chargeable items finalized
	  5. No pending approvals
	"""
	sr = frappe.get_doc("Service Request", service_request)
	blockers = []

	# Gate 1: QC Pass
	qc_status = ""
	if sr.service_order:
		qc_status = frappe.db.get_value("Sales Order", sr.service_order, "qc_status") or ""
	if qc_status != "Pass":
		blockers.append(_("QC not passed (current: {0})").format(qc_status or "Pending"))

	# Gate 2: Device at source store
	current_loc = sr.get("current_processing_location") or sr.get("current_location")
	source = sr.source_warehouse
	billing_loc = sr.get("billing_location") or source
	if current_loc and source and current_loc != source:
		transfer_status = sr.get("transfer_status") or ""
		if transfer_status != "Returned to Store":
			blockers.append(
				_("Device not at billing location ({0}). Current: {1}. Transfer status: {2}").format(
					billing_loc, current_loc, transfer_status or "N/A"
				)
			)

	# Gate 3: Latest estimate approved
	if sr.get("estimate_approval_pending"):
		blockers.append(_("Latest estimate version not yet approved by customer"))

	# Gate 4: No pending spare approvals
	pending_approvals = frappe.db.count("Spare Parts Usage", {
		"service_request": sr.name,
		"requires_approval": 1,
		"approval_status": "Pending",
		"deleted": 0,
	})
	if pending_approvals:
		blockers.append(_("{0} spare part(s) pending manager approval").format(pending_approvals))

	return {
		"ready": len(blockers) == 0,
		"blockers": blockers,
		"billing_location": billing_loc,
	}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. QC ISSUE-LEVEL REWORK
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def qc_fail_with_issues(service_request, failed_checks_json, new_issues_json=None) -> dict:
	"""Process a QC failure with issue-level granularity.

	failed_checks_json: list of {check_name, fail_reason, linked_issue_category, linked_solution}
	new_issues_json: list of {issue_category, description} — issues discovered during QC
	"""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)
	sr.flags.ignore_validate_update_after_submit = True

	failed_checks = json.loads(failed_checks_json) if isinstance(failed_checks_json, str) else failed_checks_json
	new_issues = json.loads(new_issues_json) if new_issues_json else []

	# 1. Update QC checklist on Service Order with fail details
	if sr.service_order:
		so = frappe.get_doc("Sales Order", sr.service_order)
		for check in (so.get("qc_checklist") or []):
			for fc in failed_checks:
				if check.check_name == fc.get("check_name"):
					check.result = "Fail"
					check.fail_reason = fc.get("fail_reason", "")
					check.rework_required = 1
					check.rework_iteration = (check.rework_iteration or 0) + 1
					if fc.get("linked_issue_category"):
						check.linked_issue_category = fc["linked_issue_category"]
					if fc.get("linked_solution"):
						check.linked_solution = fc["linked_solution"]

		so.qc_status = "Fail"
		so.flags.ignore_validate_update_after_submit = True
		so.save(ignore_permissions=True)

	# 2. Mark failed solution lines back to "In Progress"
	failed_solutions = set()
	for fc in failed_checks:
		if fc.get("linked_solution"):
			failed_solutions.add(fc["linked_solution"])

	for sol in (sr.get("solution_lines") or []):
		if sol.repair_solution in failed_solutions:
			sol.status = "In Progress"
			sol.technician_remarks = (sol.technician_remarks or "") + f"\nRework required after QC fail"

	# 3. Add new issues discovered during QC
	has_new_chargeable = False
	for new_issue in new_issues:
		sr.append("issue_lines", {
			"issue_category": new_issue.get("issue_category"),
			"reported_by": "Technician",
			"description": new_issue.get("description", ""),
			"status": "Open",
		})
		has_new_chargeable = True

	sr.save()

	# 4. If new chargeable issues found, trigger revised estimate
	result = {"message": _("QC failed. Rework required for {0} item(s).").format(len(failed_checks))}

	if has_new_chargeable:
		estimate_result = create_estimate_version(
			service_request,
			reason="New issues discovered during QC",
			send_to_customer=True,
		)
		result["revised_estimate"] = estimate_result
		result["message"] += _(" New issues found — revised estimate sent to customer.")

	return result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. FLOW ENFORCEMENT: diagnosis before SO creation
# ═══════════════════════════════════════════════════════════════════════════════

def validate_so_creation_prerequisites(sr):
	"""Called before creating a Service Order.
	Enforces: diagnosis → repairability → estimate → approval → SO.
	"""
	if not sr.get("analysis_confirmed"):
		frappe.throw(_(
			"Cannot create Service Order: Issue analysis has not been confirmed. "
			"Complete diagnosis in the Ops Hub first."
		))

	repairability = sr.get("repairability_status")
	if repairability != "Repairable":
		frappe.throw(_(
			"Cannot create Service Order: Device repairability not confirmed. "
			"Current status: {0}"
		).format(repairability or "Pending Analysis"))

	# Check latest estimate is approved
	if sr.get("estimate_approval_pending"):
		frappe.throw(_(
			"Cannot create Service Order: Latest estimate is pending customer approval."
		))

	latest_version = sr.get("latest_estimate_version") or 0
	if latest_version > 0:
		# Check the latest version is approved
		approved = False
		for ev in (sr.get("estimate_versions") or []):
			if ev.version_number == latest_version and ev.status == "Customer Approved":
				approved = True
				break
		if not approved:
			frappe.throw(_(
				"Cannot create Service Order: Estimate version {0} is not approved by customer."
			).format(latest_version))


# ═══════════════════════════════════════════════════════════════════════════════
# 8. SPARE RECOVERY ON NOT REPAIRABLE / BER
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_spare_recovery_status(service_request) -> dict:
	"""Check if a Service Request has consumed spares that need recovery.

	Returns:
	  - needs_recovery: bool
	  - pending_spares: list of consumed spare details
	  - all_recovered: bool (True when every consumed spare has been dispositioned)
	"""
	frappe.has_permission("Service Request", doc=service_request, throw=True)

	pending = frappe.get_all("Spare Parts Usage",
		filters={
			"service_request": service_request,
			"part_status": "Consumed",
			"deleted": 0,
			"status": "Active",
		},
		fields=["name", "spare_part_item", "item_name", "qty_used", "uom",
				"barcode_value", "purchase_cost", "sales_price"],
	)

	# Count total SPUs ever created for this SR (including recovered)
	total_consumed = frappe.db.count("Spare Parts Usage", {
		"service_request": service_request,
	})

	total_recovered = frappe.db.count("Spare Parts Usage", {
		"service_request": service_request,
		"recovery_disposition": ["is", "set"],
	})

	return {
		"needs_recovery": len(pending) > 0,
		"pending_spares": pending,
		"total_consumed": total_consumed,
		"total_recovered": total_recovered,
		"all_recovered": len(pending) == 0 and total_consumed > 0,
	}


@frappe.whitelist()
def validate_spare_recovery_before_return(service_request) -> dict:
	"""Pre-return gate: blocks device handback until all consumed spares are accounted for.

	Business rule: Before returning a Not Repairable / BER device to the customer,
	every spare that was consumed must have a recovery disposition:
	  - Good → back to stock
	  - Faulty → supplier return
	  - Damaged by Technician → damaged stock

	Returns:
	  - ready: bool
	  - blockers: list of messages
	"""
	frappe.has_permission("Service Request", doc=service_request, throw=True)
	sr = frappe.get_doc("Service Request", service_request)
	blockers = []

	# Only enforce for Not Repairable / BER / Customer Cancelled outcomes
	repair_outcome = ""
	if sr.service_order:
		repair_outcome = frappe.db.get_value("Sales Order", sr.service_order, "repair_outcome") or ""

	if repair_outcome not in ("Not Repairable", "Beyond Repair", "Customer Cancelled"):
		return {"ready": True, "blockers": []}

	# Check for consumed spares without recovery disposition
	pending = frappe.get_all("Spare Parts Usage",
		filters={
			"service_request": service_request,
			"part_status": "Consumed",
			"deleted": 0,
			"status": "Active",
		},
		fields=["name", "spare_part_item", "item_name", "qty_used"],
	)

	if pending:
		items_str = ", ".join(f"{p.item_name or p.spare_part_item} (x{p.qty_used})" for p in pending)
		blockers.append(
			_("{0} consumed spare(s) must be recovered before returning device: {1}").format(
				len(pending), items_str
			)
		)

	return {
		"ready": len(blockers) == 0,
		"blockers": blockers,
	}
