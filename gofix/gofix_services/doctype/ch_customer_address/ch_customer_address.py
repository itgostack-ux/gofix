"""Compatibility controller used only while the retirement patch migrates old rows.

There is intentionally no DocType JSON in source and no runtime hook.  Existing
production databases still need this import to load Customer documents before
``retire_ch_customer_address`` removes the legacy Table field and DocType.
"""

from frappe.model.document import Document


class CHCustomerAddress(Document):
	pass
