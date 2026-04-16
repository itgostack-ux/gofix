// Copyright (c) 2025, GoStack and contributors
// For license information, please see license.txt

frappe.ui.form.on('Service Request', {
	refresh: function(frm) {
		// Set status color indicator
		set_status_indicator(frm);
		
		// Show workflow status dashboard
		show_workflow_status(frm);
		
		// Show open requests if customer is selected
		if (frm.doc.customer && !frm.is_new()) {
			show_open_requests(frm);
		}
		
		// Add Generate Barcode button for draft documents
		if (frm.doc.docstatus === 0 && frm.doc.device_item && !frm.doc.is_barcode_generated) {
			frm.add_custom_button(__('Generate Barcode'), function() {
				generate_barcode(frm);
			}, __('Tools')).addClass('btn-secondary');
		}
		
		// Add Job Tracker button
		if (!frm.is_new()) {
			frm.add_custom_button(__('Open Job Tracker'), function() {
				frappe.set_route('job-tracker', {'service_request': frm.doc.name});
			}, __('Tools')).addClass('btn-info');
		}
		
		// ACCEPT / REJECT buttons for Service Requests with Draft decision
		if (frm.doc.decision === "Draft" && !frm.is_new() && frm.doc.docstatus === 1) {
			// Accept button (green)
			frm.add_custom_button(__('Accept'), function() {
				accept_service_request(frm);
			}).addClass('btn-success');
			
			// Reject button (red)
			frm.add_custom_button(__('Reject'), function() {
				reject_service_request(frm);
			}).addClass('btn-danger');
		}

		// NOT REPAIRABLE button — available at any active stage
		if (!frm.is_new() && frm.doc.docstatus === 1
			&& !["Delivered", "Withdrawn", "Cancelled", "Rejected"].includes(frm.doc.decision)) {
			frm.add_custom_button(__('Not Repairable'), function() {
				show_not_repairable_dialog(frm);
			}, __('Actions')).addClass('btn-danger');
		}

		// SPARE RECOVERY button — show when SR is Rejected and has pending consumed spares
		if (!frm.is_new() && frm.doc.decision === "Rejected"
			&& frm.doc.repairability_status && ["Not Repairable", "BER"].includes(frm.doc.repairability_status)) {
			frappe.xcall("gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage.get_pending_recovery_spares", {
				service_request: frm.doc.name,
			}).then(pending => {
				if (pending && pending.length) {
					frm.dashboard.set_headline(
						__('<span style="color:#dc2626"><i class="fa fa-exclamation-triangle"></i> {0} consumed spare(s) need recovery disposition</span>', [pending.length])
					);
					frm.add_custom_button(__('Recover Spares ({0})').replace('{0}', pending.length), function() {
						show_spare_recovery_form(frm, pending);
					}).addClass('btn-warning');
				} else {
					// All spares recovered → show Return Device button
					frm.add_custom_button(__('Return Device to Customer'), function() {
						create_return_delivery(frm);
					}, __('Actions')).addClass('btn-primary');
				}
			});
		}
		
		// View Service Order button if created (make it prominent)
		if (frm.doc.service_order) {
			frm.add_custom_button(__('📋 View Service Order'), function() {
				frappe.set_route('Form', 'Sales Order', frm.doc.service_order);
			}).addClass('btn-primary btn-lg');
			
			// Check for Job Sheets and show count
			frappe.call({
				method: 'frappe.client.get_count',
				args: {
					doctype: 'Job Assignment',
					filters: {
						service_order: frm.doc.service_order
					}
				},
				callback: function(r) {
					if (r.message && r.message > 0) {
						frm.add_custom_button(__('🔧 View Job Sheets ({0})').format(r.message), function() {
							frappe.set_route('List', 'Job Assignment', {
								'service_order': frm.doc.service_order
							});
						}).addClass('btn-info');
					}
				}
			});
			
			// Also add to Actions menu
			frm.page.add_menu_item(__('Open Service Order in New Tab'), function() {
				window.open('/app/sales-order/' + frm.doc.service_order, '_blank');
			});
		}
		
		// Add link to view all Service Orders
		if (!frm.is_new()) {
			frm.page.add_menu_item(__('View All Service Orders'), function() {
				frappe.set_route('List', 'Sales Order', {
					'is_service_order': 1
				});
			});
			
			frm.page.add_menu_item(__('View All Job Sheets'), function() {
				frappe.set_route('List', 'Job Assignment');
			});
		}
		
		// Filter device item to only show Devices item group
		frm.set_query('device_item', function() {
			return {
				filters: {
				'item_group': ['in', ['Devices', 'Mobile Phones', 'Laptops', 'Tablets']],
				'has_variants': 0
		
		// Filter service items to only show Services
		frm.fields_dict.service_items.grid.get_field('service_item').get_query = function() {
			return {
				filters: {
				'item_group': ['in', ['Services', 'Repair Services', 'Installation', 'Consultation']],
				'has_variants': 0
		
		// Filter spare parts to only show Spare Parts
		frm.fields_dict.spare_parts.grid.get_field('spare_part_item').get_query = function() {
			return {
				filters: {
				'item_group': ['in', ['Spare Parts', 'Mobile Parts', 'Laptop Parts', 'Tablet Parts']],
				'has_variants': 0
		
		// Filter serial no based on selected device item
		frm.set_query('serial_no', function() {
			if (frm.doc.device_item) {
				return {
					filters: {
						'item_code': frm.doc.device_item
					}
				};
			}
		});

		// ── Issue → Solution → Spare cascade filters ──────────────
		setup_cascade_filters(frm);
	},
	
	customer: function(frm) {
		// Fetch customer details and check for open requests
		if (frm.doc.customer) {
			frappe.call({
				method: 'gofix.gofix_services.doctype.service_request.service_request.get_customer_details',
				args: {
					customer: frm.doc.customer
				},
				callback: function(r) {
					if (r.message) {
						if (r.message.customer_name) {
							frm.set_value('customer_name', r.message.customer_name);
						}
						if (r.message.mobile_no && !frm.doc.contact_number) {
							frm.set_value('contact_number', r.message.mobile_no);
						}
						if (r.message.email_id && !frm.doc.email) {
							frm.set_value('email', r.message.email_id);
						}
						if (r.message.gstin && !frm.doc.gstin) {
							frm.set_value('gstin', r.message.gstin);
						}
						if (r.message.pan && !frm.doc.pan) {
							frm.set_value('pan', r.message.pan);
						}
						
						// Check for open requests
						if (!frm.is_new()) {
							show_open_requests(frm);
						}
					}
				}
			});
		}
	},
	
	device_item: function(frm) {
		// Fetch device item details
		if (frm.doc.device_item) {
			frappe.call({
				method: 'frappe.client.get',
				args: {
					doctype: 'Item',
					name: frm.doc.device_item
				},
				callback: function(r) {
					if (r.message) {
						frm.set_value('device_item_name', r.message.item_name);
						frm.set_value('brand', r.message.brand);
					}
				}
			});
		}
		
		// Clear serial no when device item changes
		if (frm.doc.serial_no) {
			frm.set_value('serial_no', '');
		}
	},
	
	serial_no: function(frm) {
		// Fetch warranty details from serial no via ch_item_master warranty API
		if (frm.doc.serial_no) {
			frappe.call({
				method: 'frappe.client.get',
				args: {
					doctype: 'Serial No',
					name: frm.doc.serial_no
				},
				callback: function(r) {
					if (r.message) {
						// Validate serial belongs to device item
						if (frm.doc.device_item && r.message.item_code !== frm.doc.device_item) {
							frappe.msgprint(__('Serial No {0} does not belong to Item {1}', 
								[frm.doc.serial_no, frm.doc.device_item]));
							frm.set_value('serial_no', '');
							return;
						}

						// Try CH Sold Plan warranty lookup
						frappe.call({
							method: 'ch_item_master.ch_item_master.warranty_api.check_warranty',
							args: {
								serial_no: frm.doc.serial_no,
								company: frm.doc.company
							},
							callback: function(wr) {
								if (wr.message && wr.message.warranty_covered) {
									frm.set_value('warranty_status', 'Under Warranty');
									let plan = wr.message.covering_plan || {};
									frm.set_value('warranty_plan', plan.warranty_plan || '');
									frm.set_value('warranty_plan_name', plan.plan_title || '');
									frm.set_value('warranty_deductible', plan.deductible_amount || 0);
									frm.set_value('warranty_expiry_date', plan.end_date || '');
								} else {
									// Fallback to Serial No warranty_expiry_date
									frm.set_value('warranty_plan', '');
									frm.set_value('warranty_plan_name', '');
									frm.set_value('warranty_deductible', 0);
									if (r.message.warranty_expiry_date) {
										frm.set_value('warranty_expiry_date', r.message.warranty_expiry_date);
										let today = frappe.datetime.get_today();
										if (r.message.warranty_expiry_date >= today) {
											frm.set_value('warranty_status', 'Under Warranty');
										} else {
											frm.set_value('warranty_status', 'Out of Warranty');
										}
									} else {
										frm.set_value('warranty_status', 'No Warranty');
									}
								}
							},
							error: function() {
								// ch_item_master not available — basic Serial No fallback
								frm.set_value('warranty_plan', '');
								frm.set_value('warranty_plan_name', '');
								frm.set_value('warranty_deductible', 0);
								if (r.message.warranty_expiry_date) {
									frm.set_value('warranty_expiry_date', r.message.warranty_expiry_date);
									let today = frappe.datetime.get_today();
									if (r.message.warranty_expiry_date >= today) {
										frm.set_value('warranty_status', 'Under Warranty');
									} else {
										frm.set_value('warranty_status', 'Out of Warranty');
									}
								} else {
									frm.set_value('warranty_status', 'No Warranty');
								}
							}
						});
					}
				}
			});
		}
	},
	
	walkin_status: function(frm) {
		// Handle withdrawal - auto-set status to Cancelled
		if (frm.doc.walkin_status === 'Withdrawn') {
			frm.set_value('status', 'Cancelled');
		}
	},
	
	priority: function(frm) {
		// Set expected completion based on priority
		if (frm.doc.priority && frm.doc.service_date && !frm.doc.expected_completion_date) {
			let days = 7; // Medium priority default
			if (frm.doc.priority === 'High') days = 3;
			if (frm.doc.priority === 'Urgent') days = 1;
			if (frm.doc.priority === 'Low') days = 14;
			
			let completion_date = frappe.datetime.add_days(frm.doc.service_date, days);
			frm.set_value('expected_completion_date', completion_date);
		}
	},
	
	service_date: function(frm) {
		// Update expected completion when service date changes
		if (frm.doc.service_date && frm.doc.priority && !frm.doc.expected_completion_date) {
			frm.trigger('priority');
		}
	},
	
	source_warehouse: function(frm) {
		// Fetch state details from warehouse when selected
		if (frm.doc.source_warehouse) {
			frappe.call({
				method: 'gofix.gofix_services.doctype.service_request.service_request.get_warehouse_state',
				args: {
					warehouse: frm.doc.source_warehouse
				},
				callback: function(r) {
					if (r.message) {
						if (r.message.state_name) {
							frm.set_value('state_name', r.message.state_name);
						}
						if (r.message.state_code) {
							frm.set_value('state_code', r.message.state_code);
						}
					}
				}
			});
		}
	}
});

// Service Items child table handlers
frappe.ui.form.on('Service Request Service Item', {
	service_item: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.service_item) {
			// Fetch item details
			frappe.call({
				method: 'frappe.client.get',
				args: {
					doctype: 'Item',
					name: row.service_item
				},
				callback: function(r) {
					if (r.message) {
						frappe.model.set_value(cdt, cdn, 'item_name', r.message.item_name);
						frappe.model.set_value(cdt, cdn, 'description', r.message.description);
					}
				}
			});
		}
	},
	
	estimated_cost: function(frm) {
		calculate_total_estimated_cost(frm);
	},
	
	service_items_remove: function(frm) {
		calculate_total_estimated_cost(frm);
	}
});

// Spare Parts child table handlers
frappe.ui.form.on('Service Request Spare Part', {
	spare_part_item: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.spare_part_item) {
			// Fetch item details
			frappe.call({
				method: 'frappe.client.get',
				args: {
					doctype: 'Item',
					name: row.spare_part_item
				},
				callback: function(r) {
					if (r.message) {
						frappe.model.set_value(cdt, cdn, 'item_name', r.message.item_name);
						frappe.model.set_value(cdt, cdn, 'description', r.message.description);
						frappe.model.set_value(cdt, cdn, 'uom', r.message.stock_uom);
						frappe.model.set_value(cdt, cdn, 'rate', r.message.standard_rate || 0);
					}
				}
			});
		}
	},
	
	qty: function(frm, cdt, cdn) {
		calculate_spare_part_amount(frm, cdt, cdn);
	},
	
	rate: function(frm, cdt, cdn) {
		calculate_spare_part_amount(frm, cdt, cdn);
	},
	
	spare_parts_remove: function(frm) {
		calculate_total_estimated_cost(frm);
	}
});

function calculate_spare_part_amount(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	let amount = flt(row.qty) * flt(row.rate);
	frappe.model.set_value(cdt, cdn, 'amount', amount);
	calculate_total_estimated_cost(frm);
}

function calculate_total_estimated_cost(frm) {
	let total = 0;
	
	// Add service items cost
	if (frm.doc.service_items) {
		frm.doc.service_items.forEach(function(item) {
			total += flt(item.estimated_cost);
		});
	}
	
	// Add spare parts cost
	if (frm.doc.spare_parts) {
		frm.doc.spare_parts.forEach(function(part) {
			total += flt(part.amount);
		});
	}
	
	frm.set_value('total_estimated_cost', total);
}

function set_status_indicator(frm) {
	// Set color indicator based on status
	let color_map = {
		'Open': 'blue',
		'In Progress': 'orange',
		'On Hold': 'yellow',
		'Completed': 'green',
		'Cancelled': 'red'
	};
	
	if (frm.doc.status) {
		frm.page.set_indicator(__(frm.doc.status), color_map[frm.doc.status] || 'gray');
	}
	
	// Additional indicator for customer type
	if (frm.doc.customer_type === 'NEW') {
		frm.dashboard.add_indicator(__('New Customer'), 'blue');
	} else if (frm.doc.customer_type === 'REGULAR') {
		frm.dashboard.add_indicator(__('Regular Customer'), 'green');
	}
	
	// Warranty status indicator
	if (frm.doc.warranty_status === 'Under Warranty') {
		let label = __('Under Warranty');
		if (frm.doc.warranty_plan_name) {
			label += ' — ' + frm.doc.warranty_plan_name;
		}
		frm.dashboard.add_indicator(label, 'green');
		if (frm.doc.warranty_deductible) {
			frm.dashboard.add_indicator(
				__('Deductible: {0}', [format_currency(frm.doc.warranty_deductible)]),
				'blue'
			);
		}
	} else if (frm.doc.warranty_status === 'Out of Warranty') {
		frm.dashboard.add_indicator(__('Out of Warranty'), 'orange');
	}
}

function show_open_requests(frm) {
	if (!frm.doc.customer) return;
	
	frappe.call({
		method: 'gofix.gofix_services.doctype.service_request.service_request.get_open_requests',
		args: {
			name: frm.doc.name
		},
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				// Show open requests in a grid
				let html = '<div class="open-requests-section">';
				html += '<h6>' + __('Open Service Requests for this Customer') + '</h6>';
				html += '<table class="table table-bordered table-condensed">';
				html += '<thead><tr>';
				html += '<th>' + __('Request No') + '</th>';
				html += '<th>' + __('Date') + '</th>';
				html += '<th>' + __('Device') + '</th>';
				html += '<th>' + __('Status') + '</th>';
				html += '<th>' + __('Advance Paid') + '</th>';
				html += '</tr></thead><tbody>';
				
				r.message.forEach(function(req) {
					let esc = frappe.utils.escape_html;
					html += '<tr>';
					html += '<td><a href="/desk/service-request/' + encodeURIComponent(req.name) + '">' + esc(req.name) + '</a></td>';
					html += '<td>' + frappe.datetime.str_to_user(req.service_date) + '</td>';
					html += '<td>' + esc(req.device_item_name || '') + '</td>';
					html += '<td>' + esc(req.status) + '</td>';
					html += '<td>' + format_currency(req.advance_amount || 0) + '</td>';
					html += '</tr>';
				});
				
				html += '</tbody></table></div>';
				
				frm.dashboard.add_section(html);
			}
		}
	});
}

function get_customer_contact(frm, customer) {
	// Get customer's primary contact
	frappe.call({
		method: 'frappe.client.get_list',
		args: {
			doctype: 'Dynamic Link',
			filters: {
				'link_doctype': 'Customer',
				'link_name': customer,
				'parenttype': 'Contact'
			},
			fields: ['parent'],
			limit: 1
		},
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				frappe.call({
					method: 'frappe.client.get',
					args: {
						doctype: 'Contact',
						name: r.message[0].parent
					},
					callback: function(r) {
						if (r.message) {
							if (!frm.doc.contact_number && r.message.mobile_no) {
								frm.set_value('contact_number', r.message.mobile_no);
							}
							if (!frm.doc.email && r.message.email_id) {
								frm.set_value('email', r.message.email_id);
							}
						}
					}
				});
			}
		}
	});
}

function create_service_invoice(frm) {
	frappe.call({
		method: 'gofix.gofix_services.doctype.service_request.service_request.create_service_invoice',
		args: {
			name: frm.doc.name
		},
		callback: function(r) {
			if (r.message) {
				frappe.msgprint(__('Service Invoice created successfully'));
				frm.reload_doc();
			}
		}
	});
}

function generate_barcode(frm) {
	frappe.confirm(
		__('Generate a unique barcode for this service request?'),
		function() {
			// User confirmed, call server method
			frappe.call({
				method: 'gofix.gofix_services.doctype.service_request.service_request.generate_barcode_manual',
				args: {
					name: frm.doc.name
				},
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({
							message: __('Barcode {0} generated successfully', [r.message]),
							indicator: 'green'
						});
						frm.reload_doc();
					}
				}
			});
		}
	);
}

