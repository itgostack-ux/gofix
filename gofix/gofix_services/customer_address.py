# Copyright (c) 2026, GoFix and contributors
# Server-side helpers for CH Customer Address management.

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import cint, cstr


BILLING_TYPES = ("Billing", "Both")
ADDRESS_LINE_MAX_LENGTH = 240


def validate_single_active_address(doc, method=None):
	"""Enforce single active address per type (Billing / Shipping / Both) on Customer save.

	If two rows of the same type are both marked is_active=1 this raises a clear error
	rather than silently accepting invalid state.
	"""
	billing_addresses = doc.get("billing_addresses") or []
	if not billing_addresses:
		return

	normalise_customer_address_rows(doc)

	active_billing = []
	active_shipping = []

	for row in billing_addresses:
		if not row.get("is_active"):
			continue
		rtype = row.get("address_type") or "Billing"
		if rtype in ("Billing", "Both"):
			active_billing.append(row.address_line1 or row.name)
		if rtype in ("Shipping", "Both"):
			active_shipping.append(row.address_line1 or row.name)

	if len(active_billing) > 1:
		frappe.throw(
			_("Only one Billing address can be marked Active at a time. "
			  "Currently active: {0}").format(", ".join(active_billing)),
			title=_("Duplicate Active Billing Address"),
		)

	if len(active_shipping) > 1:
		frappe.throw(
			_("Only one Shipping address can be marked Active at a time. "
			  "Currently active: {0}").format(", ".join(active_shipping)),
			title=_("Duplicate Active Shipping Address"),
		)


def normalise_customer_address_rows(doc, method=None):
	"""Fill derived fields and activate the first billing row during imports.

	Data Import does not run client-side fetches/defaults, so imported rows can
	land with no active billing address and stale city/state display fields.
	"""
	billing_addresses = doc.get("billing_addresses") or []
	if not billing_addresses:
		return

	first_billing = None
	has_active_billing = False
	for row in billing_addresses:
		_apply_child_row_updates(row, _normalised_address_values(row))
		row_type = row.get("address_type") or "Billing"
		if row_type in BILLING_TYPES and first_billing is None:
			first_billing = row
		if row.get("is_active") and row_type in BILLING_TYPES:
			has_active_billing = True

	if first_billing and not has_active_billing:
		first_billing.is_active = 1


def sync_standard_customer_address(doc, method=None):
	"""Mirror the active CH billing address into ERPNext Address.

	Data Import must finish persisting the Customer before an Address can safely
	link to it. Successful imported customers are reconciled in one pass by
	``on_data_import_change``.
	"""
	if getattr(frappe.flags, "in_import", False):
		return

	customer = doc.name if hasattr(doc, "name") else cstr(doc)
	if customer:
		sync_customer_address(customer)


def on_data_import_change(doc, method=None):
	"""Finalize Customer imports once all Data Import rows have a log entry."""
	if doc.reference_doctype != "Customer":
		return
	if doc.status not in ("Success", "Partial Success"):
		return
	if not _data_import_is_complete(doc):
		return

	try:
		customer_names = frappe.get_all(
			"Data Import Log",
			filters={"data_import": doc.name, "success": 1},
			pluck="docname",
		)
		customer_names = list(dict.fromkeys(filter(None, customer_names)))
		if customer_names:
			sync_customer_addresses(customer_names=customer_names)
	except Exception:
		frappe.log_error(
			title=f"Customer address import sync failed for {doc.name}",
			message=frappe.get_traceback(),
		)


def sync_customer_addresses(customer_names=None, commit=False, commit_every=500, limit=None):
	"""Sync CH Customer Address rows for selected or all existing customers."""
	if not _table_exists("CH Customer Address"):
		return {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}

	if customer_names is None:
		customer_names = _customers_with_ch_addresses(limit=limit)
	else:
		customer_names = list(dict.fromkeys(filter(None, customer_names)))

	summary = {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}
	for customer in customer_names:
		if not frappe.db.exists("Customer", customer):
			summary["skipped"] += 1
			continue
		try:
			status = sync_customer_address(customer)
			summary[status] = summary.get(status, 0) + 1
			summary["processed"] += 1
			if commit and summary["processed"] % commit_every == 0:
				frappe.db.commit()
		except Exception:
			summary["errors"] += 1
			frappe.log_error(
				title=f"Customer address sync failed for {customer}",
				message=frappe.get_traceback(),
			)

	if commit:
		frappe.db.commit()
	return summary


