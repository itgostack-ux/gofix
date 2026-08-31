# Copyright (c) 2026, GoFix and contributors

"""One change to the completion time promised to a customer.

The promise is the number the customer plans their day around, so moving it is
an event with an owner and a reason — not a silent field update. Every row here
is immutable once written; the current promise lives on the Service Request.
"""

from frappe.model.document import Document


class GoFixETARevision(Document):
	pass
