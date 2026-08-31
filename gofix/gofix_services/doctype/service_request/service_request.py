# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import (
	add_days, cint, cstr, flt, getdate, now_datetime, nowdate, today,
)

from gofix.config import get_int_setting, get_setting, is_privileged_user, require_role_setting
from gofix.security import assert_service_request_access


class ServiceRequest(Document):
	_APPROVAL_EVIDENCE_FIELDS = (
		"discount_approved_by",
		"discount_exception_request",
		"substitution_approved_by",
		"substitution_exception_request",
	)

	def _validate_approval_evidence(self):
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if any(self.get(fieldname) for fieldname in self._APPROVAL_EVIDENCE_FIELDS):
				frappe.throw(_("Approval identity and evidence are server-managed."), frappe.PermissionError)
			return
		if any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self._APPROVAL_EVIDENCE_FIELDS
		):
			frappe.throw(
				_("Approval identity and exception evidence can only be changed through authorized actions."),
				frappe.PermissionError,
			)
		if before.discount_approved_by and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in ("service_discount_percent", "service_discount_amount")
		):
			frappe.throw(_("Request a new approval before changing an approved service discount."))
		if before.substitution_approved_by and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in ("original_serial_no", "replacement_serial_no")
		):
			frappe.throw(_("Request a new approval before changing an approved serial substitution."))

	def _approved_exception_actor(self, exception_name):
		if not exception_name:
			return None
		evidence = frappe.db.get_value(
			"CH Exception Request",
			exception_name,
			(
				"status",
				"approver",
				"resolved_by",
				"reference_doctype",
				"reference_name",
			),
			as_dict=True,
		)
		if not evidence:
			frappe.throw(_("The linked exception request does not exist."), frappe.ValidationError)
		if evidence.reference_doctype != self.doctype or evidence.reference_name != self.name:
			frappe.throw(_("The linked exception request belongs to another document."), frappe.PermissionError)
		if evidence.status not in ("Approved", "Auto-Approved"):
			return None
		return evidence.approver or evidence.resolved_by

	# ── Document numbering ────────────────────────────────────────────────
	#
	# SR CC SS LLL YYMMDD NNNN  ->  SRGF331682608310001
	#   |  |  |  |    |     |
	#   |  |  |  |    |     +-- counter, per store per day
	#   |  |  |  |    +-------- date
	#   |  |  |  +------------- location  (CH Store id)
	#   |  |  +---------------- state     (GST state number)
	#   |  +------------------- company   (Company.abbr)
	#   +----------------------- fixed literal
	#
	# Deliberately unseparated and fixed-width (19 chars): the ticket number IS
	# the barcode stuck on the customer's device, and a contiguous alphanumeric
	# string is what scans cleanly and parses by offset. Every segment is
	# zero-padded to a constant length, so the number can be split back apart
	# without a delimiter.
	#
	# Every component is resolved at naming time from master data, and the
	# counter is drawn with frappe's `getseries`, which is an atomic
	# INSERT .. ON DUPLICATE KEY UPDATE on `tabSeries`. Two tickets raised in
	# the same second, in the same store, cannot collide, and a deleted ticket
	# never frees a number for reuse.
	#
	# Existing tickets keep the names they were created with. Renaming live
	# Service Requests would rewrite every Sales Order, Invoice, Job Assignment
	# and Custody Log that points at them for no operational gain, so the change
	# is deliberately forward-only.

	NUMBER_PREFIX = "SR"
	UNKNOWN_COMPANY = "XX"
	UNKNOWN_STATE = "00"
	UNKNOWN_STORE = "000"
	COMPANY_CODE_LEN = 2
	COUNTER_DIGITS = 4

	def autoname(self):
		self.name = self.build_service_request_number()

	def build_service_request_number(self) -> str:
		company_code = self._company_code()
		state_code = self._gst_state_code()
		store_code = self._store_code()
		date_str = now_datetime().strftime("%y%m%d")

		prefix = f"{self.NUMBER_PREFIX}{company_code}{state_code}{store_code}{date_str}"
		counter = cint(getseries(prefix, self.COUNTER_DIGITS))
		return f"{prefix}{counter:0{self.COUNTER_DIGITS}d}"

	def _company_code(self) -> str:
		"""Company abbreviation (GF, BM). Never the company name — it changes."""
		abbr = frappe.db.get_value("Company", self.company, "abbr") if self.company else None
		code = self._sanitise(abbr, self.UNKNOWN_COMPANY)
		# Fixed width, because there is no delimiter to find the boundary with.
		return code.ljust(self.COMPANY_CODE_LEN, "X")[:self.COMPANY_CODE_LEN]

	def _gst_state_code(self) -> str:
		"""GST state number of the store the device was received at.

		Read here rather than from self.state_code: naming runs before
		validate(), so fetch_warehouse_details() has not populated that field
		yet on a new document.
		"""
		code = None
		warehouse = self.source_warehouse
		if warehouse:
			address = frappe.db.get_value("Warehouse", warehouse, "address")
			if address:
				code = frappe.db.get_value("Address", address, "gst_state_number")
		if not code and self.company:
			company_address = frappe.db.get_value(
				"Dynamic Link",
				{"link_doctype": "Company", "link_name": self.company,
				 "parenttype": "Address"},
				"parent",
			)
			if company_address:
				code = frappe.db.get_value("Address", company_address, "gst_state_number")
		code = self._sanitise(code, self.UNKNOWN_STATE)
		return code.rjust(2, "0")[:2]

	def _store_code(self) -> str:
		"""Numeric CH Store id, zero-padded.

		The store's own code (GF-ANNANAGAR) is up to 22 characters and already
		repeats the company abbreviation, so the short numeric id is used to
		keep the ticket number speakable.
		"""
		if not self.source_warehouse:
			return self.UNKNOWN_STORE
		store_id = frappe.db.get_value("CH Store", {"warehouse": self.source_warehouse}, "store_id")
		if not store_id:
			# The ticket may carry a non-Sellable bin; climb to the owning store.
			group = frappe.db.get_value("Warehouse", self.source_warehouse, "parent_warehouse")
			if group:
				store_id = frappe.db.get_value("CH Store", {"warehouse_group": group}, "store_id")
		if not store_id:
			return self.UNKNOWN_STORE
		return str(cint(store_id)).rjust(3, "0")[-3:]

	@classmethod
	def parse_number(cls, name: str) -> dict:
		"""Split a ticket number back into its parts.

		The format carries no delimiter, so this is the counterpart to
		build_service_request_number: it is what proves the fixed widths are
		sufficient. Returns {} for a legacy or unrecognised number rather than
		raising — historic tickets are named SR-YYMMDD-#### and must stay
		readable.
		"""
		text = cstr(name)
		expected = (
			2 + cls.COMPANY_CODE_LEN + 2 + 3 + 6 + cls.COUNTER_DIGITS
		)
		if len(text) != expected or not text.startswith(cls.NUMBER_PREFIX):
			return {}
		i = len(cls.NUMBER_PREFIX)
		company = text[i:i + cls.COMPANY_CODE_LEN]; i += cls.COMPANY_CODE_LEN
		state = text[i:i + 2]; i += 2
		store = text[i:i + 3]; i += 3
		date = text[i:i + 6]; i += 6
		counter = text[i:]
		if not (state.isdigit() and store.isdigit() and date.isdigit() and counter.isdigit()):
			return {}
		return {
			"company_code": company,
			"state_code": state,
			"store_code": store,
			"date": f"20{date[0:2]}-{date[2:4]}-{date[4:6]}",
			"counter": cint(counter),
		}

	@staticmethod
	def _sanitise(value, fallback: str) -> str:
		"""Keep only characters that are safe in a document name."""
		cleaned = "".join(ch for ch in cstr(value).upper() if ch.isalnum())
		return cleaned or fallback

	def before_insert(self):
		"""Set defaults before first insert"""
		self.set_warehouse_defaults()
		self.set_received_by()
		self._init_competitive_ops_fields()
		self.ensure_tracking_token()

	def ensure_tracking_token(self):
		"""Mint the per-document tracking salt; the token digest is derived after naming."""
		if not self.meta.has_field("tracking_token_salt") or self.get("tracking_token_salt"):
			return
		from gofix.tracking import make_tracking_salt
		self.tracking_token_salt = make_tracking_salt()

	def after_insert(self):
		self._store_tracking_token_digest()

	def _store_tracking_token_digest(self):
		"""Persist the digest of the deterministic tracking token once the name exists."""
		if not self.meta.has_field("tracking_token") or self.get("tracking_token"):
			return
		salt = self.get("tracking_token_salt")
		if not salt:
			return
		from gofix.tracking import derive_tracking_token, tracking_token_digest
		token = derive_tracking_token(self.name, salt)
		self.db_set("tracking_token", tracking_token_digest(token), update_modified=False)
	
	def validate(self):
		self._validate_approval_evidence()
		self.detect_customer_type()
		self.detect_visit_type()
		self.check_open_requests()
		self.fetch_customer_details()
		self._sync_customer_id()
		# Fetch warehouse details - especially state for GST
		if self.source_warehouse:
			self.fetch_warehouse_details()
		self.fetch_warranty_from_serial()
		self.validate_withdrawal()
		self.validate_contact_details()
		self.validate_referral_code()
		self.validate_dates()
		self.validate_mandatory_fields()
		self.validate_issue_solution_cascade()
		self._validate_customer_estimate_decision()
		self.sync_decision_to_workflow_state()
		self._validate_serial_substitution()
		self._validate_service_discount()
		self._validate_warranty_claim_cap()
		self._detect_repeat_complaint()

	def _validate_customer_estimate_decision(self):
		if self.is_new() or self.flags.get("customer_estimate_authorized") or self.flags.get("estimate_decision_override"):
			return
		before = self.get_doc_before_save()
		if not before:
			return
		previous = {row.name: row.status for row in (before.get("estimate_versions") or [])}
		for row in self.get("estimate_versions") or []:
			if row.status in ("Customer Approved", "Customer Rejected") and previous.get(row.name) != row.status:
				frappe.throw(
					_("Customer estimate decisions must use the authenticated customer action or audited override endpoint."),
					frappe.PermissionError,
				)

	def before_save(self):
		"""Generate barcode if not exists and fetch warehouse details"""
		self.generate_barcode()
		# Fetch warehouse state details if missing
		if self.source_warehouse and (not self.state_name or not self.state_code):
			self.fetch_warehouse_details()
		# Sync walk-in status with the canonical repair lifecycle.
		self.sync_walkin_status()
		# Log status transitions
		self._log_status_transition()

	def _log_status_transition(self):
		"""Append a GoFix Status Log row whenever the canonical decision changes."""
		old_decision = (self.get_doc_before_save() or {}).get("decision") if self.get_doc_before_save() else None
		new_decision = self.decision
		if not old_decision or old_decision == new_decision:
			return
		from frappe.utils import now_datetime, time_diff_in_hours
		prev_at = None
		lifecycle_rows = [row for row in self.status_log if (row.get("event_type") or "Lifecycle") == "Lifecycle"]
		if lifecycle_rows:
			prev_at = lifecycle_rows[-1].changed_at
		elapsed = 0
		if prev_at:
			elapsed = round(time_diff_in_hours(now_datetime(), prev_at), 2)
		self.append("status_log", {
			"event_type": "Lifecycle",
			"from_status": old_decision,
			"to_status": new_decision,
			"changed_by": frappe.session.user,
			"changed_at": now_datetime(),
			"time_in_previous_status_hours": elapsed,
		})

		# GF-11 fix: Notify customer on key status changes
		self._notify_customer_on_status_change(old_decision, new_decision)

	def on_change(self):
		"""Persist lifecycle audit rows even when controlled actions use db_set."""
		old = self.get_doc_before_save()
		old_decision = old.get("decision") if old else None
		new_decision = self.get("decision")
		if not old_decision or old_decision == new_decision:
			return
		latest = frappe.get_all(
			"GoFix Status Log",
			filters={
				"parent": self.name,
				"parenttype": "Service Request",
				"parentfield": "status_log",
				"event_type": "Lifecycle",
			},
			fields=["from_status", "to_status", "changed_at", "idx"],
			order_by="idx desc",
			limit_page_length=1,
		)
		if latest and latest[0].from_status == old_decision and latest[0].to_status == new_decision:
			return
		from frappe.utils import now_datetime, time_diff_in_hours
		changed_at = now_datetime()
		elapsed = round(time_diff_in_hours(changed_at, latest[0].changed_at), 2) if latest and latest[0].changed_at else 0
		row = frappe.new_doc("GoFix Status Log")
		row.parent = self.name
		row.parenttype = "Service Request"
		row.parentfield = "status_log"
		row.idx = (latest[0].idx if latest else 0) + 1
		row.event_type = "Lifecycle"
		row.from_status = old_decision
		row.to_status = new_decision
		row.changed_by = frappe.session.user
		row.changed_at = changed_at
		row.time_in_previous_status_hours = elapsed
		row.db_insert()
	
	def set_received_by(self):
		"""Set received_by to current user if not set"""
		if not self.received_by:
			self.received_by = frappe.session.user
	
	def set_warehouse_defaults(self):
		"""Set default warehouse from user defaults"""
		if not self.source_warehouse:
			# Get user's default warehouse
			user_warehouse = frappe.defaults.get_user_default("warehouse")
			
			if user_warehouse:
				self.source_warehouse = user_warehouse
			elif self.company:
				# Fallback to first non-group warehouse of company
				warehouse = frappe.db.get_value("Warehouse", 
					{"company": self.company, "is_group": 0},
					"name",
					order_by="name"
				)
				if warehouse:
					self.source_warehouse = warehouse
		
		# Set current location same as source initially
		if self.source_warehouse and not self.current_location:
			self.current_location = self.source_warehouse

	def _init_competitive_ops_fields(self):
		"""Initialize competitive ops fields on new Service Request."""
		if self.source_warehouse:
			if self.meta.has_field("billing_location") and not self.get("billing_location"):
				self.billing_location = self.source_warehouse
			if self.meta.has_field("current_processing_location") and not self.get("current_processing_location"):
				self.current_processing_location = self.source_warehouse
		if self.meta.has_field("repairability_status") and not self.get("repairability_status"):
			self.repairability_status = "Pending Analysis"
	
	def fetch_warehouse_details(self):
		"""Fetch warehouse address and state details"""
		if self.source_warehouse:
			# Get warehouse details
			warehouse = frappe.get_doc("Warehouse", self.source_warehouse)
			
			# Get address from warehouse if linked
			if warehouse.address:
				self.warehouse_address = warehouse.address
				
				# Fetch state details from address
				address = frappe.get_doc("Address", warehouse.address)
				if address.state:
					self.state_name = address.state
				if address.get("gst_state_number"):
					self.state_code = address.get("gst_state_number")
			elif warehouse.company:
				# Fallback: Try to get from company's default address
				try:
					company = frappe.get_doc("Company", warehouse.company)
					# Try to get any linked address for the company
					company_addresses = frappe.get_all("Dynamic Link",
						filters={
							"link_doctype": "Company",
							"link_name": warehouse.company,
							"parenttype": "Address"
						},
						fields=["parent"],
						limit=1)
					if company_addresses:
						address = frappe.get_doc("Address", company_addresses[0].parent)
						if address.state and not self.state_name:
							self.state_name = address.state
						if address.get("gst_state_number") and not self.state_code:
							self.state_code = address.get("gst_state_number")
				except Exception:
					pass  # Skip fallback if company address not found

	_GSTIN_RE = __import__('re').compile(
		r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'
	)

	def detect_customer_type(self):
		"""Auto-classify B2B / B2C based on GSTIN presence.

		B2B  = customer provided a valid 15-character GSTIN.
		B2C  = no GSTIN (retail / individual customer).
		"""
		gstin = (self.gstin or '').strip().upper()
		self.customer_type = 'B2B' if self._GSTIN_RE.match(gstin) else 'B2C'

	def detect_visit_type(self):
		"""Auto-classify customer lifecycle tier (industry-standard CRM segmentation).

		New     — first visit, no prior Service Requests in system.
		Regular — returning customer below the configured VIP threshold.
		VIP     — high-value loyal customer at or above the configured threshold.

		This mirrors the 'Customer Classification' concept in SAP SD / Oracle CX /
		Microsoft Dynamics and is kept separate from customer_type (B2B/B2C) which
		drives tax treatment.
		"""
		if not self.customer:
			self.visit_type = 'New'
			return

		prior_count = frappe.db.count(
			'Service Request',
			filters={'customer': self.customer, 'name': ['!=', self.name or '']}
		)
		vip_threshold = get_int_setting("vip_customer_request_threshold", 10)

		if prior_count == 0:
			self.visit_type = 'New'
		elif prior_count < vip_threshold:
			self.visit_type = 'Regular'
		else:
			self.visit_type = 'VIP'

	def check_open_requests(self):
		"""Check for open service requests for this customer"""
		if self.customer and self.is_new():
			open_requests = frappe.get_all("Service Request",
				filters={
					"customer": self.customer,
					"decision": ["in", ["Draft", "Accepted", "In Service"]],
					"walkin_status": "Accepted"
				},
				fields=["name", "service_date", "device_item_name", "decision"])
			
			self.open_requests_count = len(open_requests)
			
			# Warning if open requests exist
			if open_requests and self.walkin_status == "Accepted":
				request_nos = ", ".join([d.name for d in open_requests])
				frappe.msgprint(
					_("This customer has {0} open service request(s): {1}").format(
						len(open_requests), request_nos),
					title=_("Open Requests Found"),
					indicator="orange"
				)

	def fetch_customer_details(self):
		"""Fetch customer contact details and billing address"""
		if self.customer:
			customer = frappe.get_doc("Customer", self.customer)

			# Fetch primary contact if not provided
			if not self.contact_number or not self.email:
				contacts = frappe.get_all("Dynamic Link",
					filters={
						"link_doctype": "Customer",
						"link_name": self.customer,
						"parenttype": "Contact"
					},
					fields=["parent"],
					limit=1)

				if contacts:
					contact = frappe.get_doc("Contact", contacts[0].parent)
					if not self.contact_number and contact.mobile_no:
						self.contact_number = contact.mobile_no
					if not self.email and contact.email_id:
						self.email = contact.email_id

			# Populate billing address from customer's active billing address
			self._fetch_billing_address_from_customer(customer)

	def _fetch_billing_address_from_customer(self, customer=None):
		"""Populate billing_address_display, billing_gstin, billing_state_name,
		billing_state_code from the customer's primary standard Address.
		Only overwrites if the field is empty (preserves manual edits).
		"""
		if not self.meta.has_field("billing_address_display"):
			return  # custom fields not yet installed

		if customer is None:
			if not self.customer:
				return
			customer = frappe.get_doc("Customer", self.customer)

		billing_addr = _get_active_address(customer, "Billing")
		if not billing_addr:
			return

		lines = [
			billing_addr.address_line1,
			billing_addr.address_line2,
			", ".join(filter(None, [billing_addr.city, billing_addr.state, billing_addr.pincode])),
		]
		display = "\n".join(l for l in lines if l)

		self.billing_address_display = display
		if billing_addr.gstin:
			self.billing_gstin = billing_addr.gstin
		if billing_addr.state:
			self.billing_state_name = billing_addr.state
		if billing_addr.state_code:
			self.billing_state_code = billing_addr.state_code

		# Mirror to shipping when same_as_billing
		if self.meta.has_field("same_as_billing"):
			if self.get("same_as_billing") or self.get("same_as_billing") is None:
				if self.meta.has_field("shipping_address_display"):
					self.shipping_address_display = display

	def fetch_warranty_from_serial(self):
		"""Fetch warranty status from CH Sold Plan via ch_item_master warranty API.
		Falls back to Serial No warranty_expiry_date if no sold plans exist."""
		# Skip if warranty was already set by a warranty claim
		if self.flags.get("skip_warranty_fetch"):
			return
		if not self.serial_no:
			if not self.warranty_status:
				self.warranty_status = "No Warranty"
			return

		# For walk-in repairs the device's IMEI may not yet be registered in
		# the Serial No table (first-time customer).  Skip validation and
		# warranty fetch gracefully — the serial can be registered later.
		if not frappe.db.exists("Serial No", self.serial_no):
			if not self.warranty_status:
				self.warranty_status = "No Warranty"
			return

		serial = frappe.get_doc("Serial No", self.serial_no)

		# Validate serial belongs to the selected device item (only if serial is known)
		if self.device_item and serial.item_code != self.device_item:
			frappe.throw(_("Serial No {0} does not belong to Item {1}").format(
				self.serial_no, self.device_item))

		# Try CH Sold Plan lookup via warranty API
		try:
			from ch_item_master.ch_item_master.warranty_api import check_warranty
			result = check_warranty(serial_no=self.serial_no, company=self.company)

			if result.get("warranty_covered"):
				self.warranty_status = "Under Warranty"
				covering = result.get("covering_plan") or {}
				self.warranty_plan = covering.get("warranty_plan")
				self.warranty_plan_name = covering.get("plan_title")
				self.warranty_deductible = covering.get("deductible_amount")
				self.warranty_expiry_date = covering.get("end_date")
				# Capture the specific Active VAS Plans row so on_submit can
				# increment claims_used on the correct policy. The master
				# warranty_plan link alone is insufficient — a serial can carry
				# multiple plans (own + extended) and only one is being consumed.
				self.active_warranty_plan = covering.get("name")
			else:
				# No active sold plan — fallback to Serial No expiry
				self._fallback_warranty_from_serial(serial)
		except ImportError:
			# ch_item_master not installed — use basic Serial No lookup
			self._fallback_warranty_from_serial(serial)
		except frappe.PermissionError:
			# The VAS scope guard fails closed when it cannot establish which
			# company a serial belongs to. For a serial that was never sold with
			# a plan -- an internal GoFix unit, a walk-in device -- that is the
			# expected answer, not a fault: there is no cover to find. Falling
			# back is right; logging it as an error was not.
			#
			# It had written 448 Error Log rows, which is the real damage: a log
			# full of an expected condition is a log nobody reads, and the
			# genuine failures below were sitting in it unnoticed.
			self._fallback_warranty_from_serial(serial)
		except Exception:
			# Anything else -- a database or configuration fault -- is a real
			# error and still logged, but must not block intake.
			frappe.log_error(frappe.get_traceback(), f"Warranty lookup failed for {self.serial_no}")
			self._fallback_warranty_from_serial(serial)

	def _notify_customer_on_status_change(self, old_status, new_status):
		"""GF-11: Send email/SMS notification to customer on key status transitions."""
		# Only notify on customer-relevant transitions
		notify_statuses = {
			"Received", "Diagnosis Complete", "Repair In Progress",
			"QC Pass", "Ready for Delivery", "Delivered",
			"Not Repairable", "Customer Cancelled",
			"Rejected", "Cancelled",
		}
		if new_status not in notify_statuses:
			return

		customer_email = self.get("customer_email") or self.get("email")
		customer_mobile = self.get("customer_mobile") or self.get("mobile_no")
		customer_name = self.get("customer_name") or "Customer"

		subject = f"Service Request {self.name} — Status Update: {new_status}"
		sr_url = frappe.utils.get_url_to_form("Service Request", self.name)
		message = (
			"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
			"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
			"<div style='padding:16px'>"
			f"<p>Dear {frappe.utils.escape_html(customer_name)},</p>"
			f"<p>Your service request <b>{self.name}</b> for <b>{frappe.utils.escape_html(self.get('item_name', 'your device'))}</b> has been updated to: <b>{frappe.utils.escape_html(new_status)}</b>.</p>"
			f"<p><b>Previous Status:</b> {frappe.utils.escape_html(old_status or 'N/A')}</p>"
			f"<p><a href='{sr_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Request</a></p>"
			"</div></div>"
		)

		if customer_email:
			try:
				frappe.sendmail(
					recipients=[customer_email],
					subject=subject,
					message=message,
					reference_doctype="Service Request",
					reference_name=self.name,
					now=True,
				)
			except Exception:
				frappe.log_error(frappe.get_traceback(),
								f"SR {self.name} customer email notification failed")

		if customer_mobile:
			try:
				from frappe.core.doctype.sms_settings.sms_settings import send_sms
				sms_text = f"GoFix: Service Request {self.name} status updated to {new_status}."
				send_sms([customer_mobile], sms_text)
			except Exception:
				pass  # SMS is optional

	def _fallback_warranty_from_serial(self, serial):
		"""Basic warranty check from Serial No.warranty_expiry_date (legacy fallback)."""
		if serial.warranty_expiry_date:
			self.warranty_expiry_date = serial.warranty_expiry_date
			if getdate(serial.warranty_expiry_date) >= getdate(today()):
				self.warranty_status = "Under Warranty"
			else:
				self.warranty_status = "Out of Warranty"
		else:
			self.warranty_status = "No Warranty"

	def validate_dates(self):
		"""Validate service and completion dates"""
		# Prevent future dates for service request
		if getdate(self.service_date) > getdate(today()):
			frappe.throw(_("Service Request Date cannot be in the future"), title=_("Service Request Error"))
		
		# Validate expected completion date
		if self.expected_completion_date and self.service_date:
			if getdate(self.expected_completion_date) < getdate(self.service_date):
				frappe.throw(_("Expected Completion Date cannot be before Service Request Date"), title=_("Service Request Error"))
		
		# Validate received datetime
		if self.received_datetime:
			from frappe.utils import get_datetime, now_datetime
			if get_datetime(self.received_datetime) > now_datetime():
				frappe.throw(_("Received Date & Time cannot be in the future"), title=_("Service Request Error"))
			
			# Validate expected completion datetime >= received datetime
			expected_completion_time = self.get("expected_completion_time")
			if self.expected_completion_date and expected_completion_time:
				expected_dt = get_datetime(f"{self.expected_completion_date} {expected_completion_time}")
				if expected_dt < get_datetime(self.received_datetime):
					frappe.throw(_("Expected Completion Date & Time must be after Received Date & Time"), title=_("Service Request Error"))
		
		# Validate actual completion date
		if self.get("actual_completion_date") and self.service_date:
			if getdate(self.get("actual_completion_date")) < getdate(self.service_date):
				frappe.throw(_("Actual Completion Date cannot be before Service Request Date"), title=_("Service Request Error"))

	def _sync_customer_id(self):
		"""Populate ch_customer_id / ch_membership_id from Customer master."""
		if not self.customer or (self.get("ch_customer_id") and self.get("ch_membership_id")):
			return
		cust = frappe.db.get_value(
			"Customer", self.customer,
			["ch_customer_id", "ch_membership_id"],
			as_dict=True,
		)
		if cust:
			if not self.get("ch_customer_id"):
				self.ch_customer_id = cust.ch_customer_id
			if not self.get("ch_membership_id"):
				self.ch_membership_id = cust.ch_membership_id

	def validate_withdrawal(self):
		"""Validate withdrawal reason is provided if status is withdrawn"""
		if self.walkin_status == "Withdrawn":
			if not self.withdrawal_reason:
				frappe.throw(_("Withdrawal Reason is mandatory when Walk-in Status is Withdrawn"), title=_("Service Request Error"))
			
			# Auto-cancel if withdrawn.
			if self.decision != "Cancelled":
				self.decision = "Cancelled"
	
	def sync_walkin_status(self):
		"""Auto-sync walk-in status based on decision"""
		# If decision is Accepted, set walk-in status to Accepted (customer left device)
		if self.decision == "Accepted" and not self.walkin_status:
			self.walkin_status = "Accepted"
		# If decision is Draft and walk-in status is not explicitly set, leave it blank
		elif self.decision == "Draft" and self.walkin_status == "Accepted":
			# Don't auto-clear if user explicitly set it
			pass
		# If rejected/cancelled, clear walk-in status unless explicitly withdrawn
		elif self.decision in ["Rejected", "Cancelled"] and self.walkin_status == "Accepted":
			self.walkin_status = None
	
	def sync_decision_to_workflow_state(self):
		"""Keep Frappe Workflow state aligned with the canonical lifecycle."""
		if self.decision:
			if self.meta.has_field("workflow_state"):
				self.workflow_state = self.decision
	
	def on_update_after_submit(self):
		"""Handle post-submission updates - mainly for Accept/Reject"""
		# Raise the Service Order once the job is actually sellable — diagnosed,
		# repairable, and quoted at a price the customer approved.
		#
		# "Accepted" alone is NOT that moment. A device checked in at the counter
		# is accepted the instant it is handed over, long before anyone knows
		# what the repair costs, so keying off the decision alone made every save
		# during Analysis fail on the prerequisite gate. Ask whether the chain is
		# complete instead, and stay quiet while it isn't.
		if self.decision == "Accepted" and not self.service_order:
			from gofix.gofix_services.orchestration import can_create_service_order

			if can_create_service_order(self):
				self.create_service_order()

		if self.is_completed_status() and not self.flags.get("skip_completion_artifacts"):
			self.ensure_completion_artifacts()
			# INT-1 fix: Sync completion back to linked Warranty Claim
			self._sync_warranty_claim_completion()
			# INT-2 fix: Update serial lifecycle status to "Repaired"
			self._update_serial_lifecycle_on_completion()

		# The handset goes home to its owner, so it leaves our custody. Without
		# this the Customer Device bin only ever grows and a count of what we
		# are holding stops meaning anything.
		if self.decision == "Delivered":
			from gofix.customer_device_stock import release_customer_device

			release_customer_device(self)

	def is_completed_status(self):
		return self.decision == "Completed"

	def resolve_repair_warranty_days(self) -> int:
		"""Warranty the customer actually gets on this repair.

		Market convention: the cover is the SHORTEST of the workmanship warranty
		on the repairs performed and the warranty on the parts fitted, so an
		aftermarket screen shortens the cover even when the labour is guaranteed
		for longer. Falls back to the site default when nothing is configured.
		"""
		terms = []

		for row in self.get("solution_lines") or []:
			if row.status in ("Cancelled", "Skipped") or not row.repair_solution:
				continue
			days = frappe.db.get_value("Repair Solution", row.repair_solution, "warranty_days")
			if days:
				terms.append(int(days))

		for row in self.get("spare_lines") or []:
			if row.status in ("Returned", "Damaged") or not row.spare_item:
				continue
			days = frappe.db.get_value("Item", row.spare_item, "gofix_part_warranty_days")
			if days:
				terms.append(int(days))

		if terms:
			return min(terms)
		return get_int_setting("default_repair_warranty_days", 30)

	def ensure_completion_artifacts(self):
		"""Create the billing and stock artifacts expected at repair completion."""
		completion_date = self.get("actual_completion_date") or today()

		if self.meta.has_field("actual_completion_date") and not self.get("actual_completion_date"):
			self.db_set("actual_completion_date", completion_date, update_modified=False)
			self.set("actual_completion_date", completion_date)

		if self.meta.has_field("repair_warranty_expiry") and not self.repair_warranty_expiry:
			warranty_days = self.repair_warranty_days or self.resolve_repair_warranty_days()
			self.db_set(
				"repair_warranty_expiry",
				add_days(completion_date, warranty_days),
				update_modified=False,
			)
			self.repair_warranty_expiry = add_days(completion_date, warranty_days)

		if not self.get("service_invoice"):
			# Auto-invoice only when the device is back at its home store (or the
			# customer has approved off-store billing via OTP) — otherwise defer
			# to the store's POS / Ops Hub billing after the return transfer.
			from gofix.gofix_services.api import billing_location_status

			loc = billing_location_status(self)
			if loc["at_home_store"] or loc["otp_verified"]:
				self.create_service_invoice()
			else:
				self.add_comment(
					"Comment",
					_(
						"Invoice deferred — device is at {0}, billing happens at home store {1} "
						"(or with customer OTP consent)."
					).format(loc["device_at"] or _("in transit"), loc["home_store"]),
				)

		# Inventory is posted only by submitted Spare Parts Usage documents.

	def _sync_warranty_claim_completion(self):
		"""INT-1: When Service Request is completed, mark the linked Warranty Claim as repair-complete."""
		try:
			claim_name = frappe.db.get_value(
				"CH Warranty Claim", {"service_request": self.name, "docstatus": 1}, "name"
			)
			if not claim_name:
				return
			claim = frappe.get_doc("CH Warranty Claim", claim_name)
			if claim.claim_status in ("Ticket Created", "In Repair", "Sent to Manufacturer"):
				claim.mark_repair_complete(
					remarks=_("Auto-completed via Service Request {0}").format(self.name)
				)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				_("Failed to sync warranty claim for SR {0}").format(self.name),
			)

	def _update_serial_lifecycle_on_completion(self):
		"""INT-2: Update serial lifecycle status to 'Repaired' when repair is completed."""
		serial_no = self.get("serial_no")
		if not serial_no:
			return
		try:
			from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
				update_lifecycle_status_for_document as update_lifecycle_status,
			)
			update_lifecycle_status(
				serial_no=serial_no,
				new_status="Repaired",
				company=self.company,
				remarks=_("Repair completed via Service Request {0}").format(self.name),
			)
		except ImportError:
			pass  # ch_item_master not installed
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				_("Failed to update serial lifecycle for SR {0}").format(self.name),
			)

	def _resolve_service_item(self):
		"""Pick the Item to bill the repair against.

		This used to be ``get_value("Item", {"is_stock_item": 0})`` — the first
		non-stock Item MariaDB happened to return, with no ordering and no
		filters. Device *templates* are non-stock too, and they outnumber real
		service items roughly 64:1, so the query would routinely return a
		template and acceptance died inside ``set_missing_values()`` with
		"Item X is a template, please select one of its variants" — naming an
		item unrelated to the ticket.

		The choice is explicit rather than inferred, because "non-stock sales
		Item" does not mean "repair item" here: almost every such Item on
		these sites is a warranty/VAS plan product (GoCare, OnsiteGo,
		AppleCare, GoAssure), and billing a repair to one would misstate
		revenue. Order of preference:

		  1. a service item the Service Request itself declares;
		  2. ``Company.gofix_default_service_item``.

		If none resolves we raise rather than guess.
		"""
		declared = next(
			(r.service_item for r in (self.get("service_items") or []) if r.service_item),
			None,
		)
		if declared and self._is_billable_item(declared):
			return declared

		configured = None
		if self.company and frappe.db.has_column("Company", "gofix_default_service_item"):
			configured = frappe.db.get_value(
				"Company", self.company, "gofix_default_service_item"
			)
		if configured and self._is_billable_item(configured):
			return configured

		frappe.throw(
			_(
				"No repair service item is configured for {0}, so the Service "
				"Order cannot be billed.<br><br>Set <b>Default Repair Service "
				"Item</b> on the Company or declare a service item on this request."
			).format(frappe.bold(self.company or "-")),
			title=_("Service Item Missing"),
		)

	@staticmethod
	def _is_billable_item(item_code):
		"""True when `item_code` can go on a Sales Order line as-is."""
		row = frappe.db.get_value(
			"Item", item_code, ["has_variants", "disabled"], as_dict=True
		)
		return bool(row) and not row.has_variants and not row.disabled

	def create_service_order(self):
		"""Create the Service Order (Sales Order) for this request, once.

		Idempotent by design: several paths legitimately try to raise the order
		for the same ticket — the post-submit handler, the accept hook, and
		estimate approval — and whichever gets there first wins. Re-reading from
		the database matters because the in-memory doc that calls this is often
		the STALE one: a hook fired during a nested save already wrote the link.
		"""
		existing = self.service_order or frappe.db.get_value(
			"Service Request", self.name, "service_order"
		)
		if existing:
			self.service_order = existing
			return existing

		# Enforce: diagnosis → repairability → estimate approval → SO
		try:
			from gofix.gofix_services.orchestration import validate_so_creation_prerequisites
			validate_so_creation_prerequisites(self)
		except ImportError:
			pass  # orchestration module not yet available
		
		# Create Sales Order as Service Order
		so = frappe.new_doc("Sales Order")
		so.customer = self.customer
		so.company = self.company
		so.transaction_date = frappe.utils.today()
		# Use the expected completion date from Service Request, default to 7 days
		default_delivery_days = get_int_setting("default_service_delivery_days", 7)
		so.delivery_date = self.expected_completion_date or frappe.utils.add_days(
			frappe.utils.today(), default_delivery_days
		)
		
		# Set company address for GST compliance (required for India GST)
		company_address = frappe.db.get_value("Dynamic Link",
			{
				"link_doctype": "Company",
				"link_name": self.company,
				"parenttype": "Address"
			},
			"parent")
		
		if company_address:
			so.company_address = company_address
		
		# Set title
		so.title = f"Service - {self.customer_name} - {self.serial_no or self.device_item_name}"
		
		# Mark as Service Order
		so.is_service_order = 1
		
		# Link to Service Request
		so.service_request = self.name
		
		# Copy Device Information
		so.device_brand = self.brand
		so.device_model = self.device_item_name
		so.imei_serial_no = self.serial_no
		so.device_condition = self.device_condition
		so.device_condition_desc = self.product_condition_desc
		so.accessories_received = self.accessories_received
		
		# Copy Issue Information
		so.issue_category = self.issue_category
		so.issue_description = self.issue_description
		
		# Copy customer-reported issues for comparison with technician findings
		so.customer_reported_issues = self.issue_description
		
		# Copy Security Information
		so.password_pattern = self.password if self.password else ""
		if self.pattern:
			so.password_pattern += f"\nPattern: {self.pattern}" if so.password_pattern else f"Pattern: {self.pattern}"
		so.backup_status = self.backup_info
		so.actual_imei = self.actual_imei
		
		# Copy Service Planning
		so.service_priority = self.priority
		so.warranty_status = self.warranty_status
		so.warranty_expiry_date = self.warranty_expiry_date
		so.warranty_plan = self.warranty_plan
		so.warranty_deductible = self.warranty_deductible
		so.estimated_delivery_date = frappe.utils.add_days(
			frappe.utils.today(), default_delivery_days
		)
		
		# Copy Warehouse/Location — ensure warehouse belongs to the SO company
		_wh = self.source_warehouse
		if _wh and not frappe.db.exists("Warehouse", {"name": _wh, "company": self.company}):
			# Warehouse belongs to a different company — use first matching warehouse
			_wh = frappe.db.get_value("Warehouse",
				{"company": self.company, "is_group": 0, "disabled": 0},
				"name") or None
		so.set_warehouse = _wh
		so.current_location = self.current_location
		so.state_name = self.state_name
		so.state_code = self.state_code
		
		# Set QC Status to Pending
		so.qc_status = "Pending"
		
		# Set Delivery Mode default
		so.delivery_mode = "Pick-up"
		
		# Add service item
		service_item = self._resolve_service_item()

		so.append('items', {
			'item_code': service_item,
			'item_name': f'Service Repair - {self.device_item_name}',
			'description': self.issue_description,
			'qty': 1.0,
			'rate': float(self.estimated_cost or 0),
			'warehouse': _wh
		})
		
		# Let ERPNext set missing values (company address, tax template, etc.)
		so.set_missing_values()
		
		# Save and link
		try:
			frappe.has_permission("Sales Order", "create", throw=True)
			so.insert()
			
			# Update Service Request with Service Order link using db_set (document is submitted)
			self.db_set("service_order", so.name, update_modified=False)
			
			frappe.msgprint(_("Service Order {0} created successfully").format(so.name),
				title=_("Success"),
				indicator="green")
			
			return so.name
		except Exception as exc:
			frappe.log_error(frappe.get_traceback(), f"Error creating Service Order for {self.name}")
			# Say WHY. "Review the server error log" sent a counter clerk to a
			# place they cannot reach for a message that was usually a plain
			# validation error they could have fixed themselves — a device
			# condition the Sales Order did not accept, a missing address.
			detail = frappe.utils.strip_html(str(exc)).strip()
			frappe.throw(
				_("The Service Order could not be created: {0}").format(
					detail or _("no reason was reported; see the server error log.")),
				title=_("Service Order Not Created"),
			)

	def calculate_costs(self):
		"""Calculate total costs from service items and spare parts"""
		total_estimated = 0
		
		# Calculate from service items
		for item in self.service_items:
			if item.estimated_cost:
				total_estimated += flt(item.estimated_cost)
		
		# Planned spares are child rows of the Service Request aggregate.
		for part in self.get("spare_lines") or []:
			if part.rate and part.qty and part.status not in ("Returned", "Damaged"):
				part.amount = flt(part.rate) * flt(part.qty)
				total_estimated += flt(part.amount)
		
		self.total_estimated_cost = total_estimated

	def on_submit(self):
		"""Actions on submission"""
		if self.is_completed_status():
			self.ensure_completion_artifacts()
		# Consume the linked Active VAS Plans entitlement (SAP CS / Oracle
		# Service pattern: submitting a Service Order on a covered device draws
		# down the coverage counter). Idempotent via CH VAS Ledger lookup, and
		# skipped when a CH Warranty Claim is present so the claim's own
		# closure remains the single consumption event.
		self._consume_active_warranty_plan()

	def on_cancel(self):
		"""Cancel related documents"""
		# Cancel service invoice if exists
		if self.service_invoice:
			invoice = frappe.get_doc("Sales Invoice", self.service_invoice)
			if invoice.docstatus == 1:
				frappe.throw(_("Please cancel Sales Invoice {0} first").format(self.service_invoice), title=_("Service Request Error"))

		# Spare Parts Usage owns each standard Stock Entry. The aggregate cannot
		# be cancelled while any submitted inventory voucher remains live.
		stock_entries = frappe.get_all(
			"Spare Parts Usage",
			filters={"service_request": self.name, "docstatus": 1, "stock_entry": ("is", "set")},
			pluck="stock_entry",
		)
		for stock_entry_name in stock_entries:
			if frappe.db.get_value("Stock Entry", stock_entry_name, "docstatus") == 1:
				frappe.throw(
					_("Please cancel Stock Entry {0} first").format(stock_entry_name),
					title=_("Service Request Error"),
				)

		# Cancellation audit trail: log to CH VAS Ledger only when this SR
		# had previously consumed a coverage entitlement. We deliberately do
		# NOT decrement claims_used — once a Service Order consumed coverage,
		# reversal requires a compensating manual entry (same pattern SAP CS
		# uses for confirmed service order cancellations). Ops can reconcile
		# via CH VAS Ledger reports when a genuine over-count occurred.
		if self.active_warranty_plan:
			had_claim = frappe.db.exists(
				"CH VAS Ledger",
				{
					"sold_plan": self.active_warranty_plan,
					"event_type": "Claim Used",
					"reference_doctype": "Service Request",
					"reference_name": self.name,
				},
			)
			if had_claim:
				try:
					from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event
					log_vas_event(
						sold_plan=self.active_warranty_plan,
						event_type="Claim Reversed",
						reference_doctype="Service Request",
						reference_name=self.name,
						remarks=(
							f"Service Request {self.name} cancelled — "
							"claims_used NOT decremented (manual reconciliation "
							"required if this was a genuine reversal)"
						),
					)
				except Exception:
					frappe.log_error(
						frappe.get_traceback(),
						f"VAS ledger cancel-log failed for {self.name}",
					)

	def _consume_active_warranty_plan(self):
		"""Bump claims_used on the linked Active VAS Plans row.

		Skips when:
		  * ``active_warranty_plan`` is not set (nothing to consume)
		  * warranty_status != Under Warranty (out-of-warranty paid repair)
		  * a ``warranty_claim`` is linked — the CH Warranty Claim's own
		    closure calls ``record_claim`` with the same idempotency key,
		    so letting the SR fire too would risk double-counting on the
		    off-chance the ledger dedup query races.

		Idempotency: ``Active VAS Plans.record_claim`` checks the CH VAS
		Ledger for an existing "Claim Used" row with our (Service Request,
		self.name) reference and skips the bump if found. Safe to call
		multiple times (e.g. after amend + resubmit).
		"""
		if not self.active_warranty_plan:
			return
		if (self.warranty_status or "").strip() != "Under Warranty":
			return
		if self.warranty_claim:
			return  # CH Warranty Claim closure owns the counter for this event.

		try:
			sp = frappe.get_doc("Active VAS Plans", self.active_warranty_plan)
			sp.record_claim(
				service_reference=self.name,
				claim_cost=flt(self.total_estimated_cost or self.estimated_cost or 0),
				reference_doctype="Service Request",
				reference_name=self.name,
			)
		except frappe.DoesNotExistError:
			frappe.log_error(
				f"Service Request {self.name}: linked Active VAS Plan "
				f"{self.active_warranty_plan} no longer exists",
				"VAS Claim Consumption",
			)
			frappe.throw(_("The linked active warranty plan no longer exists."))
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS record_claim failed from Service Request {self.name}",
			)
			frappe.throw(_("Warranty claim usage could not be recorded. Service processing is blocked."))

	@frappe.whitelist()
	def get_device_details(self) -> list:
		"""Fetch device details within this request's customer and store scope."""
		return _get_scoped_device_details(self)

	@frappe.whitelist()
	def get_open_requests(self) -> list:
		"""Get permission-filtered open requests for this customer."""
		return _get_scoped_open_requests(self)

	def create_service_invoice(self):
		"""Create Sales Invoice for completed service with service items and spare parts"""
		if self.get("service_invoice"):
			return
		
		if not self.is_completed_status():
			frappe.throw(_("Service Invoice can only be created for Completed requests"), title=_("Service Request Error"))

		# Last stop before the device goes home: if this repair touched the
		# customer's data, the ticket has to say what happened to it.
		from gofix.compliance import assert_safe_to_hand_back

		assert_safe_to_hand_back(self, _("invoice this repair"))

		# Billing is the last gate, so it re-checks rather than trusting the
		# stage it was reached from. Two conditions, both required: QC actually
		# passed, and every identified issue is closed — fixed or rejected with
		# a reason. Without the second check a ticket could be invoiced with a
		# fault still open, because a Skipped repair used to satisfy the QC gate.
		qc_status = ""
		if self.get("service_order"):
			qc_status = frappe.db.get_value("Sales Order", self.service_order, "qc_status") or ""
		if qc_status != "Pass":
			frappe.throw(
				_("Cannot invoice — QC has not passed on {0} (current: {1}).").format(
					self.service_order or self.name, qc_status or _("not started")
				),
				title=_("QC Not Passed"),
			)

		gaps = get_unresolved_issue_gaps(self)
		if not gaps["ready_for_qc"]:
			frappe.throw(
				_("Cannot invoice — this work is still open: {0}. Every solution that "
				  "was selected must be finished before the customer is billed.").format(
					", ".join(gaps["open_solutions"]) or _("no solutions selected")
				),
				title=_("Open Issues Remain"),
			)
		
		items = self.get_service_invoice_items()
		if not items:
			frappe.throw(_("No service items or spare parts to invoice"), title=_("Service Request Error"))

		posting_date = self.get("actual_completion_date") or today()
		
		# Create invoice
		invoice = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": self.customer,
			"company": self.company,
			"posting_date": posting_date,
			"due_date": posting_date,
			"items": items,
			"remarks": f"Service Invoice for Service Request {self.name}"
		})
		
		# Store-wise P&L: attribute service revenue and spare-part COGS to the
		# servicing store's Cost Center. Without this the invoice inherits
		# Company.cost_center ("Main - BM") and every store's service margin
		# collapses into one bucket.
		from ch_item_master.ch_core.cost_center import apply_cost_center

		apply_cost_center(invoice, warehouse=self.get("source_warehouse"))

		# Apply the advance, net of any refund already posted. An "advances" row
		# must reference a real Payment Entry — ERPNext resolves any non-Journal
		# reference through tabPayment Entry, so a "Service Request" reference can
		# never reconcile. The receipt PE is minted lazily here because the intake
		# flow records only advance_amount (the refund path already posts a PE).
		net_advance = flt(self.advance_amount) - flt(self.get("advance_refund_amount"))
		if net_advance > 0:
			invoice.is_pos = 0

		frappe.has_permission("Sales Invoice", "create", throw=True)
		invoice.insert()

		if net_advance > 0:
			pe = self._ensure_advance_payment_entry(net_advance, posting_date)
			available = flt(pe.unallocated_amount) or flt(pe.paid_amount)
			allocation = min(net_advance, available, flt(invoice.grand_total))
			if allocation > 0:
				invoice.append("advances", {
					"reference_type": "Payment Entry",
					"reference_name": pe.name,
					"remarks": pe.remarks,
					"advance_amount": available,
					"allocated_amount": allocation,
				})
				invoice.save()

		invoice.submit()
		
		self._set_optional_field("service_invoice", invoice.name)
		self.db_set("decision", "Invoiced", update_modified=True)

		from gofix.gofix_services.api import auto_close_service_order_after_billing

		auto_close_service_order_after_billing(service_order=self.service_order)

		frappe.msgprint(_("Service Invoice {0} created successfully").format(invoice.name))

	def _ensure_advance_payment_entry(self, amount, posting_date):
		"""Return the submitted Payment Entry recording this SR's advance receipt,
		minting it lazily when the collection step never posted one.

		Mirrors process_advance_refund's account/mode resolution and is idempotent
		via the advance_payment_entry field and the deterministic reference_no.
		"""
		existing = (self.get("advance_payment_entry") or "").strip()
		if existing and frappe.db.get_value("Payment Entry", existing, "docstatus") == 1:
			return frappe.get_doc("Payment Entry", existing)

		reference_no = f"Advance-{self.name}"
		found = frappe.get_all(
			"Payment Entry",
			filters={
				"payment_type": "Receive",
				"party_type": "Customer",
				"party": self.customer,
				"company": self.company,
				"reference_no": reference_no,
				"docstatus": 1,
			},
			pluck="name",
			order_by="creation asc, name asc",
			limit_page_length=1,
		)
		if found:
			self.db_set("advance_payment_entry", found[0], update_modified=False)
			return frappe.get_doc("Payment Entry", found[0])

		mop_map = {"Cash": "Cash", "UPI": "Cash", "Card": "Cash", "Bank Transfer": "Bank Draft"}
		erp_mode = mop_map.get(self.get("advance_received_via") or "Cash", "Cash")

		company_account = frappe.db.get_value("Company", self.company, "default_cash_account") or \
			frappe.db.get_value("Company", self.company, "default_bank_account")
		if not company_account:
			frappe.throw(_("Please set default Cash or Bank account for company {0}").format(self.company),
				title=_("Advance Receipt Error"))

		try:
			from erpnext.accounts.party import get_party_account
			customer_account = get_party_account("Customer", self.customer, self.company)
		except Exception:
			customer_account = frappe.db.get_value("Company", self.company, "default_receivable_account")
		if not customer_account:
			frappe.throw(_("Please set default Receivable account for company {0}").format(self.company),
				title=_("Advance Receipt Error"))

		from ch_item_master.ch_core.cost_center import apply_cost_center

		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = "Receive"
		pe.party_type = "Customer"
		pe.party = self.customer
		pe.company = self.company
		pe.posting_date = posting_date
		pe.mode_of_payment = erp_mode
		pe.paid_from = customer_account
		pe.paid_from_account_currency = frappe.get_cached_value("Account", customer_account, "account_currency")
		pe.paid_to = company_account
		pe.paid_to_account_currency = frappe.get_cached_value("Account", company_account, "account_currency")
		pe.paid_amount = amount
		pe.received_amount = amount
		pe.reference_no = reference_no
		pe.reference_date = posting_date
		pe.remarks = f"Advance received for Service Request {self.name}"
		apply_cost_center(pe, warehouse=self.get("source_warehouse"))

		frappe.has_permission("Payment Entry", "create", throw=True)
		pe.insert()
		pe.submit()
		self.db_set("advance_payment_entry", pe.name, update_modified=False)
		return pe

	def _service_income_account(self, label: str) -> str | None:
		"""Resolve a service income account for this request's company.

		Returns None when the account does not exist, in which case the line is
		left without an override and ERPNext falls back to the Item Default /
		Company default income account. Never throws — a missing account must
		not block billing a completed repair.
		"""
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		if not abbr:
			return None
		name = f"{label} - {abbr}"
		return name if frappe.db.exists("Account", name) else None

	def get_service_invoice_items(self):
		# Route repair revenue to the service P&L rather than letting it fall
		# through to the company default income account. Without this, service
		# revenue posts to the retail "Sales" account and is indistinguishable
		# from a phone sale, leaving the purpose-built service accounts unused.
		warranty = (self.warranty_status or "").strip()
		labour_account = self._service_income_account(
			"Service Revenue — In Warranty" if warranty == "Under Warranty"
			else "Service Revenue — Out of Warranty"
		)
		spares_account = self._service_income_account("Spare Parts Revenue")

		items = []

		for service_item in self.service_items:
			row = {
				"item_code": service_item.service_item,
				"item_name": service_item.item_name,
				"description": service_item.description or service_item.item_name,
				"qty": 1,
				"rate": service_item.actual_cost or service_item.estimated_cost or 0,
				"uom": "Nos"
			}
			if labour_account:
				row["income_account"] = labour_account
			items.append(row)

		# Billing re-checks the spare's CURRENT disposition; it never trusts the
		# status a caller happens to send.
		#
		# `is_defective` is asserted independently of `part_status` because the
		# invariant that keeps the two aligned (validate(): is_defective =>
		# part_status "Defective") only runs while the document is a draft. A
		# submitted usage goes down the update-after-submit path, where validate()
		# is not re-run — so a part could be flagged defective and still be left
		# reading "Consumed", and it would have been billed to the customer.
		# Damaged stock must never reach an invoice, whichever field says so.
		spare_usages = frappe.get_all(
			"Spare Parts Usage",
			filters={
				"service_request": self.name,
				"docstatus": 1,
				"status": "Active",
				"part_status": ("in", ("Consumed", "Issued")),
				"deleted": 0,
				"is_defective": 0,
			},
			fields=["spare_part_item", "item_name", "qty_used", "sales_price", "uom"],
		)
		for spare_part in spare_usages:
			row = {
				"item_code": spare_part.spare_part_item,
				"item_name": spare_part.item_name,
				"description": spare_part.item_name,
				"qty": spare_part.qty_used,
				"rate": spare_part.sales_price,
				"uom": spare_part.uom
			}
			if spares_account:
				row["income_account"] = spares_account
			items.append(row)

		if items:
			return self._apply_final_cost(items)

		if self.service_order:
			service_order = frappe.get_doc("Sales Order", self.service_order)
			for row in service_order.items:
				item_row = {
					"item_code": row.item_code,
					"item_name": row.item_name,
					"description": row.description or row.item_name,
					"qty": row.qty or 1,
					"rate": row.rate,
					"uom": row.uom,
				}
				# Route it like the other two paths do. Without this the fallback
				# posted repair revenue to the company default (retail "Sales"),
				# which is exactly what the account split above exists to
				# prevent — BMTNSI26000099 credited Sales - BM rather than
				# Service Revenue — Out of Warranty - BM.
				if labour_account:
					item_row["income_account"] = labour_account
				if service_order.docstatus == 1:
					item_row["sales_order"] = service_order.name
					item_row["so_detail"] = row.name
				items.append(item_row)

		return self._apply_final_cost(items)

	def _apply_final_cost(self, items):
		"""Make the invoice total match an agreed final cost.

		``final_cost`` is the negotiated price — a goodwill reduction, or a
		figure the customer accepted after the estimate moved. The Ops Hub has
		always shown it as "Cost to Customer", but the invoice was built purely
		from the estimate lines, so a ticket set to 9,000 still billed 3,500.
		The screen and the invoice disagreed, and the invoice won silently.

		The lines are scaled proportionally rather than collapsed into one, so
		the labour/spares split — and the separate income accounts this class
		deliberately routes them to — survive the adjustment. Rounding lands on
		the last line so the total is exact to the paisa.
		"""
		final_cost = flt(self.get("final_cost") or 0)
		if not final_cost or not items:
			return items

		base_total = sum(flt(row.get("rate")) * flt(row.get("qty") or 1) for row in items)
		if flt(base_total, 2) == flt(final_cost, 2):
			return items

		if not base_total:
			# Nothing to scale against — bill it as a single agreed amount.
			items[0]["qty"] = 1
			items[0]["rate"] = final_cost
			return items

		factor = final_cost / base_total
		running = 0.0
		for row in items[:-1]:
			qty = flt(row.get("qty") or 1)
			row["rate"] = flt(flt(row.get("rate")) * factor, 2)
			running += row["rate"] * qty
		last = items[-1]
		last_qty = flt(last.get("qty") or 1) or 1
		last["rate"] = flt((final_cost - running) / last_qty, 2)
		return items

	def _set_optional_field(self, fieldname, value):
		self.set(fieldname, value)
		if self.meta.has_field(fieldname):
			self.db_set(fieldname, value, update_modified=False)

	def validate_contact_details(self):
		"""Validate mobile number and email format using Frappe built-ins"""
		from buyback.utils import validate_indian_phone
		from frappe.utils import validate_email_address

		if self.contact_number:
			self.contact_number = validate_indian_phone(self.contact_number, "Mobile Number")

		if self.alternate_contact:
			self.alternate_contact = validate_indian_phone(self.alternate_contact, "Alternate Contact")

		# Use Frappe's built-in email validator
		if self.email:
			validate_email_address(self.email, throw=True)

	def validate_courier_details(self):
		"""Validate courier details are mandatory if delivery mode is Courier"""
		if self.get("delivery_mode") == "Courier":
			if not self.get("courier_name"):
				frappe.throw(_("Courier Name is mandatory when Delivery Mode is Courier"), title=_("Service Request Error"))
			if not self.get("delivery_address"):
				frappe.msgprint(_("Warning: Delivery Address is not provided"), 
					indicator="orange", alert=True)

	def validate_referral_code(self):
		"""Validate referral code expiry and customer type"""
		if self.referral_code:
			# Check expiry date
			if self.referral_expiry_date:
				if getdate(self.referral_expiry_date) < getdate(today()):
					frappe.throw(_("Referral Code has expired on {0}").format(
						frappe.format(self.referral_expiry_date, {"fieldtype": "Date"})))
			
			# Referral codes are for new customers only — warn if returning customer
			if self.visit_type and self.visit_type != 'New':
				frappe.msgprint(
					_('Warning: Referral Code is generally applicable for NEW customers only'),
					indicator='orange', alert=True,
				)

	def validate_mandatory_fields(self):
		"""Validate mandatory fields based on Delphi requirements - only for submission"""
		# Only enforce these validations on submit
		if self.docstatus == 0:
			return

		# Skip mandatory checks for withdrawn/cancelled SRs — customer took device back
		if self.walkin_status == "Withdrawn" or self.decision == "Cancelled":
			return
		
		# Product condition description is mandatory for submission
		if not self.product_condition_desc:
			frappe.throw(_("Product Condition Description is mandatory before submission"), title=_("Service Request Error"))
		
		# Backup info is mandatory for submission
		if not self.backup_info:
			frappe.throw(_("Backup Information is mandatory before submission. Please specify what data was backed up."), title=_("Service Request Error"))
		
		# Fault/Issue description is mandatory
		if not self.issue_description:
			frappe.throw(_("Issue Description is mandatory"), title=_("Service Request Error"))
		
		# State name and code mandatory for GST compliance
		if not self.state_name or not self.state_code:
			error_msg = _("State Name and State Code are mandatory for GST compliance")
			
			# Helpful hint if warehouse selected but no address
			if self.source_warehouse:
				warehouse_doc = frappe.get_doc("Warehouse", self.source_warehouse)
				if not warehouse_doc.address:
					error_msg += f"<br><br><b>Solution:</b> Warehouse '{self.source_warehouse}' has no address linked. Please:<br>"
					error_msg += "1. Go to Warehouse master and link an Address with State details, OR<br>"
					error_msg += "2. Manually enter State Name and State Code in this form"
			
			frappe.throw(error_msg, title=_("Service Request Error"))

	def validate_backdating(self):
		"""Control backdating - require approval if more than 3 days old"""
		from frappe.utils import date_diff
		
		if self.is_new():
			days_diff = date_diff(today(), self.service_date)
			
			if days_diff > 3:
				# Check if user has permission to backdate
				if not frappe.has_permission("Service Request", "write", user=frappe.session.user):
					frappe.throw(_("Service Request Date is more than 3 days old. Backdating requires System Manager approval."), title=_("Service Request Error"))
				
				# Log the backdating
				frappe.msgprint(
					_("Warning: Service Request is being backdated by {0} days").format(days_diff),
					indicator="red",
					alert=True
				)

	def validate_delivery_date(self):
		"""Validate delivery date is not before received date"""
		if self.expected_delivery_date and self.service_date:
			if getdate(self.expected_delivery_date) < getdate(self.service_date):
				frappe.throw(_("Expected Delivery Date cannot be before Service Date"), title=_("Service Request Error"))
	
	def validate_issue_solution_cascade(self):
		"""Validate the Issue → Solution → Spare cascade.
		- Each solution must reference an issue_category present in issue_lines
		- Each spare must reference a solution present in solution_lines
		- Each spare must be a mapped spare for that solution (Solution Spare Mapping)
		"""
		issue_lines = self.get("issue_lines") or []
		solution_lines = self.get("solution_lines") or []
		spare_lines = self.get("spare_lines") or []

		# Nothing to validate if no child rows
		if not issue_lines and not solution_lines and not spare_lines:
			return

		# Collect valid issue categories from issue_lines
		valid_issue_categories = set()
		for row in issue_lines:
			if row.issue_category:
				valid_issue_categories.add(row.issue_category)

		# Validate solution_lines: each must have issue_category in issue_lines
		valid_solutions = set()
		for row in solution_lines:
			if not row.repair_solution:
				continue
			valid_solutions.add(row.repair_solution)
			if not row.issue_category:
				# fetch issue_category from the solution master
				row.issue_category = frappe.db.get_value("Repair Solution", row.repair_solution, "issue_category")
			if row.issue_category and row.issue_category not in valid_issue_categories:
				frappe.throw(
					_("Row {0}: Solution '{1}' belongs to issue category '{2}' which is not in the Issue Lines").format(
						row.idx, row.repair_solution, row.issue_category
					)
				)

		# Validate spare_lines: each must reference a solution in solution_lines
		for row in spare_lines:
			if not row.repair_solution:
				continue
			if row.repair_solution not in valid_solutions:
				frappe.throw(
					_("Row {0}: Spare '{1}' references solution '{2}' which is not in the Solution Lines").format(
						row.idx, row.spare_item or "", row.repair_solution
					)
				)
			# Validate spare is an allowed spare for this solution
			if row.spare_item:
				is_mapped = frappe.db.exists("Solution Spare Mapping", {
					"repair_solution": row.repair_solution,
					"spare_item": row.spare_item,
					"is_active": 1
				})
				if not is_mapped:
					frappe.throw(
						_("Row {0}: Spare '{1}' is not a mapped spare for solution '{2}'. Configure it in Solution Spare Mapping.").format(
							row.idx, row.spare_item, row.repair_solution
						)
					)

			# Calculate amount = qty * rate
			row.amount = flt(row.qty) * flt(row.rate)

	def generate_barcode(self):
		"""Auto-generate barcode/IMEI with prefix based on device category"""
		# Only generate if barcode field is empty and not already generated
		if self.is_barcode_generated or self.serial_no:
			return
		
		if not self.device_item:
			return
		
		# Get item details to determine category
		item = frappe.get_cached_doc("Item", self.device_item)
		item_group = item.item_group or ""
		
		# Determine prefix based on item group
		prefix = self.get_barcode_prefix(item_group)
		
		# Generate barcode: PREFIX/YYMMDD#####
		from frappe.utils import now_datetime
		date_str = now_datetime().strftime("%y%m%d")
		
		# Atomic counter + collision check against the Serial No table.
		barcode = self.next_free_barcode(prefix, date_str)
		
		# Set the barcode to serial_no field (or create a new serial no)
		if not self.serial_no:
			self.serial_no = barcode
			self.is_barcode_generated = 1
			
			# Create Serial No document if it doesn't exist
			self.create_serial_no_document(barcode, item)
	
	def get_barcode_prefix(self, item_group):
		"""Determine barcode prefix based on item group"""
		item_group_lower = item_group.lower()
		
		if any(keyword in item_group_lower for keyword in ["mobile", "phone", "smartphone"]):
			return "MO"
		elif any(keyword in item_group_lower for keyword in ["spare", "part", "component"]):
			return "SP"
		elif any(keyword in item_group_lower for keyword in ["tv", "television", "lcd", "led"]):
			return "TV"
		elif any(keyword in item_group_lower for keyword in ["accessory", "accessories", "cable", "charger", "adapter"]):
			return "AC"
		else:
			# Default to MO for general items
			return "MO"
	
	# How many times to step past an already-issued barcode before giving up.
	# A day's worth of collisions on one prefix is far beyond anything real.
	BARCODE_COLLISION_LIMIT = 1000

	def next_free_barcode(self, prefix, date_str) -> str:
		"""The next unissued device barcode for this prefix and date.

		The counter comes from `tabSeries`, which increments atomically, and the
		result is checked against `Serial No` -- the table the barcodes actually
		live in -- before it is handed out.

		The previous implementation derived the next number by scanning
		`tabService Request.serial_no` for the highest value of the day. That is
		the consuming table, not the source of truth, so any barcode whose
		Service Request had been deleted or cancelled was invisible: the scan
		returned nothing, the sequence restarted at 1, and the regenerated
		barcode collided with a live Serial No belonging to a different item.
		Intake then died on "Serial No X does not belong to Item Y" and the
		ticket could not be raised at all. The advisory lock around it never
		helped, because the number it was protecting was wrong before any race.
		"""
		series_key = f"{prefix}/{date_str}"
		for _attempt in range(self.BARCODE_COLLISION_LIMIT):
			sequence = cint(getseries(series_key, 5))
			barcode = f"{prefix}/{date_str}{sequence:05d}"
			if not frappe.db.exists("Serial No", barcode):
				return barcode
			# Already issued -- typically because the series counter is behind
			# the stored maximum after a data restore. Burn it and take the next.
		frappe.throw(
			_("Could not allocate a device barcode for {0} after {1} attempts.").format(
				series_key, self.BARCODE_COLLISION_LIMIT),
			title=_("Barcode Allocation Failed"),
		)

	def get_next_barcode_sequence(self, prefix, date_str):
		"""Deprecated: retained so external callers keep working.

		Returns the numeric part of the next free barcode. Prefer
		``next_free_barcode``, which returns the whole string and is what
		``generate_barcode`` uses.
		"""
		barcode = self.next_free_barcode(prefix, date_str)
		return cint(barcode.rsplit(date_str, 1)[-1])
	
	def create_serial_no_document(self, barcode, item):
		"""Create Serial No document for the generated barcode"""
		try:
			if not frappe.db.exists("Serial No", barcode):
				serial_no = frappe.get_doc({
					"doctype": "Serial No",
					"serial_no": barcode,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"description": f"Auto-generated for Service Request",
					"status": "Active",
					"company": self.company or frappe.defaults.get_user_default("Company")
				})
				frappe.has_permission("Serial No", "create", throw=True)
				serial_no.insert()
				frappe.msgprint(
					_("Barcode {0} generated and Serial No created").format(barcode),
					indicator="green",
					alert=True
				)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Serial No Creation Error")
			frappe.throw(
				_("The barcode could not be registered as a Serial No. Review the server error log."),
				title=_("Serial No Creation Error"),
			)

	# ── Repeat Complaint Detection ───────────────────────────────────

	def _detect_repeat_complaint(self):
		"""Detect a repeat repair within the configured business window.

		Sets is_repeat_complaint flag and links previous SR for audit trail.
		"""
		if not self.serial_no or not self.issue_category:
			return

		# Only check on new requests or when not already flagged
		if self.get("is_repeat_complaint"):
			return

		repeat_window_days = get_int_setting("repeat_repair_window_days", 30)

		previous = frappe.db.sql("""
			SELECT name, service_date, issue_category, decision
			FROM `tabService Request`
			WHERE serial_no = %s
			  AND issue_category = %s
			  AND name != %s
			  AND service_date >= DATE_SUB(%s, INTERVAL %s DAY)
			  AND decision IN ('Completed', 'Delivered', 'Invoiced')
			ORDER BY service_date DESC
			LIMIT 1
		""", (self.serial_no, self.issue_category, self.name or "",
			  self.service_date or today(), repeat_window_days), as_dict=True)

		if previous:
			self._set_optional_field("is_repeat_complaint", 1)
			self._set_optional_field("previous_service_request", previous[0].name)
			frappe.msgprint(
				_("⚠️ Repeat Complaint Detected — Same device & issue category was serviced on {0} "
				  "(SR: {1}). This may indicate incomplete previous repair.").format(
					previous[0].service_date, previous[0].name),
				title=_("Repeat Complaint"),
				indicator="red",
			)

	# ── Exception Framework: Serial Substitution (#8) ────────────────

	def _validate_serial_substitution(self):
		"""If replacement_serial_no is set, require approval."""
		if not self.replacement_serial_no or not self.original_serial_no:
			return
		if self.replacement_serial_no == self.original_serial_no:
			return

		actor = self._approved_exception_actor(self.substitution_exception_request)
		if not actor:
			frappe.throw(
				_("Serial substitution from {0} to {1} requires manager approval.").format(
					frappe.bold(self.original_serial_no),
					frappe.bold(self.replacement_serial_no),
				),
				title=_("Serial Substitution — Approval Required"),
			)
		if self.substitution_approved_by != actor:
			frappe.throw(_("Serial substitution approval identity does not match its exception."), frappe.PermissionError)

	def _create_serial_sub_exception(self):
		"""Create a CH Exception Request for serial substitution."""
		if not frappe.db.exists("CH Exception Type", "Serial Substitution"):
			# GF-2 fix: Warn instead of silently returning
			frappe.msgprint(
				_("CH Exception Type 'Serial Substitution' not found. "
				  "Exception request was not created. Please configure it in CH Item Master."),
				indicator="orange", alert=True,
			)
			return
		try:
			from ch_item_master.ch_item_master.exception_api import raise_exception
			result = raise_exception(
				exception_type="Serial Substitution",
				company=self.company,
				reason=self.substitution_reason or "Serial substitution",
				serial_no=self.original_serial_no,
				reference_doctype="Service Request",
				reference_name=self.name,
				store_warehouse=self.source_warehouse,
				customer=self.customer,
			)
			if result and result.get("name"):
				self.substitution_exception_request = result["name"]
		except ImportError:
			# GF-2 fix: Explicit error when ch_item_master not installed
			frappe.throw(
				_("ch_item_master app is required for serial substitution exceptions but is not installed."),
				title=_("Missing App Dependency"),
			)
		except Exception:
			frappe.log_error("Serial substitution exception creation failed")
			frappe.throw(
				_("Failed to create serial substitution exception request. Check error log."),
				title=_("Exception Request Failed"),
			)

	# ── Exception Framework: Service Discount (#10) ──────────────────

	def _validate_service_discount(self):
		"""If service discount is applied, require approval."""
		if not flt(self.service_discount_percent) and not flt(self.service_discount_amount):
			return

		actor = self._approved_exception_actor(self.discount_exception_request)
		if not actor:
			frappe.throw(
				_("Service discount of {0}% / ₹{1} requires manager approval.").format(
					self.service_discount_percent or 0,
					self.service_discount_amount or 0,
				),
				title=_("Service Discount — Approval Required"),
			)
		if self.discount_approved_by != actor:
			frappe.throw(_("Service discount approval identity does not match its exception."), frappe.PermissionError)

	# ── Warranty Plan Claim Cap (#18) ────────────────────────────────

	def _validate_warranty_claim_cap(self):
		"""Block submission if claims for this serial/plan exceed plan limits.

		Reads ``max_claims`` (lifetime cap) and ``claims_per_year`` (annual
		cap) from the linked CH Warranty Plan and counts prior submitted
		Service Requests for the same serial under the same plan. The cap
		applies only when ``warranty_status`` is "Under Warranty" — out-of-
		warranty repairs are not gated by the plan.

		Reuse-first: uses the canonical fields already stored on
		CH Warranty Plan (``max_claims``, ``claims_per_year``) — no new
		schema. Skips silently when the plan cannot be resolved or limits
		are unset (0 = unlimited per the field's own description).
		"""
		if (self.warranty_status or "").strip() != "Under Warranty":
			return
		if not self.warranty_plan or not self.serial_no:
			return

		try:
			plan = frappe.get_cached_doc("CH Warranty Plan", self.warranty_plan)
		except frappe.DoesNotExistError:
			return

		max_claims = int(plan.get("max_claims") or 0)
		claims_per_year = int(plan.get("claims_per_year") or 0)
		if not max_claims and not claims_per_year:
			return  # Unlimited — nothing to enforce.

		# Count prior submitted, non-cancelled SRs for this serial under
		# the same plan, excluding the current document.
		base_filters = {
			"serial_no": self.serial_no,
			"warranty_plan": self.warranty_plan,
			"warranty_status": "Under Warranty",
			"docstatus": 1,
			"name": ["!=", self.name or ""],
		}

		if max_claims:
			lifetime_count = frappe.db.count("Service Request", base_filters)
			if lifetime_count >= max_claims:
				frappe.throw(
					_(
						"Warranty plan <b>{0}</b> allows a maximum of <b>{1}</b> "
						"lifetime claim(s) on serial <b>{2}</b>. {3} prior claim(s) "
						"already submitted — please raise a Service Discount exception "
						"or convert this to an out-of-warranty repair."
					).format(plan.plan_name or self.warranty_plan, max_claims,
						self.serial_no, lifetime_count),
					title=_("Warranty Claim Cap Reached"),
				)

		if claims_per_year:
			from frappe.utils import add_months, getdate, nowdate
			# Anniversary window: last 12 months from today.
			window_start = add_months(getdate(nowdate()), -12)
			annual_filters = dict(base_filters)
			annual_filters["transaction_date"] = [">=", window_start]
			# Service Request uses ``received_datetime``/``creation`` as
			# the timestamp; fall back to creation when available.
			if frappe.get_meta("Service Request").has_field("received_datetime"):
				annual_filters.pop("transaction_date", None)
				annual_filters["received_datetime"] = [">=", window_start]
			else:
				annual_filters.pop("transaction_date", None)
				annual_filters["creation"] = [">=", window_start]

			annual_count = frappe.db.count("Service Request", annual_filters)
			if annual_count >= claims_per_year:
				frappe.throw(
					_(
						"Warranty plan <b>{0}</b> allows a maximum of <b>{1}</b> "
						"claim(s) per policy year on serial <b>{2}</b>. {3} claim(s) "
						"already submitted in the last 12 months."
					).format(plan.plan_name or self.warranty_plan, claims_per_year,
						self.serial_no, annual_count),
					title=_("Annual Warranty Claim Cap Reached"),
				)

