"""Two documents, not four.

A repair produces exactly two pieces of paper, and each answers one question:

* **Job Sheet** — handed over when the customer gives us the device. It records
  the state the device arrived in, what they told us, what they consented to,
  and their signature under all of it. Deliberately quotes no price: nothing has
  been examined yet.
* **Service Invoice** — handed over when they collect it, and only once it has
  been billed. It carries the charges, the tax, the warranty on the work, the
  handover checklist and the signature confirming the device came back working.

Four formats had grown up between those two. ``GoFix Repair Charge Sheet`` was
referenced by no code at all, and ``GoFix Delivery Receipt`` only by two tests
asserting it existed — so a counter printing "the invoice" could produce any of
three documents depending on which one they picked, each showing a different
subset of the same job.

The two retired ones are **disabled, not deleted**: they are standard formats
shipped by the app, a site may have printed them historically, and disabling is
reversible while a delete is not. What the delivery receipt did well -- the
warranty wording, the handover checklist, the signature block -- moved onto the
invoice, which is now the document the customer actually leaves with.
"""

import frappe

JOB_SHEET = "GoFix Job Sheet"
SERVICE_INVOICE = "GoFix Service Invoice"
RETIRED = ("GoFix Repair Charge Sheet", "GoFix Delivery Receipt")

# The old name, before it became the Job Sheet.
_RENAMED_FROM = "GoFix Intake Receipt"


def configure_gofix_print_formats():
	# The rename: keep any customisation someone made to the old record rather
	# than leaving two half-live formats behind.
	if frappe.db.exists("Print Format", _RENAMED_FROM):
		if frappe.db.exists("Print Format", JOB_SHEET):
			frappe.delete_doc("Print Format", _RENAMED_FROM,
			                  force=1, ignore_permissions=True)
		else:
			frappe.rename_doc("Print Format", _RENAMED_FROM, JOB_SHEET,
			                  force=True, ignore_permissions=True)

	for name in RETIRED:
		if frappe.db.exists("Print Format", name):
			frappe.db.set_value("Print Format", name, "disabled", 1,
			                    update_modified=False)

	_repoint_service_request_default()

	frappe.clear_cache()


def _repoint_service_request_default():
	"""Desk's own print view honours the doctype default.

	``Service Request`` pointed at the Repair Charge Sheet, so opening
	/print/Service Request/<name> landed on a format we have just disabled --
	Frappe falls back to Standard and the counter sees a bare field dump. The
	Job Sheet is the Service Request's document, so it becomes the default.
	"""
	ps = "Service Request-main-default_print_format"
	if frappe.db.get_value("Property Setter", ps, "value") in RETIRED:
		frappe.db.set_value("Property Setter", ps, "value", JOB_SHEET,
		                    update_modified=False)