def sync_customer_address(customer):
	"""Sync one Customer's active CH billing address to standard Address."""
	if not _table_exists("CH Customer Address"):
		return "skipped"

	customer_doc = frappe.db.get_value(
		"Customer",
		customer,
		["name", "customer_name", "customer_primary_address"],
		as_dict=True,
	)
	if not customer_doc:
		return "skipped"

	rows = frappe.get_all(
		"CH Customer Address",
		filters={
			"parent": customer,
			"parenttype": "Customer",
			"parentfield": "billing_addresses",
		},
		fields=[
			"name",
			"idx",
			"address_title",
			"address_type",
			"is_active",
			"address_line1",
			"address_line2",
			"city",
			"city_name",
			"state",
			"state_code",
			"pincode",
			"country",
			"gstin",
		],
		order_by="is_active desc, idx asc, creation asc",
	)
	if not rows:
		return "skipped"

	active_row = _pick_active_billing_row(rows)
	if not active_row:
		return "skipped"

	_normalise_db_child_row(active_row)
	_deactivate_conflicting_rows(rows, active_row)
	address_name, created = _upsert_standard_address(customer_doc, active_row)
	_set_customer_primary_address(customer, address_name)
	frappe.clear_document_cache("Customer", customer)
	return "created" if created else "updated"


def _pick_active_billing_row(rows):
	for row in rows:
		if cint(row.is_active) and (row.address_type or "Billing") in BILLING_TYPES:
			return row

	for row in rows:
		if (row.address_type or "Billing") in BILLING_TYPES:
			frappe.db.set_value(
				"CH Customer Address",
				row.name,
				"is_active",
				1,
				update_modified=False,
			)
			row.is_active = 1
			return row

	return None


def _deactivate_conflicting_rows(rows, active_row):
	for row in rows:
		if row.name == active_row.name or not cint(row.is_active):
			continue
		if _types_overlap(row.address_type or "Billing", active_row.address_type or "Billing"):
			frappe.db.set_value(
				"CH Customer Address",
				row.name,
				"is_active",
				0,
				update_modified=False,
			)


def _normalise_db_child_row(row):
	updates = _normalised_address_values(row)
	if updates:
		frappe.db.set_value("CH Customer Address", row.name, updates, update_modified=False)
		row.update(updates)


def _normalised_address_values(row):
	updates = {}
	pincode = _row_value(row, "pincode")
	city = _row_value(row, "city")
	state = _row_value(row, "state")

	if pincode and _table_exists("CH Pincode"):
		pin = frappe.db.get_value("CH Pincode", pincode, ["city", "state"], as_dict=True)
		if pin:
			if not city and pin.city:
				updates["city"] = pin.city
				city = pin.city
			if not state and pin.state:
				updates["state"] = pin.state
				state = pin.state

	if city and _table_exists("CH City"):
		city_doc = frappe.db.get_value("CH City", city, ["city_name", "state"], as_dict=True)
		if city_doc:
			if city_doc.city_name and _row_value(row, "city_name") != city_doc.city_name:
				updates["city_name"] = city_doc.city_name
			if not state and city_doc.state:
				updates["state"] = city_doc.state
				state = city_doc.state

	if state and _table_exists("CH State"):
		state_code = frappe.db.get_value("CH State", state, "state_code")
		if state_code and _row_value(row, "state_code") != state_code:
			updates["state_code"] = state_code

	return updates


def _upsert_standard_address(customer_doc, row):
	address_name = _usable_primary_address(customer_doc.name, customer_doc.customer_primary_address)
	if not address_name:
		address_name = _existing_linked_billing_address(customer_doc.name)

	if address_name:
		_update_standard_address(address_name, customer_doc, row)
		created = False
	else:
		address_name = _create_standard_address(customer_doc, row)
		created = True

	_mark_primary_address(customer_doc.name, address_name)
	return address_name, created


def _usable_primary_address(customer, address_name):
	if not address_name or not frappe.db.exists("Address", address_name):
		return None
	if _address_link_exists(address_name, customer):
		return address_name
	return None


def _existing_linked_billing_address(customer):
	rows = frappe.db.sql(
		"""
		SELECT a.name
		  FROM `tabAddress` a
		  JOIN `tabDynamic Link` dl ON dl.parent = a.name
		 WHERE dl.parenttype = 'Address'
		   AND dl.link_doctype = 'Customer'
		   AND dl.link_name = %s
		   AND a.address_type IN ('Billing', 'Office')
		 ORDER BY a.is_primary_address DESC, a.modified DESC
		 LIMIT 1
		""",
		customer,
	)
	return rows[0][0] if rows else None