# API Methods
def _require_service_lookup_access(action):
	frappe.has_permission("Service Request", ptype="read", throw=True)


def _request_warehouse_anchors(doc) -> set[str]:
	return {
		value for value in (
			doc.get("source_warehouse"),
			doc.get("current_location"),
			doc.get("current_processing_location"),
			doc.get("transferred_to_store"),
		) if value
	}


def _get_scoped_device_details(service_request) -> list:
	_require_service_lookup_access(_("view device details"))
	doc = assert_service_request_access(service_request, permission_type="read")
	if not doc.get("device_item") or not doc.get("customer"):
		return []

	customer = frappe.get_doc("Customer", doc.customer)
	customer.check_permission("read")
	item = frappe.get_doc("Item", doc.device_item)
	item.check_permission("read")
	doc.device_item_name = item.item_name
	doc.brand = item.brand

	frappe.has_permission("Serial No", ptype="read", throw=True)
	row_limit = min(get_int_setting("device_serial_lookup_limit", 100), 500)
	return frappe.get_list(
		"Serial No",
		filters={
			"item_code": doc.device_item,
			"customer": doc.customer,
			"status": "Delivered",
		},
		fields=["name", "warehouse"],
		order_by="modified desc",
		limit_page_length=row_limit,
	)


def _get_scoped_open_requests(service_request) -> list:
	_require_service_lookup_access(_("view customer service history"))
	doc = assert_service_request_access(service_request, permission_type="read")
	if not doc.get("customer"):
		return []

	customer = frappe.get_doc("Customer", doc.customer)
	customer.check_permission("read")
	filters = {
		"customer": doc.customer,
		"name": ["!=", doc.name],
		"decision": ["in", ["Draft", "Accepted", "In Service"]],
		"walkin_status": "Accepted",
	}
	if doc.get("company"):
		filters["company"] = doc.company

	row_limit = min(get_int_setting("service_history_limit", 25), 100)
	return frappe.get_list(
		"Service Request",
		filters=filters,
		fields=["name", "service_date", "device_item_name", "decision", "advance_amount"],
		order_by="service_date desc, name desc",
		limit_page_length=row_limit,
	)


