# Copyright (c) 2025, GoFix and contributors
# Custom fields for Sales Order to support Service Order functionality

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_sales_order_custom_fields():
	"""Create custom fields in Sales Order for Service Order functionality"""
	# Check if Service Request DocType exists before creating Link fields to it.
	# During install/migrate the DocType module may not be synced yet.
	if not frappe.db.table_exists("tabService Request") and not frappe.db.exists("DocType", "Service Request"):
		frappe.logger("gofix").info(
			"Skipping Sales Order custom fields: Service Request DocType not yet available"
		)
		return

	custom_fields = {
		"Sales Order": [
			# Service Request Reference Section
			{
				"fieldname": "service_order_details_section",
				"label": "Service Order Details",
				"fieldtype": "Section Break",
				"insert_after": "customer_name",
				"collapsible": 1
			},
			{
				"fieldname": "service_request",
				"label": "Service Request",
				"fieldtype": "Link",
				"options": "Service Request",
				"insert_after": "service_order_details_section",
				"read_only": 1,
				"in_list_view": 0,
				"in_standard_filter": 1
			},
			{
				"fieldname": "service_request_status",
				"label": "Service Request Status",
				"fieldtype": "Data",
				"fetch_from": "service_request.status",
				"insert_after": "service_request",
				"read_only": 1
			},
			{
				"fieldname": "service_order_column_break",
				"fieldtype": "Column Break",
				"insert_after": "service_request_status"
			},
			{
				"fieldname": "is_service_order",
				"label": "Is Service Order",
				"fieldtype": "Check",
				"insert_after": "service_order_column_break",
				"default": "0",
				"description": "Check if this is a Service Order (not regular Sales Order)"
			},
			
			# Device Information Section
			{
				"fieldname": "device_information_section",
				"label": "Device Information",
				"fieldtype": "Section Break",
				"insert_after": "is_service_order",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "device_brand",
				"label": "Device Brand",
				"fieldtype": "Link",
				"options": "Brand",
				"insert_after": "device_information_section"
			},
			{
				"fieldname": "device_model",
				"label": "Device Model",
				"fieldtype": "Data",
				"insert_after": "device_brand"
			},
			{
				"fieldname": "imei_serial_no",
				"label": "IMEI / Serial No",
				"fieldtype": "Data",
				"insert_after": "device_model",
				"in_list_view": 0,
				"in_standard_filter": 1
			},
			{
				"fieldname": "device_column_break",
				"fieldtype": "Column Break",
				"insert_after": "imei_serial_no"
			},
			{
				"fieldname": "device_condition",
				"label": "Device Condition",
				"fieldtype": "Select",
				"options": "Good\nDamaged\nBroken\nWater Damaged",
				"insert_after": "device_column_break"
			},
			{
				"fieldname": "device_condition_desc",
				"label": "Condition Description",
				"fieldtype": "Small Text",
				"insert_after": "device_condition",
				"description": "Detailed description of device physical condition"
			},
			{
				"fieldname": "accessories_received",
				"label": "Accessories Received",
				"fieldtype": "Small Text",
				"insert_after": "device_condition_desc"
			},
			
			# Issue Information Section
			{
				"fieldname": "issue_information_section",
				"label": "Issue Information",
				"fieldtype": "Section Break",
				"insert_after": "accessories_received",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "issue_category",
				"label": "Issue Category",
				"fieldtype": "Link",
				"options": "Issue Category",
				"insert_after": "issue_information_section"
			},
			{
				"fieldname": "issue_description",
				"label": "Issue Description",
				"fieldtype": "Long Text",
				"insert_after": "issue_category"
			},
			{
				"fieldname": "issue_column_break",
				"fieldtype": "Column Break",
				"insert_after": "issue_description"
			},
			{
				"fieldname": "password_pattern",
				"label": "Device Password/Pattern",
				"fieldtype": "Small Text",
				"insert_after": "issue_column_break",
				"description": "Security information for device unlock"
			},
			{
				"fieldname": "backup_status",
				"label": "Backup Status",
				"fieldtype": "Small Text",
				"insert_after": "password_pattern"
			},
			{
				"fieldname": "actual_imei",
				"label": "Actual IMEI",
				"fieldtype": "Data",
				"insert_after": "backup_status",
				"description": "Verified IMEI from device settings"
			},
			
			# Service Planning Section
			{
				"fieldname": "service_planning_section",
				"label": "Service Planning",
				"fieldtype": "Section Break",
				"insert_after": "actual_imei",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "estimated_delivery_date",
				"label": "Estimated Delivery Date",
				"fieldtype": "Date",
				"insert_after": "service_planning_section"
			},
			{
				"fieldname": "actual_delivery_date",
				"label": "Actual Delivery Date",
				"fieldtype": "Date",
				"insert_after": "estimated_delivery_date"
			},
			{
				"fieldname": "service_priority",
				"label": "Priority",
				"fieldtype": "Select",
				"options": "Low\nMedium\nHigh\nUrgent",
				"insert_after": "actual_delivery_date",
				"default": "Medium"
			},
			{
				"fieldname": "service_planning_column_break",
				"fieldtype": "Column Break",
				"insert_after": "service_priority"
			},
			{
				"fieldname": "warranty_status",
				"label": "Warranty Status",
				"fieldtype": "Select",
				"options": "\nIn Warranty\nOut of Warranty\nNo Warranty",
				"insert_after": "service_planning_column_break"
			},
			{
				"fieldname": "warranty_provider",
				"label": "Warranty Provider",
				"fieldtype": "Link",
				"options": "Supplier",
				"insert_after": "warranty_status"
			},
			{
				"fieldname": "warranty_expiry_date",
				"label": "Warranty Expiry Date",
				"fieldtype": "Date",
				"insert_after": "warranty_provider",
				"read_only": 1
			},
			{
				"fieldname": "warranty_plan",
				"label": "Warranty Plan",
				"fieldtype": "Link",
				"options": "CH Warranty Plan",
				"insert_after": "warranty_expiry_date",
				"read_only": 1,
				"description": "Active warranty plan covering this device (from Service Request)"
			},
			{
				"fieldname": "warranty_deductible",
				"label": "Warranty Deductible",
				"fieldtype": "Currency",
				"insert_after": "warranty_plan",
				"read_only": 1,
				"description": "Customer deductible per claim under the warranty plan"
			},
			
			# Quality Control Section
			{
				"fieldname": "quality_control_section",
				"label": "Quality Control",
				"fieldtype": "Section Break",
				"insert_after": "warranty_deductible",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "qc_status",
				"label": "QC Status",
				"fieldtype": "Select",
				"options": "Pending\nAwaiting\nIn Progress\nPass\nFail",
				"insert_after": "quality_control_section",
				"default": "Pending",
				"allow_on_submit": 1
			},
			{
				"fieldname": "repair_outcome",
				"label": "Repair Outcome",
				"fieldtype": "Select",
				"options": "\nRepaired\nNot Repairable\nCustomer Cancelled",
				"insert_after": "qc_status",
				"allow_on_submit": 1,
				"description": "Mark as Not Repairable or Customer Cancelled to close without QC"
			},
			{
				"fieldname": "qc_checked_by",
				"label": "QC Checked By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "repair_outcome",
				"read_only": 1
			},
			{
				"fieldname": "qc_datetime",
				"label": "QC Date & Time",
				"fieldtype": "Datetime",
				"insert_after": "qc_checked_by",
				"read_only": 1
			},
			{
				"fieldname": "qc_column_break",
				"fieldtype": "Column Break",
				"insert_after": "qc_datetime"
			},
			{
				"fieldname": "qc_remarks",
				"label": "QC Remarks",
				"fieldtype": "Text",
				"insert_after": "qc_column_break"
			},
			{
				"fieldname": "qc_checklist_section",
				"label": "QC Checklist",
				"fieldtype": "Section Break",
				"insert_after": "qc_remarks",
				"depends_on": "eval:doc.is_service_order==1",
				"collapsible": 1
			},
			{
				"fieldname": "qc_checklist",
				"label": "QC Checklist",
				"fieldtype": "Table",
				"options": "GoFix QC Checklist",
				"insert_after": "qc_checklist_section",
				"allow_on_submit": 1
			},
			
			# Delivery Information Section
			{
				"fieldname": "delivery_information_section",
				"label": "Delivery Information",
				"fieldtype": "Section Break",
				"insert_after": "qc_checklist",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "delivery_mode",
				"label": "Delivery Mode",
				"fieldtype": "Select",
				"options": "Pick-up\nCourier\nHand Delivery",
				"insert_after": "delivery_information_section",
				"default": "Pick-up"
			},
			{
				"fieldname": "courier_name",
				"label": "Courier Name",
				"fieldtype": "Data",
				"insert_after": "delivery_mode",
				"depends_on": "eval:doc.delivery_mode=='Courier'"
			},
			{
				"fieldname": "tracking_number",
				"label": "Tracking Number",
				"fieldtype": "Data",
				"insert_after": "courier_name",
				"depends_on": "eval:doc.delivery_mode=='Courier'"
			},
			{
				"fieldname": "delivery_column_break",
				"fieldtype": "Column Break",
				"insert_after": "tracking_number"
			},
			{
				"fieldname": "delivery_address_display",
				"label": "Delivery Address",
				"fieldtype": "Small Text",
				"insert_after": "delivery_column_break"
			},
			{
				"fieldname": "delivered_datetime",
				"label": "Delivered Date & Time",
				"fieldtype": "Datetime",
				"insert_after": "delivery_address_display",
				"read_only": 1
			},
			
			# Warehouse / Location Section
			{
				"fieldname": "warehouse_location_section",
				"label": "Warehouse & Location",
				"fieldtype": "Section Break",
				"insert_after": "delivered_datetime",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "current_location",
				"label": "Current Location",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "warehouse_location_section",
				"description": "Current physical location of the device"
			},
			{
				"fieldname": "location_column_break",
				"fieldtype": "Column Break",
				"insert_after": "current_location"
			},
			{
				"fieldname": "state_name",
				"label": "State Name",
				"fieldtype": "Data",
				"insert_after": "location_column_break",
				"read_only": 1
			},
			{
				"fieldname": "state_code",
				"label": "State Code",
				"fieldtype": "Data",
				"insert_after": "state_name",
				"read_only": 1,
				"description": "GST State Code"
			},

			# ── Estimate Approval Section ────────────────────────────
			{
				"fieldname": "estimate_approval_section",
				"label": "Estimate Approval",
				"fieldtype": "Section Break",
				"insert_after": "state_code",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "estimate_sent",
				"label": "Estimate Sent",
				"fieldtype": "Check",
				"insert_after": "estimate_approval_section",
				"default": "0"
			},
			{
				"fieldname": "estimate_sent_datetime",
				"label": "Estimate Sent At",
				"fieldtype": "Datetime",
				"insert_after": "estimate_sent",
				"read_only": 1
			},
			{
				"fieldname": "estimate_sent_via",
				"label": "Estimate Sent Via",
				"fieldtype": "Select",
				"insert_after": "estimate_sent_datetime",
				"options": "\nEmail\nWhatsApp\nSMS\nIn-Person",
				"depends_on": "eval:doc.estimate_sent"
			},
			{
				"fieldname": "estimate_approval_column_break",
				"fieldtype": "Column Break",
				"insert_after": "estimate_sent_via"
			},
			{
				"fieldname": "estimate_approval_status",
				"label": "Est. Approval Status",
				"fieldtype": "Select",
				"insert_after": "estimate_approval_column_break",
				"options": "\nPending\nCustomer Approved\nCustomer Rejected\nExpired",
				"in_standard_filter": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "estimate_approved_datetime",
				"label": "Customer Response At",
				"fieldtype": "Datetime",
				"insert_after": "estimate_approval_status",
				"read_only": 1
			},
			{
				"fieldname": "estimate_expiry_date",
				"label": "Estimate Expiry Date",
				"fieldtype": "Date",
				"insert_after": "estimate_approved_datetime",
				"description": "Auto-expire estimate after this date"
			},
			{
				"fieldname": "estimate_approval_rule",
				"label": "Approval Rule",
				"fieldtype": "Link",
				"options": "GoFix Approval Rule",
				"insert_after": "estimate_expiry_date",
				"read_only": 1
			},
			{
				"fieldname": "estimate_customer_remarks",
				"label": "Customer Remarks",
				"fieldtype": "Small Text",
				"insert_after": "estimate_approval_rule"
			},

			# ── Decision Approval (maker-checker) ───────────────────
			{
				"fieldname": "decision_approval_section",
				"label": "Decision Approval",
				"fieldtype": "Section Break",
				"insert_after": "estimate_customer_remarks",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "decision_approval_status",
				"label": "Decision Approval Status",
				"fieldtype": "Select",
				"insert_after": "decision_approval_section",
				"options": "\nPending\nApproved\nRejected",
				"allow_on_submit": 1
			},
			{
				"fieldname": "decision_approved_by",
				"label": "Decision Approved By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "decision_approval_status",
				"read_only": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "decision_approval_datetime",
				"label": "Decision Approval At",
				"fieldtype": "Datetime",
				"insert_after": "decision_approved_by",
				"read_only": 1
			},
			{
				"fieldname": "decision_approval_column_break",
				"fieldtype": "Column Break",
				"insert_after": "decision_approval_datetime"
			},
			{
				"fieldname": "decision_approval_rule",
				"label": "Approval Rule",
				"fieldtype": "Link",
				"options": "GoFix Approval Rule",
				"insert_after": "decision_approval_column_break",
				"read_only": 1
			},
			{
				"fieldname": "decision_approval_remarks",
				"label": "Approval Remarks",
				"fieldtype": "Small Text",
				"insert_after": "decision_approval_rule",
				"allow_on_submit": 1
			},

			# ── Delivery Control Section ─────────────────────────────
			{
				"fieldname": "delivery_control_section",
				"label": "Delivery Control",
				"fieldtype": "Section Break",
				"insert_after": "decision_approval_remarks",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "delivery_otp",
				"label": "Delivery OTP",
				"fieldtype": "Data",
				"insert_after": "delivery_control_section",
				"read_only": 1,
				"description": "OTP sent to customer for device handover verification"
			},
			{
				"fieldname": "delivery_otp_verified",
				"label": "OTP Verified",
				"fieldtype": "Check",
				"insert_after": "delivery_otp",
				"read_only": 1,
				"default": "0"
			},
			{
				"fieldname": "delivery_otp_sent_at",
				"label": "OTP Sent At",
				"fieldtype": "Datetime",
				"insert_after": "delivery_otp_verified",
				"read_only": 1
			},
			{
				"fieldname": "delivery_control_column_break",
				"fieldtype": "Column Break",
				"insert_after": "delivery_otp_sent_at"
			},
			{
				"fieldname": "payment_verified",
				"label": "Payment Verified",
				"fieldtype": "Check",
				"insert_after": "delivery_control_column_break",
				"default": "0",
				"description": "Confirm all pending payments are cleared before delivery"
			},
			{
				"fieldname": "accessories_returned",
				"label": "Accessories Returned",
				"fieldtype": "Check",
				"insert_after": "payment_verified",
				"default": "0",
				"description": "Confirm all accessories returned to customer"
			},
			{
				"fieldname": "customer_signature",
				"label": "Customer Signature",
				"fieldtype": "Signature",
				"insert_after": "accessories_returned",
				"description": "Digital signature from customer on device handover"
			},
			{
				"fieldname": "delivery_remarks",
				"label": "Delivery Remarks",
				"fieldtype": "Small Text",
				"insert_after": "customer_signature"
			},

			# ── Service Costing Section ──────────────────────────────
			{
				"fieldname": "service_costing_section",
				"label": "Service Costing",
				"fieldtype": "Section Break",
				"insert_after": "delivery_remarks",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "spare_parts_cost",
				"label": "Spare Parts Cost",
				"fieldtype": "Currency",
				"insert_after": "service_costing_section",
				"read_only": 1,
				"description": "Total purchase cost of spare parts consumed"
			},
			{
				"fieldname": "spare_parts_revenue",
				"label": "Spare Parts Revenue",
				"fieldtype": "Currency",
				"insert_after": "spare_parts_cost",
				"read_only": 1,
				"description": "Total sales price of spare parts billed"
			},
			{
				"fieldname": "labor_cost",
				"label": "Labor Cost",
				"fieldtype": "Currency",
				"insert_after": "spare_parts_revenue",
				"description": "Technician labor cost for this repair"
			},
			{
				"fieldname": "service_costing_column_break",
				"fieldtype": "Column Break",
				"insert_after": "labor_cost"
			},
			{
				"fieldname": "total_repair_cost",
				"label": "Total Repair Cost",
				"fieldtype": "Currency",
				"insert_after": "service_costing_column_break",
				"read_only": 1,
				"description": "spare_parts_cost + labor_cost"
			},
			{
				"fieldname": "repair_margin",
				"label": "Repair Margin",
				"fieldtype": "Currency",
				"insert_after": "total_repair_cost",
				"read_only": 1,
				"description": "Revenue - Cost"
			},
			{
				"fieldname": "repair_margin_pct",
				"label": "Margin %",
				"fieldtype": "Percent",
				"insert_after": "repair_margin",
				"read_only": 1
			},
			{
				"fieldname": "cost_bearer",
				"label": "Cost Bearer",
				"fieldtype": "Select",
				"insert_after": "repair_margin_pct",
				"options": "\nCustomer\nCompany (Warranty)\nCompany (Goodwill)\nVendor Claim",
				"description": "Who bears the repair cost"
			},

			# ── Suggested Pricing & Override Tracking ────────────────
			{
				"fieldname": "suggested_pricing_section",
				"label": "Suggested Pricing",
				"fieldtype": "Section Break",
				"insert_after": "cost_bearer",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1",
				"description": "Auto-calculated suggested price vs actual billed. Overrides tracked for CEO dashboard."
			},
			{
				"fieldname": "suggested_labor_cost",
				"label": "Suggested Labor Cost",
				"fieldtype": "Currency",
				"insert_after": "suggested_pricing_section",
				"read_only": 1,
				"description": "Technician hourly rate × actual hours (from Job Assignment)"
			},
			{
				"fieldname": "suggested_total_cost",
				"label": "Suggested Total",
				"fieldtype": "Currency",
				"insert_after": "suggested_labor_cost",
				"read_only": 1,
				"description": "spare_parts_revenue + suggested_labor_cost"
			},
			{
				"fieldname": "suggested_pricing_column_break",
				"fieldtype": "Column Break",
				"insert_after": "suggested_total_cost"
			},
			{
				"fieldname": "price_override_amount",
				"label": "Price Override (Δ)",
				"fieldtype": "Currency",
				"insert_after": "suggested_pricing_column_break",
				"read_only": 1,
				"description": "Actual billed amount - Suggested total. Positive = charged more, Negative = discount given."
			},
			{
				"fieldname": "price_override_reason",
				"label": "Override Reason",
				"fieldtype": "Small Text",
				"insert_after": "price_override_amount",
				"allow_on_submit": 1
			},
			{
				"fieldname": "price_overridden_by",
				"label": "Overridden By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "price_override_reason",
				"read_only": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "technician_damage_cost",
				"label": "Technician Damage Cost",
				"fieldtype": "Currency",
				"insert_after": "price_overridden_by",
				"read_only": 1,
				"description": "Total cost of spare parts damaged by technician during repair"
			},

			# ── Technician Issues (on Service Order) ─────────────────
			{
				"fieldname": "technician_issues_section",
				"label": "Customer vs Technician Issues",
				"fieldtype": "Section Break",
				"insert_after": "technician_damage_cost",
				"collapsible": 1,
				"depends_on": "eval:doc.is_service_order==1"
			},
			{
				"fieldname": "customer_reported_issues",
				"label": "Customer Reported Issues",
				"fieldtype": "Small Text",
				"insert_after": "technician_issues_section",
				"read_only": 1,
				"description": "Copied from Service Request — what customer described"
			},
			{
				"fieldname": "technician_identified_issues",
				"label": "Technician Identified Issues",
				"fieldtype": "Small Text",
				"insert_after": "customer_reported_issues",
				"allow_on_submit": 1,
				"description": "What technician found during diagnosis (may differ from customer report)"
			},

			# ── QC & Delivery Timestamps ─────────────────────────────
			{
				"fieldname": "qc_pass_datetime",
				"label": "QC Passed At",
				"fieldtype": "Datetime",
				"insert_after": "qc_datetime",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Auto-set when QC status changes to Pass"
			},
			{
				"fieldname": "delivery_ready_datetime",
				"label": "Delivery Ready At",
				"fieldtype": "Datetime",
				"insert_after": "delivered_datetime",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Auto-set when all delivery gates pass"
			},

			# ── QC Rework Tracking ───────────────────────────────────
			{
				"fieldname": "rework_count",
				"label": "Rework Count",
				"fieldtype": "Int",
				"insert_after": "qc_remarks",
				"default": "0",
				"read_only": 1,
				"allow_on_submit": 1,
				"description": "Number of times this SO was sent back for rework after QC Fail"
			},
			{
				"fieldname": "max_rework_limit",
				"label": "Max Rework Limit",
				"fieldtype": "Int",
				"insert_after": "rework_count",
				"default": "3",
				"allow_on_submit": 1,
				"description": "Maximum allowed rework attempts before escalation"
			}
		]
	}

	create_custom_fields(custom_fields, ignore_validate=True, update=False)
	frappe.db.commit()

	print("✅ Sales Order custom fields created successfully")


