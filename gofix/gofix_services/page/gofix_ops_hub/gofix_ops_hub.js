/**
 * GoFix Ops Hub — Professional Repair Operations Dashboard
 *
 * A step-by-step operations interface for managing repair tickets
 * from intake through diagnosis, repair, QC, and invoicing.
 *
 * Layout: Split-panel (sidebar queue | main detail with stepper)
 */
frappe.pages["gofix-ops-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("GoFix Ops Hub"),
		single_column: true,
	});
	// Show loading indicator immediately
	$(page.body).html('<div class="text-center mt-5"><i class="fa fa-spinner fa-spin fa-2x text-muted"></i><p class="text-muted mt-2">Loading GoFix Ops Hub&hellip;</p></div>');
	try {
		new GoFixOpsHub(page);
	} catch(e) {
		console.error("GoFix Ops Hub: constructor error", e);
		$(page.body).html('<div class="text-center mt-5"><h4 class="text-danger">Failed to initialise Ops Hub</h4><pre>' + (e.stack || e.message || e) + '</pre></div>');
	}
};

/* ═══════════════════════════════════════════════════════════════════════════ */
/*  Constants                                                                */
/* ═══════════════════════════════════════════════════════════════════════════ */
const STAGES = [
	{ key: "analysis",  label: "Analysis",  icon: "fa-search",         color: "#3b82f6" },
	{ key: "confirm",   label: "Confirm",   icon: "fa-check-circle",   color: "#8b5cf6" },
	{ key: "solutions", label: "Solutions",  icon: "fa-bolt",          color: "#f59e0b" },
	{ key: "assign",    label: "Assign",     icon: "fa-user-plus",     color: "#10b981" },
	{ key: "repair",    label: "Repair",     icon: "fa-wrench",        color: "#ef4444" },
	{ key: "qc",        label: "QC",         icon: "fa-check-square-o", color: "#6366f1" },
	{ key: "invoice",   label: "Invoice",    icon: "fa-file-text-o",   color: "#059669" },
];

const STAGE_BADGE = {
	analysis:  { label: "Analysis",  cls: "badge-blue" },
	confirm:   { label: "Confirm",   cls: "badge-purple" },
	solutions: { label: "Solutions", cls: "badge-yellow" },
	assign:    { label: "Assign",    cls: "badge-green" },
	repair:    { label: "Repair",    cls: "badge-red" },
	qc:        { label: "QC",        cls: "badge-indigo" },
	invoice:   { label: "Invoice",   cls: "badge-green" },
	rework:    { label: "Rework",    cls: "badge-red" },
	done:      { label: "Done",      cls: "badge-done" },
	closed:    { label: "Closed",    cls: "badge-muted" },
	draft:     { label: "Draft",     cls: "badge-muted" },
};

const PRIORITY_COLOR = { Urgent: "#dc2626", High: "#f59e0b", Medium: "#3b82f6", Low: "#94a3b8" };
const API = "gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub";

/* ═══════════════════════════════════════════════════════════════════════════ */
/*  Main Class                                                               */
/* ═══════════════════════════════════════════════════════════════════════════ */
class GoFixOpsHub {
	constructor(page) {
		this.page = page;
		this.parent = $(page.body);
		this.ctx = {};
		this.queue = [];
		this.selectedSR = null;
		this.detail = null;
		this._search_timer = null;
		this._init();
	}

	async _init() {
		try {
			this.ctx = await frappe.xcall(`${API}.get_ops_context`);
		} catch (e) {
			console.error("GoFix Ops Hub: get_ops_context failed", e);
			this.ctx = { warehouses: [], is_manager: false };
		}
		try {
			this._build_toolbar();
			this._build_layout();
			this._load_queue();
		} catch (e) {
			console.error("GoFix Ops Hub: init failed", e);
			this.parent.html(`<div class="text-center mt-5"><h4 class="text-danger">Ops Hub failed to load</h4><pre>${frappe.utils.escape_html(e.message || e)}</pre></div>`);
		}
	}

	/* ── Toolbar ────────────────────────────────────────────────────────── */
	_build_toolbar() {
		// Warehouse filter
		const wh_options = ["<option value=''>" + __("All Stores") + "</option>"];
		(this.ctx.warehouses || []).forEach(w => {
			const short = w.split(" - ")[0];
			wh_options.push(`<option value="${frappe.utils.escape_html(w)}">${frappe.utils.escape_html(short)}</option>`);
		});

		this.page.set_secondary_action(__("Refresh"), () => this._refresh_all(), "refresh");

		// Custom toolbar fields
		this.page.add_inner_message(`
			<div class="goh-toolbar">
				<select class="form-control input-xs goh-tb-warehouse">${wh_options.join("")}</select>
				<select class="form-control input-xs goh-tb-stage">
					<option value="active">${__("Active Tickets")}</option>
					<option value="all">${__("All Tickets")}</option>
					${STAGES.map(s => `<option value="${s.key}">${s.label}</option>`).join("")}
					<option value="rework">${__("Rework (QC Fail)")}</option>
					<option value="done">${__("Done")}</option>
					<option value="closed">${__("Closed")}</option>
				</select>
				<select class="form-control input-xs goh-tb-priority">
					<option value="">${__("All Priorities")}</option>
					<option value="Urgent">${__("Urgent")}</option>
					<option value="High">${__("High")}</option>
					<option value="Medium">${__("Medium")}</option>
					<option value="Low">${__("Low")}</option>
				</select>
			</div>
		`);

		// Bind toolbar events
		this.page.wrapper.find(".goh-tb-warehouse, .goh-tb-stage, .goh-tb-priority").on("change", () => this._load_queue());
	}

	/* ── Layout ─────────────────────────────────────────────────────────── */
	_build_layout() {
		this.parent.html(`
			<div class="goh-root">
				<aside class="goh-sidebar">
					<div class="goh-search-bar">
						<i class="fa fa-search goh-search-icon"></i>
						<input type="text" class="goh-search-input" placeholder="${__("Search SR#, customer, serial...")}" />
						<span class="goh-queue-count badge badge-muted">0</span>
					</div>
					<div class="goh-queue-list" id="goh-queue"></div>
				</aside>
				<main class="goh-main" id="goh-main">
					<div class="goh-empty-state">
						<i class="fa fa-wrench fa-3x text-muted"></i>
						<h4 class="mt-3 text-muted">${__("GoFix Operations Hub")}</h4>
						<p class="text-muted">${__("Select a ticket from the queue to start working")}</p>
					</div>
				</main>
			</div>
		`);

		// Search with debounce
		this.parent.find(".goh-search-input").on("input", () => {
			clearTimeout(this._search_timer);
			this._search_timer = setTimeout(() => this._load_queue(), 300);
		});
	}

	/* ── Queue ──────────────────────────────────────────────────────────── */
	async _load_queue() {
		const toolbar = this.page.wrapper;
		const warehouse = toolbar.find(".goh-tb-warehouse").val() || "";
		const stage = toolbar.find(".goh-tb-stage").val() || "active";
		const priority = toolbar.find(".goh-tb-priority").val() || "";
		const search = this.parent.find(".goh-search-input").val() || "";

		try {
			let data = await frappe.xcall(`${API}.get_ticket_queue`, {
				warehouse, search, stage_filter: stage,
			});

			// Client-side priority filter
			if (priority) {
				data = data.filter(r => r.priority === priority);
			}

			// Client-side stage filter (server returns "active" or "all", further refine)
			if (stage && !["active", "all"].includes(stage)) {
				data = data.filter(r => r.ops_stage === stage);
			}

			this.queue = data || [];
		} catch (e) {
			this.queue = [];
			frappe.show_alert({ message: __("Failed to load queue"), indicator: "red" });
		}
		this._render_queue();
	}

	_render_queue() {
		const container = this.parent.find("#goh-queue");
		this.parent.find(".goh-queue-count").text(this.queue.length);

		if (!this.queue.length) {
			container.html(`<div class="goh-queue-empty"><i class="fa fa-inbox"></i><p>${__("No tickets match your filters")}</p></div>`);
			return;
		}

		container.html(this.queue.map(sr => this._queue_card(sr)).join(""));

		// Bind clicks
		container.find(".goh-q-card").on("click", (e) => {
			const name = $(e.currentTarget).data("sr");
			this._select_ticket(name);
		});
	}