@frappe.whitelist()
def get_customer_details(customer) -> dict:
	"""Get customer details including contact info (whitelisted for client-side calls)"""
	if not customer:
		return {}
	_require_service_lookup_access(_("view customer details"))
	result = {}
	customer_doc = frappe.get_doc("Customer", customer)
	customer_doc.check_permission("read")
	result['customer_name'] = customer_doc.customer_name
	result['gstin'] = customer_doc.gstin if hasattr(customer_doc, 'gstin') else None
	result['pan'] = customer_doc.pan if hasattr(customer_doc, 'pan') else None
	
	# Get primary contact
	contacts = frappe.get_all("Dynamic Link",
		filters={
			"link_doctype": "Customer",
			"link_name": customer,
			"parenttype": "Contact"
		},
		fields=["parent"],
		limit=1)
	
	if contacts:
		contact = frappe.get_doc("Contact", contacts[0].parent)
		contact.check_permission("read")
		result['mobile_no'] = contact.mobile_no
		result['email_id'] = contact.email_id
	
	return result

@frappe.whitelist()
def get_open_requests(name) -> list:
	"""Get permission-filtered open service requests for the same customer."""
	return _get_scoped_open_requests(name)

@frappe.whitelist(methods=["POST"])
def generate_barcode_manual(name) -> dict:
	"""Manually generate barcode for a service request"""
	doc = _get_locked_service_request(name)
	
	# Temporarily reset the flag to allow regeneration
	doc.is_barcode_generated = 0
	doc.serial_no = None
	
	# Generate new barcode
	doc.generate_barcode()
	doc.save()
	
	return doc.serial_no


