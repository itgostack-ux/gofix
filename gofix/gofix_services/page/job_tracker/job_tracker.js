frappe.pages['job-tracker'].on_page_load = function(wrapper) {
const page = frappe.ui.make_app_page({
parent: wrapper,
title: 'Job Tracker',
single_column: true,
});
frappe.job_tracker = new JobTrackerBoard(page);
};

// ─────────────────────────────────────────────────────────────────────────────
// Job Tracker — Kanban Board
// Managers see ALL active service requests in status columns.
// Click any card to open the detail drawer. Assign from the drawer.
// ─────────────────────────────────────────────────────────────────────────────
class JobTrackerBoard {
constructor(page) {
this.page       = page;
this.parent     = $(page.body);
this._detail    = null;
this._data      = null;
this._filters   = {
company: this._active_company(),
date_from: frappe.datetime.add_days(frappe.datetime.nowdate(), -30),
date_to:   frappe.datetime.nowdate(),
warehouse: null,
search:    '',
};
this.setup();
}

_active_company() {
const lock = window.ch_erp15 && window.ch_erp15.company_lock;
if (lock && typeof lock.active_company === 'function') {
return lock.active_company() || '';
}
return frappe.defaults.get_user_default('Company') || frappe.defaults.get_user_default('company') || '';
}

setup() {
this._build_toolbar();
this._build_layout();
this._load();
this._setup_realtime();
}

_build_toolbar() {
this.page.add_field({
fieldname: 'date_from', label: __('From'), fieldtype: 'Date',
default: this._filters.date_from,
change: () => { this._filters.date_from = this.page.fields_dict.date_from.get_value(); this._load(); },
});
this.page.add_field({
fieldname: 'date_to', label: __('To'), fieldtype: 'Date',
default: this._filters.date_to,
change: () => { this._filters.date_to = this.page.fields_dict.date_to.get_value(); this._load(); },
});
this.page.add_field({
fieldname: 'warehouse', label: __('Store'), fieldtype: 'Link', options: 'Warehouse',
get_query: () => ({
query: 'gofix.gofix_services.store_context.warehouse_query',
filters: { company: this._active_company() || this._filters.company || '' },
}),
change: () => { this._filters.warehouse = this.page.fields_dict.warehouse.get_value() || null; this._load(); },
});
this.page.add_button(__('Refresh'), () => this._load(), { icon: 'refresh' });
}

_build_layout() {
this.parent.html(`
<div class="jt-wrapper">
<div class="jt-summary" id="jt-summary"></div>
<div class="jt-search-bar">
<input type="text" id="jt-search" class="form-control input-sm jt-search-input"
placeholder="${__('Search SR, customer, device, serial…')}">
</div>
<div class="jt-board" id="jt-board">
<div class="jt-loading"><i class="fa fa-spinner fa-spin fa-2x"></i><br>${__('Loading…')}</div>
</div>
<div class="jt-drawer-overlay" id="jt-drawer-overlay"></div>
<div class="jt-drawer" id="jt-drawer">
<div class="jt-drawer-header">
<span class="jt-drawer-title" id="jt-drawer-title">—</span>
<span class="jt-drawer-close" id="jt-drawer-close"><i class="fa fa-times"></i></span>
</div>
<div class="jt-drawer-body" id="jt-drawer-body">
<div class="jt-loading"><i class="fa fa-spinner fa-spin"></i></div>
</div>
</div>
</div>
`);

let _st;
this.parent.find('#jt-search').on('input', e => {
clearTimeout(_st);
_st = setTimeout(() => { this._filters.search = e.target.value.trim(); this._render(this._data); }, 350);
});
this.parent.on('click', '#jt-drawer-close, #jt-drawer-overlay', () => this._close_drawer());
}

_load() {
this.parent.find('#jt-board').html(
`<div class="jt-loading"><i class="fa fa-spinner fa-spin fa-2x"></i><br>${__('Loading…')}</div>`
);
frappe.xcall('gofix.gofix_services.page.job_tracker.job_tracker.get_board_data', {
company: this._active_company() || this._filters.company || '',
warehouse: this._filters.warehouse || '',
date_from: this._filters.date_from,
date_to:   this._filters.date_to,
}).then(data => {
this._data = data;
this._render(data);
}).catch(() => {
this.parent.find('#jt-board').html(
`<div class="jt-loading text-danger"><i class="fa fa-exclamation-circle"></i> ${__('Failed to load jobs')}</div>`
);
});
}

_render(data) {
if (!data) return;
const search = (this._filters.search || '').toLowerCase();

// Summary pills
const sumHTML = data.columns.map(col => {
const count = (data.cards_by_status[col.status] || []).length;
return `<div class="jt-sum-pill" style="border-color:${col.color};" data-status="${frappe.utils.escape_html(col.status)}">
<span class="jt-sum-count" style="color:${col.color};">${count}</span>
<span class="jt-sum-label">${__(col.label)}</span>
</div>`;
}).join('');
this.parent.find('#jt-summary').html(sumHTML);

// Board columns
let boardHTML = '';
data.columns.forEach(col => {
const all_cards = data.cards_by_status[col.status] || [];
const cards = all_cards.filter(sr => {
if (!search) return true;
return (sr.name + ' ' + (sr.customer_name || '') + ' ' + (sr.device_item_name || '') + ' ' + (sr.serial_no || '') + ' ' + (sr.contact_number || ''))
.toLowerCase().includes(search);
});
const cardHTML = cards.map(sr => this._card_html(sr)).join('') ||
`<div class="jt-no-cards text-muted"><i class="fa fa-inbox"></i><br>${__('No jobs')}</div>`;

boardHTML += `
<div class="jt-col">
<div class="jt-col-header" style="border-top:3px solid ${col.color};">
<span class="jt-col-title">
<i class="fa ${col.icon}" style="color:${col.color};"></i> ${__(col.label)}
</span>
<span class="badge jt-col-badge" style="background:${col.color};">${cards.length}</span>
</div>
<div class="jt-col-body" data-status="${frappe.utils.escape_html(col.status)}">
${cardHTML}
</div>
</div>`;
});

this.parent.find('#jt-board').html(boardHTML);
this.parent.find('.jt-card').on('click', e => {
const name = $(e.currentTarget).closest('.jt-card').data('name');
if (name) this._open_drawer(name);
});

// Summary pill click → open SR list with that status filter
this.parent.find('.jt-sum-pill').on('click', e => {
const status = $(e.currentTarget).data('status');
const filters = { decision: status };
if (status === 'Rejected') {
filters.repairability_status = ['in', ['Not Repairable', 'BER']];
}
const co = this._active_company() || this._filters.company || '';
if (co) filters.company = co;
if (this._filters.warehouse) filters.source_warehouse = this._filters.warehouse;
frappe.set_route('List', 'Service Request', filters);
});
}

_card_html(sr) {
const esc = frappe.utils.escape_html;
const pColors = { High: '#ef4444', Urgent: '#dc2626', Medium: '#f59e0b', Low: '#10b981' };
const pColor  = pColors[sr.priority] || '#6b7280';
const today   = frappe.datetime.nowdate();
const daysOpen = frappe.datetime.get_day_diff(today, sr.service_date);
const overdue  = sr.expected_completion_date && sr.expected_completion_date < today;

return `
<div class="jt-card${overdue ? ' jt-card-overdue' : ''}" data-name="${esc(sr.name)}">
<div class="jt-card-head">
<span class="jt-card-sr">${esc(sr.name)}</span>
<span class="jt-card-priority" style="background:${pColor}20;color:${pColor};">${esc(sr.priority)}</span>
</div>
<div class="jt-card-customer">
<i class="fa fa-user text-muted"></i>
<b>${esc(sr.customer_name || sr.customer)}</b>
${sr.contact_number ? `<span class="text-muted"> · ${esc(sr.contact_number)}</span>` : ''}
</div>
<div class="jt-card-device">
<i class="fa fa-mobile text-muted"></i>
${esc(sr.device_item_name || sr.device_item)}
${sr.serial_no ? `<code class="jt-card-serial"> ${esc(sr.serial_no)}</code>` : ''}
</div>
${sr.issue_category ? `<div class="jt-card-issue"><i class="fa fa-tag text-muted"></i> ${esc(sr.issue_category)}</div>` : ''}
<div class="jt-card-footer">
<span class="jt-card-meta"><i class="fa fa-calendar text-muted"></i> ${frappe.datetime.str_to_user(sr.service_date)}</span>
<span class="jt-card-age ${daysOpen > 7 ? 'jt-age-warn' : ''}">${daysOpen}d</span>
</div>
${sr.engineer_name
? `<div class="jt-card-engineer">
<i class="fa fa-wrench text-muted"></i>
${esc(sr.engineer_name)}
${sr.assignment_status ? `<span class="jt-eng-badge">${esc(sr.assignment_status)}</span>` : ''}
</div>`
: `<div class="jt-card-unassigned"><i class="fa fa-user-times"></i> ${__('Unassigned')}</div>`
}
</div>`;
}

// ─────────── Detail Drawer ───────────────────────────────────────────────

_open_drawer(sr_name) {
this._detail = sr_name;
this.parent.find('#jt-drawer-title').text(sr_name);
this.parent.find('#jt-drawer-body').html(
`<div class="jt-loading"><i class="fa fa-spinner fa-spin"></i></div>`
);
this.parent.find('#jt-drawer').addClass('jt-drawer-open');
this.parent.find('#jt-drawer-overlay').addClass('jt-overlay-open');

frappe.xcall('gofix.gofix_services.page.job_tracker.job_tracker.get_sr_detail', { sr_name })
.then(d => this._render_drawer(d))
.catch(() => {
this.parent.find('#jt-drawer-body').html(
`<p class="text-danger">${__('Failed to load details')}</p>`
);
});
}

_close_drawer() {
this._detail = null;
this.parent.find('#jt-drawer').removeClass('jt-drawer-open');
this.parent.find('#jt-drawer-overlay').removeClass('jt-overlay-open');
}

_render_drawer(d) {
const esc = frappe.utils.escape_html;
const sr   = d.sr;
const ci   = d.customer_info || {};
const asgn = d.assignments  || [];
const parts = d.spare_parts || [];

const STATUS_COLORS = {
Draft:'#6b7280', Accepted:'#3b82f6', 'In Service':'#f59e0b',
Completed:'#10b981', Invoiced:'#8b5cf6', Delivered:'#10b981',
Withdrawn:'#ef4444', Rejected:'#ef4444', Cancelled:'#ef4444',
};
const sc = STATUS_COLORS[sr.decision] || '#6b7280';

const assign_html = asgn.length
? `<table class="jt-table">
<thead><tr><th>${__('Date')}</th><th>${__('Technician')}</th><th>${__('Type')}</th><th>${__('Status')}</th></tr></thead>
<tbody>${asgn.map(a => `
<tr>
<td>${frappe.datetime.str_to_user(a.assignment_date)}</td>
<td>${esc(a.engineer_display)}</td>
<td>${esc(a.job_type || a.assignment_type || '—')}</td>
<td><span class="jt-badge">${esc(a.assignment_status)}</span></td>
</tr>`).join('')}</tbody>
</table>`
: `<p class="text-muted small">${__('No assignments yet')}</p>`;

const parts_html = parts.length
? `<table class="jt-table">
<thead><tr><th>${__('Part')}</th><th>${__('Qty')}</th><th>${__('Price')}</th><th>${__('Status')}</th></tr></thead>
<tbody>${parts.map(p => `
<tr>
<td>${esc(p.item_name || p.spare_part_item)}</td>
<td>${p.qty_used}</td>
<td>${format_currency(p.sales_price || 0)}</td>
<td>${esc(p.status)}</td>
</tr>`).join('')}</tbody>
</table>`
: `<p class="text-muted small">${__('No spare parts used')}</p>`;

const today = frappe.datetime.nowdate();
const daysOpen = frappe.datetime.get_day_diff(today, sr.service_date);

this.parent.find('#jt-drawer-body').html(`
<div class="jt-dw-actions">
<a href="/desk/service-request/${encodeURIComponent(sr.name)}" target="_blank"
   class="btn btn-xs btn-default"><i class="fa fa-external-link"></i> ${__('Open Form')}</a>
<button class="btn btn-xs btn-primary" id="jt-btn-assign">
<i class="fa fa-user-plus"></i> ${__('Assign Technician')}</button>
<button class="btn btn-xs btn-default" id="jt-btn-print">
<i class="fa fa-print"></i> ${__('Print')}</button>
</div>

<div class="jt-dw-badges">
<span class="jt-badge" style="background:${sc}20;color:${sc};">${esc(sr.decision)}</span>
<span class="jt-badge jt-badge-pri">${esc(sr.priority || '—')}</span>
<span class="jt-badge">${esc(sr.warranty_status || __('No Warranty'))}</span>
<span class="jt-badge text-muted">${daysOpen} ${__('day(s) open')}</span>
</div>

<div class="jt-dw-grid">
<div class="jt-dw-box">
<h6 class="jt-dw-h"><i class="fa fa-user"></i> ${__('Customer')}</h6>
<p class="mb-1"><b>${esc(ci.customer_name || sr.customer)}</b></p>
<p class="mb-1 small"><i class="fa fa-phone"></i> ${esc(sr.contact_number || ci.mobile_no || '—')}</p>
<p class="mb-0 small"><i class="fa fa-envelope"></i> ${esc(ci.email_id || '—')}</p>
</div>
<div class="jt-dw-box">
<h6 class="jt-dw-h"><i class="fa fa-mobile"></i> ${__('Device')}</h6>
<p class="mb-1"><b>${esc(sr.device_item_name || sr.device_item)}</b></p>
<p class="mb-1 small">Serial: <code>${esc(sr.serial_no || '—')}</code></p>
<p class="mb-0 small">${__('Condition')}: ${esc(sr.device_condition || '—')}</p>
</div>
</div>

<div class="jt-dw-box">
<h6 class="jt-dw-h"><i class="fa fa-exclamation-circle"></i> ${__('Issue')}</h6>
<p class="mb-1"><b>${esc(sr.issue_category || '—')}</b></p>
<p class="mb-0 small">${esc(sr.issue_description ? sr.issue_description.replace(/<[^>]+>/g,'').substring(0,200) : '—')}</p>
</div>

<div class="jt-dw-grid jt-dw-dates">
<div><span class="text-muted small">${__('Received')}</span><br><b>${frappe.datetime.str_to_user(sr.service_date)}</b></div>
<div><span class="text-muted small">${__('Expected')}</span><br><b class="${sr.expected_completion_date && sr.expected_completion_date < today ? 'text-danger' : ''}">${sr.expected_completion_date ? frappe.datetime.str_to_user(sr.expected_completion_date) : '—'}</b></div>
<div><span class="text-muted small">${__('Store')}</span><br><b>${esc(sr.source_warehouse || '—')}</b></div>
</div>

<div class="jt-dw-section">
<h6 class="jt-dw-h"><i class="fa fa-users"></i> ${__('Assignments')} <span class="badge ml-1">${asgn.length}</span></h6>
${assign_html}
</div>

<div class="jt-dw-section">
<h6 class="jt-dw-h"><i class="fa fa-cogs"></i> ${__('Spare Parts')} <span class="badge ml-1">${parts.length}</span></h6>
${parts_html}
</div>
`);

this.parent.find('#jt-btn-assign').on('click', () => this._show_assign_dialog(sr.name));
this.parent.find('#jt-btn-print').on('click', () => frappe.set_route('print', 'Service Request', sr.name));
}

// ─────────── Assign Dialog ───────────────────────────────────────────────

_show_assign_dialog(sr_name) {
const dlg = new frappe.ui.Dialog({
title: __('Assign Technician — {0}', [sr_name]),
fields: [
{ fieldname:'service_engineer', label:__('Technician'), fieldtype:'Link', options:'Employee', reqd:1 },
{
fieldname:'job_type', label:__('Job Type'), fieldtype:'Select', reqd:1, default:'Repair',
options:'Repair\nDiagnosis\nQC\nSpare Parts Replacement\nSoftware Update\nTesting',
},
{ fieldname:'estimated_hours', label:__('Estimated Hours'), fieldtype:'Float', default:1 },
],
primary_action_label: __('Assign'),
primary_action: vals => {
frappe.xcall('gofix.gofix_services.page.job_tracker.job_tracker.create_assignment', {
service_request: sr_name,
engineer:        vals.service_engineer,
job_type:        vals.job_type,
estimated_hours: vals.estimated_hours || null,
}).then(() => {
frappe.show_alert({ message: __('Assigned successfully'), indicator: 'green' });
dlg.hide();
this._open_drawer(sr_name);
this._load();
}).catch(err => {
frappe.msgprint({ title: __('Assignment Failed'), indicator: 'red',
message: frappe.utils.strip_html((err && err.message) || String(err)) });
});
},
});
dlg.show();
}

_setup_realtime() {
frappe.realtime.on('job_assignment_update', () => this._load());
frappe.realtime.on('spare_parts_update',     () => this._load());
}
}
