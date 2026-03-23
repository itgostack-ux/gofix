frappe.pages['job-tracker'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Job Tracker',
		single_column: true
	});

	frappe.job_tracker = new JobTracker(page);
}

class JobTracker {
	constructor(page) {
		this.page = page;
		this.parent = $(this.page.body);
		this.service_request = null;
		this.setup();
	}

	setup() {
		this.setup_toolbar();
		this.render_layout();
		this.setup_realtime();
	}

	setup_toolbar() {
		// Search bar
		this.page.add_field({
			fieldname: 'service_request',
			label: __('Service Request'),
			fieldtype: 'Link',
			options: 'Service Request',
			change: () => {
				this.service_request = this.page.fields_dict.service_request.get_value();
				if (this.service_request) {
					this.load_service_request();
				}
			}
		});

		// Refresh button
		this.page.add_button(__('Refresh'), () => {
			if (this.service_request) {
				this.load_service_request();
			}
		}, {icon: 'refresh'});

		// Print button
		this.page.add_button(__('Print Job Sheet'), () => {
			if (this.service_request) {
				this.print_job_sheet();
			}
		}, {icon: 'printer'});
	}

	render_layout() {
		this.parent.html(`
			<div class="job-tracker-container">
				<!-- Service Request Summary -->
				<div class="row">
					<div class="col-md-8">
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-primary text-white">
								<h5 class="mb-0"><i class="fa fa-briefcase"></i> Service Request Details</h5>
							</div>
							<div class="card-body" id="service-details">
								<p class="text-muted">Please select a Service Request from above</p>
							</div>
						</div>

						<!-- Assignment Section -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-info text-white">
								<h5 class="mb-0"><i class="fa fa-users"></i> Assignment</h5>
							</div>
							<div class="card-body" id="assignment-section">
								<div class="row">
									<div class="col-md-12">
										<button class="btn btn-sm btn-primary mb-3" id="btn-assign-team">
											<i class="fa fa-plus"></i> Assign Team
										</button>
										<button class="btn btn-sm btn-info mb-3" id="btn-assign-user">
											<i class="fa fa-user"></i> Assign User
										</button>
										<button class="btn btn-sm btn-success mb-3" id="btn-assign-technician">
											<i class="fa fa-wrench"></i> Assign Technician
										</button>
									</div>
								</div>
								<div id="assignment-history"></div>
							</div>
						</div>

						<!-- Spare Parts Section -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-warning text-dark">
								<h5 class="mb-0"><i class="fa fa-cogs"></i> Spare Parts Management</h5>
							</div>
							<div class="card-body">
								<div class="mb-3">
									<button class="btn btn-sm btn-primary" id="btn-add-spare">
										<i class="fa fa-plus"></i> Add Spare Part
									</button>
									<span class="badge badge-info ml-2" id="spare-count">0 Parts Used</span>
								</div>
								<div id="spare-parts-list"></div>
							</div>
						</div>

						<!-- Service Closure -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-success text-white">
								<h5 class="mb-0"><i class="fa fa-check-circle"></i> Service Closure</h5>
							</div>
							<div class="card-body" id="closure-section">
								<div class="row">
									<div class="col-md-12">
										<button class="btn btn-success" id="btn-mark-repaired">
											<i class="fa fa-check"></i> Mark as Repaired
										</button>
										<button class="btn btn-danger ml-2" id="btn-mark-not-repaired">
											<i class="fa fa-times"></i> Not Repaired
										</button>
									</div>
								</div>
							</div>
						</div>
					</div>

					<!-- Right Sidebar -->
					<div class="col-md-4">
						<!-- Customer Info -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-secondary text-white">
								<h6 class="mb-0"><i class="fa fa-user"></i> Customer</h6>
							</div>
							<div class="card-body" id="customer-info">
								<p class="text-muted small">No customer data</p>
							</div>
						</div>

						<!-- Device Info -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-dark text-white">
								<h6 class="mb-0"><i class="fa fa-mobile"></i> Device</h6>
							</div>
							<div class="card-body" id="device-info">
								<p class="text-muted small">No device data</p>
							</div>
						</div>

						<!-- Audit Trails -->
						<div class="card shadow-sm mb-3">
							<div class="card-header bg-purple text-white">
								<h6 class="mb-0"><i class="fa fa-history"></i> Activity Log</h6>
							</div>
							<div class="card-body">
								<ul class="nav nav-tabs" role="tablist">
									<li class="nav-item">
										<a class="nav-link active" data-toggle="tab" href="#tab-technician">Technician</a>
									</li>
									<li class="nav-item">
										<a class="nav-link" data-toggle="tab" href="#tab-spare">Spare Parts</a>
									</li>
									<li class="nav-item">
										<a class="nav-link" data-toggle="tab" href="#tab-logs">Logs</a>
									</li>
								</ul>
								<div class="tab-content mt-2">
									<div id="tab-technician" class="tab-pane fade show active">
										<div id="technician-audit"></div>
									</div>
									<div id="tab-spare" class="tab-pane fade">
										<div id="spare-audit"></div>
									</div>
									<div id="tab-logs" class="tab-pane fade">
										<div id="support-logs"></div>
									</div>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);

		this.bind_events();
	}

	bind_events() {
		// Assignment buttons
		$('#btn-assign-team').on('click', () => this.show_assignment_dialog('Team'));
		$('#btn-assign-user').on('click', () => this.show_assignment_dialog('User'));
		$('#btn-assign-technician').on('click', () => this.show_assignment_dialog('Technician'));

		// Spare parts
		$('#btn-add-spare').on('click', () => this.show_spare_dialog());

		// Service closure
		$('#btn-mark-repaired').on('click', () => this.mark_service_complete('Repaired'));
		$('#btn-mark-not-repaired').on('click', () => this.mark_service_complete('Not Repaired'));
	}

	load_service_request() {
		frappe.call({
			method: 'frappe.client.get',
			args: {
				doctype: 'Service Request',
				name: this.service_request
			},
			callback: (r) => {
				if (r.message) {
					this.render_service_details(r.message);
					this.load_customer_info(r.message.customer);
					this.load_device_info(r.message);
					this.load_assignments();
					this.load_spare_parts();
					this.load_audit_trails();
				}
			}
		});
	}

	render_service_details(doc) {
		const status_colors = {
			'Draft': 'secondary',
			'Accepted': 'primary',
			'In Service': 'info',
			'Completed': 'success',
			'Invoiced': 'success',
			'Delivered': 'success',
			'Withdrawn': 'warning',
			'Rejected': 'danger',
			'Cancelled': 'danger'
		};
		const esc = frappe.utils.escape_html;

		$('#service-details').html(`
			<div class="row">
				<div class="col-md-6">
					<p><strong>SR No:</strong> ${esc(doc.name)}</p>
					<p><strong>Date:</strong> ${frappe.datetime.str_to_user(doc.service_date)}</p>
					<p><strong>Status:</strong> <span class="badge badge-${status_colors[doc.status] || 'secondary'}">${esc(doc.status)}</span></p>
				</div>
				<div class="col-md-6">
					<p><strong>Priority:</strong> <span class="badge badge-secondary">${esc(doc.priority)}</span></p>
					<p><strong>Expected:</strong> ${doc.expected_completion_date ? frappe.datetime.str_to_user(doc.expected_completion_date) : '-'}</p>
					<p><strong>Issue:</strong> ${esc(doc.issue_category || '-')}</p>
				</div>
			</div>
			<hr>
			<p><strong>Issue Description:</strong></p>
			<p class="text-muted">${esc(doc.issue_description || 'No description provided')}</p>
		`);
	}

	load_customer_info(customer) {
		frappe.call({
			method: 'frappe.client.get',
			args: {
				doctype: 'Customer',
				name: customer
			},
			callback: (r) => {
				if (r.message) {
					const esc = frappe.utils.escape_html;
					$('#customer-info').html(`
						<p class="mb-1"><strong>${esc(r.message.customer_name)}</strong></p>
						<p class="mb-1 small"><i class="fa fa-phone"></i> ${esc(r.message.mobile_no || '-')}</p>
						<p class="mb-1 small"><i class="fa fa-envelope"></i> ${esc(r.message.email_id || '-')}</p>
						<p class="mb-0 small"><i class="fa fa-map-marker"></i> ${esc(r.message.customer_primary_address || '-')}</p>
					`);
				}
			}
		});
	}

	load_device_info(doc) {
		const esc = frappe.utils.escape_html;
		$('#device-info').html(`
			<p class="mb-1"><strong>${esc(doc.device_item_name || doc.device_item)}</strong></p>
			<p class="mb-1 small">Brand: ${esc(doc.brand || '-')}</p>
			<p class="mb-1 small">Serial/IMEI: ${esc(doc.serial_no || '-')}</p>
			<p class="mb-1 small">Condition: ${esc(doc.device_condition || '-')}</p>
			<p class="mb-0 small">Warranty: <span class="badge badge-sm badge-info">${esc(doc.warranty_status || 'Unknown')}</span></p>
		`);
	}

	load_assignments() {
		frappe.call({
			method: 'frappe.client.get_list',
			args: {
				doctype: 'Job Assignment',
				filters: {service_request: this.service_request},
				fields: ['name', 'assignment_date', 'team', 'user', 'service_engineer', 'assignment_status'],
				order_by: 'assignment_date desc'
			},
			callback: (r) => {
				this.render_assignments(r.message || []);
			}
		});
	}

	render_assignments(assignments) {
		if (!assignments.length) {
			$('#assignment-history').html('<p class="text-muted small">No assignments yet</p>');
			return;
		}

		let html = '<div class="timeline">';
		assignments.forEach(a => {
			const esc = frappe.utils.escape_html;
			const assigned_to = esc(a.service_engineer || a.user || a.team || 'Unassigned');
			html += `
				<div class="timeline-item">
					<div class="timeline-badge bg-info"></div>
					<div class="timeline-panel">
						<div class="timeline-heading">
							<h6 class="mb-0">${assigned_to}</h6>
							<p class="small text-muted mb-0">${frappe.datetime.str_to_user(a.assignment_date)}</p>
						</div>
						<div class="timeline-body">
							<span class="badge badge-sm badge-${a.assignment_status === 'Completed' ? 'success' : 'warning'}">${esc(a.assignment_status)}</span>
						</div>
					</div>
				</div>
			`;
		});
		html += '</div>';
		$('#assignment-history').html(html);
	}

	load_spare_parts() {
		frappe.call({
			method: 'frappe.client.get_list',
			args: {
				doctype: 'Spare Parts Usage',
				filters: {service_request: this.service_request},
				fields: ['name', 'spare_part_item', 'barcode_value', 'qty_used', 'sales_price', 'status'],
				order_by: 'line_seq_no'
			},
			callback: (r) => {
				this.render_spare_parts(r.message || []);
			}
		});
	}

	render_spare_parts(parts) {
		$('#spare-count').text(`${parts.length} Parts Used`);
		
		if (!parts.length) {
			$('#spare-parts-list').html('<p class="text-muted small">No spare parts used</p>');
			return;
		}

		let html = '<div class="table-responsive"><table class="table table-sm table-hover">';
		html += '<thead><tr><th>Part</th><th>Barcode</th><th>Qty</th><th>Price</th><th>Status</th><th>Actions</th></tr></thead><tbody>';
		
		parts.forEach(p => {
			const esc = frappe.utils.escape_html;
			html += `
				<tr>
					<td><small>${esc(p.spare_part_item)}</small></td>
					<td><small><code>${esc(p.barcode_value)}</code></small></td>
					<td>${p.qty_used}</td>
					<td>${format_currency(p.sales_price || 0)}</td>
					<td><span class="badge badge-sm badge-${p.status === 'Active' ? 'success' : 'secondary'}">${esc(p.status)}</span></td>
					<td>
						<button class="btn btn-xs btn-secondary" onclick="frappe.job_tracker.move_spare_to_main('${esc(p.name)}')">
							<i class="fa fa-undo"></i>
						</button>
						<button class="btn btn-xs btn-danger" onclick="frappe.job_tracker.move_spare_to_dispose('${esc(p.name)}')">
							<i class="fa fa-trash"></i>
						</button>
					</td>
				</tr>
			`;
		});
		
		html += '</tbody></table></div>';
		$('#spare-parts-list').html(html);
	}

	load_audit_trails() {
		// Load technician audit — child table in Job Assignment, must query Technician Audit doctype directly
		frappe.call({
			method: 'frappe.client.get_list',
			args: {
				doctype: 'Job Assignment',
				filters: {service_request: this.service_request},
				fields: ['name'],
				order_by: 'assignment_date asc'
			},
			callback: (r) => {
				const ja_names = (r.message || []).map(j => j.name);
				if (ja_names.length) {
					frappe.call({
						method: 'frappe.client.get_list',
						args: {
							doctype: 'Technician Audit',
							filters: [['parent', 'in', ja_names]],
							fields: ['service_engineer', 'assignment_from_time', 'assignment_to_time', 'time_duration', 'operation', 'is_active_record'],
							order_by: 'assignment_from_time asc',
							limit: 50
						},
						callback: (res) => {
							this.render_technician_audit(res.message || []);
						}
					});
				} else {
					this.render_technician_audit([]);
				}
			}
		});

		// Load spare parts audit
		this.load_spare_parts_audit();

		// Load support logs (comments)
		this.load_support_logs();
	}

	render_technician_audit(audits) {
		if (!audits || !audits.length) {
			$('#technician-audit').html('<p class="text-muted small">No audit records</p>');
			return;
		}

		let html = '<ul class="list-unstyled">';
		audits.forEach(a => {
			const esc = frappe.utils.escape_html;
			html += `
				<li class="mb-2 small">
					<strong>${esc(a.service_engineer)}</strong><br>
					<span class="text-muted">${esc(a.operation)} - ${esc(a.time_duration)}</span><br>
					${frappe.datetime.str_to_user(a.assignment_from_time)} → ${a.assignment_to_time ? frappe.datetime.str_to_user(a.assignment_to_time) : 'Ongoing'}
				</li>
			`;
		});
		html += '</ul>';
		$('#technician-audit').html(html);
	}

	load_spare_parts_audit() {
		frappe.call({
			method: 'frappe.client.get_list',
			args: {
				doctype: 'Spare Parts Usage',
				filters: {service_request: this.service_request},
				fields: ['spare_part_item', 'item_name', 'qty_used', 'sales_price', 'status', 'reason_desc', 'transaction_date'],
				order_by: 'transaction_date asc',
				limit: 50
			},
			callback: (r) => {
				const parts = r.message || [];
				if (!parts.length) {
					$('#spare-audit').html('<p class="text-muted small">No spare parts history</p>');
					return;
				}
				const status_badge = {Active: 'success', 'Moved to Main Stock': 'info', 'Moved to Dispose Stock': 'warning', 'Deleted': 'danger'};
				let html = '<ul class="list-unstyled">';
				parts.forEach(p => {
					const esc = frappe.utils.escape_html;
					const badge = status_badge[p.status] || 'secondary';
					html += `<li class="mb-2 small">
						<strong>${esc(p.item_name || p.spare_part_item)}</strong> x${p.qty_used}<br>
						<span class="badge badge-sm badge-${badge}">${esc(p.status)}</span>
						${p.reason_desc ? ' <span class="text-muted">— ' + esc(p.reason_desc) + '</span>' : ''}<br>
						<span class="text-muted">${frappe.datetime.str_to_user(p.transaction_date)}</span>
					</li>`;
				});
				html += '</ul>';
				$('#spare-audit').html(html);
			}
		});
	}

	load_support_logs() {
		frappe.call({
			method: 'frappe.client.get_list',
			args: {
				doctype: 'Comment',
				filters: {
					reference_doctype: 'Service Request',
					reference_name: this.service_request
				},
				fields: ['content', 'comment_type', 'creation', 'owner'],
				order_by: 'creation desc',
				limit: 20
			},
			callback: (r) => {
				const logs = r.message || [];
				if (!logs.length) {
					$('#support-logs').html('<p class="text-muted small">No activity logs</p>');
					return;
				}
				let html = '<ul class="list-unstyled">';
				logs.forEach(log => {
					const esc = frappe.utils.escape_html;
					html += `<li class="mb-2 small">
						<span class="badge badge-sm badge-secondary">${esc(log.comment_type)}</span>
						<span class="text-muted ml-1">${frappe.datetime.str_to_user(log.creation)}</span>
						<span class="text-muted"> by ${esc(log.owner)}</span><br>
						<span>${esc(log.content || '')}</span>
					</li>`;
				});
				html += '</ul>';
				$('#support-logs').html(html);
			}
		});
	}

	show_assignment_dialog(type) {
		if (!this.service_request) {
			frappe.msgprint(__('Please select a Service Request first'));
			return;
		}

		let d = new frappe.ui.Dialog({
			title: __('Assign {0}', [type]),
			fields: [
				{
					fieldname: 'team',
					label: __('Team'),
					fieldtype: 'Link',
					options: 'Employee Group',
					reqd: type === 'Team'
				},
				{
					fieldname: 'user',
					label: __('User'),
					fieldtype: 'Link',
					options: 'User',
					reqd: type === 'User'
				},
				{
					fieldname: 'service_engineer',
					label: __('Service Engineer'),
					fieldtype: 'Link',
					options: 'Employee',
					reqd: type === 'Technician'
				},
				{
					fieldname: 'comments',
					label: __('Comments'),
					fieldtype: 'Small Text'
				}
			],
			primary_action_label: __('Assign'),
			primary_action: (values) => {
				frappe.call({
					method: 'frappe.client.insert',
					args: {
						doc: {
							doctype: 'Job Assignment',
							service_request: this.service_request,
							assignment_date: frappe.datetime.nowdate(),
							team: values.team,
							user: values.user,
							service_engineer: values.service_engineer,
							assignment_type: type + ' Assignment',
							comments: values.comments
						}
					},
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({message: __('Assignment created successfully'), indicator: 'green'});
							d.hide();
							this.load_assignments();
						}
					}
				});
			}
		});

		d.show();
	}

	show_spare_dialog() {
		if (!this.service_request) {
			frappe.msgprint(__('Please select a Service Request first'));
			return;
		}

		let d = new frappe.ui.Dialog({
			title: __('Add Spare Part'),
			fields: [
				{
					fieldname: 'spare_part_item',
					label: __('Spare Part'),
					fieldtype: 'Link',
					options: 'Item',
					reqd: 1
				},
				{
					fieldname: 'barcode_value',
					label: __('Barcode'),
					fieldtype: 'Data',
					reqd: 1
				},
				{
					fieldname: 'qty_used',
					label: __('Quantity'),
					fieldtype: 'Float',
					default: 1,
					reqd: 1
				},
				{
					fieldname: 'is_billable',
					label: __('Billable'),
					fieldtype: 'Check',
					default: 1
				}
			],
			primary_action_label: __('Add'),
			primary_action: (values) => {
				frappe.call({
					method: 'frappe.client.insert',
					args: {
						doc: {
							doctype: 'Spare Parts Usage',
							service_request: this.service_request,
							transaction_date: frappe.datetime.nowdate(),
							spare_part_item: values.spare_part_item,
							barcode_value: values.barcode_value,
							qty_used: values.qty_used,
							is_billable: values.is_billable
						}
					},
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({message: __('Spare part added successfully'), indicator: 'green'});
							d.hide();
							this.load_spare_parts();
						}
					}
				});
			}
		});

		d.show();
	}

	move_spare_to_main(spare_name) {
		frappe.prompt({
			label: __('Reason'),
			fieldname: 'reason',
			fieldtype: 'Select',
			options: 'Wrong Spare\nNot Suitable\nOrder Cancel\nReplace',
			reqd: 1
		}, (values) => {
			frappe.call({
				method: 'gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage.move_to_main_stock',
				args: {
					name: spare_name,
					reason: values.reason
				},
				callback: () => {
					frappe.show_alert({message: __('Moved to main stock'), indicator: 'green'});
					this.load_spare_parts();
				}
			});
		}, __('Move to Main Stock'));
	}

	move_spare_to_dispose(spare_name) {
		frappe.prompt({
			label: __('Reason'),
			fieldname: 'reason',
			fieldtype: 'Select',
			options: 'Manufacture Defect\nDamage\nLost',
			reqd: 1
		}, (values) => {
			frappe.call({
				method: 'gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage.move_to_dispose_stock',
				args: {
					name: spare_name,
					reason: values.reason
				},
				callback: () => {
					frappe.show_alert({message: __('Moved to dispose stock'), indicator: 'orange'});
					this.load_spare_parts();
				}
			});
		}, __('Move to Dispose'));
	}

	mark_service_complete(status) {
		if (!this.service_request) {
			frappe.msgprint(__('Please select a Service Request first'));
			return;
		}

		frappe.prompt({
			label: __('Closing Comments'),
			fieldname: 'comments',
			fieldtype: 'Small Text',
			reqd: 1
		}, (values) => {
			frappe.call({
				method: 'frappe.client.set_value',
				args: {
					doctype: 'Service Request',
					name: this.service_request,
					fieldname: {
						decision: status === 'Repaired' ? 'Completed' : 'In Service',
						remarks: values.comments + `\n\nService Status: ${status}`
					}
				},
				callback: () => {
					frappe.show_alert({message: __('Service marked as {0}', [status]), indicator: 'green'});
					this.load_service_request();
				}
			});
		}, __('Close Service - {0}', [status]));
	}

	print_job_sheet() {
		if (!this.service_request) {
			frappe.msgprint(__('Please select a Service Request first'));
			return;
		}

		frappe.set_route('print', 'Service Request', this.service_request);
	}

	setup_realtime() {
		// Listen for real-time updates
		frappe.realtime.on('job_assignment_update', () => {
			if (this.service_request) {
				this.load_assignments();
			}
		});

		frappe.realtime.on('spare_parts_update', () => {
			if (this.service_request) {
				this.load_spare_parts();
			}
		});
	}
}