// Accept Service Request - Create Service Order
function accept_service_request(frm) {
	frappe.confirm(
		__('Accept this Service Request? This will create a Service Order for detailed processing.'),
		function() {
			// Call server method to accept
			frappe.call({
				method: 'gofix.gofix_services.doctype.service_request.service_request.accept_service_request',
				args: {
					service_request: frm.doc.name
				},
				freeze: true,
				freeze_message: __('Creating Service Order...'),
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({
							message: __('Service Request Accepted! Service Order {0} created', [r.message]),
							indicator: 'green'
						}, 5);
						
						// Reload to show Service Order link
						frm.reload_doc();
					}
				}
			});
		}
	);
}

// Reject Service Request
function reject_service_request(frm) {
	frappe.prompt([
		{
			fieldname: 'rejection_reason',
			label: __('Rejection Reason'),
			fieldtype: 'Text',
			reqd: 1
		}
	],
	function(values) {
		// Call server method to reject
		frappe.call({
			method: 'gofix.gofix_services.doctype.service_request.service_request.reject_service_request',
			args: {
				service_request: frm.doc.name,
				rejection_reason: values.rejection_reason
			},
			freeze: true,
			freeze_message: __('Rejecting Service Request...'),
			callback: function(r) {
				if (r.message) {
					frappe.show_alert({
						message: __('Service Request Rejected'),
						indicator: 'red'
					}, 5);
					frm.reload_doc();
				}
			}
		});
	},
	__('Reject Service Request'),
	__('Reject')
	);
}

