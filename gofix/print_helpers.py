"""Everything the printed documents need that the Service Request does not hold.

A job sheet is the only thing the customer walks out with, and for a chain it
has to look like it came from a branch rather than from a database: the branch's
own address and phone, the company's GSTIN, a scannable link to the repair. None
of that lives on the Service Request -- the request stores a warehouse id, and
``GF-ALWARTHIRUNAGAR-Sellable`` is not an address you can post a phone to.

Computed here rather than in the template because print formats cannot call
whitelisted methods, and because a Jinja expression that reaches three doctypes
deep is not something anyone can debug from a browser.
"""

import base64
import io

import frappe

# Where the branch details come from. CH Store is the one doctype that carries
# company, warehouse, city, state and a phone number together -- see the store
# geo sync: geography lives on CH Store, never on Warehouse.
_STORE_FIELDS = ("store_name", "store_code", "address", "city", "state",
                 "pincode", "contact_phone")


def _branch(warehouse):
	"""The counter this device was handed in at, as a customer would address it."""
	if not warehouse:
		return {}
	name = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")
	if not name:
		# Warehouses are suffixed with the company abbreviation and the bin type;
		# the store code is the stable part in front of both.
		code = warehouse.split(" - ")[0].rsplit("-", 1)[0]
		name = frappe.db.get_value("CH Store", {"store_code": code}, "name")
	if not name:
		return {}
	row = frappe.db.get_value("CH Store", name, _STORE_FIELDS, as_dict=True) or {}
	line2 = ", ".join([p for p in (row.get("city"), row.get("state")) if p])
	if row.get("pincode"):
		line2 = f"{line2} {row['pincode']}".strip()
	row["address_line_2"] = line2
	return row


def _company(company):
	if not company:
		return {}
	row = frappe.db.get_value(
		"Company", company, ["name", "gstin", "phone_no", "email", "website"],
		as_dict=True) or {}
	if not row.get("gstin"):
		# Older sites carry the registration on the billing Address instead.
		row["gstin"] = frappe.db.sql("""
			SELECT a.gstin FROM `tabAddress` a
			JOIN `tabDynamic Link` dl ON dl.parent = a.name
			WHERE dl.link_doctype = 'Company' AND dl.link_name = %s
			  AND IFNULL(a.gstin, '') <> '' LIMIT 1""", company)
		row["gstin"] = row["gstin"][0][0] if row["gstin"] else ""
	return row


def qr_data_uri(text, scale=3):
	"""A QR as an inline PNG.

	Inline because a print format is rendered detached from the site -- a
	<img src="/files/..."> is a second request the PDF renderer may not be
	authenticated to make, and an unauthenticated one silently prints a blank.
	"""
	if not text:
		return ""
	try:
		import pyqrcode

		buf = io.BytesIO()
		pyqrcode.create(text, error="M").png(buf, scale=scale, quiet_zone=2)
		return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
	except Exception:
		# A missing QR is a cosmetic loss; a traceback loses the whole document.
		frappe.log_error(frappe.get_traceback(), "job sheet QR")
		return ""


def person_name(user_or_executive):
	"""A name a customer can ask for, never a login or a docname."""
	if not user_or_executive:
		return ""
	if frappe.db.exists("POS Executive", user_or_executive):
		return (frappe.db.get_value("POS Executive", user_or_executive, "executive_name")
		        or user_or_executive)
	return frappe.db.get_value("User", user_or_executive, "full_name") or user_or_executive


def device_label(doc) -> str:
	"""The device as a customer would name it.

	``device_model`` stores the taxonomy path -- "Smart Phones-iOS Phones-Apple-
	Apple iPhone 11 Pro Max" -- which is how the item master files it, not how
	anyone would read it off a receipt. The sold item's own name is best when we
	have one; otherwise take the leaf of the path, and do not put the brand in
	front of a leaf that already starts with it.
	"""
	if doc.get("device_item_name"):
		return doc.device_item_name
	model = (doc.get("device_model") or "").split("-")[-1].strip()
	brand = (doc.get("device_brand") or doc.get("brand") or "").strip()
	if model and brand and not model.lower().startswith(brand.lower()):
		return f"{brand} {model}"
	return model or brand or (doc.get("device_item") or "")


def job_sheet_context(sr_name) -> dict:
	"""Branch, company and tracking QR for one repair's job sheet."""
	doc = frappe.get_doc("Service Request", sr_name)
	from gofix.tracking import tracking_url_for_print

	track = tracking_url_for_print(doc.name) if doc.get("tracking_token") else ""
	return {
		"branch": _branch(doc.get("source_warehouse")),
		"company": _company(doc.get("company")),
		"track_url": track,
		"track_qr": qr_data_uri(track),
		"job_qr": qr_data_uri(doc.name, scale=3),
		"device_label": device_label(doc),
		"taken_in_by": person_name(doc.get("intake_executive"))
		               or person_name(doc.get("received_by") or doc.get("accepted_by") or doc.owner),
	}