def _safe_set_sr_workflow_state(doc, value):
	"""Backward-compatible workflow_state setter for Service Request.

	Some sites do not have a `workflow_state` column on Service Request.
	Guard writes to avoid SQL 1054 errors.
	"""
	if frappe.db.has_column("Service Request", "workflow_state"):
		doc.db_set("workflow_state", value, update_modified=False)


def _get_locked_service_request(service_request):
	doc = assert_service_request_access(service_request, permission_type="write")
	if not frappe.db.get_value("Service Request", doc.name, "name", for_update=True):
		frappe.throw(_("Service Request {0} no longer exists.").format(doc.name), frappe.DoesNotExistError)
	doc.reload()
	return assert_service_request_access(doc, permission_type="write")

@frappe.whitelist(methods=["POST"])
def accept_service_request(service_request) -> str:
	"""Take the device in and open the job — the ticket moves to Analysis.

	Accepting means the device is now ours to work on. It does NOT mean the job
	has been priced: nobody has diagnosed it yet, so there is nothing to quote
	and no order to raise. The Service Order is raised later, at Customer
	Confirmation, once analysis and solutions have produced a real figure.

	This used to call ``create_service_order()`` unconditionally and therefore
	always failed — the SO gate requires confirmed analysis, which by definition
	has not happened at acceptance — so the Accept button threw on every
	un-diagnosed ticket. The ``draft → analysis`` timeline entry at the end was
	always the intent; only the forced order contradicted it.

	Returns the Service Order name when one legitimately exists (a ticket that
	was already diagnosed and approved), otherwise an empty string.
	"""
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")

	if doc.decision == "Accepted" and doc.service_order:
		frappe.msgprint(_("Service Request already accepted. Service Order: {0}").format(doc.service_order))
		return doc.service_order

	# Update decision using db_set to work with submitted docs
	doc.db_set("decision", "Accepted", update_modified=True)
	doc.db_set("accepted_by", frappe.session.user, update_modified=False)
	doc.db_set("accepted_datetime", frappe.utils.now(), update_modified=False)
	doc.db_set("walkin_status", "Accepted", update_modified=False)  # Customer left device
	_safe_set_sr_workflow_state(doc, "Accepted")

	# Raise the order only if this ticket is genuinely ready for one.
	doc.reload()
	from gofix.gofix_services.orchestration import can_create_service_order

	if not doc.service_order and can_create_service_order(doc):
		doc.create_service_order()

	# Acceptance immediately enters the operational In Service state.
	doc.db_set("decision", "In Service", update_modified=False)
	_safe_set_sr_workflow_state(doc, "In Service")

	# Log intake → analysis transition for ops timeline
	try:
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import _log_ops_stage
		_log_ops_stage(service_request, "draft", "analysis")
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Failed to log acceptance timeline for {service_request}")

	return doc.service_order or ""