// Show workflow status dashboard
function show_workflow_status(frm) {
	if (frm.is_new()) return;
	
	let html = '<div class="row" style="margin: 10px 0;">';
	
	// Step 1: Service Request
	html += '<div class="col-sm-4">';
	html += '<div class="progress-step text-center">';
	html += '<div class="step-icon" style="background: #4CAF50; color: white; width: 50px; height: 50px; line-height: 50px; border-radius: 50%; margin: 0 auto; font-size: 20px;">✓</div>';
	html += '<div class="step-title" style="margin-top: 8px; font-weight: bold;">Service Request</div>';
	html += '<div class="step-status" style="color: #666;">' + frm.doc.name + '</div>';
	html += '</div></div>';
	
	// Step 2: Service Order
	if (frm.doc.service_order) {
		html += '<div class="col-sm-4">';
		html += '<div class="progress-step text-center">';
		html += '<div class="step-icon" style="background: #2196F3; color: white; width: 50px; height: 50px; line-height: 50px; border-radius: 50%; margin: 0 auto; font-size: 20px;">✓</div>';
		html += '<div class="step-title" style="margin-top: 8px; font-weight: bold;">Service Order</div>';
		html += '<div class="step-status"><a href="/desk/sales-order/' + frm.doc.service_order + '" style="color: #2196F3;">' + frm.doc.service_order + '</a></div>';
		html += '</div></div>';
		
		// Step 3: Job Sheets - check if any exist
		frappe.call({
			method: 'frappe.client.get_count',
			args: {
				doctype: 'Job Assignment',
				filters: {
					service_order: frm.doc.service_order
				}
			},
			callback: function(r) {
				if (r.message && r.message > 0) {
					let job_html = '<div class="col-sm-4">';
					job_html += '<div class="progress-step text-center">';
					job_html += '<div class="step-icon" style="background: #FF9800; color: white; width: 50px; height: 50px; line-height: 50px; border-radius: 50%; margin: 0 auto; font-size: 20px;">' + r.message + '</div>';
					job_html += '<div class="step-title" style="margin-top: 8px; font-weight: bold;">Job Sheets</div>';
					job_html += '<div class="step-status"><a href="/desk/job-assignment?service_order=' + encodeURIComponent(frm.doc.service_order) + '" style="color: #FF9800;">' + r.message + ' Active</a></div>';
					job_html += '</div></div>';
					
					$(frm.dashboard.wrapper).find('.workflow-status').append(job_html);
				}
			}
		});
	} else {
		html += '<div class="col-sm-4">';
		html += '<div class="progress-step text-center">';
		html += '<div class="step-icon" style="background: #E0E0E0; color: #999; width: 50px; height: 50px; line-height: 50px; border-radius: 50%; margin: 0 auto; font-size: 20px;">2</div>';
		html += '<div class="step-title" style="margin-top: 8px; font-weight: bold; color: #999;">Service Order</div>';
		html += '<div class="step-status" style="color: #999;">Pending</div>';
		html += '</div></div>';
		
		html += '<div class="col-sm-4">';
		html += '<div class="progress-step text-center">';
		html += '<div class="step-icon" style="background: #E0E0E0; color: #999; width: 50px; height: 50px; line-height: 50px; border-radius: 50%; margin: 0 auto; font-size: 20px;">3</div>';
		html += '<div class="step-title" style="margin-top: 8px; font-weight: bold; color: #999;">Job Sheets</div>';
		html += '<div class="step-status" style="color: #999;">Pending</div>';
		html += '</div></div>';
	}
	
	html += '</div>';
	
	frm.dashboard.add_section('<div class="workflow-status">' + html + '</div>', __('Workflow Progress'));
}

