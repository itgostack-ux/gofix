#!/usr/bin/env python3
"""
Test the complete 3-step workflow: Service Request → Service Order → Job Sheet
This script tests Phase 1, 2, and 3 implementations
"""

import frappe
from frappe.utils import today, now

def test_complete_workflow():
	"""Test the complete workflow from Service Request to Job Sheet"""
	
	print("\n" + "="*80)
	print("TESTING COMPLETE WORKFLOW: Service Request → Service Order → Job Sheet")
	print("="*80 + "\n")
	
	# Step 1: Create Service Request
	print("STEP 1: Creating Service Request...")
	
	# Get first customer
	customer = frappe.get_list("Customer", limit=1)
	if not customer:
		print("❌ No customers found. Please create a customer first.")
		return None, None, None
	customer_name = customer[0].name
	
	# Get warehouse (use default company)
	company = frappe.defaults.get_global_default("company")
	warehouse = frappe.get_list("Warehouse", filters={"company": company, "is_group": 0}, limit=1)
	if not warehouse:
		print("❌ No warehouses found.")
		return None, None, None
	warehouse_name = warehouse[0].name

	# Get or create a device item
	device_item = frappe.get_list("Item", filters={"is_stock_item": 0}, limit=1)
	if not device_item:
		print("❌ No non-stock items found. Please create items first.")
		return None, None, None
	device_item_code = device_item[0].name

	contact = frappe.db.get_value("Customer", customer_name, "mobile_no") or "9999999999"
	source_wh = warehouse_name

	sr = frappe.new_doc("Service Request")
	sr.customer = customer_name
	sr.company = company
	sr.contact_number = contact
	sr.source_warehouse = source_wh
	sr.device_item = device_item_code
	sr.issue_description = "Cracked screen, touch not working"
	sr.product_condition_desc = "Minor scratches, screen cracked"
	sr.backup_info = "Customer confirmed backup completed"
	sr.estimated_cost = 5000
	sr.priority = "Medium"
	sr.insert(ignore_permissions=True)
	sr.submit()
	# Simulate diagnosis completion
	frappe.db.set_value("Service Request", sr.name, {
		"analysis_confirmed": 1,
		"repairability_status": "Repairable",
		"estimate_approval_pending": 0,
	}, update_modified=False)
	frappe.db.commit()
	print(f"✅ Service Request created: {sr.name}")
	print(f"   Status: {sr.status}, Decision: {sr.decision}")
	
	# Step 2: Accept Service Request (Creates Service Order)
	print("\nSTEP 2: Accepting Service Request (creates Service Order)...")
	from gofix.gofix_services.doctype.service_request.service_request import accept_service_request
	accept_service_request(sr.name)
	sr.reload()
	
	if sr.service_order:
		print(f"✅ Service Order created: {sr.service_order}")
		print(f"   Service Request status updated to: {sr.status}")
		
		# Load Service Order
		so = frappe.get_doc("Sales Order", sr.service_order)
		print(f"\nService Order Details:")
		print(f"   - Customer: {so.customer}")
		print(f"   - Is Service Order: {so.is_service_order}")
		print(f"   - Service Request Link: {so.service_request}")
		print(f"   - Device: {so.device_brand} {so.device_model}")
		print(f"   - IMEI: {so.imei_serial_no}")
		print(f"   - Issue: {so.issue_category} - {so.issue_description}")
		print(f"   - QC Status: {so.qc_status}")
		print(f"   - Priority: {so.service_priority}")
		
		# Submit Service Order
		so.submit()
		print(f"✅ Service Order submitted: {so.name}")
		
		# Step 3: Create Job Sheet from Service Order
		print("\nSTEP 3: Creating Job Sheet from Service Order...")
		
		# Get first employee to assign
		employee = frappe.get_list("Employee", limit=1)
		if not employee:
			print("❌ No employees found. Creating test employee...")
			emp = frappe.new_doc("Employee")
			emp.first_name = "Test"
			emp.last_name = "Technician"
			emp.employee_name = "Test Technician"
			emp.company = frappe.defaults.get_global_default("company")
			emp.insert(ignore_permissions=True)
			employee_id = emp.name
		else:
			employee_id = employee[0].name
		
		# Call API method to create Job Sheet
		from gofix.gofix_services.doctype.job_assignment.job_assignment import create_job_sheet_from_service_order
		
		job_sheet_name = create_job_sheet_from_service_order(
			service_order=so.name,
			service_engineer=employee_id,
			job_type="Repair",
			estimated_hours=2.5
		)
		
		print(f"✅ Job Sheet created: {job_sheet_name}")
		
		# Load and verify Job Sheet
		js = frappe.get_doc("Job Assignment", job_sheet_name)
		print(f"\nJob Sheet Details:")
		print(f"   - Service Order: {js.service_order}")
		print(f"   - Service Request: {js.service_request}")
		print(f"   - Service Engineer: {js.service_engineer}")
		print(f"   - Job Type: {js.job_type}")
		print(f"   - Priority: {js.priority}")
		print(f"   - Status: {js.assignment_status}")
		print(f"   - Estimated Hours: {js.estimated_hours}")
		print(f"   - Assigned By: {js.assigned_by}")
		print(f"   - Assignment Date: {js.assignment_date}")
		
		# Step 4: Simulate work being done
		print("\nSTEP 4: Simulating work completion...")
		js.start_datetime = now()
		js.work_performed = "Replaced screen digitizer, tested touch functionality, recalibrated display"
		js.technician_remarks = "Device tested successfully. All functions working."
		js.assignment_status = "Completed"
		
		# Simulate 2 hours of work
		from frappe.utils import add_to_date
		js.end_datetime = add_to_date(js.start_datetime, hours=2)
		js.save()
		
		print(f"✅ Job Sheet updated with work completion")
		print(f"   - Start Time: {js.start_datetime}")
		print(f"   - End Time: {js.end_datetime}")
		print(f"   - Actual Hours: {js.actual_hours} (auto-calculated)")
		print(f"   - Status: {js.assignment_status}")
		
		# Summary
		print("\n" + "="*80)
		print("WORKFLOW TEST COMPLETED SUCCESSFULLY!")
		print("="*80)
		print(f"\nWorkflow Summary:")
		print(f"1. Service Request: {sr.name} (Status: {sr.status})")
		print(f"2. Service Order:   {so.name} (Status: {so.status})")
		print(f"3. Job Sheet:       {js.name} (Status: {js.assignment_status})")
		print(f"\n✅ All 3 phases working correctly!")
		print(f"✅ Complete workflow: SR → Accept → SO → Create Job Sheet → Work Done")
		
		return sr.name, so.name, js.name
	else:
		print("❌ Service Order was not created!")
		return None, None, None


if __name__ == "__main__":
	# Run test
	sr_name, so_name, js_name = test_complete_workflow()
	
	if sr_name and so_name and js_name:
		print("\n" + "="*80)
		print("QUICK ACCESS LINKS:")
		print("="*80)
		print(f"Service Request: http://erpnext.local:8000/app/service-request/{sr_name}")
		print(f"Service Order:   http://erpnext.local:8000/app/sales-order/{so_name}")
		print(f"Job Sheet:       http://erpnext.local:8000/app/job-assignment/{js_name}")
		print("="*80 + "\n")

run_all = test_complete_workflow
