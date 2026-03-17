"""
Sample data for GoFix - Mobile phone service/repair management
Run this script to populate sample data for testing
"""

import frappe
from frappe.utils import today, add_days


def create_sample_data():
	"""Create all sample data"""
	print("Creating sample data for GoFix...")
	
	create_brands()
	create_item_groups()
	create_walkin_sources()
	create_issue_categories()
	create_withdrawal_reasons()
	create_device_items()
	create_service_items()
	create_spare_part_items()
	create_sample_customers()
	
	frappe.db.commit()
	print("Sample data created successfully!")


def create_brands():
	"""Create brands for mobile phones"""
	print("Creating Brands...")
	
	brands = ["Apple", "Samsung", "OnePlus", "Google", "Xiaomi", "Oppo", "Vivo"]
	
	for brand_name in brands:
		if not frappe.db.exists("Brand", brand_name):
			doc = frappe.get_doc({
				"doctype": "Brand",
				"brand": brand_name
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Brand: {brand_name}")


def create_item_groups():
	"""Create Item Groups hierarchy"""
	print("Creating Item Groups...")
	
	item_groups = [
		{
			"item_group_name": "Devices",
			"parent_item_group": "All Item Groups",
			"is_group": 1
		},
		{
			"item_group_name": "Mobile Phones",
			"parent_item_group": "Devices",
			"is_group": 0
		},
		{
			"item_group_name": "Laptops",
			"parent_item_group": "Devices",
			"is_group": 0
		},
		{
			"item_group_name": "Tablets",
			"parent_item_group": "Devices",
			"is_group": 0
		},
		{
			"item_group_name": "Services",
			"parent_item_group": "All Item Groups",
			"is_group": 1
		},
		{
			"item_group_name": "Repair Services",
			"parent_item_group": "Services",
			"is_group": 0
		},
		{
			"item_group_name": "Installation",
			"parent_item_group": "Services",
			"is_group": 0
		},
		{
			"item_group_name": "Spare Parts",
			"parent_item_group": "All Item Groups",
			"is_group": 1
		},
		{
			"item_group_name": "Mobile Parts",
			"parent_item_group": "Spare Parts",
			"is_group": 0
		},
		{
			"item_group_name": "Laptop Parts",
			"parent_item_group": "Spare Parts",
			"is_group": 0
		},
	]
	
	for group in item_groups:
		if not frappe.db.exists("Item Group", group["item_group_name"]):
			doc = frappe.get_doc({
				"doctype": "Item Group",
				"item_group_name": group["item_group_name"],
				"parent_item_group": group["parent_item_group"],
				"is_group": group["is_group"]
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Item Group: {group['item_group_name']}")


def create_walkin_sources():
	"""Create Walk-in Sources"""
	print("Creating Walk-in Sources...")
	
	sources = [
		{"source_name": "Website", "description": "Customer found us through our website"},
		{"source_name": "Walk-in", "description": "Customer walked into our store directly"},
		{"source_name": "Phone Call", "description": "Customer called us"},
		{"source_name": "Referral", "description": "Referred by existing customer"},
		{"source_name": "Social Media", "description": "Found us on social media (Facebook, Instagram, etc.)"},
		{"source_name": "Google Search", "description": "Found us through Google search"},
		{"source_name": "Advertisement", "description": "Saw our advertisement"},
		{"source_name": "POS Counter", "description": "Walk-in logged from POS counter"},
	]
	
	for source in sources:
		if not frappe.db.exists("Walkin Source", source["source_name"]):
			doc = frappe.get_doc({
				"doctype": "Walkin Source",
				"source_name": source["source_name"],
				"description": source["description"],
				"is_active": 1
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Walk-in Source: {source['source_name']}")


def create_issue_categories():
	"""Create Issue Categories"""
	print("Creating Issue Categories...")
	
	categories = [
		{"category_name": "Screen Issues", "description": "Broken, cracked, or unresponsive screen", "estimated_repair_hours": 2.0},
		{"category_name": "Battery Issues", "description": "Battery draining fast, not charging, or swollen", "estimated_repair_hours": 1.5},
		{"category_name": "Water Damage", "description": "Device exposed to water or liquid", "estimated_repair_hours": 4.0},
		{"category_name": "Software Problems", "description": "OS issues, app crashes, slow performance", "estimated_repair_hours": 2.0},
		{"category_name": "Charging Port Issues", "description": "Charging port not working or loose", "estimated_repair_hours": 1.5},
		{"category_name": "Speaker/Microphone Issues", "description": "Audio problems during calls or playback", "estimated_repair_hours": 2.0},
		{"category_name": "Camera Issues", "description": "Camera not working or blurry", "estimated_repair_hours": 2.5},
		{"category_name": "Button Issues", "description": "Power, volume, or home button not working", "estimated_repair_hours": 1.5},
		{"category_name": "Network Issues", "description": "No signal, WiFi, or Bluetooth problems", "estimated_repair_hours": 3.0},
		{"category_name": "Physical Damage", "description": "Dents, scratches, or broken body", "estimated_repair_hours": 3.0},
	]
	
	for category in categories:
		if not frappe.db.exists("Issue Category", category["category_name"]):
			doc = frappe.get_doc({
				"doctype": "Issue Category",
				"category_name": category["category_name"],
				"description": category["description"],
				"estimated_repair_hours": category["estimated_repair_hours"],
				"is_active": 1
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Issue Category: {category['category_name']}")


def create_withdrawal_reasons():
	"""Create Withdrawal Reasons"""
	print("Creating Withdrawal Reasons...")
	
	reasons = [
		{"reason_name": "Too Expensive", "reason_type": "Financial Constraint", "description": "Customer found repair cost too high"},
		{"reason_name": "Fixed Elsewhere", "reason_type": "Customer Decision", "description": "Customer got it repaired somewhere else"},
		{"reason_name": "Not Repairable", "reason_type": "Technical Limitation", "description": "Device cannot be repaired"},
		{"reason_name": "Customer Changed Mind", "reason_type": "Customer Decision", "description": "Customer decided not to proceed"},
		{"reason_name": "Buying New Device", "reason_type": "Customer Decision", "description": "Customer decided to buy new device instead"},
		{"reason_name": "Parts Not Available", "reason_type": "Technical Limitation", "description": "Required spare parts not available"},
		{"reason_name": "Takes Too Long", "reason_type": "Customer Decision", "description": "Repair time too long for customer"},
	]
	
	for reason in reasons:
		if not frappe.db.exists("Withdrawal Reason", reason["reason_name"]):
			doc = frappe.get_doc({
				"doctype": "Withdrawal Reason",
				"reason_name": reason["reason_name"],
				"reason_type": reason["reason_type"],
				"description": reason["description"],
				"is_active": 1
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Withdrawal Reason: {reason['reason_name']}")


def create_device_items():
	"""Create sample mobile phone device items"""
	print("Creating Device Items...")
	
	devices = [
		{
			"item_code": "DEVICE-IPHONE-14-PRO",
			"item_name": "Apple iPhone 14 Pro",
			"item_group": "Mobile Phones",
			"brand": "Apple",
			"description": "iPhone 14 Pro - 6.1 inch display",
			"standard_rate": 99999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
		{
			"item_code": "DEVICE-IPHONE-13",
			"item_name": "Apple iPhone 13",
			"item_group": "Mobile Phones",
			"brand": "Apple",
			"description": "iPhone 13 - 6.1 inch display",
			"standard_rate": 69999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
		{
			"item_code": "DEVICE-SAMSUNG-S23",
			"item_name": "Samsung Galaxy S23",
			"item_group": "Mobile Phones",
			"brand": "Samsung",
			"description": "Galaxy S23 - 6.1 inch display",
			"standard_rate": 74999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
		{
			"item_code": "DEVICE-SAMSUNG-S22",
			"item_name": "Samsung Galaxy S22",
			"item_group": "Mobile Phones",
			"brand": "Samsung",
			"description": "Galaxy S22 - 6.1 inch display",
			"standard_rate": 59999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
		{
			"item_code": "DEVICE-ONEPLUS-11",
			"item_name": "OnePlus 11",
			"item_group": "Mobile Phones",
			"brand": "OnePlus",
			"description": "OnePlus 11 - 6.7 inch display",
			"standard_rate": 56999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
		{
			"item_code": "DEVICE-PIXEL-7",
			"item_name": "Google Pixel 7",
			"item_group": "Mobile Phones",
			"brand": "Google",
			"description": "Pixel 7 - 6.3 inch display",
			"standard_rate": 59999.00,
			"is_stock_item": 0,
			"is_sales_item": 0
		},
	]
	
	for device in devices:
		if not frappe.db.exists("Item", device["item_code"]):
			doc = frappe.get_doc({
				"doctype": "Item",
				"item_code": device["item_code"],
				"item_name": device["item_name"],
				"item_group": device["item_group"],
				"brand": device["brand"],
				"description": device["description"],
				"stock_uom": "Nos",
				"is_stock_item": device["is_stock_item"],
				"is_sales_item": device["is_sales_item"],
				"standard_rate": device["standard_rate"]
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Device: {device['item_name']}")


def create_service_items():
	"""Create service items"""
	print("Creating Service Items...")
	
	services = [
		{
			"item_code": "SVC-SCREEN-REPAIR",
			"item_name": "Screen Repair Service",
			"item_group": "Repair Services",
			"description": "Screen replacement and repair service",
			"standard_rate": 3500.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-BATTERY-REPLACEMENT",
			"item_name": "Battery Replacement Service",
			"item_group": "Repair Services",
			"description": "Battery replacement service",
			"standard_rate": 2000.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-WATER-DAMAGE-REPAIR",
			"item_name": "Water Damage Repair",
			"item_group": "Repair Services",
			"description": "Water damage diagnosis and repair",
			"standard_rate": 5000.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-SOFTWARE-REPAIR",
			"item_name": "Software Repair Service",
			"item_group": "Repair Services",
			"description": "OS reinstallation and software fixes",
			"standard_rate": 1500.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-CHARGING-PORT-REPAIR",
			"item_name": "Charging Port Repair",
			"item_group": "Repair Services",
			"description": "Charging port replacement and repair",
			"standard_rate": 2500.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-CAMERA-REPAIR",
			"item_name": "Camera Repair Service",
			"item_group": "Repair Services",
			"description": "Camera replacement and repair",
			"standard_rate": 4000.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-SPEAKER-REPAIR",
			"item_name": "Speaker/Microphone Repair",
			"item_group": "Repair Services",
			"description": "Speaker or microphone replacement",
			"standard_rate": 2000.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
		{
			"item_code": "SVC-DATA-RECOVERY",
			"item_name": "Data Recovery Service",
			"item_group": "Repair Services",
			"description": "Data backup and recovery service",
			"standard_rate": 3000.00,
			"is_stock_item": 0,
			"is_sales_item": 1,
			"gst_hsn_code": "998599"
		},
	]
	
	for service in services:
		if not frappe.db.exists("Item", service["item_code"]):
			doc = frappe.get_doc({
				"doctype": "Item",
				"item_code": service["item_code"],
				"item_name": service["item_name"],
				"item_group": service["item_group"],
				"description": service["description"],
				"stock_uom": "Nos",
				"is_stock_item": service["is_stock_item"],
				"is_sales_item": service["is_sales_item"],
				"standard_rate": service["standard_rate"],
				"gst_hsn_code": service.get("gst_hsn_code")
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Service: {service['item_name']}")


def create_spare_part_items():
	"""Create spare part items"""
	print("Creating Spare Part Items...")
	
	spare_parts = [
		{
			"item_code": "PART-IPHONE14-SCREEN",
			"item_name": "iPhone 14 Pro Screen (OEM)",
			"item_group": "Mobile Parts",
			"brand": "Apple",
			"description": "Original iPhone 14 Pro OLED display",
			"standard_rate": 15000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-IPHONE13-SCREEN",
			"item_name": "iPhone 13 Screen (OEM)",
			"item_group": "Mobile Parts",
			"brand": "Apple",
			"description": "Original iPhone 13 OLED display",
			"standard_rate": 12000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-IPHONE14-BATTERY",
			"item_name": "iPhone 14 Pro Battery (OEM)",
			"item_group": "Mobile Parts",
			"brand": "Apple",
			"description": "Original iPhone 14 Pro battery",
			"standard_rate": 3500.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-IPHONE13-BATTERY",
			"item_name": "iPhone 13 Battery (OEM)",
			"item_group": "Mobile Parts",
			"brand": "Apple",
			"description": "Original iPhone 13 battery",
			"standard_rate": 3000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-SAMSUNG-S23-SCREEN",
			"item_name": "Samsung S23 Screen (Original)",
			"item_group": "Mobile Parts",
			"brand": "Samsung",
			"description": "Original Samsung S23 AMOLED display",
			"standard_rate": 14000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-SAMSUNG-S23-BATTERY",
			"item_name": "Samsung S23 Battery (Original)",
			"item_group": "Mobile Parts",
			"brand": "Samsung",
			"description": "Original Samsung S23 battery",
			"standard_rate": 2800.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-SAMSUNG-S22-SCREEN",
			"item_name": "Samsung S22 Screen (Original)",
			"item_group": "Mobile Parts",
			"brand": "Samsung",
			"description": "Original Samsung S22 AMOLED display",
			"standard_rate": 11000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-ONEPLUS-11-SCREEN",
			"item_name": "OnePlus 11 Screen (Original)",
			"item_group": "Mobile Parts",
			"brand": "OnePlus",
			"description": "Original OnePlus 11 AMOLED display",
			"standard_rate": 9000.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-CHARGING-PORT-USB-C",
			"item_name": "USB-C Charging Port (Generic)",
			"item_group": "Mobile Parts",
			"description": "Generic USB-C charging port connector",
			"standard_rate": 500.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
		{
			"item_code": "PART-CHARGING-PORT-LIGHTNING",
			"item_name": "Lightning Charging Port (iPhone)",
			"item_group": "Mobile Parts",
			"brand": "Apple",
			"description": "iPhone Lightning charging port connector",
			"standard_rate": 800.00,
			"is_stock_item": 1,
			"is_sales_item": 1
		},
	]
	
	for part in spare_parts:
		if not frappe.db.exists("Item", part["item_code"]):
			doc = frappe.get_doc({
				"doctype": "Item",
				"item_code": part["item_code"],
				"item_name": part["item_name"],
				"item_group": part["item_group"],
				"brand": part.get("brand"),
				"description": part["description"],
				"stock_uom": "Nos",
				"is_stock_item": part["is_stock_item"],
				"is_sales_item": part["is_sales_item"],
				"standard_rate": part["standard_rate"],
				"gst_hsn_code": part.get("gst_hsn_code", "85177990")
			})
			doc.insert(ignore_permissions=True)
			print(f"  Created Spare Part: {part['item_name']}")


def create_sample_customers():
	"""Create sample customers for testing"""
	print("Creating Sample Customers...")
	
	customers = [
		{
			"customer_name": "Rajesh Kumar",
			"customer_type": "Individual",
			"mobile_no": "9876543210",
			"email_id": "rajesh.kumar@example.com"
		},
		{
			"customer_name": "Priya Sharma",
			"customer_type": "Individual",
			"mobile_no": "9876543211",
			"email_id": "priya.sharma@example.com"
		},
		{
			"customer_name": "Tech Solutions Pvt Ltd",
			"customer_type": "Company",
			"mobile_no": "9876543212",
			"email_id": "contact@techsolutions.com"
		},
	]
	
	for customer in customers:
		if not frappe.db.exists("Customer", customer["customer_name"]):
			# Create customer
			cust_doc = frappe.get_doc({
				"doctype": "Customer",
				"customer_name": customer["customer_name"],
				"customer_type": customer["customer_type"],
				"customer_group": "Individual" if customer["customer_type"] == "Individual" else "Commercial",
				"territory": "India"
			})
			cust_doc.insert(ignore_permissions=True)
			
			# Create contact
			contact_doc = frappe.get_doc({
				"doctype": "Contact",
				"first_name": customer["customer_name"].split()[0],
				"mobile_no": customer["mobile_no"],
				"email_id": customer["email_id"],
				"links": [{
					"link_doctype": "Customer",
					"link_name": customer["customer_name"]
				}]
			})
			contact_doc.insert(ignore_permissions=True)
			
			print(f"  Created Customer: {customer['customer_name']}")


if __name__ == "__main__":
	create_sample_data()