	_queue_card(sr) {
		const esc = frappe.utils.escape_html;
		const badge = STAGE_BADGE[sr.ops_stage] || STAGE_BADGE.draft;
		const pcolor = PRIORITY_COLOR[sr.priority] || "#94a3b8";
		const active = sr.name === this.selectedSR ? "goh-q-active" : "";
		const device = sr.device_item_name || sr.device_item || "";
		const sla = this._sla_indicator(sr);

		return `
			<div class="goh-q-card ${active}" data-sr="${esc(sr.name)}">
				<div class="goh-q-row1">
					<span class="goh-q-sr">${esc(sr.name)}</span>
					<span class="goh-badge ${badge.cls}">${__(badge.label)}</span>
				</div>
				<div class="goh-q-row2">
					<span class="goh-q-customer" title="${esc(sr.customer_name || "")}">${esc(sr.customer_name || sr.customer || "—")}</span>
					<span class="goh-q-priority" style="color:${pcolor};" title="${esc(sr.priority || "")}">
						<i class="fa fa-circle"></i>
					</span>
				</div>
				<div class="goh-q-row3">
					<span class="goh-q-device">${esc(device)}</span>
					${sla}
				</div>
				<div class="goh-q-row4 text-muted">
					<span><i class="fa fa-calendar-o"></i> ${frappe.datetime.str_to_user(sr.service_date)}</span>
					${sr.serial_no ? `<span class="ml-2" title="Serial"><i class="fa fa-barcode"></i> ${esc(sr.serial_no)}</span>` : ""}
				</div>
			</div>
		`;
	}

	_sla_indicator(sr) {
		if (!sr.expected_completion_date) return "";
		const exp = frappe.datetime.str_to_obj(sr.expected_completion_date);
		const now = new Date();
		const diffHrs = (exp - now) / (1000 * 60 * 60);
		if (diffHrs < 0) {
			return `<span class="goh-sla goh-sla-breach" title="${__("SLA Breached")}"><i class="fa fa-exclamation-triangle"></i></span>`;
		} else if (diffHrs < 24) {
			return `<span class="goh-sla goh-sla-warn" title="${__("Due Today")}"><i class="fa fa-clock-o"></i></span>`;
		}
		return "";
	}

	_select_ticket(name) {
		this.selectedSR = name;
		this.parent.find(".goh-q-card").removeClass("goh-q-active");
		this.parent.find(`.goh-q-card[data-sr="${name}"]`).addClass("goh-q-active");
		this._load_detail(name);
	}

	/* ── Detail ─────────────────────────────────────────────────────────── */
	async _load_detail(sr_name) {
		const main = this.parent.find("#goh-main");
		main.html(`<div class="goh-loading"><i class="fa fa-spinner fa-spin fa-2x"></i></div>`);

		let data;
		try {
			data = await frappe.xcall(`${API}.get_ticket_detail`, { sr_name });
		} catch (e) {
			console.error("GoFix Ops Hub: API error", e);
			main.html(`<div class="goh-empty-state"><i class="fa fa-exclamation-circle fa-3x text-danger"></i><p class="mt-2">${__("API Error loading ticket")}</p><pre class="text-muted small mt-2">${frappe.utils.escape_html(String(e && (e.message || e) || "Unknown"))}</pre></div>`);
			return;
		}
		try {
			this.detail = data;
			this._render_detail(data);
		} catch (e) {
			console.error("GoFix Ops Hub: render error", e);
			main.html(`<div class="goh-empty-state"><i class="fa fa-exclamation-circle fa-3x text-danger"></i><p class="mt-2">${__("Render Error")}</p><pre class="text-muted small mt-2">${frappe.utils.escape_html(e.stack || e.message || String(e))}</pre></div>`);
		}
	}

	_render_detail(d) {
		const esc = frappe.utils.escape_html;

		// Choose which content panel to render based on ops_stage
		let contentHtml = "";
		const renderer = {
			analysis:  () => this._html_analysis(d),
			confirm:   () => this._html_confirm(d),
			solutions: () => this._html_solutions(d),
			assign:    () => this._html_assign(d),
			repair:    () => this._html_repair(d),
			qc:        () => this._html_qc(d),
			invoice:   () => this._html_invoice(d),
			rework:    () => this._html_rework(d),
			done:      () => this._html_done(d),
		};

		contentHtml = (renderer[d.ops_stage] || (() =>
			`<div class="goh-section p-3 text-muted">${__("Ticket is")} <b>${esc(d.decision)}</b>. ${__("No further action needed.")}</div>`
		))();

		this.parent.find("#goh-main").html(`
			<div class="goh-detail">
				${this._stepper_html(d.ops_stage)}
				${this._banner_html(d)}
				<div class="goh-tabs-bar" id="goh-tabs">
					<button class="goh-tab goh-tab-active" data-tab="work"><i class="fa fa-tasks"></i> ${__("Work")}</button>
					<button class="goh-tab" data-tab="device"><i class="fa fa-mobile"></i> ${__("Device")}</button>
					<button class="goh-tab" data-tab="timeline"><i class="fa fa-clock-o"></i> ${__("Timeline")}</button>
					<button class="goh-tab" data-tab="notes"><i class="fa fa-sticky-note-o"></i> ${__("Notes")}</button>
				</div>
				<div class="goh-tab-content" id="goh-tab-work">${contentHtml}</div>
				<div class="goh-tab-content goh-hidden" id="goh-tab-device">${this._html_device_tab(d)}</div>
				<div class="goh-tab-content goh-hidden" id="goh-tab-timeline">${this._html_timeline_tab(d)}</div>
				<div class="goh-tab-content goh-hidden" id="goh-tab-notes">${this._html_notes_tab(d)}</div>
			</div>
		`);

		this._bind_tabs();
		this._bind_stepper_nav(d);
		this._bind_step_events(d);
	}

	/* ── Stepper ────────────────────────────────────────────────────────── */
	_stepper_html(active) {
		const activeIdx = STAGES.findIndex(s => s.key === active);
		return `
			<div class="goh-stepper">
				${STAGES.map((s, i) => {
					let cls = "goh-step-future";
					if (i < activeIdx) cls = "goh-step-done";
					else if (i === activeIdx) cls = "goh-step-active";
					const clickable = (i < activeIdx) ? "goh-step-clickable" : "";
					return `
						<div class="goh-step ${cls} ${clickable}" title="${__(s.label)}" data-stage="${s.key}">
							<div class="goh-step-dot" style="${cls === "goh-step-active" ? `background:${s.color};border-color:${s.color};` : ""}">
								${cls === "goh-step-done" ? '<i class="fa fa-check"></i>' : (i + 1)}
							</div>
							<div class="goh-step-label">${__(s.label)}</div>
						</div>
						${i < STAGES.length - 1 ? `<div class="goh-step-line ${i < activeIdx ? "goh-line-done" : ""}"></div>` : ""}
					`;
				}).join("")}
			</div>
		`;
	}

	/* ── Stepper Navigation (click completed steps to go back) ─────────── */
	_bind_stepper_nav(d) {
		const self = this;
		this.parent.find(".goh-step-clickable").on("click", function () {
			const stage = $(this).data("stage");
			if (!stage || stage === d.ops_stage) return;

			// Temporarily render the clicked stage's content (read-only view)
			// but keep the real ops_stage for the stepper highlighting
			const viewData = Object.assign({}, d, { _view_stage: stage });
			const renderer = {
				analysis:  () => self._html_analysis(viewData),
				confirm:   () => self._html_confirm(viewData),
				solutions: () => self._html_solutions(viewData),
				assign:    () => self._html_assign(viewData),
				repair:    () => self._html_repair(viewData),
				qc:        () => self._html_qc(viewData),
			};
			const html = (renderer[stage] || (() => ""))();
			if (!html) return;

			self.parent.find("#goh-tab-work").html(`
				<div class="goh-nav-banner">
					<i class="fa fa-arrow-left"></i>
					${__("Viewing")} <b>${__(STAGES.find(s => s.key === stage)?.label || stage)}</b>
					<span class="text-muted ml-2">(${__("current stage")}: ${__(STAGES.find(s => s.key === d.ops_stage)?.label || d.ops_stage)})</span>
					<button class="btn btn-xs btn-primary ml-3" id="goh-back-to-current"><i class="fa fa-arrow-right"></i> ${__("Back to Current Step")}</button>
				</div>
				${html}
			`);

			// Re-bind events for the viewed stage
			self._bind_step_events(viewData);

			// Bind the "back to current" button
			self.parent.find("#goh-back-to-current").on("click", () => {
				self._load_detail(d.name);
			});
		});
	}

