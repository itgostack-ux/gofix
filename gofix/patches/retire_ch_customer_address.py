"""Move legacy child addresses to ERPNext Address and retire the duplicate table."""

import frappe
from frappe.contacts.doctype.address.address import get_address_display
from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields
from frappe.utils import cint, cstr


def execute():
	if frappe.db.table_exists("CH Customer Address"):
		_migrate_rows()

	delete_custom_fields({"Customer": ["billing_addresses", "billing_addresses_section"]})
	if frappe.db.exists("DocType", "CH Customer Address"):
		frappe.delete_doc("DocType", "CH Customer Address", force=True, ignore_permissions=True)
	if frappe.db.table_exists("CH Customer Address"):
		frappe.db.sql_ddl("DROP TABLE `tabCH Customer Address`")
	frappe.clear_cache(doctype="Customer")


def _migrate_rows():
	rows = frappe.db.sql(
		"""
		SELECT a.name, a.parent, a.idx, a.address_title, a.address_type, a.is_active,
		       a.address_line1, a.address_line2, a.city, a.city_name, a.state,
		       a.state_code, a.pincode, a.country, a.gstin,
		       c.customer_name, c.customer_primary_address,
		       existing.name AS existing_address,
		       existing.address_line1 AS existing_line1,
		       existing.city AS existing_city,
		       existing.pincode AS existing_pincode
		  FROM `tabCH Customer Address` a
		  JOIN `tabCustomer` c ON c.name = a.parent
		  LEFT JOIN `tabAddress` existing ON existing.name = c.customer_primary_address
		 WHERE a.parenttype = 'Customer' AND a.parentfield = 'billing_addresses'
		 ORDER BY a.parent, a.idx, a.creation
		""",
		as_dict=True,
	)
	by_customer = {}
	for row in rows:
		by_customer.setdefault(row.parent, []).append(row)

	for customer, customer_rows in by_customer.items():
		customer_name = customer_rows[0].customer_name or customer
		primary = None
		first_billing = None
		for row in customer_rows:
			row_type = row.address_type or "Billing"
			is_billing = row_type in ("Billing", "Both")
			if is_billing and first_billing is None:
				first_billing = row
			address_name = _find_or_create(customer, customer_name, row)
			if is_billing and cint(row.is_active):
				primary = address_name
		if not primary and first_billing:
			primary = _find_or_create(customer, customer_name, first_billing)
		if primary and primary != customer_rows[0].customer_primary_address:
			frappe.db.set_value(
				"Customer",
				customer,
				{
					"customer_primary_address": primary,
					"primary_address": get_address_display(primary) or "",
				},
				update_modified=False,
			)


def _find_or_create(customer, customer_name, row):
	line1 = _clean(row.address_line1) or customer_name
	line2 = _clean(row.address_line2)
	city = _city_name(row)
	if (
		row.existing_address
		and _clean(row.existing_line1) == line1
		and cstr(row.existing_city) == cstr(city)
		and cstr(row.existing_pincode) == cstr(row.pincode)
	):
		return row.existing_address
	linked = frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Address",
			"link_doctype": "Customer",
			"link_name": customer,
		},
		pluck="parent",
		limit_page_length=0,
	)
	if linked:
		existing = frappe.db.get_value(
			"Address",
			{"name": ("in", linked), "address_line1": line1, "city": city, "pincode": row.pincode or ""},
			"name",
		)
		if existing:
			return existing

	address = frappe.new_doc("Address")
	address.address_title = row.address_title or customer_name
	address.address_type = "Shipping" if row.address_type == "Shipping" else "Billing"
	address.address_line1 = line1[:240]
	address.address_line2 = line2[:240]
	address.city = city or "Not Specified"
	address.state = row.state or ""
	address.pincode = row.pincode or ""
	address.country = row.country or "India"
	address.is_primary_address = cint(row.is_active and row.address_type in (None, "", "Billing", "Both"))
	address.is_shipping_address = cint(row.is_active and row.address_type in ("Shipping", "Both"))
	if address.meta.has_field("gstin"):
		address.gstin = row.gstin or ""
	if address.meta.has_field("gst_state_number"):
		address.gst_state_number = row.state_code or ""
	address.append("links", {"link_doctype": "Customer", "link_name": customer})
	address.flags.ignore_mandatory = True
	address.insert(ignore_permissions=True)
	return address.name


def _city_name(row):
	if row.city_name:
		return row.city_name
	if row.city and frappe.db.table_exists("CH City"):
		return frappe.db.get_value("CH City", row.city, "city_name") or row.city
	return row.city or ""


def _clean(value):
	return " ".join(cstr(value).replace("\r", " ").replace("\n", " ").split())
