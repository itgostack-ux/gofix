# Copyright (c) 2026, GoFix and contributors
# Custom fields for competitive-grade repair operations:
#   - Location model (source vs current processing location)
#   - Repairability decision
#   - Estimate versioning
#   - Enhanced QC issue-level tracking

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_competitive_ops_fields():
	"""Add location model, repairability, estimate versioning, QC enhancements, and WhatsApp templates."""
	_create_service_request_location_fields()
	_create_qc_issue_level_fields()
	_create_whatsapp_estimate_template_fields()


def _create_service_request_location_fields():
	"""Source vs Current Processing Location model + repairability decision."""
	if not frappe.db.exists("DocType", "Service Request"):
		return

	custom_fields = {
		"Service Request": [
			# ── Location Model ──
			{
				"fieldname": "location_model_section",
				"label": "Repair Location",
				"fieldtype": "Section Break",
				"insert_after": "current_location",
				"collapsible": 1,
			},
			{
				"fieldname": "current_processing_location",
				"label": "Current Processing Location",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "location_model_section",
				"allow_on_submit": 1,
				"description": "Where the device is currently being repaired (may differ from source store)",
			},
			{
				"fieldname": "repair_location_type",
				"label": "Repair Location Type",
				"fieldtype": "Select",
				"options": "\nStore\nZone Service Center\nMaster Warehouse",
				"insert_after": "current_processing_location",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "loc_col_break",
				"fieldtype": "Column Break",
				"insert_after": "repair_location_type",
			},
			{
				"fieldname": "billing_location",
				"label": "Billing Location",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "loc_col_break",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Invoice will be created at this location (always the source store)",
			},
			{
				"fieldname": "last_transfer_reference",
				"label": "Last Transfer Reference",
				"fieldtype": "Link",
				"options": "Stock Entry",
				"insert_after": "billing_location",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			# ── Repairability Decision ──
			{
				"fieldname": "repairability_section",
				"label": "Repairability Decision",
				"fieldtype": "Section Break",
				"insert_after": "last_transfer_reference",
				"collapsible": 1,
			},
			{
				"fieldname": "repairability_status",
				"label": "Repairability Status",
				"fieldtype": "Select",
				"options": "\nPending Analysis\nRepairable\nNot Repairable\nBER\nCustomer Declined",
				"insert_after": "repairability_section",
				"allow_on_submit": 1,
				"in_standard_filter": 1,
				"description": "Formal technician decision after diagnosis",
			},
			{
				"fieldname": "repairability_reason",
				"label": "Reason",
				"fieldtype": "Small Text",
				"insert_after": "repairability_status",
				"allow_on_submit": 1,
				"depends_on": "eval:doc.repairability_status && doc.repairability_status != 'Pending Analysis'",
			},
			{
				"fieldname": "repair_col_break",
				"fieldtype": "Column Break",
				"insert_after": "repairability_reason",
			},
			{
				"fieldname": "repairability_decided_by",
				"label": "Decided By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "repair_col_break",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "repairability_decided_at",
				"label": "Decision Datetime",
				"fieldtype": "Datetime",
				"insert_after": "repairability_decided_by",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			# ── Estimate Versioning ──
			{
				"fieldname": "estimate_version_section",
				"label": "Estimate Versions",
				"fieldtype": "Section Break",
				"insert_after": "repairability_decided_at",
				"collapsible": 1,
			},
			{
				"fieldname": "estimate_versions",
				"label": "Estimate History",
				"fieldtype": "Table",
				"options": "Estimate Version",
				"insert_after": "estimate_version_section",
				"allow_on_submit": 1,
				"read_only": 1,
			},
			{
				"fieldname": "latest_estimate_version",
				"label": "Latest Version #",
				"fieldtype": "Int",
				"insert_after": "estimate_versions",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "estimate_approval_pending",
				"label": "Estimate Approval Pending",
				"fieldtype": "Check",
				"insert_after": "latest_estimate_version",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Repair is paused until customer approves the latest estimate",
			},
			# ── Repair Pause ──
			{
				"fieldname": "repair_paused",
				"label": "Repair Paused",
				"fieldtype": "Check",
				"insert_after": "estimate_approval_pending",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Repair is blocked: awaiting estimate approval, parts, or transfer",
			},
			{
				"fieldname": "repair_pause_reason",
				"label": "Pause Reason",
				"fieldtype": "Select",
				"options": "\nAwaiting Estimate Approval\nAwaiting Spare Parts\nAwaiting Device Transfer\nAwaiting Manager Approval",
				"insert_after": "repair_paused",
				"read_only": 1,
				"allow_on_submit": 1,
				"depends_on": "repair_paused",
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: Location model + repairability + estimate version fields created.")


def _create_qc_issue_level_fields():
	"""Add issue-level tracking to QC Checklist (on Sales Order)."""
	if not frappe.db.exists("DocType", "GoFix QC Checklist"):
		return

	custom_fields = {
		"GoFix QC Checklist": [
			{
				"fieldname": "linked_issue_category",
				"label": "Issue Category",
				"fieldtype": "Link",
				"options": "Issue Category",
				"insert_after": "check_name",
				"in_list_view": 1,
				"description": "The specific issue this QC check relates to",
			},
			{
				"fieldname": "linked_solution",
				"label": "Repair Solution",
				"fieldtype": "Link",
				"options": "Repair Solution",
				"insert_after": "linked_issue_category",
			},
			{
				"fieldname": "fail_reason",
				"label": "Fail Reason",
				"fieldtype": "Small Text",
				"insert_after": "remarks",
				"depends_on": "eval:doc.result=='Fail'",
				"mandatory_depends_on": "eval:doc.result=='Fail'",
			},
			{
				"fieldname": "rework_required",
				"label": "Rework Required",
				"fieldtype": "Check",
				"insert_after": "fail_reason",
				"default": "0",
				"description": "If checked, this failed issue will be sent back for rework",
			},
			{
				"fieldname": "rework_iteration",
				"label": "Rework Iteration",
				"fieldtype": "Int",
				"insert_after": "rework_required",
				"default": "0",
				"read_only": 1,
			},
			{
				"fieldname": "new_issue_identified",
				"label": "New Issue Identified",
				"fieldtype": "Check",
				"insert_after": "rework_iteration",
				"default": "0",
				"description": "QC found a new issue not in original diagnosis",
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: QC issue-level tracking fields created.")


def _create_whatsapp_estimate_template_fields():
	"""Add estimate approval + revised estimate + tracking link template name fields to CH WhatsApp Settings."""
	if not frappe.db.exists("DocType", "CH WhatsApp Settings"):
		return

	custom_fields = {
		"CH WhatsApp Settings": [
			{
				"fieldname": "gofix_estimate_approval",
				"label": "Estimate Approval",
				"fieldtype": "Data",
				"insert_after": "gofix_sla_breach",
				"description": "WhatsApp template for initial estimate approval request",
			},
			{
				"fieldname": "gofix_revised_estimate",
				"label": "Revised Estimate",
				"fieldtype": "Data",
				"insert_after": "gofix_estimate_approval",
				"description": "WhatsApp template for revised estimate (version 2+)",
			},
			{
				"fieldname": "gofix_tracking_link",
				"label": "Tracking Link",
				"fieldtype": "Data",
				"insert_after": "gofix_revised_estimate",
				"description": "WhatsApp template for sending repair tracking URL",
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
	frappe.logger("gofix").info("GoFix: WhatsApp estimate template fields created.")