	/* ── Info Banner ────────────────────────────────────────────────────── */
	_banner_html(d) {
		const esc = frappe.utils.escape_html;
		const pcolor = PRIORITY_COLOR[d.priority] || "#94a3b8";
		const badge = STAGE_BADGE[d.ops_stage] || STAGE_BADGE.draft;

		const sla_html = d.expected_completion_date ? (() => {
			const exp = frappe.datetime.str_to_obj(d.expected_completion_date);
			const diffH = ((exp - new Date()) / 3600000).toFixed(1);
			const cls = diffH < 0 ? "goh-sla-breach" : diffH < 24 ? "goh-sla-warn" : "goh-sla-ok";
			const label = diffH < 0 ? __("SLA Breached") : diffH < 24 ? __("Due Soon") : __("On Track");
			return `<span class="goh-sla-pill ${cls}">${label} (${Math.abs(diffH)}h)</span>`;
		})() : "";

		return `
			<div class="goh-banner">
				<div class="goh-banner-left">
					<div class="goh-banner-title">
						<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="goh-sr-link">${esc(d.name)}</a>
						<span class="goh-badge ${badge.cls}">${__(badge.label)}</span>
						<span class="goh-priority-dot" style="background:${pcolor};" title="${esc(d.priority || "")}">${esc(d.priority || "")}</span>
						${d.is_repeat_complaint ? `<span class="goh-badge badge-red" title="${__("Repeat Complaint")}"><i class="fa fa-repeat"></i> ${__("Repeat")}</span>` : ""}
					</div>
					<div class="goh-banner-meta">
						<span><i class="fa fa-user"></i> ${esc(d.customer_name || d.customer)}</span>
						${d.contact_number ? `<span><i class="fa fa-phone"></i> <a href="tel:${esc(d.contact_number)}">${esc(d.contact_number)}</a></span>` : ""}
						<span><i class="fa fa-mobile"></i> ${esc(d.device_item_name || d.device_item || "—")}</span>
						${d.serial_no ? `<span><i class="fa fa-barcode"></i> ${esc(d.serial_no)}</span>` : ""}
						${d.brand ? `<span><i class="fa fa-tag"></i> ${esc(d.brand)}</span>` : ""}
					</div>
				</div>
				<div class="goh-banner-right">
					${sla_html}
					<div class="goh-banner-actions">
						<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="btn btn-xs btn-default" title="${__("Open Full SR")}">
							<i class="fa fa-external-link"></i>
						</a>
						${d.service_order ? `<a href="/app/sales-order/${encodeURIComponent(d.service_order)}" target="_blank" class="btn btn-xs btn-default" title="${__("Service Order")}"><i class="fa fa-file-text-o"></i></a>` : ""}
					</div>
				</div>
			</div>
		`;
	}