def _create_standard_address(customer_doc, row):
	address = frappe.new_doc("Address")
	_fill_standard_address(address, customer_doc, row)
	address.append("links", {"link_doctype": "Customer", "link_name": customer_doc.name})
	address.flags.ignore_mandatory = True
	_insert_or_retry_without_pincode(address)
	return address.name


def _update_standard_address(address_name, customer_doc, row):
	address = frappe.get_doc("Address", address_name)
	_fill_standard_address(address, customer_doc, row)
	if not _address_link_exists(address.name, customer_doc.name):
		address.append("links", {"link_doctype": "Customer", "link_name": customer_doc.name})
	address.flags.ignore_mandatory = True
	_save_or_retry_without_pincode(address)


def _fill_standard_address(address, customer_doc, row):
	line1, line2 = _standard_address_lines(
		row.address_line1,
		row.address_line2,
		customer_doc.customer_name or customer_doc.name,
	)
	address.address_title = row.address_title or customer_doc.customer_name or customer_doc.name
	address.address_type = "Billing"
	address.address_line1 = line1
	address.address_line2 = line2
	address.city = _display_city(row)
	address.state = row.state or ""
	address.pincode = row.pincode or ""
	address.country = row.country or "India"
	address.gstin = row.gstin or ""
	address.is_primary_address = 1
	address.is_shipping_address = 0


def _insert_or_retry_without_pincode(address):
	try:
		address.insert()
	except frappe.ValidationError as exc:
		if not _is_postal_code_error(exc):
			raise
		address.pincode = ""
		address.insert()


def _save_or_retry_without_pincode(address):
	try:
		address.save()
	except frappe.ValidationError as exc:
		if not _is_postal_code_error(exc):
			raise
		address.pincode = ""
		address.save()


def _is_postal_code_error(exc):
	text = cstr(exc)
	return "Postal Code" in text or "postal code" in text or "pincode" in text.lower()


def _standard_address_lines(address_line1, address_line2, fallback):
	line1 = _clean_address_text(address_line1) or _clean_address_text(fallback) or "Address Missing"
	line2 = _clean_address_text(address_line2)
	if len(line1) > ADDRESS_LINE_MAX_LENGTH:
		overflow = line1[ADDRESS_LINE_MAX_LENGTH:].strip()
		line1 = line1[:ADDRESS_LINE_MAX_LENGTH].rstrip()
		line2 = " ".join(part for part in (overflow, line2) if part).strip()
	if len(line2) > ADDRESS_LINE_MAX_LENGTH:
		line2 = line2[:ADDRESS_LINE_MAX_LENGTH].rstrip()
	return line1, line2


def _clean_address_text(value):
	return " ".join(cstr(value).replace("\r", " ").replace("\n", " ").split())


def _mark_primary_address(customer, address_name):
	linked_addresses = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
	)
	for linked_address in linked_addresses:
		frappe.db.set_value(
			"Address",
			linked_address,
			"is_primary_address",
			1 if linked_address == address_name else 0,
			update_modified=False,
		)


def _set_customer_primary_address(customer, address_name):
	frappe.db.set_value(
		"Customer",
		customer,
		{
			"customer_primary_address": address_name,
			"primary_address": get_address_display(address_name) or "",
		},
		update_modified=False,
	)


def _address_link_exists(address_name, customer):
	return frappe.db.exists(
		"Dynamic Link",
		{
			"parenttype": "Address",
			"parent": address_name,
			"link_doctype": "Customer",
			"link_name": customer,
		},
	)


def _display_city(row):
	if row.city and _table_exists("CH City"):
		city_name = frappe.db.get_value("CH City", row.city, "city_name")
		if city_name:
			return city_name
	return row.city_name or row.city or ""


def _customers_with_ch_addresses(limit=None):
	sql = """
		SELECT DISTINCT a.parent
		  FROM `tabCH Customer Address` a
		  JOIN `tabCustomer` c ON c.name = a.parent
		 WHERE a.parenttype = 'Customer'
		   AND a.parentfield = 'billing_addresses'
		 ORDER BY a.parent
	"""
	if limit:
		sql += " LIMIT %(limit)s"
		return frappe.db.sql_list(sql, {"limit": cint(limit)})
	return frappe.db.sql_list(sql)


def _data_import_is_complete(doc):
	payload_count = cint(doc.payload_count)
	if not payload_count:
		return True
	log_count = frappe.db.count("Data Import Log", {"data_import": doc.name})
	return log_count >= payload_count


def _row_value(row, fieldname):
	return cstr(row.get(fieldname)).strip()


def _apply_child_row_updates(row, updates):
	for fieldname, value in updates.items():
		row.set(fieldname, value)


def _table_exists(doctype):
	return frappe.db.table_exists(doctype)