// ── Issue → Solution → Spare cascade support ──────────────────────────

function setup_cascade_filters(frm) {
	// Solution Lines: only allow solutions whose issue_category is in issue_lines
	frm.fields_dict.solution_lines && (
		frm.fields_dict.solution_lines.grid.get_field('repair_solution').get_query = function(doc, cdt, cdn) {
			let issue_cats = (doc.issue_lines || []).map(r => r.issue_category).filter(Boolean);
			return {
				filters: {
					'is_active': 1,
					'issue_category': ['in', issue_cats.length ? issue_cats : ['__none__']]
				}
			};
		}
	);

	// Spare Lines: only allow solutions that are already in solution_lines
	frm.fields_dict.spare_lines && (
		frm.fields_dict.spare_lines.grid.get_field('repair_solution').get_query = function(doc) {
			let sol_names = (doc.solution_lines || []).map(r => r.repair_solution).filter(Boolean);
			return {
				filters: {
					'name': ['in', sol_names.length ? sol_names : ['__none__']]
				}
			};
		}
	);

	// Spare Lines: spare_item filtered by Solution Spare Mapping
	frm.fields_dict.spare_lines && (
		frm.fields_dict.spare_lines.grid.get_field('spare_item').get_query = function(doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			if (!row.repair_solution) {
				frappe.msgprint(__('Please select a Solution first'));
				return { filters: { 'name': '__none__' } };
			}
			// Get mapped spare items for this solution
			return {
				query: 'gofix.gofix_services.api.get_mapped_spare_items',
				filters: { 'repair_solution': row.repair_solution }
			};
		}
	);
}

