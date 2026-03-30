# Copyright (c) 2025, GoFix and contributors
# Custom fields for Sales Order to support Service Order functionality

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_sales_order_custom_fields():
	"""Create custom fields in Sales Order for Service Order functionality"""
	
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
			}
		]
	}
	
	create_custom_fields(custom_fields, update=True)
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
