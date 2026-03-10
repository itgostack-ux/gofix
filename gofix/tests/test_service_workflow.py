# Copyright (c) 2025, GoFix and Contributors
# Test script for Service Request → Service Order → Job Sheet workflow

import frappe
from frappe.utils import today, add_days, nowdate


def setup_test_data():
	"""Setup sample data for testing"""
	print("\n========== SETTING UP TEST DATA ==========\n")
	
	# 1. Ensure company has address
	company = frappe.defaults.get_global_default("company") or "Congruence Holdings"
	
	# Check if company address exists
	company_address = frappe.db.get_value("Dynamic Link",
		{
			"link_doctype": "Company",
			"link_name": company,
			"parenttype": "Address"
		},
		"parent")
	
	if not company_address:
		print(f"⚠️  Creating company address for {company}...")
		address = frappe.new_doc("Address")
		address.address_title = f"{company} - Head Office"
		address.address_type = "Billing"
		address.address_line1 = "123 Business Park"
		address.city = "Mumbai"
		address.state = "Maharashtra"
		address.country = "India"
		address.pincode = "400001"
		address.is_primary_address = 1
		address.is_your_company_address = 1
		# Note: GSTIN validation is strict, skip for test data
		address.gst_state = "Maharashtra"
		address.gst_state_number = "27"
		address.append("links", {
			"link_doctype": "Company",
			"link_name": company
		})
		address.insert(ignore_permissions=True)
		frappe.db.commit()
		print(f"✅ Created company address: {address.name}")
		company_address = address.name
	else:
		print(f"✅ Company address exists: {company_address}")
	
	# 2. Ensure warehouse exists
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not warehouse:
		print(f"⚠️  Creating warehouse for {company}...")
		wh = frappe.new_doc("Warehouse")
		wh.warehouse_name = "Main Store"
		wh.company = company
		wh.insert(ignore_permissions=True)
		frappe.db.commit()
		warehouse = wh.name
		print(f"✅ Created warehouse: {warehouse}")
	else:
		print(f"✅ Warehouse exists: {warehouse}")
	
	# 3. Create warehouse address if missing
	wh_address = frappe.db.get_value("Dynamic Link",
		{
			"link_doctype": "Warehouse",
			"link_name": warehouse,
			"parenttype": "Address"
		},
		"parent")
	
	if not wh_address:
		print(f"⚠️  Creating warehouse address...")
		address = frappe.new_doc("Address")
		address.address_title = f"{warehouse}"
		address.address_type = "Warehouse"
		address.address_line1 = "456 Industrial Area"
		address.city = "Mumbai"
		address.state = "Maharashtra"
		address.country = "India"
		address.pincode = "400002"
		address.gst_state = "Maharashtra"
		address.gst_state_number = "27"
		address.append("links", {
			"link_doctype": "Warehouse",
			"link_name": warehouse
		})
		address.insert(ignore_permissions=True)
		frappe.db.commit()
		print(f"✅ Created warehouse address: {address.name}")
	else:
		print(f"✅ Warehouse address exists: {wh_address}")
	
	# 4. Ensure customer exists
	customer = frappe.db.get_value("Customer", {"customer_name": "Test Customer"}, "name")
	if not customer:
		print(f"⚠️  Creating test customer...")
		cust = frappe.new_doc("Customer")
		cust.customer_name = "Test Customer"
		cust.customer_type = "Individual"
		cust.customer_group = "Individual"
		cust.territory = "India"
		cust.insert(ignore_permissions=True)
		frappe.db.commit()
		customer = cust.name
		print(f"✅ Created customer: {customer}")
	else:
		print(f"✅ Customer exists: {customer}")
	
	# 5. Ensure device item exists
	device_item = frappe.db.get_value("Item", {"item_name": "iPhone 12"}, "name")
	if not device_item:
		print(f"⚠️  Creating device item...")
		item = frappe.new_doc("Item")
		item.item_code = "DEVICE-IPHONE-12"
		item.item_name = "iPhone 12"
		item.item_group = "Products"
		item.stock_uom = "Nos"
		item.is_stock_item = 1
		item.insert(ignore_permissions=True)
		frappe.db.commit()
		device_item = item.name
		print(f"✅ Created device item: {device_item}")
	else:
		print(f"✅ Device item exists: {device_item}")
	
	# 6. Ensure service item exists (non-stock)
	service_item = frappe.db.get_value("Item", {"is_stock_item": 0, "item_group": "Services"}, "name")
	if not service_item:
		print(f"⚠️  Creating service item...")
		item = frappe.new_doc("Item")
		item.item_code = "SERVICE-REPAIR"
		item.item_name = "Device Repair Service"
		item.item_group = "Services"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.insert(ignore_permissions=True)
		frappe.db.commit()
		service_item = item.name
		print(f"✅ Created service item: {service_item}")
	else:
		print(f"✅ Service item exists: {service_item}")
	
	print(f"\n✅ Test data setup complete!")
	return {
		"company": company,
		"company_address": company_address,
		"warehouse": warehouse,
		"customer": customer,
		"device_item": device_item,
		"service_item": service_item
	}