def register_sales_order_client_script():
	"""Register custom client script for Sales Order"""
	import os
	
	# Path to custom JS file
	js_file_path = frappe.get_app_path('gofix', 'gofix_services', 'doctype', 'sales_order_service.js')
	
	if not os.path.exists(js_file_path):
		print(f"❌ JavaScript file not found: {js_file_path}")
		return
	
	# Read the JS content
	with open(js_file_path, 'r') as f:
		js_content = f.read()
	
	# Check if Client Script already exists
	if frappe.db.exists('Client Script', {'name': 'Sales Order Service Order'}):
		doc = frappe.get_doc('Client Script', 'Sales Order Service Order')
		doc.script = js_content
		doc.save()
		print("✅ Sales Order Client Script updated")
	else:
		# Create new Client Script
		client_script = frappe.new_doc('Client Script')
		client_script.name = 'Sales Order Service Order'
		client_script.dt = 'Sales Order'
		client_script.script_type = 'Form'
		client_script.enabled = 1
		client_script.script = js_content
		client_script.insert()
		print("✅ Sales Order Client Script created")
	
	frappe.db.commit()


def execute():
	"""Entry point for patch execution"""
	create_sales_order_custom_fields()
	register_sales_order_client_script()


if __name__ == "__main__":
	create_sales_order_custom_fields()
