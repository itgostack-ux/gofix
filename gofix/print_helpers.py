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
from frappe import _

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


def issue_rows(doc) -> list:
	"""Every fault on the ticket, named and attributed.

	The sheet used to print ``issue_category`` -- ONE field -- while the ticket
	carried a whole ``issue_lines`` table. A customer who reported three faults
	got a job sheet mentioning one of them, which is the document they would
	later be held to.

	Ordered customer-first: what they told us at the counter comes before what
	we found afterwards, because that is the order the conversation happened in.
	"""
	rows = []
	for r in (doc.get("issue_lines") or []):
		by_customer = (r.get("reported_by") or "") != "Technician"
		who = _("Customer") if by_customer else (
			r.get("reported_by_technician_name")
			or frappe.db.get_value("Employee", r.get("reported_by_technician"), "employee_name")
			or person_name(r.get("owner"))
			or _("Technician"))
		rows.append({
			"category": r.get("issue_category") or "",
			"by_customer": by_customer,
			"who": who,
			"description": (r.get("description") or "").strip(),
			"status": r.get("status") or "",
		})
	rows.sort(key=lambda x: (not x["by_customer"],))
	return rows


def accessory_list(doc) -> str:
	"""What came in with the device.

	The structured list when the counter filled one in, the free-text field
	otherwise -- both exist and only one is usually populated.
	"""
	listed = [r.accessory for r in (doc.get("accessories_list") or []) if r.get("accessory")]
	if listed:
		return ", ".join(listed)
	return (doc.get("accessories_received") or "").strip()


def repair_breakup(sr_name) -> dict:
	"""What the repair actually consisted of, line by line.

	The tax invoice lists what was billed -- often one rolled-up line -- which
	answers "how much" but not "for what". A customer collecting a phone asks
	the second question, and the answer already exists on the ticket: the
	solutions carried out and the spares fitted.

	Labour comes from the same pricing engine the estimate and the Ops Hub use,
	so the figures agree with what the customer was quoted. Parts come from the
	spare lines, which carry the rate actually charged rather than a rule.
	"""
	if not sr_name or not frappe.db.exists("Service Request", sr_name):
		return {"lines": [], "spares": [], "labour": 0.0, "parts": 0.0, "total": 0.0}

	sr = frappe.get_doc("Service Request", sr_name)
	sr.check_permission("read")

	dropped = ("Cancelled", "Skipped")
	solutions = [r for r in (sr.get("solution_lines") or [])
	             if r.status not in dropped and r.repair_solution]

	priced = {}
	if solutions:
		try:
			from gofix.gofix_services.doctype.gofix_pricing_rule.gofix_pricing_rule import (
				calculate_estimate_from_rules,
			)
			result = calculate_estimate_from_rules(
				issue_categories=[r.issue_category for r in (sr.get("issue_lines") or [])],
				solutions=[{"repair_solution": r.repair_solution,
				            "issue_category": r.issue_category} for r in solutions],
				brand=sr.get("brand"),
				warranty_status=sr.get("warranty_status"),
				company=sr.get("company"),
				warranty_plan=sr.get("warranty_plan"),
				device_item=sr.get("device_item"),
			)
			for line in (result.get("line_details") or []):
				priced.setdefault(line.get("repair_solution"), line)
		except Exception:
			# A pricing failure must not cost the customer their invoice.
			frappe.log_error(frappe.get_traceback(), "repair_breakup pricing")

	names = {}
	codes = [r.repair_solution for r in solutions]
	if codes:
		names = {r.name: r.solution_name for r in frappe.get_all(
			"Repair Solution", filters={"name": ("in", codes)},
			fields=["name", "solution_name"], limit_page_length=len(codes))}

	lines, labour = [], 0.0
	for r in solutions:
		p = priced.get(r.repair_solution) or {}
		amount = frappe.utils.flt(p.get("labor"))
		labour += amount
		lines.append({
			"solution": names.get(r.repair_solution) or r.repair_solution,
			"issue": r.issue_category or "",
			"technician": r.technician_name or "",
			"minutes": frappe.utils.cint(r.estimated_minutes),
			"status": r.status or "",
			"labour": amount,
		})

	# Anything the engine priced that is not a chosen solution -- the standing
	# service charge, for one -- still has to appear or the total will not add up.
	seen = {r.repair_solution for r in solutions}
	for code, p in priced.items():
		if code in seen:
			continue
		amount = frappe.utils.flt(p.get("labor"))
		labour += amount
		# These come from the rate card as item codes -- the standing service
		# charge is one -- and "GOFIX-SERVICE-CHARGE" is a catalogue key, not
		# something to hand a customer.
		label = (names.get(code)
		         or frappe.db.get_value("Item", code, "item_name")
		         or code)
		lines.append({"solution": label, "issue": "",
		              "technician": "", "minutes": 0, "status": "", "labour": amount})

	spares, parts = [], 0.0
	for r in (sr.get("spare_lines") or []):
		if r.status in dropped:
			continue
		amount = frappe.utils.flt(r.amount) or (frappe.utils.flt(r.qty) * frappe.utils.flt(r.rate))
		parts += amount
		spares.append({
			"item": r.item_name or r.spare_item,
			"issue": r.issue_category or "",
			"qty": frappe.utils.flt(r.qty),
			"uom": r.uom or "",
			"rate": frappe.utils.flt(r.rate),
			"amount": amount,
			"serial": (r.installed_part_serial or "").strip(),
		})

	return {"lines": lines, "spares": spares, "labour": labour, "parts": parts,
	        "total": labour + parts}


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
		"issues": issue_rows(doc),
		"accessories": accessory_list(doc),
		"taken_in_by": person_name(doc.get("intake_executive"))
		               or person_name(doc.get("received_by") or doc.get("accepted_by") or doc.owner),
	}