	/* ── Tabs ───────────────────────────────────────────────────────────── */
	_bind_tabs() {
		this.parent.find("#goh-tabs .goh-tab").on("click", (e) => {
			const tab = $(e.currentTarget).data("tab");
			this.parent.find(".goh-tab").removeClass("goh-tab-active");
			$(e.currentTarget).addClass("goh-tab-active");
			this.parent.find(".goh-tab-content").addClass("goh-hidden");
			this.parent.find(`#goh-tab-${tab}`).removeClass("goh-hidden");
		});
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Device Tab                                                           */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_device_tab(d) {
		const esc = frappe.utils.escape_html;
		const rows = [
			["Device", d.device_item_name || d.device_item],
			["Brand", d.brand],
			["Serial No", d.serial_no],
			["IMEI", d.actual_imei],
			["Condition", d.device_condition],
			["Mode of Service", d.mode_of_service],
			["Warranty", d.warranty_status],
			["Warranty Plan", d.warranty_plan_name],
			["Estimated Cost", d.estimated_cost ? `₹${format_number(d.estimated_cost)}` : ""],
			["Store", d.source_warehouse],
			["Received", d.received_datetime ? frappe.datetime.str_to_user(d.received_datetime) : d.service_date ? frappe.datetime.str_to_user(d.service_date) : ""],
			["Expected By", d.expected_completion_date ? frappe.datetime.str_to_user(d.expected_completion_date) : ""],
			["Advance Paid", d.advance_amount ? `₹${format_number(d.advance_amount)}` : ""],
		].filter(r => r[1]);

		const assignRows = (d.assignments || []).map(a => `
			<tr>
				<td>${esc(a.engineer_display)}</td>
				<td><span class="goh-badge badge-muted">${esc(a.job_type)}</span></td>
				<td><span class="goh-badge ${a.assignment_status === "Completed" ? "badge-green" : a.assignment_status === "In Progress" ? "badge-blue" : "badge-muted"}">${esc(a.assignment_status)}</span></td>
				<td>${a.estimated_hours || "—"}</td>
				<td>${a.actual_hours || "—"}</td>
			</tr>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-mobile"></i> ${__("Device Information")}</div>
				<div class="goh-kv-grid">
					${rows.map(r => `<div class="goh-kv"><span class="goh-kv-label">${__(r[0])}</span><span class="goh-kv-value">${esc(r[1])}</span></div>`).join("")}
				</div>
			</div>

			${d.issue_description ? `
				<div class="goh-section">
					<div class="goh-section-title"><i class="fa fa-comment-o"></i> ${__("Customer Complaint")}</div>
					<div class="goh-text-block">${d.issue_description}</div>
				</div>
			` : ""}

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-users"></i> ${__("Assignments")}</div>
				${assignRows ? `
					<table class="goh-table">
						<thead><tr><th>${__("Technician")}</th><th>${__("Type")}</th><th>${__("Status")}</th><th>${__("Est.Hr")}</th><th>${__("Act.Hr")}</th></tr></thead>
						<tbody>${assignRows}</tbody>
					</table>
				` : `<p class="text-muted">${__("No assignments yet")}</p>`}
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Timeline Tab                                                         */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_timeline_tab(d) {
		const esc = frappe.utils.escape_html;
		const log = d.status_log || [];

		if (!log.length) {
			return `<div class="goh-section"><p class="text-muted">${__("No status changes recorded yet")}</p></div>`;
		}

		const items = log.slice().reverse().map(entry => {
			const dt = entry.changed_at ? frappe.datetime.str_to_user(entry.changed_at) : "";
			const dur = entry.hours_in_prev ? `<span class="text-muted small">(${entry.hours_in_prev}h in ${esc(entry.from_status)})</span>` : "";
			return `
				<div class="goh-tl-item">
					<div class="goh-tl-dot"></div>
					<div class="goh-tl-content">
						<div class="goh-tl-header">
							<span class="goh-badge badge-muted">${esc(entry.from_status)}</span>
							<i class="fa fa-arrow-right text-muted mx-1"></i>
							<span class="goh-badge badge-blue">${esc(entry.to_status)}</span>
							${dur}
						</div>
						<div class="goh-tl-meta text-muted small">
							${esc(entry.changed_by_name || entry.changed_by || "")} &middot; ${dt}
						</div>
					</div>
				</div>
			`;
		}).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-clock-o"></i> ${__("Status Timeline")}</div>
				<div class="goh-timeline">${items}</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Notes Tab                                                            */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_notes_tab(d) {
		const esc = frappe.utils.escape_html;
		return `
			<div class="goh-section">
				${d.customer_remarks ? `
					<div class="goh-note-block">
						<div class="goh-note-label"><i class="fa fa-user"></i> ${__("Customer Remarks")}</div>
						<div class="goh-note-text">${esc(d.customer_remarks)}</div>
					</div>
				` : ""}
				${d.internal_remarks ? `
					<div class="goh-note-block">
						<div class="goh-note-label"><i class="fa fa-lock"></i> ${__("Internal Remarks")}</div>
						<div class="goh-note-text">${esc(d.internal_remarks)}</div>
					</div>
				` : ""}
				${!d.customer_remarks && !d.internal_remarks ? `<p class="text-muted">${__("No remarks recorded")}</p>` : ""}
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 1 — Technical Analysis                                          */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_analysis(d) {
		const esc = frappe.utils.escape_html;

		const activeIssues = (d.issue_lines || []).filter(r => r.status !== "Deleted");
		const deletedIssues = (d.issue_lines || []).filter(r => r.status === "Deleted");

		const issueRows = activeIssues.map((row, i) => `
			<tr data-name="${esc(row.name)}" data-idx="${i}">
				<td><input class="form-control input-xs goh-issue-cat" value="${esc(row.issue_category)}" list="goh-cat-list" placeholder="${__("Issue Category")}"></td>
				<td>
					<select class="form-control input-xs goh-issue-reporter">
						<option value="Technician" ${row.reported_by === "Technician" ? "selected" : ""}>${__("Technician")}</option>
						<option value="Customer" ${row.reported_by === "Customer" ? "selected" : ""}>${__("Customer")}</option>
					</select>
				</td>
				<td><input class="form-control input-xs goh-issue-desc" value="${esc(row.description)}" placeholder="${__("Description")}"></td>
				<td><span class="goh-badge ${row.status === "Resolved" ? "badge-green" : row.status === "Open" ? "badge-blue" : "badge-muted"}">${esc(row.status)}</span></td>
				<td><button class="btn btn-xs btn-danger goh-issue-remove" data-row="${esc(row.name)}"><i class="fa fa-trash"></i></button></td>
			</tr>
		`).join("");

		const deletedLog = deletedIssues.length ? `
			<div class="goh-section mt-3" style="border-left:3px solid #dc2626; background:#fef2f2; padding:10px 14px;">
				<div class="goh-section-title text-danger"><i class="fa fa-history"></i> ${__("Deleted Issues Log")} (${deletedIssues.length})</div>
				<table class="goh-table">
					<thead><tr><th>${__("Issue")}</th><th>${__("Reported By")}</th><th>${__("Description")}</th><th>${__("Reason for Deletion")}</th><th>${__("Deleted By")}</th><th>${__("Deleted At")}</th></tr></thead>
					<tbody>
						${deletedIssues.map(row => `
							<tr style="text-decoration: line-through; opacity: 0.7;">
								<td>${esc(row.issue_category)}</td>
								<td>${esc(row.reported_by)}</td>
								<td>${esc(row.description || "—")}</td>
								<td class="text-danger">${esc(row.deleted_reason || "—")}</td>
								<td>${esc(frappe.user.full_name(row.deleted_by) || row.deleted_by || "—")}</td>
								<td>${row.deleted_at ? frappe.datetime.str_to_user(row.deleted_at) : "—"}</td>
							</tr>
						`).join("")}
					</tbody>
				</table>
			</div>
		` : "";

		return `
			<datalist id="goh-cat-list"></datalist>
			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-search"></i> ${__("Technical Analysis")}
					<span class="text-muted small ml-2">${__("Identify all issues with the device")}</span>
				</div>

				${d.issue_description ? `
					<div class="goh-complaint-block">
						<div class="goh-note-label"><i class="fa fa-comment"></i> ${__("Customer Complaint")}</div>
						<div class="goh-note-text">${d.issue_description}</div>
					</div>
				` : ""}

				<table class="goh-table" id="goh-issue-table">
					<thead>
						<tr><th>${__("Issue Category")}</th><th style="width:130px">${__("Reported By")}</th><th>${__("Description")}</th><th style="width:80px">${__("Status")}</th><th style="width:40px"></th></tr>
					</thead>
					<tbody id="goh-issue-tbody">
						${issueRows || `<tr><td colspan="5" class="text-muted text-center">${__("No issues added yet. Click + to add.")}</td></tr>`}
					</tbody>
				</table>

				<div class="goh-section-actions">
					<button class="btn btn-xs btn-default" id="goh-add-issue"><i class="fa fa-plus"></i> ${__("Add Issue")}</button>
					<button class="btn btn-xs btn-default ml-2" id="goh-save-issues"><i class="fa fa-save"></i> ${__("Save")}</button>
					<button class="btn btn-xs btn-primary ml-2" id="goh-confirm-analysis"><i class="fa fa-check"></i> ${__("Confirm Analysis")}</button>
				</div>
			</div>
			${deletedLog}
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 2 — Customer Confirmation                                       */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_confirm(d) {
		const esc = frappe.utils.escape_html;

		const issueList = (d.issue_lines || []).map(r => `
			<div class="goh-confirm-issue">
				<span class="goh-badge badge-blue">${esc(r.issue_category)}</span>
				<span class="text-muted small">${esc(r.description || "")}</span>
			</div>
		`).join("");

		const sentAt = d.confirmation_sent_at ? frappe.datetime.str_to_user(d.confirmation_sent_at) : "";

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-check-circle"></i> ${__("Customer Confirmation")}</div>
				<p class="text-muted">${__("Share analysis with customer and get their confirmation to proceed with repair.")}</p>

				<div class="goh-confirm-issues-wrap">
					<h6>${__("Identified Issues")}</h6>
					${issueList || `<p class="text-muted">${__("No issues logged")}</p>`}
				</div>

				${d.estimated_cost ? `
					<div class="goh-confirm-cost">
						<span class="goh-kv-label">${__("Estimated Cost")}</span>
						<span class="goh-cost-value">₹${format_number(d.estimated_cost)}</span>
					</div>
				` : ""}

				${sentAt ? `<div class="goh-sent-indicator"><i class="fa fa-whatsapp text-success"></i> ${__("WhatsApp sent")} ${sentAt}</div>` : ""}

				<div class="goh-section-actions">
					<button class="btn btn-xs btn-default" id="goh-back-to-analysis"><i class="fa fa-arrow-left"></i> ${__("Back to Analysis")}</button>
					<button class="btn btn-xs btn-success ml-2" id="goh-send-wa"><i class="fa fa-whatsapp"></i> ${__("Send WhatsApp")}</button>
					<button class="btn btn-xs btn-primary ml-2" id="goh-mark-confirmed"><i class="fa fa-check"></i> ${__("Mark Confirmed")}</button>
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 3 — Solution Assignment                                         */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_solutions(d) {
		const esc = frappe.utils.escape_html;
		const existingSols = (d.solution_lines || []).map(s => `
			<div class="goh-sol-existing">
				<span class="goh-badge badge-green">${esc(s.repair_solution)}</span>
				<span class="text-muted small">${esc(s.issue_category || "")} — ${s.estimated_minutes}min</span>
				${s.requires_spare ? `<span class="goh-badge badge-yellow">${__("Spare")}</span>` : ""}
			</div>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-bolt"></i> ${__("Assign Solutions")}</div>
				<p class="text-muted">${__("Select repair solutions for each identified issue.")}</p>

				${existingSols ? `
					<div class="goh-existing-sols">
						<h6>${__("Already Assigned")}</h6>
						${existingSols}
					</div>
				` : ""}

				<div id="goh-sol-picker">
					<div class="text-center p-3"><i class="fa fa-spinner fa-spin"></i> ${__("Loading solutions...")}</div>
				</div>

				<div class="goh-section-actions">
					<button class="btn btn-xs btn-default" id="goh-back-to-confirm"><i class="fa fa-arrow-left"></i> ${__("Back to Confirmation")}</button>
					<button class="btn btn-xs btn-primary ml-2" id="goh-save-solutions"><i class="fa fa-check"></i> ${__("Save Solutions")}</button>
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 4 — Technician Assignment                                       */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_assign(d) {
		const esc = frappe.utils.escape_html;
		const solSummary = (d.solution_lines || []).map(s => `
			<span class="goh-badge badge-muted">${esc(s.repair_solution)}</span>
		`).join(" ");

		const existingAssign = (d.assignments || []).map(a => `
			<div class="goh-assign-chip">
				<i class="fa fa-user"></i>
				<b>${esc(a.engineer_display)}</b>
				<span class="goh-badge badge-muted ml-1">${esc(a.job_type)}</span>
				<span class="goh-badge ${a.assignment_status === "Completed" ? "badge-green" : "badge-blue"} ml-1">${esc(a.assignment_status)}</span>
			</div>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-user-plus"></i> ${__("Assign Technician")}</div>
				<p class="text-muted">${__("Solutions to work on:")} ${solSummary}</p>

				${existingAssign ? `<div class="goh-existing-assigns mb-3">${existingAssign}</div>` : ""}

				<div class="row">
					<div class="col-sm-5">
						<label class="goh-field-label">${__("Technician")}</label>
						<div id="goh-tech-field"></div>
					</div>
					<div class="col-sm-3">
						<label class="goh-field-label">${__("Job Type")}</label>
						<select class="form-control input-sm" id="goh-job-type">
							<option value="Repair">${__("Repair")}</option>
							<option value="Diagnosis">${__("Diagnosis")}</option>
							<option value="Spare Parts Replacement">${__("Spare Parts")}</option>
							<option value="Software Update">${__("Software Update")}</option>
							<option value="Testing">${__("Testing")}</option>
						</select>
					</div>
					<div class="col-sm-2">
						<label class="goh-field-label">${__("Est. Hours")}</label>
						<input class="form-control input-sm" id="goh-est-hours" type="number" value="2" min="0.5" step="0.5">
					</div>
					<div class="col-sm-2 d-flex align-items-end">
						<button class="btn btn-sm btn-primary w-100" id="goh-do-assign"><i class="fa fa-check"></i> ${__("Assign")}</button>
					</div>
				</div>

				<div class="goh-section-actions mt-2">
					<button class="btn btn-xs btn-default" id="goh-back-to-solutions"><i class="fa fa-arrow-left"></i> ${__("Back to Solutions")}</button>
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 5 — Repair Execution                                            */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_repair(d) {
		const esc = frappe.utils.escape_html;
		const sols = d.solution_lines || [];
		const activeSols = sols.filter(s => s.status !== "Cancelled");
		const cancelledSols = sols.filter(s => s.status === "Cancelled");
		const allDone = activeSols.length > 0 && activeSols.every(s => s.status === "Completed" || s.status === "Skipped");
		const doneCount = activeSols.filter(s => s.status === "Completed" || s.status === "Skipped").length;

		const STATUS_CLS = { Planned: "badge-muted", "In Progress": "badge-blue", Completed: "badge-green", Skipped: "badge-yellow", Cancelled: "badge-red" };

		const solCards = activeSols.map(sol => `
			<div class="goh-repair-card" data-row="${esc(sol.name)}">
				<div class="goh-repair-card-head">
					<span class="goh-repair-sol-name">${esc(sol.repair_solution || "—")}</span>
					<span class="goh-badge ${STATUS_CLS[sol.status] || "badge-muted"}">${esc(sol.status)}</span>
				</div>
				<div class="goh-repair-card-meta text-muted small">
					<span><i class="fa fa-tag"></i> ${esc(sol.issue_category || "")}</span>
					${sol.estimated_minutes ? `<span class="ml-2"><i class="fa fa-clock-o"></i> ${sol.estimated_minutes}min</span>` : ""}
				</div>
				${sol.technician_remarks ? `<div class="goh-repair-remarks">${esc(sol.technician_remarks)}</div>` : ""}
				<div class="goh-repair-card-actions">
					${sol.status !== "In Progress" && sol.status !== "Completed" ? `<button class="btn btn-xs btn-default goh-sol-start" data-row="${esc(sol.name)}"><i class="fa fa-play"></i> ${__("Start")}</button>` : ""}
					${sol.status === "In Progress" ? `<button class="btn btn-xs btn-success goh-sol-complete" data-row="${esc(sol.name)}"><i class="fa fa-check"></i> ${__("Done")}</button>` : ""}
					${sol.status !== "Completed" && sol.status !== "Skipped" ? `<button class="btn btn-xs btn-warning goh-sol-skip" data-row="${esc(sol.name)}">${__("Skip")}</button>` : ""}
					${sol.status !== "Completed" ? `<button class="btn btn-xs btn-danger goh-sol-cancel" data-row="${esc(sol.name)}"><i class="fa fa-times"></i> ${__("Cancel")}</button>` : ""}
				</div>
			</div>
		`).join("");

		const cancelledCards = cancelledSols.length ? `
			<div class="goh-cancelled-section mt-3">
				<div class="text-muted small mb-1"><i class="fa fa-ban"></i> ${__("Cancelled Solutions")}</div>
				${cancelledSols.map(sol => `
					<div class="goh-repair-card goh-cancelled-card" style="opacity:0.6">
						<div class="goh-repair-card-head">
							<span class="goh-repair-sol-name" style="text-decoration:line-through">${esc(sol.repair_solution || "—")}</span>
							<span class="goh-badge badge-red">${__("Cancelled")}</span>
						</div>
						<div class="goh-repair-remarks text-danger small"><i class="fa fa-comment"></i> ${esc(sol.cancel_reason || sol.technician_remarks || "No reason provided")}</div>
					</div>
				`).join("")}
			</div>
		` : "";

		// Spare parts — separate active and damaged
		const activeSpares = (d.spare_lines || []).filter(sp => sp.status !== "Damaged");
		const damagedSpares = (d.spare_lines || []).filter(sp => sp.status === "Damaged");

		const spareRows = activeSpares.map(sp => `
			<tr data-spare-row="${esc(sp.name)}">
				<td>${esc(sp.item_name || sp.spare_item)}</td>
				<td class="text-center">${sp.qty} ${esc(sp.uom || "")}</td>
				<td class="text-right">₹${format_number(sp.rate)}</td>
				<td class="text-center"><button class="btn btn-xs btn-outline-danger goh-spare-damage" data-row="${esc(sp.name)}" title="${__("Mark Damaged")}"><i class="fa fa-exclamation-triangle"></i></button></td>
			</tr>
		`).join("");

		const damagedRows = damagedSpares.map(sp => `
			<tr style="opacity:0.6; text-decoration:line-through">
				<td>${esc(sp.item_name || sp.spare_item)}</td>
				<td class="text-center">${sp.qty} ${esc(sp.uom || "")}</td>
				<td class="text-right">₹${format_number(sp.rate)}</td>
				<td class="text-center"><span class="goh-badge badge-red">${__("Damaged")}</span></td>
			</tr>
			<tr style="opacity:0.6"><td colspan="4" class="text-danger small"><i class="fa fa-comment"></i> ${esc(sp.remarks || "")}</td></tr>
		`).join("");

		// Technician info
		const techInfo = (d.assignments || []).filter(a => a.assignment_status !== "Cancelled").map(a => `
			<span class="goh-assign-chip-sm"><i class="fa fa-user"></i> ${esc(a.engineer_display)} <span class="goh-badge badge-muted">${esc(a.job_type)}</span></span>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-users"></i> ${__("Technician")}
					<button class="btn btn-xs btn-default ml-2" id="goh-handoff-btn"><i class="fa fa-exchange"></i> ${__("Hand Off")}</button>
				</div>
				<div class="goh-tech-chips">${techInfo || `<span class="text-muted">${__("None assigned")}</span>`}</div>
			</div>

			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-wrench"></i> ${__("Repair Progress")}
					<span class="goh-progress-count">${doneCount}/${activeSols.length}</span>
				</div>
				${allDone ? `<div class="goh-all-done-banner"><i class="fa fa-check-circle"></i> ${__("All solutions completed!")}</div>` : ""}
				<div class="goh-repair-cards">${solCards || `<p class="text-muted">${__("No solutions assigned")}</p>`}</div>
				${cancelledCards}
			</div>

			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-cogs"></i> ${__("Spare Parts")}
					<button class="btn btn-xs btn-default ml-2" id="goh-add-spare-btn"><i class="fa fa-plus"></i> ${__("Add")}</button>
				</div>
				${spareRows || damagedRows ? `
					<table class="goh-table">
						<thead><tr><th>${__("Part")}</th><th class="text-center">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-center" style="width:50px"></th></tr></thead>
						<tbody>${spareRows}${damagedRows}</tbody>
					</table>
				` : `<p class="text-muted">${__("No spare parts used")}</p>`}
			</div>

			${allDone ? `
				<div class="goh-section-actions">
					<button class="btn btn-xs btn-default" id="goh-back-to-assign"><i class="fa fa-arrow-left"></i> ${__("Back to Assignment")}</button>
					<button class="btn btn-sm btn-primary ml-2" id="goh-submit-qc"><i class="fa fa-check-square-o"></i> ${__("Submit for QC")}</button>
				</div>
			` : `
				<div class="goh-section-actions">
					<button class="btn btn-xs btn-default" id="goh-back-to-assign"><i class="fa fa-arrow-left"></i> ${__("Back to Assignment")}</button>
				</div>
			`}
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 6 — Quality Control                                             */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_qc(d) {
		const esc = frappe.utils.escape_html;
		const qc_status = d.qc_status || "Awaiting";
		const checklist = d.qc_checklist || [];

		// Solution summary
		const solChips = (d.solution_lines || []).map(s => `
			<span class="goh-badge ${s.status === "Completed" ? "badge-green" : "badge-yellow"}">${esc(s.repair_solution)}</span>
		`).join(" ");

		const checkRows = checklist.map(row => `
			<tr class="goh-qc-row" data-name="${esc(row.name)}">
				<td>${esc(row.check_name)}</td>
				<td>
					<select class="form-control input-xs goh-qc-result" data-name="${esc(row.name)}" data-check="${esc(row.check_name)}">
						<option value="">${__("—")}</option>
						<option value="Pass" ${row.result === "Pass" ? "selected" : ""}>${__("Pass")}</option>
						<option value="Fail" ${row.result === "Fail" ? "selected" : ""}>${__("Fail")}</option>
						<option value="N/A" ${row.result === "N/A" ? "selected" : ""}>${__("N/A")}</option>
					</select>
				</td>
				<td><input class="form-control input-xs goh-qc-remarks" data-name="${esc(row.name)}" data-check="${esc(row.check_name)}" value="${esc(row.remarks || "")}" placeholder="${__("Remarks")}"></td>
			</tr>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-wrench"></i> ${__("Completed Solutions")}</div>
				<div class="goh-sol-chips">${solChips || `<span class="text-muted">${__("None")}</span>`}</div>
			</div>

			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-check-square-o"></i> ${__("QC Checklist")}
					<span class="goh-badge ${qc_status === "Pass" ? "badge-green" : qc_status === "Fail" ? "badge-red" : "badge-indigo"}">${esc(qc_status)}</span>
				</div>

				${checklist.length ? `
					<table class="goh-table" id="goh-qc-table">
						<thead><tr><th>${__("Check")}</th><th style="width:110px">${__("Result")}</th><th>${__("Remarks")}</th></tr></thead>
						<tbody>${checkRows}</tbody>
					</table>
					<div class="mt-2">
						<button class="btn btn-xs btn-default" id="goh-save-qc"><i class="fa fa-save"></i> ${__("Save Results")}</button>
					</div>
				` : `<p class="text-muted">${__("No QC checklist found. Please submit for QC from the Repair step first.")}</p>`}
			</div>

			<div class="goh-section-actions">
				<button class="btn btn-sm btn-success" id="goh-qc-pass"><i class="fa fa-check-circle"></i> ${__("QC Pass")}</button>
				<button class="btn btn-sm btn-danger ml-2" id="goh-qc-fail"><i class="fa fa-times-circle"></i> ${__("QC Fail")}</button>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 7a — Invoice (QC Pass)                                          */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_invoice(d) {
		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-check-circle" style="color:#10b981"></i> ${__("QC Passed — Ready for Invoice")}</div>
				<p class="text-muted">${__("Quality check complete. Loading billing summary...")}</p>
			</div>

			<div class="goh-section" id="goh-invoice-body">
				<div class="text-center p-3"><i class="fa fa-spinner fa-spin"></i></div>
			</div>

			<div class="goh-section-actions">
				<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="btn btn-sm btn-default"><i class="fa fa-external-link"></i> ${__("Open SR")}</a>
				${d.service_invoice ? `<a href="/app/sales-invoice/${encodeURIComponent(d.service_invoice)}" target="_blank" class="btn btn-sm btn-primary ml-2"><i class="fa fa-file-text-o"></i> ${__("View Invoice")}</a>` : ""}
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 7b — Rework (QC Fail)                                           */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_rework(d) {
		const esc = frappe.utils.escape_html;
		const failed = (d.qc_checklist || []).filter(c => c.result === "Fail");
		const allChecks = (d.qc_checklist || []).map(row => `
			<tr>
				<td>${esc(row.check_name)}</td>
				<td><span class="goh-badge ${row.result === "Pass" ? "badge-green" : row.result === "Fail" ? "badge-red" : "badge-muted"}">${esc(row.result || "—")}</span></td>
				<td class="text-muted small">${esc(row.remarks || "")}</td>
			</tr>
		`).join("");

		const failItems = failed.map(r => `
			<div class="goh-fail-item"><i class="fa fa-times-circle text-danger"></i> ${esc(r.check_name)} ${r.remarks ? `— <span class="text-muted">${esc(r.remarks)}</span>` : ""}</div>
		`).join("");

		return `
			<div class="goh-section goh-rework-alert">
				<div class="goh-section-title"><i class="fa fa-exclamation-triangle text-danger"></i> ${__("QC Failed — Rework Required")}</div>
				${failItems ? `<div class="goh-fail-list">${failItems}</div>` : `<p class="text-muted">${__("QC result: Fail")}</p>`}
			</div>

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-list"></i> ${__("Full QC Checklist")}</div>
				<table class="goh-table">
					<thead><tr><th>${__("Check")}</th><th>${__("Result")}</th><th>${__("Remarks")}</th></tr></thead>
					<tbody>${allChecks || `<tr><td colspan="3" class="text-muted text-center">${__("No checklist")}</td></tr>`}</tbody>
				</table>
			</div>

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-user-plus"></i> ${__("Reassign to Technician")}</div>
				<div class="row">
					<div class="col-sm-5">
						<label class="goh-field-label">${__("Technician")}</label>
						<div id="goh-rework-tech-field"></div>
					</div>
					<div class="col-sm-3">
						<label class="goh-field-label">${__("Job Type")}</label>
						<select class="form-control input-sm" id="goh-rework-job-type">
							<option value="Repair">${__("Repair")}</option>
							<option value="Diagnosis">${__("Diagnosis")}</option>
							<option value="Testing">${__("Testing")}</option>
						</select>
					</div>
					<div class="col-sm-4">
						<label class="goh-field-label">${__("Manager Notes")}</label>
						<textarea class="form-control input-sm" id="goh-rework-notes" rows="2" placeholder="${__("Instructions for rework...")}"></textarea>
					</div>
				</div>
				<div class="goh-section-actions mt-2">
					<button class="btn btn-sm btn-primary" id="goh-rework-assign"><i class="fa fa-user-plus"></i> ${__("Reassign & Send to Repair")}</button>
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Done                                                                 */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_done(d) {
		const esc = frappe.utils.escape_html;
		return `
			<div class="goh-done-state">
				<i class="fa fa-check-circle fa-3x" style="color:#10b981"></i>
				<h4 class="mt-2">${__("Repair Complete")}</h4>
				<p class="text-muted">${__("Status")}: <b>${esc(d.decision)}</b></p>
				<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="btn btn-default btn-sm mt-2"><i class="fa fa-external-link"></i> ${__("View Service Request")}</a>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Event Binding                                                        */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_bind_step_events(d) {
		const content = this.parent.find("#goh-tab-work");
		const self = this;

		/* Determine which stage's events to bind */
		const activeStage = d._view_stage || d.ops_stage;
		const isViewing = !!d._view_stage;

		/* If past QC (qc / invoice / done), previous steps are read-only */
		if (isViewing && ["qc", "invoice", "done"].includes(d.ops_stage)) return;

		/* ── Analysis ────────────────────────────────────────────────── */
		if (activeStage === "analysis") {
			// Load categories
			frappe.xcall(`${API}.get_issue_categories`).then(cats => {
				const opts = (cats || []).map(c => `<option value="${frappe.utils.escape_html(c)}">`).join("");
				this.parent.find("#goh-cat-list").html(opts);
			});

			content.find("#goh-add-issue").on("click", () => {
				const tbody = this.parent.find("#goh-issue-tbody");
				tbody.find("td[colspan]").closest("tr").remove();
				const idx = tbody.find("tr").length;
				tbody.append(`
					<tr data-idx="${idx}">
						<td><input class="form-control input-xs goh-issue-cat" list="goh-cat-list" placeholder="${__("Issue Category")}"></td>
						<td><select class="form-control input-xs goh-issue-reporter"><option value="Technician">${__("Technician")}</option><option value="Customer">${__("Customer")}</option></select></td>
						<td><input class="form-control input-xs goh-issue-desc" placeholder="${__("Description")}"></td>
						<td></td>
						<td><button class="btn btn-xs btn-danger goh-issue-remove"><i class="fa fa-trash"></i></button></td>
					</tr>
				`);
			});

			content.on("click", ".goh-issue-remove", function () {
				const tr = $(this).closest("tr");
				const rowName = $(this).data("row") || tr.data("name");

				// Unsaved row (newly added, no name yet) — just remove from DOM
				if (!rowName) {
					tr.remove();
					return;
				}

				// Saved row — ask for reason, then soft-delete
				const dlg = new frappe.ui.Dialog({
					title: __("Delete Issue"),
					fields: [{ fieldname: "reason", label: __("Reason for deletion"), fieldtype: "Small Text", reqd: 1 }],
					primary_action_label: __("Delete Issue"),
					primary_action: v => {
						frappe.xcall(`${API}.delete_issue_line`, { sr_name: d.name, issue_row_name: rowName, reason: v.reason })
							.then(() => { dlg.hide(); frappe.show_alert({ message: __("Issue deleted."), indicator: "orange" }); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			content.find("#goh-save-issues").on("click", () => {
				const issues = self._collect_issues();
				if (!issues.length) return frappe.show_alert({ message: __("Add at least one issue."), indicator: "orange" });
				frappe.xcall(`${API}.save_issue_lines`, { sr_name: d.name, issues_json: JSON.stringify(issues) })
					.then(() => { frappe.show_alert({ message: __("Issues saved."), indicator: "green" }); self._load_detail(d.name); });
			});

			content.find("#goh-confirm-analysis").on("click", () => {
				const issues = self._collect_issues();
				if (!issues.length) return frappe.show_alert({ message: __("Add at least one issue."), indicator: "orange" });
				frappe.xcall(`${API}.save_issue_lines`, { sr_name: d.name, issues_json: JSON.stringify(issues) })
					.then(() => frappe.xcall(`${API}.confirm_analysis`, { sr_name: d.name }))
					.then(() => { frappe.show_alert({ message: __("Analysis confirmed."), indicator: "green" }); self._refresh_all(); });
			});
		}

		/* ── Confirm ─────────────────────────────────────────────────── */
		if (activeStage === "confirm") {
			content.find("#goh-send-wa").on("click", () => {
				frappe.xcall(`${API}.send_confirmation_whatsapp`, { sr_name: d.name })
					.then(r => {
						frappe.show_alert({ message: r.whatsapp_sent ? __("WhatsApp sent!") : __("WhatsApp not configured. Mark manually."), indicator: r.whatsapp_sent ? "green" : "orange" });
						self._load_detail(d.name);
					});
			});

			content.find("#goh-mark-confirmed").on("click", () => {
				frappe.xcall(`${API}.mark_customer_confirmed`, { sr_name: d.name })
					.then(() => { frappe.show_alert({ message: __("Customer confirmed."), indicator: "green" }); self._refresh_all(); });
			});

			content.find("#goh-back-to-analysis").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "analysis" })
					.then(() => self._refresh_all());
			});
		}

		/* ── Solutions ───────────────────────────────────────────────── */
		if (activeStage === "solutions") {
			this._load_solutions_for_categories(d);

			content.find("#goh-back-to-confirm").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "confirm" })
					.then(() => self._refresh_all());
			});

			content.find("#goh-save-solutions").on("click", () => {
				const selected = [];
				this.parent.find(".goh-sol-check:checked").each(function () {
					const needs_spare = $(this).data("requires-spare") == "1" ? 1 : 0;
					selected.push({
						repair_solution: $(this).data("solution"),
						issue_category: $(this).data("category"),
						solution_code: $(this).data("code"),
						estimated_minutes: parseInt($(this).data("minutes") || 0),
						requires_spare: needs_spare,
						auto_add_spares: needs_spare,
					});
				});
				if (!selected.length) return frappe.show_alert({ message: __("Select at least one solution."), indicator: "orange" });
				frappe.xcall(`${API}.save_solution_assignment`, { sr_name: d.name, solutions_json: JSON.stringify(selected) })
					.then(() => { frappe.show_alert({ message: __("Solutions saved."), indicator: "green" }); self._refresh_all(); });
			});
		}

		/* ── Assign ──────────────────────────────────────────────────── */
		if (activeStage === "assign") {
			this._init_link_field("#goh-tech-field", "Employee", __("Search technician..."), {
				status: "Active",
			});

			content.find("#goh-back-to-solutions").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "solutions" })
					.then(() => self._refresh_all());
			});

			content.find("#goh-do-assign").on("click", () => {
				const tech = this._tech_field && this._tech_field.get_value();
				if (!tech) return frappe.show_alert({ message: __("Select a technician."), indicator: "orange" });
				frappe.xcall(`${API}.assign_technician`, {
					sr_name: d.name, technician: tech,
					job_type: content.find("#goh-job-type").val() || "Repair",
					estimated_hours: parseFloat(content.find("#goh-est-hours").val() || 2),
				}).then(() => { frappe.show_alert({ message: __("Technician assigned!"), indicator: "green" }); self._refresh_all(); });
			});
		}

		/* ── Repair ──────────────────────────────────────────────────── */
		if (activeStage === "repair") {
			content.find("#goh-back-to-assign").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "assign" })
					.then(() => self._refresh_all());
			});

			content.on("click", ".goh-sol-start", function () {
				frappe.xcall(`${API}.update_solution_status`, { sr_name: d.name, solution_row_name: $(this).data("row"), status: "In Progress" })
					.then(() => self._load_detail(d.name));
			});

			content.on("click", ".goh-sol-complete", function () {
				const rowName = $(this).data("row");
				const dlg = new frappe.ui.Dialog({
					title: __("Complete Solution"), fields: [{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" }],
					primary_action_label: __("Done"),
					primary_action: v => {
						frappe.xcall(`${API}.update_solution_status`, { sr_name: d.name, solution_row_name: rowName, status: "Completed", remarks: v.remarks || "" })
							.then(() => { dlg.hide(); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			content.on("click", ".goh-sol-skip", function () {
				frappe.xcall(`${API}.update_solution_status`, { sr_name: d.name, solution_row_name: $(this).data("row"), status: "Skipped" })
					.then(() => self._load_detail(d.name));
			});

			content.on("click", ".goh-sol-cancel", function () {
				const rowName = $(this).data("row");
				const dlg = new frappe.ui.Dialog({
					title: __("Cancel Solution"),
					fields: [{ fieldname: "reason", label: __("Reason for cancellation"), fieldtype: "Small Text", reqd: 1 }],
					primary_action_label: __("Cancel Solution"),
					primary_action: v => {
						frappe.xcall(`${API}.update_solution_status`, { sr_name: d.name, solution_row_name: rowName, status: "Cancelled", remarks: v.reason })
							.then(() => { dlg.hide(); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			content.on("click", ".goh-spare-damage", function () {
				const rowName = $(this).data("row");
				const dlg = new frappe.ui.Dialog({
					title: __("Mark Spare as Damaged"),
					fields: [{ fieldname: "remarks", label: __("Reason (required)"), fieldtype: "Small Text", reqd: 1 }],
					primary_action_label: __("Mark Damaged"),
					primary_action: v => {
						frappe.xcall(`${API}.mark_spare_damaged`, { sr_name: d.name, spare_row_name: rowName, remarks: v.remarks })
							.then(() => { dlg.hide(); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			content.find("#goh-add-spare-btn").on("click", () => {
				const dlg = new frappe.ui.Dialog({
					title: __("Add Spare Part"),
					fields: [
						{ fieldname: "spare_item", label: __("Spare Part"), fieldtype: "Link", options: "Item", reqd: 1,
							get_query: () => ({ filters: { item_group: ["descendants of (inclusive)", "Spare Parts"] } }) },
						{ fieldname: "qty", label: __("Qty"), fieldtype: "Float", default: 1, reqd: 1 },
						{ fieldname: "rate", label: __("Rate"), fieldtype: "Currency", default: 0 },
					],
					primary_action_label: __("Add"),
					primary_action: v => {
						frappe.xcall(`${API}.add_spare_to_ticket`, { sr_name: d.name, spare_item: v.spare_item, qty: v.qty, rate: v.rate || 0 })
							.then(() => { dlg.hide(); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			content.find("#goh-handoff-btn").on("click", () => {
				const dlg = new frappe.ui.Dialog({
					title: __("Hand Off"), fields: [
						{ fieldname: "new_technician", label: __("Technician"), fieldtype: "Link", options: "Employee", reqd: 1 },
						{ fieldname: "job_type", label: __("Job Type"), fieldtype: "Select", options: "Repair\nDiagnosis\nSpare Parts Replacement\nTesting", default: "Repair" },
						{ fieldname: "reason", label: __("Reason"), fieldtype: "Small Text" },
					],
					primary_action_label: __("Hand Off"),
					primary_action: v => {
						frappe.xcall(`${API}.handoff_to_technician`, { sr_name: d.name, new_technician: v.new_technician, job_type: v.job_type, reason: v.reason || "" })
							.then(() => { dlg.hide(); self._load_detail(d.name); frappe.show_alert({ message: __("Handed off."), indicator: "green" }); });
					},
				});
				dlg.show();
			});

			content.find("#goh-submit-qc").on("click", () => {
				frappe.confirm(__("Submit for QC? Remaining solutions will be marked as Completed."), () => {
					frappe.xcall(`${API}.submit_for_qc`, { sr_name: d.name })
						.then(() => { frappe.show_alert({ message: __("Submitted for QC!"), indicator: "green" }); self._refresh_all(); });
				});
			});
		}

		/* ── QC ──────────────────────────────────────────────────────── */
		if (activeStage === "qc") {
			content.find("#goh-save-qc").on("click", () => {
				const checklist = [];
				this.parent.find("#goh-qc-table tbody tr").each(function () {
					checklist.push({
						name: $(this).data("name"),
						check_name: $(this).find(".goh-qc-result").data("check"),
						result: $(this).find(".goh-qc-result").val(),
						remarks: $(this).find(".goh-qc-remarks").val() || "",
					});
				});
				frappe.xcall(`${API}.save_qc_results`, { sr_name: d.name, checklist_json: JSON.stringify(checklist) })
					.then(() => frappe.show_alert({ message: __("QC results saved."), indicator: "green" }));
			});

			content.find("#goh-qc-pass").on("click", () => {
				frappe.confirm(__("Mark QC as Pass? Ticket will move to Invoice."), () => {
					frappe.xcall(`${API}.complete_qc`, { sr_name: d.name, qc_result: "Pass" })
						.then(() => { frappe.show_alert({ message: __("QC Passed!"), indicator: "green" }); self._refresh_all(); });
				});
			});

			content.find("#goh-qc-fail").on("click", () => {
				frappe.confirm(__("Mark QC as Fail? Floor manager will need to reassign."), () => {
					frappe.xcall(`${API}.complete_qc`, { sr_name: d.name, qc_result: "Fail" })
						.then(() => { frappe.show_alert({ message: __("QC Failed."), indicator: "red" }); self._refresh_all(); });
				});
			});
		}

		/* ── Invoice ─────────────────────────────────────────────────── */
		if (activeStage === "invoice") {
			frappe.xcall(`${API}.get_invoice_summary`, { sr_name: d.name }).then(s => {
				const esc = frappe.utils.escape_html;
				const makeRows = items => items.map(i => `
					<tr><td>${esc(i.item_name || i.item_code)}</td><td class="text-right">${i.qty}</td><td class="text-right">₹${format_number(i.rate)}</td><td class="text-right">₹${format_number(i.amount)}</td></tr>
				`).join("");
				const makeDamagedRows = items => items.map(i => `
					<tr class="text-danger"><td>${esc(i.item_name || i.item_code)} <small class="text-muted">${esc(i.remarks || "")}</small></td><td class="text-right">${i.qty}</td><td class="text-right">₹${format_number(i.rate)}</td><td class="text-right">₹${format_number(i.amount)}</td></tr>
				`).join("");

				this.parent.find("#goh-invoice-body").html(`
					${s.service_items.length ? `
						<h6>${__("Service Charges")}</h6>
						<table class="goh-table"><thead><tr><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-right">${__("Amount")}</th></tr></thead>
						<tbody>${makeRows(s.service_items)}</tbody>
						<tfoot><tr><td colspan="3" class="text-right"><b>${__("Subtotal")}</b></td><td class="text-right"><b>₹${format_number(s.service_total)}</b></td></tr></tfoot></table>
					` : ""}
					${s.spare_items.length ? `
						<h6 class="mt-3">${__("Spare Parts (Consumed)")}</h6>
						<table class="goh-table"><thead><tr><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-right">${__("Amount")}</th></tr></thead>
						<tbody>${makeRows(s.spare_items)}</tbody>
						<tfoot><tr><td colspan="3" class="text-right"><b>${__("Subtotal")}</b></td><td class="text-right"><b>₹${format_number(s.spare_total)}</b></td></tr></tfoot></table>
					` : ""}
					${s.damaged_spare_items && s.damaged_spare_items.length ? `
						<h6 class="mt-3 text-danger"><i class="fa fa-exclamation-triangle"></i> ${__("Damaged Spares (Company Cost)")}</h6>
						<table class="goh-table"><thead><tr><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-right">${__("Amount")}</th></tr></thead>
						<tbody>${makeDamagedRows(s.damaged_spare_items)}</tbody>
						<tfoot><tr><td colspan="3" class="text-right"><b>${__("Subtotal")}</b></td><td class="text-right"><b class="text-danger">₹${format_number(s.damaged_spare_total)}</b></td></tr></tfoot></table>
					` : ""}
					${s.discount ? `<div class="text-muted mt-2">${__("Discount")}: -₹${format_number(s.discount)}</div>` : ""}
					<div class="goh-grand-total mt-3">
						<h4>${__("Cost to Customer")}: <span style="color:#059669">₹${format_number(s.customer_total)}</span></h4>
						${s.damaged_spare_items && s.damaged_spare_items.length ? `
							<h5 class="mt-1 text-muted">${__("Cost to Company")}: <span style="color:#dc2626">₹${format_number(s.company_total)}</span></h5>
						` : ""}
					</div>
					${s.service_invoice ? `<div class="mt-2"><span class="goh-badge badge-green">${__("Invoiced")}</span> <a href="/app/sales-invoice/${encodeURIComponent(s.service_invoice)}" target="_blank">${esc(s.service_invoice)}</a></div>` : `<p class="text-muted mt-2">${__("Invoice will be created at POS during handover.")}</p>`}
				`);
			});
		}

		/* ── Rework ──────────────────────────────────────────────────── */
		if (activeStage === "rework") {
			this._init_link_field("#goh-rework-tech-field", "Employee", __("Select technician..."), {
				status: "Active",
			}, "_rework_tech");

			content.find("#goh-rework-assign").on("click", () => {
				const tech = this._rework_tech && this._rework_tech.get_value();
				if (!tech) return frappe.show_alert({ message: __("Select a technician."), indicator: "orange" });
				frappe.xcall(`${API}.reassign_after_qc_fail`, {
					sr_name: d.name, technician: tech,
					job_type: content.find("#goh-rework-job-type").val() || "Repair",
					manager_notes: content.find("#goh-rework-notes").val() || "",
				}).then(() => { frappe.show_alert({ message: __("Reassigned. Ticket back in Repair."), indicator: "green" }); self._refresh_all(); });
			});
		}

		/* ── Hide stage-transition buttons when viewing a previous step ── */
		if (isViewing) {
			content.find(
				"#goh-confirm-analysis, #goh-send-wa, #goh-mark-confirmed, " +
				"#goh-back-to-analysis, #goh-save-solutions, #goh-back-to-confirm, " +
				"#goh-do-assign, #goh-back-to-solutions, #goh-submit-qc, " +
				"#goh-handoff-btn, #goh-back-to-assign, #goh-rework-assign"
			).hide();
		}
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Helper Methods                                                       */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_collect_issues() {
		const issues = [];
		this.parent.find("#goh-issue-tbody tr").each(function () {
			const cat = $(this).find(".goh-issue-cat").val();
			if (!cat || !cat.trim()) return;
			issues.push({
				issue_category: cat.trim(),
				reported_by: $(this).find(".goh-issue-reporter").val() || "Technician",
				description: $(this).find(".goh-issue-desc").val() || "",
				status: "Open",
			});
		});
		return issues;
	}

	_init_link_field(selector, doctype, placeholder, filters, propName) {
		const wrapper = this.parent.find(selector);
		if (!wrapper.length) return;
		const ctrl = frappe.ui.form.make_control({
			df: { fieldname: propName || "tech", fieldtype: "Link", options: doctype, placeholder, get_query: () => ({ filters }) },
			parent: wrapper, render_input: true,
		});
		if (propName) this[propName] = ctrl;
		else this._tech_field = ctrl;
	}

	async _load_solutions_for_categories(d) {
		const self = this;
		const activeIssues = (d.issue_lines || []).filter(r => r.status !== "Deleted");
		const categories = [...new Set(activeIssues.map(i => i.issue_category).filter(Boolean))];
		if (!categories.length) {
			this.parent.find("#goh-sol-picker").html(`<p class="text-muted">${__("No issues documented yet.")}</p>`);
			return;
		}

		let allHtml = "";
		for (const cat of categories) {
			const esc = frappe.utils.escape_html;
			try {
				const sols = await frappe.xcall(`${API}.get_solutions_for_issue`, { issue_category: cat });
				const rows = (sols || []).map(s => `
					<label class="goh-sol-option">
						<input type="checkbox" class="goh-sol-check"
							data-solution="${esc(s.name)}" data-category="${esc(cat)}"
							data-code="${esc(s.solution_code || "")}" data-minutes="${s.estimated_minutes || 0}"
							data-requires-spare="${s.requires_spare ? 1 : 0}">
						<span class="goh-sol-name">${esc(s.solution_name || s.name)}</span>
						<span class="text-muted small ml-2">${s.estimated_minutes || 0}min</span>
						${s.requires_spare ? `<span class="goh-badge badge-yellow ml-1">${__("Spare")}</span>` : ""}
					</label>
				`).join("");
				allHtml += `
					<div class="goh-sol-category" data-category="${esc(cat)}">
						<h6 class="goh-sol-cat-title"><i class="fa fa-tag"></i> ${esc(cat)}</h6>
						${rows}
						<button class="btn btn-xs btn-default goh-add-sol-btn mt-1" data-category="${esc(cat)}"><i class="fa fa-plus"></i> ${__("Add Solution")}</button>
					</div>`;
			} catch (_) {
				allHtml += `
					<div class="goh-sol-category" data-category="${esc(cat)}">
						<h6 class="goh-sol-cat-title"><i class="fa fa-tag"></i> ${esc(cat)}</h6>
						<p class="text-muted small">${__("No pre-defined solutions.")}</p>
						<button class="btn btn-xs btn-default goh-add-sol-btn mt-1" data-category="${esc(cat)}"><i class="fa fa-plus"></i> ${__("Add Solution")}</button>
					</div>`;
			}
		}
		this.parent.find("#goh-sol-picker").html(allHtml);

		// Bind "Add Solution" buttons
		this.parent.find(".goh-add-sol-btn").on("click", function () {
			const cat = $(this).data("category");
			const dlg = new frappe.ui.Dialog({
				title: __("Add Solution for {0}", [cat]),
				fields: [
					{ fieldname: "solution_name", label: __("Solution Name"), fieldtype: "Data", reqd: 1, description: __("e.g. Screen Replacement, Battery Replace") },
					{ fieldname: "estimated_minutes", label: __("Estimated Minutes"), fieldtype: "Int", default: 30 },
					{ fieldname: "requires_spare", label: __("Requires Spare Part"), fieldtype: "Check" },
					{ fieldname: "description", label: __("Description"), fieldtype: "Small Text" },
				],
				primary_action_label: __("Create & Select"),
				primary_action: v => {
					frappe.xcall(`${API}.quick_create_solution`, {
						solution_name: v.solution_name,
						issue_category: cat,
						estimated_minutes: v.estimated_minutes || 30,
						requires_spare: v.requires_spare ? 1 : 0,
						description: v.description || "",
					}).then(r => {
						dlg.hide();
						if (r.exists) {
							frappe.show_alert({ message: __("Solution '{0}' already exists — added to list.", [v.solution_name]), indicator: "blue" });
						} else {
							frappe.show_alert({ message: __("Solution created!"), indicator: "green" });
						}
						// Reload solutions picker to show the new one
						self._load_solutions_for_categories(d);
					});
				},
			});
			dlg.show();
		});
	}

	_refresh_all() {
		this._load_queue();
		if (this.selectedSR) this._load_detail(this.selectedSR);
	}
}
