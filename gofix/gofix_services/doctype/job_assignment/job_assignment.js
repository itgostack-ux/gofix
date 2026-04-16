// Copyright (c) 2025, GoStack and contributors
// For license information, please see license.txt

const OPS_API = "gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub";

frappe.ui.form.on('Job Assignment', {
	refresh: function(frm) {
		// "Not Repairable" button — technicians and managers can mark at any active stage
		if (!frm.is_new() && frm.doc.service_request
			&& frm.doc.assignment_status
			&& !["Completed", "Closed", "Cancelled"].includes(frm.doc.assignment_status)
			&& !["Not Repairable", "Beyond Repair", "Customer Cancelled"].includes(frm.doc.repair_outcome)) {
			frm.add_custom_button(__('Not Repairable'), function() {
				show_not_repairable_from_ja(frm);
			}, __('Actions')).addClass('btn-danger');
		}
	},
});

function show_not_repairable_from_ja(frm) {
	let d = new frappe.ui.Dialog({
		title: __('Mark as Not Repairable'),
		fields: [
			{
				fieldtype: 'Select', fieldname: 'status', label: __('Status'),
				options: 'Not Repairable\nBER', reqd: 1, default: 'Not Repairable',
				description: __('BER = Beyond Economical Repair'),
			},
			{
				fieldtype: 'Small Text', fieldname: 'reason', label: __('Reason'),
				reqd: 1, description: __('Explain why the device cannot be repaired'),
			},
		],
		primary_action_label: __('Confirm'),
		primary_action(values) {
			d.disable_primary_action();
			frappe.xcall(`${OPS_API}.mark_not_repairable`, {
				sr_name: frm.doc.service_request,
				status: values.status,
				reason: values.reason,
			}).then(r => {
				d.hide();
				frappe.show_alert({ message: r.message, indicator: 'orange' });
				if (r.needs_spare_recovery && r.pending_spares && r.pending_spares.length) {
					frappe.msgprint({
						title: __('Spare Recovery Required'),
						message: __('There are {0} consumed spare(s) that need recovery. '
							+ 'Please open the Service Request to complete spare recovery.', [r.pending_spares.length]),
						indicator: 'red',
						primary_action: {
							label: __('Open Service Request'),
							action() {
								frappe.set_route('Form', 'Service Request', frm.doc.service_request);
							},
						},
					});
				}
				frm.reload_doc();
			}).catch(() => d.enable_primary_action());
		},
	});
	d.show();
}