@frappe.whitelist(methods=["POST"])
def reject_service_request(service_request, rejection_reason) -> bool:
	"""Reject Service Request
	
	This method handles rejecting submitted Service Requests
	"""
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")

	# Anything already fitted has to be taken back out and accounted for before
	# the device leaves — see gofix.spare_lifecycle.assert_spares_recovered.
	from gofix.spare_lifecycle import assert_spares_recovered

	assert_spares_recovered(service_request, _("reject this ticket"))

	# Update decision using db_set to work with submitted docs
	doc.db_set("decision", "Rejected", update_modified=True)
	doc.db_set("rejection_reason", rejection_reason, update_modified=False)
	doc.db_set("walkin_status", None, update_modified=False)  # Clear walk-in status
	_safe_set_sr_workflow_state(doc, "Rejected")
	
	return True


def get_unresolved_issue_gaps(sr) -> dict:
	"""QC-entry gate: every solution that was SELECTED must be finished.

	Returns:
	  uncovered_issues — active issue categories with no completed solution.
	                     INFORMATIONAL ONLY — reported at QC, never blocking.
	  open_solutions   — selected solutions not yet Completed/Skipped/Cancelled
	  ready_for_qc     — True when at least one solution exists and none is open

	The gate used to require every identified issue to carry a completed
	solution. The solution list offered per issue is a catalogue, not a work
	order: some issues have no applicable solution for a given device at all
	("No solutions apply to this device in this category"), and an issue
	raised at intake often turns out not to need work. That made the gate
	unsatisfiable — the technician could neither assign a solution nor pass
	QC, and the ticket bounced out of QC on every reload.

	What QC certifies is the work that was actually chosen, so that is what
	is enforced: every selected solution must be finished. Issues nobody
	worked on are surfaced to the QC sign-off instead of silently blocking
	it, so the decision to leave one alone is visible rather than invisible.

	Applies equally to solutions added later by the technician: work added
	mid-repair re-opens the gate until it too is finished.
	"""
	if isinstance(sr, str):
		sr = frappe.get_doc("Service Request", sr)

	# Every issue must reach a terminal state before QC: either FIXED (Resolved,
	# which needs a Completed solution behind it) or REJECTED with a reason
	# (Cancelled / Not Reproducible / Deleted). Anything still Open or In
	# Progress blocks.
	CLOSED_ISSUE = ("Resolved", "Cancelled", "Not Reproducible", "Deleted")

	active_issues = {
		row.issue_category
		for row in sr.get("issue_lines", [])
		if row.status not in CLOSED_ISSUE and row.issue_category
	}

	# Only a COMPLETED solution resolves an issue. A Skipped solution is work
	# that was consciously not done — it cannot stand in for a fix, or a ticket
	# could be invoiced with the fault still present. If the team decides not to
	# do the work, the ISSUE has to be rejected explicitly, which is a decision
	# with a reason attached rather than a side effect of skipping a task.
	covered = set()
	open_solutions = []
	for row in sr.get("solution_lines", []):
		if row.status in ("Cancelled", "Skipped"):
			continue
		if row.status == "Completed":
			covered.add(row.issue_category)
		else:
			open_solutions.append(f"{row.repair_solution} ({row.issue_category})")

	uncovered = sorted(active_issues - covered)
	# A ticket with no solutions at all has had no work done on it, so there is
	# nothing for QC to certify — that stays blocked.
	has_work = any(
		row.status not in ("Cancelled",) for row in sr.get("solution_lines", [])
	)
	return {
		"uncovered_issues": uncovered,
		"open_solutions": open_solutions,
		"has_work": has_work,
		"ready_for_qc": has_work and not open_solutions,
	}


