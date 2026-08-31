# Copyright (c) 2026, CH and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class GoFixDevicePhoto(Document):
	"""One photograph of a customer device, at intake or at handover.

	The pair is the point: what the device looked like when the counter took
	it in, and what it looked like before the customer was billed. A dispute
	about a scratch that "was not there before" is settled by the two rows,
	which is why ``stage`` and ``captured_at`` are not optional.
	"""
	pass
