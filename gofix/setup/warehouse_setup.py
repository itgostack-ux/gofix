# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

"""
Warehouse Setup for Multi-Store Service Management
Creates warehouse structure and links addresses
"""

import frappe
from frappe import _

from gofix.config import require_role_setting


def create_warehouse_structure(company, stores=None):
	"""Create warehouse hierarchy for GoFix stores"""
	
	if not company:
		frappe.throw(_("Company is required"), title=_("Validation Error"))
	
	warehouses = []
	
	# Create parent group
	parent_warehouse = create_warehouse(
		warehouse_name="All GoFix Warehouses",
		company=company,
		is_group=1
	)
	warehouses.append(parent_warehouse)
	
	# Create Master Hub
	master_hub = create_warehouse(
		warehouse_name="Master Hub - Repair Center",
		company=company,
		parent_warehouse=parent_warehouse,
		is_group=0
	)
	warehouses.append(master_hub)
	
	# Set as company's master hub warehouse
	frappe.db.set_value("Company", company, "master_hub_warehouse", master_hub)
	
	for store in frappe.parse_json(stores) if stores else []:
		store_name = (store.get("name") or "").strip()
		if not store_name:
			frappe.throw(_("Every store entry must include a name."))
		store_wh = create_warehouse(
			warehouse_name=store_name,
			company=company,
			parent_warehouse=parent_warehouse,
			is_group=0
		)
		warehouses.append(store_wh)
	
	frappe.msgprint(
		_("Created {0} warehouses successfully").format(len(warehouses)),
		title=_("Warehouse Setup Complete"),
		indicator="green"
	)
	
	return warehouses


def create_warehouse(warehouse_name, company, parent_warehouse=None, is_group=0):
	"""Create or get existing warehouse"""
	
	company_abbr = frappe.get_cached_value("Company", company, "abbr")
	warehouse_id = f"{warehouse_name} - {company_abbr}"
	
	if frappe.db.exists("Warehouse", warehouse_id):
		return warehouse_id
	
	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = warehouse_name
	warehouse.company = company
	warehouse.is_group = is_group
	warehouse.parent_warehouse = parent_warehouse
	
	warehouse.insert()
	return warehouse.name


@frappe.whitelist(methods=["POST"])
def setup_warehouses_for_company(company, stores=None) -> dict:
	"""Public API to setup warehouses"""
	require_role_setting(
		"warehouse_setup_roles",
		("System Manager",),
		action=_("set up service warehouses"),
	)
	return create_warehouse_structure(company, stores)


def link_address_to_warehouse(warehouse, address):
	"""Link an address to a warehouse"""
	
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} does not exist").format(warehouse), title=_("Validation Error"))
	
	if not frappe.db.exists("Address", address):
		frappe.throw(_("Address {0} does not exist").format(address), title=_("Validation Error"))
	
	# Update warehouse with address
	frappe.db.set_value("Warehouse", warehouse, "address", address)
	
	frappe.msgprint(
		_("Address {0} linked to Warehouse {1}").format(address, warehouse),
		indicator="green"
	)


@frappe.whitelist(methods=["POST"])
def create_store_address(warehouse, address_line1, city, state, pincode, country="India") -> dict:
	"""Create and link address for a store warehouse"""
	
	require_role_setting(
		"warehouse_setup_roles",
		("System Manager",),
		action=_("create a store address"),
	)
	
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} does not exist").format(warehouse), title=_("Validation Error"))
	
	# Get company from warehouse
	company = frappe.get_cached_value("Warehouse", warehouse, "company")
	
	# Create address
	address = frappe.new_doc("Address")
	address.address_title = warehouse
	address.address_type = "Warehouse"
	address.address_line1 = address_line1
	address.city = city
	address.state = state
	address.pincode = pincode
	address.country = country
	
	# Link to company
	address.append("links", {
		"link_doctype": "Company",
		"link_name": company
	})
	
	address.insert()
	
	# Link address to warehouse
	frappe.db.set_value("Warehouse", warehouse, "address", address.name)
	
	frappe.msgprint(
		_("Address {0} created and linked to {1}").format(address.name, warehouse),
		title=_("Address Created"),
		indicator="green"
	)
	
	return address.name


@frappe.whitelist(methods=["POST"])
def set_user_default_warehouse(user, warehouse) -> None:
	"""Set default warehouse for a user"""
	
	require_role_setting(
		"warehouse_setup_roles",
		("System Manager",),
		action=_("set a user's default warehouse"),
	)
	
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist").format(user), title=_("Validation Error"))
	
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} does not exist").format(warehouse), title=_("Validation Error"))
	
	# Set user default
	frappe.defaults.set_user_default("warehouse", warehouse, user)
	
	# Also get company from warehouse and set that
	company = frappe.get_cached_value("Warehouse", warehouse, "company")
	if company:
		frappe.defaults.set_user_default("Company", company, user)
	
	frappe.msgprint(
		_("Default warehouse {0} set for user {1}").format(warehouse, user),
		indicator="green"
	)


@frappe.whitelist()
def get_warehouse_details(warehouse) -> dict:
	"""Get warehouse details including address and state"""
	
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} does not exist").format(warehouse), title=_("Validation Error"))
	
	wh = frappe.get_doc("Warehouse", warehouse)
	
	details = {
		"warehouse": wh.name,
		"warehouse_name": wh.warehouse_name,
		"company": wh.company,
		"warehouse_type": wh.warehouse_type,
		"is_group": wh.is_group,
		"parent_warehouse": wh.parent_warehouse,
		"address": wh.address,
		"state_name": None,
		"state_code": None,
		"city": None,
		"pincode": None
	}
	
	# Get address details
	if wh.address:
		address = frappe.get_doc("Address", wh.address)
		details.update({
			"address_line1": address.address_line1,
			"city": address.city,
			"state_name": address.state,
			"state_code": address.gst_state_number,
			"pincode": address.pincode,
			"country": address.country
		})
	
	return details


def validate_warehouse_setup(company):
	"""Validate if warehouse setup is complete"""
	
	warehouses = frappe.get_all(
		"Warehouse",
		filters={"company": company},
		fields=["name", "warehouse_name", "address", "warehouse_type"]
	)
	
	if not warehouses:
		frappe.msgprint(
			_("No warehouses found for company {0}. Please run warehouse setup.").format(company),
			indicator="red"
		)
		return False
	
	# Check for master hub
	master_hub = frappe.get_cached_value("Company", company, "default_warehouse")
	if not master_hub:
		frappe.msgprint(
			_("Master Hub warehouse not set in company {0}").format(company),
			indicator="orange"
		)
	
	# Check which warehouses don't have addresses
	no_address = [w.warehouse_name for w in warehouses if not w.address]
	if no_address:
		frappe.msgprint(
			_("Following warehouses don't have addresses: {0}").format(", ".join(no_address)),
			indicator="orange"
		)
	
	return True
