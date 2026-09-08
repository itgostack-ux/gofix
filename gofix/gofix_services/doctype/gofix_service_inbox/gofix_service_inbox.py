"""A repair request that has not yet become a ticket.

Why this exists
---------------
Requests arrived from a website form, a mobile app, WhatsApp and the phone, and
there was nowhere for them to land. Whoever took the message either opened a
Service Request on the spot -- committing the shop to a device it had not seen
-- or wrote it down somewhere the system could not see. When the customer later
walked in, the counter had no way to know they had already been in touch, so it
started again from a blank form and the customer repeated themselves.

An inbox is the standard answer: a lightweight record of "someone asked", kept
separate from the ticket, which is the record of "we have their device". Every
service desk works this way -- Zendesk, Freshdesk, Jira Service Management,
ServiceNow all separate the request from the work order, and for the same reason:
the request may never become work, may be a duplicate, or may be spam.

The number is the key. A request is matched to a customer by phone, so when they
arrive at the counter the store types the number they already have and the
history is there -- the enquiry, the device, the fault they described -- instead
of a blank form.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class GoFixServiceInbox(Document):
	def validate(self):
		self.contact_number = normalise_phone(self.contact_number)
		self.alternate_number = normalise_phone(self.alternate_number)
		if not self.received_at:
			self.received_at = now_datetime()
		if not self.company:
			# Company is required and is a fact about the session, not something
			# the person taking a phone call should have to pick.
			self.company = (frappe.defaults.get_user_default("Company")
			                or frappe.db.get_single_value("Global Defaults", "default_company"))
		self._match_customer()
		self._guard_conversion()

	def _match_customer(self):
		"""Recognise a returning customer by their number.

		Phone is the identity key across this bench -- customer names are not
		unique -- so the match is on mobile_no, not on the name they gave.
		"""
		if self.customer or not self.contact_number:
			return
		match = frappe.db.get_value(
			"Customer", {"mobile_no": self.contact_number}, ["name", "customer_name"])
		if match:
			self.customer = match[0]
			if not self.customer_name:
				self.customer_name = match[1]

	def _guard_conversion(self):
		"""Converted means a ticket exists. It must not be claimed without one."""
		if self.status == "Converted" and not self.service_request:
			frappe.throw(
				_("This request cannot be marked Converted without a Service Request. "
				  "Convert it from the counter so the ticket and the request stay linked."),
				title=_("Nothing To Convert To"))

	def add_note(self, note, channel=None):
		self.append("notes", {
			"note_datetime": now_datetime(), "channel": channel or self.channel,
			"noted_by": frappe.session.user, "note": note})


def normalise_phone(number) -> str:
	"""Reduce a number to its last ten digits.

	The same customer reaches us as +91 98404 22782 from WhatsApp, 09840422782
	from a web form and 9840422782 at the counter. Matching on the raw string
	means three records and a counter that recognises none of them.
	"""
	digits = "".join(c for c in str(number or "") if c.isdigit())
	return digits[-10:] if len(digits) >= 10 else digits
