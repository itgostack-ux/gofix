// Custom client script for Sales Order - Service Order enhancements
// Add "Create Job Sheet" button for Service Orders

frappe.ui.form.on('Sales Order', {
	refresh: function(frm) {
		// Check if this is a Service Order
		if (frm.doc.is_service_order && frm.doc.docstatus === 1) {
			// Add "Create Job Sheet" button
			frm.add_custom_button(__('Create Job Sheet'), function() {
				create_job_sheet(frm);
			}, __('Create')).addClass('btn-primary');
			
			// Add "View Job Sheets" button if any exist
			frappe.call({
				method: 'frappe.client.get_count',
				args: {
					doctype: 'Job Assignment',
					filters: {
						service_order: frm.doc.name
					}
				},
				callback: function(r) {
					if (r.message && r.message > 0) {
						frm.add_custom_button(__('View Job Sheets ({0})').format(r.message), function() {
							frappe.route_options = {
								"service_order": frm.doc.name
							};
							frappe.set_route("List", "Job Assignment");
						});
					}
				}
			});
			
			// Add "View Service Request" button
			if (frm.doc.service_request) {
				frm.add_custom_button(__('View Service Request'), function() {
					frappe.set_route('Form', 'Service Request', frm.doc.service_request);
				});
			}
		}
	}
});

function create_job_sheet(frm) {
	// Dialog to get job details
	let d = new frappe.ui.Dialog({
		title: __('Create Job Sheet'),
		fields: [
			{
				fieldtype: 'Link',
				fieldname: 'service_engineer',
				label: __('Service Engineer'),
				options: 'Employee',
				reqd: 1,
				description: __('Assign to a service engineer')
			},
			{
				fieldtype: 'Select',
				fieldname: 'job_type',
				label: __('Job Type'),
				options: 'Repair\nDiagnosis\nQC\nSpare Parts Replacement\nSoftware Update\nTesting',
				default: 'Repair',
				reqd: 1
			},
			{
				fieldtype: 'Column Break'
			},
			{
				fieldtype: 'Select',
				fieldname: 'priority',
				label: __('Priority'),
				options: 'Low\nMedium\nHigh\nUrgent',
				default: frm.doc.service_priority || 'Medium'
			},
			{
				fieldtype: 'Float',
				fieldname: 'estimated_hours',
				label: __('Estimated Hours'),
				precision: 2
			},
			{
				fieldtype: 'Section Break'
			},
			{
				fieldtype: 'Small Text',
				fieldname: 'comments',
				label: __('Assignment Comments')
			}
		],
		primary_action_label: __('Create Job Sheet'),
		primary_action: function(values) {
			frappe.call({
				method: 'gofix.gofix_services.doctype.job_assignment.job_assignment.create_job_sheet_from_service_order',
				args: {
					service_order: frm.doc.name,
					service_engineer: values.service_engineer,
					job_type: values.job_type,
					estimated_hours: values.estimated_hours
				},
				freeze: true,
				freeze_message: __('Creating Job Sheet...'),
				callback: function(r) {
					if (r.message) {
						d.hide();
						frappe.show_alert({
							message: __('Job Sheet {0} created', [r.message]),
							indicator: 'green'
						}, 5);
						
						// Refresh form to show updated buttons
						frm.reload_doc();
						
						// Open the created Job Sheet
						frappe.set_route('Form', 'Job Assignment', r.message);
					}
				}
			});
		}
	});
	
	d.show();
}