def close_issues_with_completed_solutions(sr) -> list:
	"""Mark an issue Resolved once a solution for it is Completed.

	Saves the technician from closing the fault by hand after doing the work.
	An issue whose only solutions were Skipped is deliberately NOT closed here —
	that is a judgement call and needs an explicit rejection with a reason.
	"""
	fixed = {
		row.issue_category
		for row in sr.get("solution_lines", [])
		if row.status == "Completed" and row.issue_category
	}
	closed = []
	for row in sr.get("issue_lines", []):
		if row.status in ("Open", "In Progress") and row.issue_category in fixed:
			row.status = "Resolved"
			closed.append(row.issue_category)
	return closed


def missing_removed_part_details(sr) -> list:
	"""Consumed spares whose removed-part genealogy is incomplete.

	Every physically-fitted spare must record the OLD part's serial and
	condition (defective-return credit + OEM evidence chain) AND the NEW
	part's serial before the ticket can close. Universal consumables (thermal
	paste, screws) and non-stock lines are exempt — they carry no serial.
	"""
	if isinstance(sr, str):
		sr = frappe.get_doc("Service Request", sr)
	missing = []
	for row in sr.get("spare_lines", []):
		# "Sold" is "Consumed" that has been billed and paid — the part was still
		# fitted, so it still owes a serial.
		if row.status not in ("Consumed", "Sold"):
			continue
		item_flags = frappe.db.get_value(
			"Item", row.spare_item, ["is_stock_item", "gofix_universal_spare"], as_dict=True
		) or frappe._dict()
		if not item_flags.get("is_stock_item") or item_flags.get("gofix_universal_spare"):
			continue
		if (
			not (row.get("removed_part_serial") or "").strip()
			or not (row.get("removed_part_condition") or "").strip()
			or not (row.get("installed_part_serial") or "").strip()
		):
			missing.append(row.item_name or row.spare_item)
	return missing


