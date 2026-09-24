# Copyright (c) 2026, GoStack and contributors

"""One row per thing that happened to a spare on a repair.

The spare lines themselves only ever show their *current* state, so a part
that was requested, failed QC, went to the damaged bin and was replaced left
no trace of any of it once the replacement was fitted. This is the durable
record behind that: append-only, never edited, and written from one place
(``gofix.gofix_services.spare_qc.log_event``) so every writer produces the
same shape.

Damage attribution is the event type, not a separate field. A part that never
passed QC cannot have been damaged by the technician fitting it, so the two
questions the floor actually asks -- "did it arrive unusable?" and "did we
break it?" -- are answered by ``QC Failed`` versus ``Damaged by Technician``.
Keeping them as one vocabulary avoids a fourth way of saying "damaged" on top
of the three the spare doctypes already carry.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class GoFixSpareEventLog(Document):
    def validate(self):
        # Append-only. An edited history is not a history, and these rows are
        # the evidence behind a part being written off or charged to a store.
        if not self.is_new():
            frappe.throw(
                _("Spare event log rows record what happened and cannot be edited."),
                title=_("Read Only"),
            )
