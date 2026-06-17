// Copyright (c) 2026, GoFix and contributors
// Customer form customization — enforce single active address per type
// and show address summary in the form header.

frappe.ui.form.on("Customer", {
	refresh: function (frm) {
		_refresh_address_summary(frm);
	},
});

frappe.ui.form.on("CH Customer Address", {
	is_active: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.is_active) return;

		// Deactivate all other rows of the same address_type
		(frm.doc.billing_addresses || []).forEach(function (addr) {
			if (addr.name === cdn) return; // skip current row
			if (addr.address_type === row.address_type || addr.address_type === "Both" || row.address_type === "Both") {
				// Only deactivate if it would conflict (same type covers billing or shipping)
				const conflicts = _types_overlap(addr.address_type, row.address_type);
				if (conflicts) {
					frappe.model.set_value(addr.doctype, addr.name, "is_active", 0);
				}
			}
		});

		frm.refresh_field("billing_addresses");
		_refresh_address_summary(frm);
	},

	address_type: function (frm) {
		_refresh_address_summary(frm);
	},

	billing_addresses_remove: function (frm) {
		_refresh_address_summary(frm);
	},

	billing_addresses_add: function (frm) {
		// Default new row to Billing type, inactive until user sets it
	},
});

/**
 * Returns true if address types overlap (both cover Billing or both cover Shipping).
 * "Both" overlaps with everything.
 */
function _types_overlap(typeA, typeB) {
	if (typeA === "Both" || typeB === "Both") return true;
	return typeA === typeB;
}

/**
 * Show active billing / shipping address in the form's info section.
 */
function _refresh_address_summary(frm) {
	if (frm.is_new()) return;
	const addrs = frm.doc.billing_addresses || [];

	const active_billing = addrs.find(
		(a) => a.is_active && (a.address_type === "Billing" || a.address_type === "Both")
	);
	const active_shipping = addrs.find(
		(a) => a.is_active && (a.address_type === "Shipping" || a.address_type === "Both")
	);

	let html = "";
	if (active_billing) {
		html += `<span style="margin-right:16px">
			<b>📍 Billing:</b> ${_fmt_addr(active_billing)}
			${active_billing.gstin ? `<span style="color:#6b7280;font-size:11px"> | GSTIN: ${active_billing.gstin}</span>` : ""}
		</span>`;
	} else {
		html += `<span style="color:#dc2626;margin-right:16px"><b>⚠ No active billing address</b></span>`;
	}

	if (active_shipping && active_shipping !== active_billing) {
		html += `<span><b>🚚 Shipping:</b> ${_fmt_addr(active_shipping)}</span>`;
	} else if (active_billing) {
		html += `<span style="color:#6b7280"><i>Shipping same as billing</i></span>`;
	}

	frm.dashboard.set_headline(html);
}

function _fmt_addr(addr) {
	return [addr.address_line1, addr.city_name || addr.city, addr.state, addr.pincode]
		.filter(Boolean)
		.join(", ");
}