def complete_service_request(service_request, completion_date=None):
	"""Mark a Service Request completed and create monetization artifacts."""
	doc = frappe.get_doc("Service Request", service_request)
	doc.check_permission("write")
	updates = {}

	if doc.decision != "Completed":
		updates["decision"] = "Completed"

	if completion_date and doc.meta.has_field("actual_completion_date") and not doc.get("actual_completion_date"):
		updates["actual_completion_date"] = completion_date

	if updates:
		doc.db_set(updates, update_modified=True)
		doc.reload()

	doc.ensure_completion_artifacts()
	return doc.name


@frappe.whitelist(methods=["POST"])
def request_service_discount(
	service_request,
	discount_percent=0,
	discount_amount=0,
	reason=None,
):
	doc = _get_locked_service_request(service_request)
	discount_percent = flt(discount_percent)
	discount_amount = flt(discount_amount)
	if discount_percent < 0 or discount_percent > 100 or discount_amount < 0:
		frappe.throw(_("Discount percentage must be 0-100 and amount cannot be negative."))
	if not discount_percent and not discount_amount:
		frappe.throw(_("Enter a discount percentage or amount."))
	if flt(doc.estimated_cost) and discount_amount > flt(doc.estimated_cost):
		frappe.throw(_("Discount amount cannot exceed the estimated service cost."))

	frappe.db.set_value(
		"Service Request",
		doc.name,
		{
			"service_discount_percent": discount_percent,
			"service_discount_amount": discount_amount,
			"discount_approval_reason": reason or _("Service discount requested"),
			"discount_approved_by": None,
			"discount_exception_request": None,
		},
		update_modified=True,
	)

	from ch_item_master.ch_item_master.exception_api import raise_exception

	requested_value = discount_amount or flt(doc.estimated_cost) * discount_percent / 100
	result = raise_exception(
		exception_type="Service Discount",
		company=doc.company,
		reason=reason or _("Service discount requested"),
		requested_value=requested_value,
		original_value=flt(doc.estimated_cost),
		reference_doctype="Service Request",
		reference_name=doc.name,
		store_warehouse=doc.source_warehouse,
		customer=doc.customer,
	)
	exception_name = result.get("name") if result else None
	if not exception_name:
		frappe.throw(_("The service discount exception could not be created."))
	evidence = frappe.db.get_value(
		"CH Exception Request",
		exception_name,
		["status", "approver", "resolved_by"],
		as_dict=True,
	)
	approved_by = (
		(evidence.approver or evidence.resolved_by)
		if evidence and evidence.status in ("Approved", "Auto-Approved")
		else None
	)
	frappe.db.set_value(
		"Service Request",
		doc.name,
		{
			"discount_exception_request": exception_name,
			"discount_approved_by": approved_by,
		},
		update_modified=False,
	)
	return {
		"status": result.get("status") or "Pending",
		"service_request": doc.name,
		"exception_request": exception_name,
		"approved_by": approved_by,
	}


@frappe.whitelist(methods=["POST"])
def approve_service_discount(
	service_request,
	approver_user=None,
	remarks=None,
	otp_code=None,
	otp_mobile=None,
):
	frappe.has_permission("Service Request", ptype="submit", throw=True)
	doc = _get_locked_service_request(service_request)
	actor = frappe.session.user
	if approver_user and approver_user != actor:
		frappe.throw(_("Approver identity is derived from the authenticated session."), frappe.PermissionError)
	if not doc.discount_exception_request:
		frappe.throw(_("Request service discount approval before approving it."))

	from ch_item_master.ch_item_master.exception_api import approve_exception

	approve_exception(
		exception_name=doc.discount_exception_request,
		approver_user=actor,
		channel="Workflow Approval",
		otp_code=otp_code,
		otp_mobile=otp_mobile,
		remarks=remarks,
	)
	frappe.db.set_value(
		"Service Request",
		doc.name,
		"discount_approved_by",
		actor,
		update_modified=True,
	)
	return {
		"status": "Approved",
		"service_request": doc.name,
		"exception_request": doc.discount_exception_request,
		"approved_by": actor,
	}