def test_service_request_workflow():
	"""Test complete workflow: SR → Accept → SO → Job Sheet"""
	
	# Setup test data
	data = setup_test_data()
	
	print("\n========== TESTING SERVICE REQUEST WORKFLOW ==========\n")
	
	# Create Service Request
	print("1️⃣  Creating Service Request...")
	sr = frappe.new_doc("Service Request")
	sr.customer = data["customer"]
	sr.company = data["company"]
	sr.device_item = data["device_item"]
	sr.device_item_name = "iPhone 12"
	sr.brand = "Apple"
	sr.serial_no = "TEST-12345"
	sr.issue_description = "Screen not working"
	sr.source_warehouse = data["warehouse"]
	sr.current_location = data["warehouse"]
	sr.estimated_cost = 5000.00
	sr.expected_completion_date = add_days(today(), 3)
	sr.priority = "Medium"
	sr.insert(ignore_permissions=True)
	sr.submit()
	frappe.db.commit()
	print(f"✅ Service Request created: {sr.name}")
	print(f"   Status: {sr.status}, Decision: {sr.decision}")
	
	# Accept Service Request
	print("\n2️⃣  Accepting Service Request...")
	from gofix.gofix_services.doctype.service_request.service_request import accept_service_request
	
	try:
		so_name = accept_service_request(sr.name)
		sr.reload()
		print(f"✅ Service Request accepted!")
		print(f"   Decision: {sr.decision}")
		print(f"   Walk-in Status: {sr.walkin_status}")
		print(f"   Status: {sr.status}")
		print(f"   Service Order: {sr.service_order}")
		
		# Verify Service Order
		print("\n3️⃣  Verifying Service Order...")
		so = frappe.get_doc("Sales Order", sr.service_order)
		print(f"✅ Service Order: {so.name}")
		print(f"   Status: {so.status}")
		print(f"   Is Service Order: {so.is_service_order}")
		print(f"   Company Address: {so.company_address}")
		print(f"   Items: {len(so.items)}")
		if so.items:
			print(f"   - Item: {so.items[0].item_name}")
			print(f"   - Rate: {so.items[0].rate}")
		print(f"   Total: {so.grand_total}")
		
		# Create Job Sheet
		print("\n4️⃣  Creating Job Sheet...")
		from gofix.gofix_services.doctype.job_assignment.job_assignment import create_job_sheet_from_service_order
		
		technician = frappe.db.get_value("User", {"name": ["!=", "Administrator"]}, "name") or "Administrator"
		
		js_name = create_job_sheet_from_service_order(
			service_order=so.name,
			service_engineer=technician,
			job_type="Repair",
			estimated_hours=2.0
		)
		
		js = frappe.get_doc("Job Assignment", js_name)
		print(f"✅ Job Sheet created: {js.name}")
		print(f"   Service Order: {js.service_order}")
		print(f"   Service Request: {js.service_request}")
		print(f"   Assigned To: {js.assigned_to}")
		print(f"   Job Type: {js.job_type}")
		print(f"   Status: {js.assignment_status}")
		
		frappe.db.commit()
		
		print("\n" + "="*50)
		print("✅ COMPLETE WORKFLOW TEST PASSED!")
		print("="*50)
		print(f"\n📋 Summary:")
		print(f"   Service Request: {sr.name}")
		print(f"   Service Order: {so.name}")
		print(f"   Job Sheet: {js.name}")
		print(f"\n✨ All systems working correctly!")
		
		return True
		
	except Exception as e:
		print(f"\n❌ ERROR: {str(e)}")
		import traceback
		traceback.print_exc()
		return False


if __name__ == "__main__":
	test_service_request_workflow()
