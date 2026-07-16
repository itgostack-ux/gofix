"""
Store Executive Queue — Backend API

Request Inbox for store executives showing all service requests at their store
with clear stage indicators and actions.

Stages:
  - New Request (Draft, not yet analyzed)
  - Awaiting Analysis (Accepted, no diagnosis yet)
  - Analysis Done (Diagnosis complete, awaiting estimate)
  - Estimate Pending (Estimate created, awaiting customer approval)
  - Awaiting Customer Approval (Sent to customer)
  - Approved for Repair (Customer approved)
  - In Repair (Active repair)
  - QC (Quality check)
  - Ready for Invoice (QC passed, device at source)
  - Ready for Delivery (Invoiced)
  - Not Repairable / Closed
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from gofix.gofix_services.store_context import active_company, build_store_context

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False


@frappe.whitelist()
def get_store_context(company=None) -> dict:
	"""Return store context — warehouses, user info, summary counts."""
	return build_store_context(company=company)


@frappe.whitelist()
def get_queue(warehouse=None, search=None, date_from=None, date_to=None, stage_filter="all", company=None) -> dict:
	"""Return store queue — all SRs at this store with stage classification.

	Unlike Ops Hub (which requires a Service Order), this shows ALL requests
	including new intakes that haven't been accepted yet.
	"""
	frappe.has_permission("Service Request", "read", throw=True)
	company = active_company(company)

	if not date_from:
		date_from = add_days(nowdate(), -30)
	if not date_to:
		date_to = nowdate()

	filters = [
		["service_date", ">=", date_from],
		["service_date", "<=", date_to],
		["status", "not in", ["Cancelled"]],
	]

	if company:
		filters.append(["company", "=", company])
	if warehouse:
		filters.append(["source_warehouse", "=", warehouse])

	if search:
		filters.append([
			"name", "like", f"%{search}%",
		])

	# Check which custom fields exist
	extra_fields = []
	for cf in ("analysis_confirmed", "customer_confirmed", "repairability_status",
	           "estimate_approval_pending", "repair_paused", "repair_pause_reason",
	           "current_processing_location", "latest_estimate_version"):
		if frappe.db.has_column("Service Request", cf):
			extra_fields.append(cf)

	sr_list = frappe.get_list(
		"Service Request",
		filters=filters,
		fields=[
			"name", "customer", "customer_name", "contact_number",
			"device_item", "device_item_name", "serial_no", "brand",
			"issue_category", "issue_description",
			"decision", "status", "priority",
			"service_date", "expected_completion_date",
			"source_warehouse", "current_location",
			"service_order", "estimated_cost",
			"warranty_status", "warranty_plan_name",
			"service_invoice", "walkin_status",
		] + extra_fields,
		order_by="priority desc, service_date asc",
		limit=200,
	)

	# Batch: Get estimate version counts
	sr_names = [r["name"] for r in sr_list]
	if not sr_names:
		return {"requests": [], "summary": _empty_summary()}

	# Classify each SR into a queue stage
	for sr in sr_list:
		sr["queue_stage"] = _classify_queue_stage(sr)
		sr["queue_stage_label"] = QUEUE_STAGES.get(sr["queue_stage"], sr["queue_stage"])
		sr["days_open"] = (getdate(nowdate()) - getdate(sr["service_date"])).days if sr.get("service_date") else 0

	# Apply stage filter
	if stage_filter and stage_filter != "all":
		sr_list = [sr for sr in sr_list if sr["queue_stage"] == stage_filter]

	# Summary counts
	summary = _build_summary(sr_list)

	return {"requests": sr_list, "summary": summary}


QUEUE_STAGES = {
	"new_request": "New Request",
	"awaiting_analysis": "Awaiting Analysis",
	"analysis_done": "Analysis Done",
	"estimate_pending": "Estimate Pending",
	"awaiting_approval": "Awaiting Customer Approval",
	"approved": "Approved for Repair",
	"in_repair": "In Repair",
	"transferred": "Transferred Out",
	"qc": "Quality Check",
	"ready_invoice": "Ready for Invoice",
	"ready_delivery": "Ready for Delivery",
	"delivered": "Delivered",
	"not_repairable": "Not Repairable",
	"closed": "Closed",
}


def _classify_queue_stage(sr):
	"""Classify SR into a queue stage for store executive view."""
	decision = sr.get("decision", "")
	status = sr.get("status", decision)

	# Terminal states
	if decision == "Delivered":
		return "delivered"
	if decision in ("Rejected", "Withdrawn"):
		return "not_repairable"
	if decision == "Cancelled":
		return "closed"

	# Invoiced = ready for delivery
	if decision == "Invoiced":
		return "ready_delivery"

	# Check for transfer
	current_loc = sr.get("current_processing_location") or sr.get("current_location")
	source = sr.get("source_warehouse")
	if current_loc and source and current_loc != source:
		return "transferred"

	# Completed + QC = ready for invoice or QC stage
	if decision == "Completed":
		return "ready_invoice"

	# In Service
	if decision == "In Service":
		return "in_repair"

	# Accepted but checking sub-stages
	if decision == "Accepted":
		# Check repairability
		repairability = sr.get("repairability_status") or ""
		if repairability in ("Not Repairable", "BER", "Customer Declined"):
			return "not_repairable"

		# Check estimate approval
		if sr.get("estimate_approval_pending"):
			return "awaiting_approval"

		# Check if estimate exists
		if sr.get("latest_estimate_version"):
			# Estimate exists, check approval
			if sr.get("customer_confirmed") or not sr.get("estimate_approval_pending"):
				return "approved"
			return "estimate_pending"

		# Check analysis
		if sr.get("analysis_confirmed"):
			return "analysis_done"

		return "awaiting_analysis"

	# Draft = new request
	return "new_request"


def _build_summary(sr_list):
	"""Build stage-wise summary counts."""
	summary = {}
	for stage in QUEUE_STAGES:
		summary[stage] = 0
	for sr in sr_list:
		stage = sr.get("queue_stage", "new_request")
		summary[stage] = summary.get(stage, 0) + 1
	summary["total"] = len(sr_list)
	return summary


def _empty_summary():
	summary = {stage: 0 for stage in QUEUE_STAGES}
	summary["total"] = 0
	return summary


@frappe.whitelist()
def get_request_detail(sr_name) -> dict:
	"""Return full SR detail for the store executive detail panel."""
	frappe.has_permission("Service Request", sr_name, "read", throw=True)
	sr = frappe.get_doc("Service Request", sr_name)

	# Estimate versions
	estimate_versions = []
	for ev in (sr.get("estimate_versions") or []):
		estimate_versions.append({
			"version_number": ev.version_number,
			"estimate_amount": ev.estimate_amount,
			"labor_cost": ev.labor_cost,
			"spare_cost": ev.spare_cost,
			"status": ev.status,
			"created_at": str(ev.created_at) if ev.created_at else "",
			"reason_for_revision": ev.reason_for_revision or "",
			"customer_remarks": ev.customer_remarks or "",
		})

	# Status log
	status_log = [
		{
			"from_status": row.from_status,
			"to_status": row.to_status,
			"changed_by": row.changed_by,
			"changed_at": str(row.changed_at) if row.changed_at else "",
			"hours_in_prev": flt(row.get("time_in_previous_status_hours")),
		}
		for row in (sr.get("status_log") or [])
	]

	return {
		"name": sr.name,
		"customer": sr.customer,
		"customer_name": sr.customer_name,
		"contact_number": sr.contact_number or "",
		"email": sr.get("email") or "",
		"device_item_name": sr.device_item_name or "",
		"serial_no": sr.serial_no or "",
		"brand": sr.brand or "",
		"device_condition": sr.device_condition or "",
		"issue_category": sr.issue_category or "",
		"issue_description": sr.issue_description or "",
		"warranty_status": sr.warranty_status or "",
		"warranty_plan_name": sr.get("warranty_plan_name") or "",
		"decision": sr.decision,
		"status": sr.status,
		"priority": sr.priority,
		"estimated_cost": flt(sr.estimated_cost),
		"service_date": str(sr.service_date) if sr.service_date else "",
		"source_warehouse": sr.source_warehouse or "",
		"current_processing_location": sr.get("current_processing_location") or "",
		"repair_location_type": sr.get("repair_location_type") or "",
		"repairability_status": sr.get("repairability_status") or "",
		"repairability_reason": sr.get("repairability_reason") or "",
		"estimate_approval_pending": cint(sr.get("estimate_approval_pending")),
		"repair_paused": cint(sr.get("repair_paused")),
		"repair_pause_reason": sr.get("repair_pause_reason") or "",
		"latest_estimate_version": cint(sr.get("latest_estimate_version")),
		"service_order": sr.service_order or "",
		"service_invoice": sr.get("service_invoice") or "",
		"estimate_versions": estimate_versions,
		"status_log": status_log,
	}


@frappe.whitelist()
def quick_accept(service_request) -> dict:
	"""Quick-accept an SR from Store Queue and ensure Service Order is created.

	Historically this API only flipped decision/walk-in flags, which left some
	requests accepted but without a linked Service Order.

	Now this API delegates to canonical `accept_service_request` inside a DB
	savepoint:
	  - On success: SR becomes accepted/in-service + Service Order is linked.
	  - On prerequisite failure (analysis/repairability/estimate approval):
	    rollback to pre-accept state and return an explicit blocked reason.
	"""
	sr = frappe.get_doc("Service Request", service_request)
	frappe.has_permission("Service Request", doc=sr, ptype="write", throw=True)

	if sr.decision != "Draft":
		frappe.throw(_("Can only accept Draft requests. Current: {0}").format(sr.decision), title=_("Validation Error"))

	if sr.docstatus == 0:
		sr.save()
		sr.submit()

	# Canonical accept flow creates SO + sets final status/workflow correctly.
	from gofix.gofix_services.doctype.service_request.service_request import accept_service_request
	savepoint = "quick_accept_so"
	frappe.db.savepoint(savepoint)
	try:
		so_name = accept_service_request(sr.name)
	except frappe.ValidationError as e:
		# Roll back any partial decision/status db_set done inside accept API.
		frappe.db.rollback(save_point=savepoint)
		sr.reload()
		return {
			"ok": False,
			"name": sr.name,
			"service_order": "",
			"blocked": True,
			"message": str(e),
		}
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise

	return {
		"ok": True,
		"message": _("Request accepted"),
		"name": sr.name,
		"service_order": so_name,
	}