@frappe.whitelist(methods=["POST"])
def request_item_replacement(service_request, original_serial_no, replacement_serial_no, reason=None):
	"""Open a formal replacement request on a submitted Service Request."""
	doc = _get_locked_service_request(service_request)

	if not original_serial_no or not replacement_serial_no:
		frappe.throw(_("Original and replacement serial numbers are required."))
	if original_serial_no == replacement_serial_no:
		frappe.throw(_("Replacement serial number must be different from the original serial number."))
	if doc.get("serial_no") and original_serial_no != doc.serial_no:
		frappe.throw(
			_("Original serial number does not match the Service Request."),
			frappe.ValidationError,
		)
	for serial_no in (original_serial_no, replacement_serial_no):
		serial_doc = frappe.get_doc("Serial No", serial_no)
		serial_doc.check_permission("read")
	if doc.get("device_item"):
		replacement_item = frappe.db.get_value("Serial No", replacement_serial_no, "item_code")
		if replacement_item != doc.device_item:
			frappe.throw(
				_("Replacement serial number does not belong to the Service Request item."),
				frappe.ValidationError,
			)

	updates = {
		"original_serial_no": original_serial_no,
		"replacement_serial_no": replacement_serial_no,
		"substitution_reason": reason or _("Replacement requested during service"),
		"substitution_approved_by": None,
		"substitution_exception_request": None,
	}
	frappe.db.set_value("Service Request", doc.name, updates, update_modified=True)
	doc.reload()

	try:
		if not doc.substitution_exception_request:
			doc._create_serial_sub_exception()
			if doc.substitution_exception_request:
				frappe.db.set_value(
					"Service Request",
					doc.name,
					"substitution_exception_request",
					doc.substitution_exception_request,
					update_modified=False,
				)
	except Exception:
		frappe.log_error(frappe.get_traceback(), _("Replacement request setup failed for {0}").format(doc.name))

	try:
		doc.add_comment(
			"Comment",
			_("Item replacement requested: {0} → {1}. Reason: {2}").format(
				original_serial_no,
				replacement_serial_no,
				reason or _("Not specified"),
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Failed to audit replacement request for {doc.name}")

	return {
		"status": "Requested",
		"service_request": doc.name,
		"original_serial_no": original_serial_no,
		"replacement_serial_no": replacement_serial_no,
		"exception_request": doc.substitution_exception_request,
	}


@frappe.whitelist(methods=["POST"])
def approve_item_replacement(service_request, approver_user=None, remarks=None):
	"""Approve a pending item replacement request."""
	frappe.has_permission("Service Request", ptype="submit", throw=True)
	doc = _get_locked_service_request(service_request)

	actor = frappe.session.user
	if approver_user and approver_user != actor:
		frappe.throw(
			_("Approver identity is derived from the authenticated session."),
			frappe.PermissionError,
		)
	approver_user = actor
	if not doc.substitution_exception_request:
		frappe.throw(_("A linked substitution exception is required before approval."))
	from ch_item_master.ch_item_master.exception_api import approve_exception

	approve_exception(
		exception_name=doc.substitution_exception_request,
		approver_user=approver_user,
		channel="Workflow Approval",
		remarks=remarks or _("Approved from Service Request replacement workflow"),
	)
	frappe.db.set_value(
		"Service Request",
		doc.name,
		"substitution_approved_by",
		approver_user,
		update_modified=True,
	)
	doc.reload()
	doc.add_comment(
		"Comment",
		_("Item replacement approved by {0}. {1}").format(
			approver_user,
			remarks or "",
		),
	)

	return {
		"status": "Approved",
		"service_request": doc.name,
		"approved_by": approver_user,
		"exception_request": doc.substitution_exception_request,
	}


@frappe.whitelist(methods=["POST"])
def complete_item_replacement(service_request, replacement_serial_no=None, completed_by=None):
	"""Finalize the approved serial substitution on the Service Request."""
	frappe.has_permission("Service Request", ptype="write", throw=True)
	doc = _get_locked_service_request(service_request)
	actor = frappe.session.user
	if completed_by and completed_by != actor:
		frappe.throw(
			_("Completion identity is derived from the authenticated session."),
			frappe.PermissionError,
		)

	if replacement_serial_no and replacement_serial_no != doc.replacement_serial_no:
		frappe.throw(
			_("The approved replacement serial number cannot be changed during completion."),
			frappe.ValidationError,
		)

	if not doc.original_serial_no or not doc.replacement_serial_no:
		frappe.throw(_("Replacement request details are incomplete."))
	if not doc.substitution_approved_by:
		frappe.throw(_("Replacement approval is required before completion."))
	exception_status = frappe.db.get_value(
		"CH Exception Request", doc.substitution_exception_request, "status"
	)
	if exception_status not in ("Approved", "Auto-Approved"):
		frappe.throw(_("The linked substitution exception is not approved."))

	updates = {}
	if doc.meta.has_field("serial_no"):
		updates["serial_no"] = doc.replacement_serial_no
	if doc.decision == "Draft":
		updates["decision"] = "In Service"
	if updates:
		doc.db_set(updates, update_modified=True)

	doc.add_comment(
		"Info",
		_("Replacement completed by {0}: {1} replaced with {2}").format(
			actor,
			doc.original_serial_no,
			doc.replacement_serial_no,
		),
	)

	return {
		"status": "Completed",
		"service_request": doc.name,
		"serial_no": doc.replacement_serial_no,
		"approved_by": doc.substitution_approved_by,
	}

@frappe.whitelist()
def get_device_item_details(item_code: str) -> dict:
	"""Return item_name and brand for a device item.

	Variant items often don't have 'brand' set directly — it lives only on
	the template.  This function falls back to the template brand so the
	Service Request and Quick Intake always show the correct brand.
	"""
	_require_service_lookup_access(_("view device item details"))
	item = frappe.get_doc("Item", item_code)
	item.check_permission("read")
	brand = item.brand or ""
	if not brand and item.variant_of:
		template = frappe.get_doc("Item", item.variant_of)
		template.check_permission("read")
		brand = template.brand or ""
	return {"item_name": item.item_name, "brand": brand}


@frappe.whitelist()
def get_warehouse_state(warehouse) -> dict:
	"""Get state details from warehouse address
	
	Returns state_name and state_code for GST compliance
	"""
	if not warehouse:
		return {}
	_require_service_lookup_access(_("view warehouse state"))
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	warehouse_doc.check_permission("read")
	from gofix.scope_guard import assert_warehouse

	assert_warehouse(warehouse=warehouse, company=warehouse_doc.company)
	if warehouse_doc.address:
		address = frappe.get_doc("Address", warehouse_doc.address)
		address.check_permission("read")
		return {
			"state_name": address.state or "",
			"state_code": address.get("gst_state_number") or "",
		}
	if warehouse_doc.company:
		company = frappe.get_doc("Company", warehouse_doc.company)
		company.check_permission("read")
		company_addresses = frappe.get_all(
			"Dynamic Link",
			filters={
				"link_doctype": "Company",
				"link_name": warehouse_doc.company,
				"parenttype": "Address",
			},
			fields=["parent"],
			limit=1,
		)
		if company_addresses:
			address = frappe.get_doc("Address", company_addresses[0].parent)
			address.check_permission("read")
			return {
				"state_name": address.state or "",
				"state_code": address.get("gst_state_number") or "",
			}
	return {}


def _get_active_address(customer_doc, address_type="Billing"):
	"""Return the customer's authoritative ERPNext ``Address`` projection."""
	customer = customer_doc.name if hasattr(customer_doc, "name") else str(customer_doc)
	linked = frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Address",
			"link_doctype": "Customer",
			"link_name": customer,
		},
		pluck="parent",
		limit_page_length=200,
	)
	if not linked:
		return None

	fields = [
		"name", "address_line1", "address_line2", "city", "state", "pincode",
		"country", "address_type", "is_primary_address", "is_shipping_address",
	]
	for optional in ("gstin", "gst_state_number"):
		if frappe.get_meta("Address").has_field(optional):
			fields.append(optional)
	rows = frappe.get_all(
		"Address",
		filters={"name": ("in", linked), "disabled": 0},
		fields=fields,
		limit_page_length=200,
	)
	if not rows:
		return None

	primary = customer_doc.get("customer_primary_address") if hasattr(customer_doc, "get") else None
	wants_shipping = address_type == "Shipping"
	def rank(row):
		return (
			0 if (wants_shipping and row.is_shipping_address) else 1,
			0 if (not wants_shipping and row.name == primary) else 1,
			0 if (not wants_shipping and row.is_primary_address) else 1,
			0 if row.address_type == address_type else 1,
			row.name,
		)
	address = sorted(rows, key=rank)[0]
	address.city_name = address.city or ""
	address.state_code = address.get("gst_state_number") or ""
	return address


@frappe.whitelist()
def get_customer_billing_address(
	customer,
	service_request=None,
	company=None,
	warehouse=None,
):
	"""Return the primary standard ERPNext Address for *customer*.

	Called from the SR form JS after customer is selected.
	Returns a plain dict with address fields, or None if not set.
	"""
	if not customer:
		return None
	_require_service_lookup_access(_("view a customer billing address"))

	if service_request:
		doc = assert_service_request_access(service_request, permission_type="read")
		if doc.get("customer") != customer:
			frappe.throw(_("Customer does not match the Service Request."), frappe.PermissionError)
		if company and doc.get("company") != company:
			frappe.throw(_("Company does not match the Service Request."), frappe.PermissionError)
		if warehouse and warehouse not in _request_warehouse_anchors(doc):
			frappe.throw(_("Warehouse does not match the Service Request."), frappe.PermissionError)
	elif not is_privileged_user():
		if not company or not warehouse:
			frappe.throw(
				_("Company and warehouse are required for a new Service Request."),
				frappe.PermissionError,
			)
		frappe.has_permission("Service Request", ptype="create", throw=True)
		company_doc = frappe.get_doc("Company", company)
		company_doc.check_permission("read")
		warehouse_doc = frappe.get_doc("Warehouse", warehouse)
		warehouse_doc.check_permission("read")
		if warehouse_doc.company != company:
			frappe.throw(_("Warehouse does not belong to the selected company."), frappe.PermissionError)
		from gofix.scope_guard import assert_warehouse

		assert_warehouse(warehouse=warehouse, company=company)

	customer_doc = frappe.get_doc("Customer", customer)
	customer_doc.check_permission("read")
	addr = _get_active_address(customer_doc, "Billing")
	if not addr:
		return None
	return {
		"address_line1": addr.address_line1,
		"address_line2": addr.address_line2 or "",
		"city": addr.city_name or addr.city,
		"state": addr.state or "",
		"state_code": addr.state_code or "",
		"pincode": addr.pincode or "",
		"country": addr.country or get_setting("company_country", ""),
		"gstin": addr.gstin or "",
	}


def flag_unclaimed_devices(days_threshold=None):
	"""Flag one bounded batch of completed, undelivered devices."""
	from frappe.utils import add_days, today

	days_threshold = max(
		cint(days_threshold) or get_int_setting("unclaimed_device_days", 15, minimum=1),
		1,
	)
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	cutoff = add_days(today(), -days_threshold)
	rows = frappe.get_all(
		"Service Request",
		filters={
			"decision": ["in", ["Completed", "Invoiced"]],
			"unclaimed_flag": 0,
			"docstatus": 1,
			"modified": ["<=", cutoff],
		},
		pluck="name",
		order_by="modified asc, name asc",
		limit=batch_limit + 1,
	)
	unclaimed = rows[:batch_limit]
	if unclaimed:
		frappe.db.sql(
			"""
				UPDATE `tabService Request`
				SET `unclaimed_flag` = 1, `unclaimed_date` = %(today)s
				WHERE `name` IN %(names)s
				  AND `decision` IN ('Completed', 'Invoiced')
				  AND `unclaimed_flag` = 0
				  AND `docstatus` = 1
				  AND `modified` <= %(cutoff)s
			""",
			{"names": tuple(unclaimed), "today": today(), "cutoff": cutoff},
		)
		frappe.logger("gofix").info(f"Flagged {len(unclaimed)} unclaimed devices")
	return {"flagged": len(unclaimed), "has_more": len(rows) > batch_limit}


def ensure_service_order_on_accept(doc, method=None):
	"""Hook: guarantee the SO exists once the job is sellable.

	Catches cases where decision is set via db_set / direct SQL and the class
	method on_update_after_submit didn't fire.

	Gated on the same readiness check as that method: "Accepted" means the
	device has been taken in, not that it has been diagnosed and quoted, so
	firing on the decision alone made every save during Analysis blow up on the
	prerequisite gate.
	"""
	if doc.decision != "Accepted" or doc.service_order:
		return

	from gofix.gofix_services.orchestration import can_create_service_order

	if can_create_service_order(doc):
		doc.create_service_order()
		frappe.logger("gofix").info(
			f"Auto-created SO for {doc.name} via hook"
		)


def auto_expire_stale_requests(days_threshold=None):
	"""Expire one bounded batch of untouched draft Service Requests."""
	from frappe.utils import add_days, today

	days_threshold = max(
		cint(days_threshold) or get_int_setting("stale_request_expiry_days", 30, minimum=1),
		1,
	)
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	cutoff = add_days(today(), -days_threshold)
	rows = frappe.get_all(
		"Service Request",
		filters={
			"decision": "Draft",
			"docstatus": ["<", 2],
			"service_order": ["in", [None, ""]],
			"creation": ["<=", cutoff],
		},
		pluck="name",
		order_by="creation asc, name asc",
		limit=batch_limit + 1,
	)
	stale = rows[:batch_limit]
	if stale:
		# Keep the eligibility predicates in the UPDATE as a concurrency guard:
		# a request accepted between the bounded read and this write must not expire.
		frappe.db.sql(
			"""
			UPDATE `tabService Request`
			   SET decision = 'Expired', modified = %(modified)s,
			       modified_by = %(modified_by)s
			 WHERE name IN %(names)s
			   AND decision = 'Draft'
			   AND docstatus < 2
			   AND COALESCE(service_order, '') = ''
			""",
			{
				"names": tuple(stale),
				"modified": now_datetime(),
				"modified_by": frappe.session.user,
			},
		)
		frappe.logger("gofix").info(
			f"Auto-expired {len(stale)} stale Draft SRs older than {days_threshold} days"
		)
	return {"expired": len(stale), "has_more": len(rows) > batch_limit}