// Child table: SR Issue Line
frappe.ui.form.on('SR Issue Line', {
	issue_lines_add: function(frm, cdt, cdn) {
		// Default reported_by to Customer for new rows
		frappe.model.set_value(cdt, cdn, 'reported_by', 'Customer');
		frappe.model.set_value(cdt, cdn, 'status', 'Open');
	},
	issue_lines_remove: function(frm) {
		// Refresh solution filters since available issue categories changed
		frm.refresh_fields();
	}
});

// Child table: SR Solution Line
frappe.ui.form.on('SR Solution Line', {
	repair_solution: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (!row.repair_solution) return;
		// Auto-populate default spares when a solution is selected
		frappe.call({
			method: 'gofix.gofix_services.api.get_spares_for_solution',
			args: { repair_solution: row.repair_solution },
			callback: function(r) {
				if (r.message && r.message.length) {
					r.message.forEach(function(spare) {
						if (spare.is_mandatory) {
							let child = frm.add_child('spare_lines');
							frappe.model.set_value(child.doctype, child.name, 'repair_solution', row.repair_solution);
							frappe.model.set_value(child.doctype, child.name, 'spare_item', spare.spare_item);
							frappe.model.set_value(child.doctype, child.name, 'qty', spare.default_qty || 1);
						}
					});
					frm.refresh_field('spare_lines');
				}
			}
		});
	},
	solution_lines_add: function(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'status', 'Planned');
	}
});

