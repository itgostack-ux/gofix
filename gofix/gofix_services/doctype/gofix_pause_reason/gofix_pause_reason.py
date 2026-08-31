# Copyright (c) 2026, GoFix and contributors

"""Why a repair is not being worked on right now.

A paused job is the most expensive thing in a workshop, and "On Hold" on its
own says nothing a manager can act on. Separating a technician's break from
waiting on a part from waiting on the customer is what makes the pause time
readable: the first is capacity, the second is procurement, the third is not
the workshop's problem at all. Keeping them as a master rather than a hardcoded
Select means a branch can add its own without a deployment.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class GoFixPauseReason(Document):
	def validate(self):
		self.reason_name = " ".join((self.reason_name or "").split())
		if not self.reason_name:
			frappe.throw(_("Reason is required."), title=_("Validation Error"))