// Child table: SR Spare Line
frappe.ui.form.on('SR Spare Line', {
	qty: function(frm, cdt, cdn) {
		calculate_spare_line_amount(cdt, cdn);
	},
	rate: function(frm, cdt, cdn) {
		calculate_spare_line_amount(cdt, cdn);
	}
});

function calculate_spare_line_amount(cdt, cdn) {
	let row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, 'amount', flt(row.qty) * flt(row.rate));
}

// ── Not Repairable Flow ──────────────────────────────────────────────────────

const OPS_API = "gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub";

function show_not_repairable_dialog(frm) {
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
				sr_name: frm.doc.name,
				status: values.status,
				reason: values.reason,
			}).then(r => {
				d.hide();
				frappe.show_alert({ message: r.message, indicator: 'orange' });
				if (r.needs_spare_recovery && r.pending_spares && r.pending_spares.length) {
					show_spare_recovery_form(frm, r.pending_spares);
				} else {
					frm.reload_doc();
				}
			}).catch(() => d.enable_primary_action());
		},
	});
	d.show();
}

function show_spare_recovery_form(frm, pending) {
	let rows_html = pending.map((sp, idx) => `
		<tr data-idx="${idx}" data-spu="${frappe.utils.escape_html(sp.name)}">
			<td>${idx + 1}</td>
			<td>${frappe.utils.escape_html(sp.item_name || sp.spare_part_item)}</td>
			<td class="text-center">${sp.qty_used}</td>
			<td>
				<select class="form-control input-sm spu-disposition" data-idx="${idx}">
					<option value="">-- select --</option>
					<option value="Good - Back to Stock">Good - Back to Stock</option>
					<option value="Faulty - Supplier Return">Faulty - Supplier Return</option>
					<option value="Damaged by Technician">Damaged by Technician</option>
				</select>
			</td>
			<td><input type="text" class="form-control input-sm spu-remarks" data-idx="${idx}" placeholder="Optional"></td>
			<td class="text-center spu-status" data-idx="${idx}">⏳</td>
		</tr>
	`).join('');

	let d = new frappe.ui.Dialog({
		title: __('Recover Spares — {0}', [frm.doc.name]),
		size: 'large',
		fields: [
			{
				fieldtype: 'HTML', fieldname: 'spare_table',
				options: `
					<p class="text-muted">${__('Select a disposition for each consumed spare before returning device to customer.')}</p>
					<table class="table table-bordered table-sm">
						<thead><tr>
							<th>#</th><th>${__('Spare')}</th><th class="text-center">${__('Qty')}</th>
							<th>${__('Disposition')}</th><th>${__('Remarks')}</th><th class="text-center">${__('Status')}</th>
						</tr></thead>
						<tbody>${rows_html}</tbody>
					</table>`,
			},
		],
		primary_action_label: __('Recover All'),
		primary_action() {
			let entries = [];
			let $body = d.$wrapper;
			let all_valid = true;
			pending.forEach((sp, idx) => {
				let disp = $body.find(`.spu-disposition[data-idx="${idx}"]`).val();
				let rem = $body.find(`.spu-remarks[data-idx="${idx}"]`).val();
				if (!disp) {
					$body.find(`.spu-disposition[data-idx="${idx}"]`).css('border-color', 'red');
					all_valid = false;
				}
				entries.push({ spu_name: sp.name, disposition: disp, remarks: rem, idx });
			});
			if (!all_valid) {
				frappe.msgprint(__('Please select a disposition for every spare.'));
				return;
			}
			d.disable_primary_action();
			process_spare_recoveries(frm, entries, d, 0);
		},
		secondary_action_label: __('Skip for Now'),
		secondary_action() {
			d.hide();
			frm.reload_doc();
		},
	});
	d.show();
}

function process_spare_recoveries(frm, entries, dlg, idx) {
	if (idx >= entries.length) {
		dlg.hide();
		frappe.show_alert({ message: __('All spares recovered'), indicator: 'green' });
		frm.reload_doc();
		return;
	}
	let e = entries[idx];
	let $body = dlg.$wrapper;
	$body.find(`.spu-status[data-idx="${e.idx}"]`).html('<i class="fa fa-spinner fa-spin"></i>');

	frappe.xcall(`${OPS_API}.recover_spare_from_ops_hub`, {
		sr_name: frm.doc.name,
		spu_name: e.spu_name,
		disposition: e.disposition,
		remarks: e.remarks || '',
	}).then(() => {
		$body.find(`.spu-status[data-idx="${e.idx}"]`).html('<i class="fa fa-check text-success"></i>');
		process_spare_recoveries(frm, entries, dlg, idx + 1);
	}).catch(() => {
		$body.find(`.spu-status[data-idx="${e.idx}"]`).html('<i class="fa fa-times text-danger"></i>');
		process_spare_recoveries(frm, entries, dlg, idx + 1);
	});
}

function create_return_delivery(frm) {
	frappe.confirm(
		__('Return unrepaired device ({0}) to customer? This will mark the SR as Delivered.', [frm.doc.repairability_status]),
		() => {
			frappe.xcall(`${OPS_API}.return_unrepaired_device`, {
				sr_name: frm.doc.name,
				remarks: '',
			}).then(r => {
				frappe.show_alert({ message: r.message, indicator: 'green' });
				frm.reload_doc();
			});
		}
	);
}
