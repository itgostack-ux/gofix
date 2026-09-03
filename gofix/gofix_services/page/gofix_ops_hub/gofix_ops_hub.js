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
	{ key: "solutions", label: "Solutions",  icon: "fa-bolt",          color: "#f59e0b" },
	{ key: "confirm",   label: "Confirm",   icon: "fa-check-circle",   color: "#8b5cf6" },
	{ key: "assign",    label: "Assign",     icon: "fa-user-plus",     color: "#10b981" },
	{ key: "repair",    label: "Repair",     icon: "fa-wrench",        color: "#ef4444" },
	{ key: "qc",        label: "QC",         icon: "fa-check-square-o", color: "#6366f1" },
	{ key: "invoice",   label: "Invoice",    icon: "fa-file-text-o",   color: "#059669" },
];

const STAGE_BADGE = {
	analysis:  { label: "Analysis",  cls: "badge-blue" },
	solutions: { label: "Solutions", cls: "badge-yellow" },
	confirm:   { label: "Confirm",   cls: "badge-purple" },
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

function get_ops_stage_list_filters(queue, stage) {
	const names = (queue || [])
		.filter(sr => sr.ops_stage === stage)
		.map(sr => sr.name)
		.filter(Boolean);

	return names.length ? { name: ["in", names] } : null;
}

function format_assignment_hours(value) {
	if (value === null || value === undefined || value === "") {
		return "—";
	}
	const hours = Number(value);
	if (!Number.isFinite(hours) || hours < 0) {
		return "—";
	}
	if (hours === 0) {
		return "0h";
	}

	let minutes = Math.max(1, Math.round(hours * 60));
	const wholeHours = Math.floor(minutes / 60);
	minutes %= 60;
	if (!wholeHours) {
		return `${minutes}m`;
	}
	return minutes ? `${wholeHours}h ${minutes}m` : `${wholeHours}h`;
}

if (typeof module !== "undefined" && module.exports) {
	module.exports = { format_assignment_hours, get_ops_stage_list_filters };
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/*  Main Class                                                               */
/* ═══════════════════════════════════════════════════════════════════════════ */
class GoFixOpsHub {
	constructor(page) {
		this.page = page;
		this.parent = $(page.body);
		this.ctx = {};
		this.active_company = "";
		this.queue = [];
		this.selectedSR = null;
		this.detail = null;
		this._search_timer = null;
		this._init();
	}

	async _init() {
		this.active_company = this._active_company();
		try {
			this.ctx = await frappe.xcall(`${API}.get_ops_context`, {
				company: this.active_company,
			});
			this.active_company = this.ctx.company || this.active_company || "";
		} catch (e) {
			console.error("GoFix Ops Hub: get_ops_context failed", e);
			this.ctx = { stores: [], warehouses: [], is_manager: false, company: this.active_company };
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

	_active_company() {
		const lock = window.ch_erp15 && window.ch_erp15.company_lock;
		if (lock && typeof lock.active_company === "function") {
			return lock.active_company() || "";
		}
		if (frappe.defaults) {
			return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
		}
		return "";
	}

	/* ── Toolbar ────────────────────────────────────────────────────────── */
	_build_toolbar() {
		const esc = frappe.utils.escape_html;
		const wh_options = ["<option value=''>" + __("All Stores") + "</option>"];
		const stores = this.ctx.stores || [];
		if (stores.length) {
			stores.forEach(store => {
				const value = store.warehouse || store.value || "";
				if (!value) return;
				const label = store.store_code || store.store_name || value.split(" - ")[0];
				const title = store.store_name && store.store_name !== label ? store.store_name : value;
				wh_options.push(`<option value="${esc(value)}" title="${esc(title)}">${esc(label)}</option>`);
			});
		} else {
			(this.ctx.warehouses || []).forEach(w => {
				const short = w.split(" - ")[0];
				wh_options.push(`<option value="${esc(w)}">${esc(short)}</option>`);
			});
		}

		// A blank hub and a broken hub look identical. When the server says the
		// user can see nothing, say so where they are looking.
		const access = this.ctx.access;
		if (access && access.ok === false) {
			this.page.set_indicator(__("No store access"), "orange");
			this.parent.prepend(`
				<div class="goh-access-note">
					<div class="goh-access-title">${esc(access.title || __("Nothing to show"))}</div>
					<div class="goh-access-detail">${esc(access.detail || "")}</div>
				</div>
			`);
		}

		this.page.set_secondary_action(__("Refresh"), () => this._refresh_all(), "refresh");

		// Custom toolbar fields
		const today = frappe.datetime.get_today();
		const d60ago = frappe.datetime.add_days(today, -60);
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
					<option value="rejected">${__("Rejected")}</option>
				</select>
				<select class="form-control input-xs goh-tb-priority">
					<option value="">${__("All Priorities")}</option>
					<option value="Urgent">${__("Urgent")}</option>
					<option value="High">${__("High")}</option>
					<option value="Medium">${__("Medium")}</option>
					<option value="Low">${__("Low")}</option>
				</select>
				<input type="date" class="form-control input-xs goh-tb-date-from" value="${d60ago}" title="${__("From Date")}">
				<input type="date" class="form-control input-xs goh-tb-date-to" value="${today}" title="${__("To Date")}">
			</div>
		`);

		// Bind toolbar events
		this.page.wrapper.find(".goh-tb-warehouse, .goh-tb-stage, .goh-tb-priority").on("change", () => this._load_queue());
		this.page.wrapper.find(".goh-tb-date-from, .goh-tb-date-to").on("change", () => this._load_queue());
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
		const date_from = toolbar.find(".goh-tb-date-from").val() || "";
		const date_to = toolbar.find(".goh-tb-date-to").val() || "";
		const company = this._active_company() || this.active_company || "";

		try {
			let data = await frappe.xcall(`${API}.get_ticket_queue`, {
				warehouse, search, stage_filter: stage, date_from, date_to, company,
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

		// Stage count bar with click-to-list
		const stageCounts = {};
		this.queue.forEach(sr => { stageCounts[sr.ops_stage] = (stageCounts[sr.ops_stage] || 0) + 1; });

		const pillsHTML = [...STAGES, { key: "rework", label: "Rework", color: "#ef4444" }, { key: "done", label: "Done", color: "#059669" }]
			.filter(s => stageCounts[s.key])
			.map(s => `<span class="goh-stage-pill" data-stage="${s.key}"
				style="cursor:pointer;display:inline-flex;align-items:center;gap:3px;padding:2px 8px;
				border-radius:10px;font-size:11px;background:${s.color}22;color:${s.color};border:1px solid ${s.color}44;"
				title="${__("Open in List View")}">
				${s.label}: <b>${stageCounts[s.key]}</b> <i class="fa fa-external-link" style="font-size:9px;opacity:0.6;"></i>
			</span>`).join("");

		const countBar = pillsHTML
			? `<div class="goh-stage-counts" style="display:flex;flex-wrap:wrap;gap:5px;padding:6px 8px;border-bottom:1px solid var(--border-color);">${pillsHTML}</div>`
			: "";

		container.html(countBar + this.queue.map(sr => this._queue_card(sr)).join(""));

		// Bind stage pill clicks → open SR list
		container.find(".goh-stage-pill").on("click", (e) => {
			e.stopPropagation();
			const stage = $(e.currentTarget).data("stage");
			const filters = get_ops_stage_list_filters(this.queue, stage);
			if (!filters) return;

			// ops_stage is derived from the SR, child solution/assignment rows,
			// and the linked Sales Order's QC state. No Service Request-only
			// status filter can reproduce it, so open the exact queue snapshot
			// behind the badge instead of using a broader decision filter.
			frappe.set_route("List", "Service Request", filters);
		});

		// Bind queue card clicks
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
			let errMsg = "Unknown";
			if (typeof e === "string") {
				errMsg = e;
			} else if (e?.message) {
				errMsg = e.message;
			} else if (e?._server_messages) {
				try {
					const serverMessages = JSON.parse(e._server_messages || "[]");
					const first = serverMessages?.[0] ? JSON.parse(serverMessages[0]) : null;
					errMsg = first?.message || first?.title || e.exc_type || "Unknown";
				} catch (_) {
					errMsg = e.exc_type || e.statusText || "Unknown";
				}
			} else {
				errMsg = e?.exc_type || e?.statusText || String(e || "Unknown");
			}
			main.html(`<div class="goh-empty-state"><i class="fa fa-exclamation-circle fa-3x text-danger"></i><p class="mt-2">${__("API Error loading ticket")}</p><pre class="text-muted small mt-2">${frappe.utils.escape_html(errMsg)}</pre></div>`);
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
			draft:     () => this._html_draft(d),
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

		renderer.closed = () => this._html_closed_history(d);
		contentHtml = (renderer[d.ops_stage] || (() => this._html_closed_history(d)
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
		this._bind_not_repairable(d);
		this._bind_invoice_print_actions();
	}

		_print_invoice(invoice_name) {
			if (!invoice_name) return;
			const open_print = (settings = {}) => {
				const qs = new URLSearchParams({
					doctype: "Sales Invoice",
					name: invoice_name,
					format: settings.print_format || "GoFix Service Invoice",
					no_letterhead: String(settings.no_letterhead !== undefined ? cint(settings.no_letterhead) : 1),
					trigger_print: "1",
				});
				window.open(`/printview?${qs.toString()}`, "_blank");
			};
			frappe.xcall("ch_erp15.ch_erp15.print_helpers.get_sales_invoice_print_settings", {
				invoice_name,
			}).then(open_print).catch(() => open_print());
		}

	_bind_invoice_print_actions() {
		this.parent.off("click", ".goh-print-invoice").on("click", ".goh-print-invoice", (e) => {
			e.preventDefault();
			e.stopPropagation();
			this._print_invoice($(e.currentTarget).data("invoice"));
		});
		// Delegated, and rebound with .off() first: the ticket header is
		// re-rendered on every refresh, and stacking handlers would fire one
		// print job per render.
		this.parent.off("click", ".goh-print-label").on("click", ".goh-print-label", (e) => {
			e.preventDefault();
			e.stopPropagation();
			this._print_device_label($(e.currentTarget).data("sr"));
		});
	}

	/**
	 * Print the stick-on barcode label for the device.
	 *
	 * The bars are a server-rendered PNG (Code128 via ch_erp15's print helper),
	 * not a browser-drawn canvas: a canvas barcode degrades to plain text when
	 * the page reaches a printer, and an unreadable sticker on a customer's
	 * handset is worse than none.
	 */
	_print_device_label(sr_name) {
		if (!sr_name) return;
		frappe.xcall(`${API}.get_device_label`, { sr_name }).then((d) => {
			if (!d || !d.printable) {
				frappe.msgprint({
					title: __("Barcode Unavailable"),
					message: __("The barcode image could not be generated, so the label would carry a number no scanner can read. Check the barcode library on the server."),
					indicator: "red",
				});
				return;
			}
			const esc = frappe.utils.escape_html;
			const row = (k, v) => v ? `<div class="l-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>` : "";
			const label = `
				<div class="label">
					<div class="l-store">${esc(d.store || "")}</div>
					<img class="l-bars" src="data:image/png;base64,${d.barcode_png}" alt="${esc(d.service_request)}">
					<div class="l-num">${esc(d.service_request)}</div>
					${row(__("Customer"), d.customer_name)}
					${row(__("Phone"), d.contact_number)}
					${row(__("Device"), [d.brand, d.device].filter(Boolean).join(" "))}
					${row(__("IMEI / Serial"), d.device_barcode)}
					${row(__("Received"), d.service_date)}
					${d.priority && d.priority !== "Medium"
						? `<div class="l-pri">${esc(d.priority)}</div>` : ""}
				</div>`;

			const w = window.open("", "_blank", "width=460,height=640");
			if (!w) {
				frappe.msgprint(__("Allow pop-ups for this site to print the label."));
				return;
			}
			w.document.write(`<!doctype html><html><head><title>${esc(d.service_request)}</title>
				<style>
					@page { size: 50mm 30mm; margin: 2mm; }
					body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; }
					.label { width: 46mm; padding: 1mm 0 2mm; page-break-after: always; }
					.l-store { font-size: 7pt; text-transform: uppercase; letter-spacing: .4px; }
					.l-bars { width: 100%; height: 11mm; object-fit: contain; display: block; }
					/* HRI directly under the bars: the two must never be separated. */
					.l-num { font-family: ui-monospace, Menlo, Consolas, monospace;
						font-size: 8.5pt; letter-spacing: .5px; text-align: center; margin-bottom: 1mm; }
					.l-row { display: flex; justify-content: space-between; gap: 3mm;
						font-size: 6.5pt; line-height: 1.35; }
					.l-row span { color: #555; }
					.l-row b { font-weight: 600; text-align: right; }
					.l-pri { margin-top: .6mm; font-size: 6.5pt; font-weight: 700; }
					@media print { .label:last-child { page-break-after: auto; } }
				</style></head><body>${label.repeat(d.copies || 1)}</body></html>`);
			w.document.close();
			// Wait for the barcode image to decode, or the sheet prints blank.
			const go = () => { w.focus(); w.print(); };
			const img = w.document.querySelector(".l-bars");
			if (img && !img.complete) { img.onload = go; img.onerror = go; } else { go(); }
		});
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

		// The promise made to the customer is a DATETIME. This badge used to read
		// expected_completion_date, a date with no time of day, so it measured to
		// midnight and disagreed with the countdown a few pixels below it —
		// "Due Soon (3h)" against "09:28:11 left" for the same ticket. Both now
		// come from the same server-computed countdown; the legacy date is only
		// a fallback for tickets raised before a promise was ever recorded.
		const sla_html = (() => {
			const c = d.countdown;
			if (c && c.promised && typeof c.seconds_left === "number") {
				const h = (c.seconds_left / 3600);
				const cls = c.state === "overdue" || c.state === "missed" ? "goh-sla-breach"
					: c.state === "due_soon" ? "goh-sla-warn"
					: c.state === "met" ? "goh-sla-ok" : (h < 24 ? "goh-sla-warn" : "goh-sla-ok");
				const label = c.state === "missed" ? __("Promise Missed")
					: c.state === "met" ? __("Delivered On Time")
					: c.state === "overdue" ? __("Overdue")
					: h < 24 ? __("Due Soon") : __("On Track");
				return `<span class="goh-sla-pill ${cls}">${label} (${Math.abs(h).toFixed(1)}h)</span>`;
			}
			if (!d.expected_completion_date) return "";
			const exp = frappe.datetime.str_to_obj(d.expected_completion_date);
			const diffH = ((exp - new Date()) / 3600000).toFixed(1);
			const cls = diffH < 0 ? "goh-sla-breach" : diffH < 24 ? "goh-sla-warn" : "goh-sla-ok";
			const label = diffH < 0 ? __("SLA Breached") : diffH < 24 ? __("Due Soon") : __("On Track");
			return `<span class="goh-sla-pill ${cls}" title="${__("No exact promise recorded — measured to end of day.")}">${label} (${Math.abs(diffH)}h)</span>`;
		})();

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
						<button class="btn btn-xs btn-danger goh-not-repairable-btn" title="${__("Mark Not Repairable")}" style="margin-right:4px;">
							<i class="fa fa-ban"></i> ${__("Not Repairable")}
						</button>
						<button class="btn btn-xs btn-default goh-print-label" data-sr="${esc(d.name)}"
							title="${__("Print the barcode label for the device")}">
							<i class="fa fa-barcode"></i> ${__("Label")}
						</button>
						<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="btn btn-xs btn-default" title="${__("Open Full SR")}">
							<i class="fa fa-external-link"></i>
						</a>
						${d.service_order ? `<a href="/app/sales-order/${encodeURIComponent(d.service_order)}" target="_blank" class="btn btn-xs btn-default" title="${__("Service Order")}"><i class="fa fa-file-text-o"></i></a>` : ""}
						${d.service_invoice ? `<button class="btn btn-xs btn-default goh-print-invoice" data-invoice="${esc(d.service_invoice)}" title="${__("Print Invoice")}"><i class="fa fa-print"></i></button>` : ""}
					</div>
				</div>
				${["Rejected", "Cancelled", "Withdrawn", "Expired"].includes(d.decision) ? `
					<div class="goh-rejection-banner" style="margin:8px 0 0;padding:8px 10px;border-left:3px solid var(--red-500,#e24c4c);background:var(--red-50,#fff5f5);border-radius:3px;">
						<b style="color:var(--red-600,#c0392b)">${__("{0}", [d.decision])}</b>
						${d.rejection_reason
							? `<div style="margin-top:3px;white-space:pre-wrap">${esc(d.rejection_reason)}</div>`
							: `<div style="margin-top:3px" class="text-muted">${__("No reason recorded.")}</div>`}
					</div>` : ""}
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
			["Category", d.device_category],
			["Brand", d.device_brand || d.brand],
			["Model", d.device_model],
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
			["Accessories", (d.accessories_list || []).join(", ") || d.accessories_received],
			["Coupon", d.coupon_code],
		].filter(r => r[1]);

		// Held apart from the grid above: it is the customer's own credential,
		// only here so the repair can actually be tested, and it should read as
		// something handled with care rather than another catalogue attribute.
		const lock = d.device_unlock_type
			? `<div class="goh-section">
					<div class="goh-section-title"><i class="fa fa-lock"></i> ${__("Screen Lock")}</div>
					<div class="goh-kv-grid">
						<div class="goh-kv"><span class="goh-kv-label">${__("Lock Type")}</span><span class="goh-kv-value">${esc(d.device_unlock_type)}</span></div>
						${d.device_unlock_code ? `<div class="goh-kv"><span class="goh-kv-label">${__("Unlock Code")}</span><span class="goh-kv-value" style="font-family:monospace;font-weight:700">${esc(d.device_unlock_code)}</span></div>` : ""}
					</div>
					<div class="text-muted" style="font-size:11px;margin-top:6px">${__("Given by the customer so the repair can be tested. Do not share outside the job.")}</div>
				</div>`
			: "";

		const assignRows = (d.assignments || []).filter(a => a.assignment_status !== "Cancelled").map(a => `
			<tr>
				<td>${esc(a.engineer_display)}</td>
				<td><span class="goh-badge badge-muted">${esc(a.job_type)}</span></td>
				<td><span class="goh-badge ${a.assignment_status === "Completed" ? "badge-green" : a.assignment_status === "In Progress" ? "badge-blue" : "badge-muted"}">${esc(a.assignment_status)}</span></td>
				<td>${a.estimated_hours || "—"}</td>
				<td>${format_assignment_hours(a.actual_hours)}</td>
			</tr>
		`).join("");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-mobile"></i> ${__("Device Information")}</div>
				<div class="goh-kv-grid">
					${rows.map(r => `<div class="goh-kv"><span class="goh-kv-label">${__(r[0])}</span><span class="goh-kv-value">${esc(r[1])}</span></div>`).join("")}
				</div>
			</div>

			${lock}

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
		const log = (d.status_log || []).filter(e => e.to_status);

		if (!log.length) {
			return `<div class="goh-section"><p class="text-muted">${__("No status changes recorded yet")}</p></div>`;
		}

		const fmtHours = (h) => {
			const v = parseFloat(h) || 0;
			if (!v) return "—";
			if (v < 1) return `${Math.round(v * 60)}m`;
			if (v < 24) return `${v.toFixed(1)}h`;
			return `${(v / 24).toFixed(1)}d`;
		};

		/* ── Two tracks, never one ─────────────────────────────────────
		   A ticket has a document lifecycle (Draft -> Accepted -> Completed)
		   and a shop-floor stage (Analysis -> Repair -> QC). They run in
		   parallel over the same hours, so summing them together counted
		   every hour twice. Each track is totalled on its own, the way SAP
		   keeps system status and user status apart. */
		const tracks = {};
		log.forEach(e => {
			const t = e.track || __("Lifecycle");
			(tracks[t] = tracks[t] || []).push(e);
		});

		const trackPanel = (name, entries) => {
			const perStage = {};
			let total = 0;
			entries.forEach(e => {
				const h = parseFloat(e.hours_in_prev) || 0;
				const stage = e.from_status || __("Intake");
				if (!perStage[stage]) perStage[stage] = { hours: 0, visits: 0 };
				perStage[stage].hours += h;
				perStage[stage].visits += 1;
				total += h;
			});
			const rows = Object.entries(perStage)
				.sort((a, b) => b[1].hours - a[1].hours)
				.map(([stage, v]) => {
					const share = total ? (v.hours / total) * 100 : 0;
					return `
						<tr>
							<td>${esc(stage)}</td>
							<td class="text-right goh-num">${fmtHours(v.hours)}</td>
							<td class="text-right goh-num">${v.visits > 1 ? v.visits + "&times;" : "1&times;"}</td>
							<td style="width:34%">
								<div class="goh-bar"><span style="width:${share.toFixed(1)}%"></span></div>
							</td>
							<td class="text-right goh-num">${share.toFixed(0)}%</td>
						</tr>`;
				}).join("");

			const last = entries[entries.length - 1];
			return `
				<div class="goh-section">
					<div class="goh-section-title">
						<i class="fa fa-hourglass-half"></i>
						${__("Where the time went")} — <span class="goh-track-name">${esc(name)}</span>
					</div>
					<div class="goh-tl-stats">
						<div><span class="k">${__("Transitions")}</span><span class="v">${entries.length}</span></div>
						<div><span class="k">${__("Stages touched")}</span><span class="v">${Object.keys(perStage).length}</span></div>
						<div><span class="k">${__("Elapsed to last move")}</span><span class="v">${fmtHours(total)}</span></div>
						<div><span class="k">${__("Currently in")}</span><span class="v">${esc(last.to_status || "—")}</span></div>
					</div>
					<div class="goh-tl-scroll">
						<table class="goh-tl-table">
							<thead>
								<tr>
									<th>${__("Stage")}</th>
									<th class="text-right">${__("Time in stage")}</th>
									<th class="text-right">${__("Visits")}</th>
									<th>${__("Share of total")}</th>
									<th class="text-right">%</th>
								</tr>
							</thead>
							<tbody>${rows}</tbody>
						</table>
					</div>
					<p class="text-muted" style="font-size:11px;margin:6px 2px 0">
						${__("Measured from intake. Hours are charged to the stage being left, which is where they were spent.")}
					</p>
				</div>`;
		};

		/* Shop floor first — it is the track anybody working the hub is
		   actually asking about. */
		const order = [__("Operations"), __("Lifecycle")];
		const panels = Object.keys(tracks)
			.sort((a, b) => order.indexOf(a) - order.indexOf(b))
			.map(name => trackPanel(name, tracks[name]))
			.join("");

		/* ── One chronological log, in the order things happened ──────── */
		const seenStage = {};
		const rows = log.map((e, i) => {
			const when = e.changed_at ? frappe.datetime.str_to_user(e.changed_at) : "—";
			const to = e.to_status || "—";
			const key = `${e.track}|${to}`;
			seenStage[key] = (seenStage[key] || 0) + 1;
			const repeat = seenStage[key] > 1
				? ` <span class="goh-repeat" title="${__("Ticket returned to this stage")}">${__("revisit")} ${seenStage[key]}</span>`
				: "";
			const isOps = (e.track || "") === "Operations";
			const inferred = e.inferred
				? ` <span class="goh-inferred" title="${__("No move was logged for this step. It is reconstructed from the stage the ticket had actually reached, so the waiting time is charged where it was spent.")}">${__("reconstructed")}</span>`
				: "";
			return `
				<tr>
					<td class="goh-num text-muted">${i + 1}</td>
					<td><span class="goh-track goh-track-${isOps ? "ops" : "life"}">${esc(e.track || "—")}</span></td>
					<td class="text-muted">${esc(e.from_status || __("Intake"))}</td>
					<td><b>${esc(to)}</b>${repeat}${inferred}</td>
					<td class="text-right goh-num">${fmtHours(e.hours_in_prev)}</td>
					<td>${esc(e.changed_by_name || e.changed_by || "—")}</td>
					<td class="text-muted">${when}</td>
				</tr>`;
		}).join("");

		return `
			${panels}

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-clock-o"></i> ${__("Status Timeline")}</div>
				<p class="text-muted" style="font-size:11px;margin:0 2px 8px">
					${__("Both tracks, in the order they happened. The two run in parallel over the same hours — read each one down its own column, not across.")}
				</p>
				<div class="goh-tl-scroll">
					<table class="goh-tl-table">
						<thead>
							<tr>
								<th style="width:2.5rem">#</th>
								<th>${__("Track")}</th>
								<th>${__("From")}</th>
								<th>${__("To")}</th>
								<th class="text-right">${__("Time in previous")}</th>
								<th>${__("By")}</th>
								<th>${__("When")}</th>
							</tr>
						</thead>
						<tbody>${rows}</tbody>
					</table>
				</div>
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
	/**
	 * Who the ticket is pending with during Analysis, and for how long.
	 *
	 * Analysis / Solutions / Confirm are real technician work, but the first
	 * Job Assignment used to be created only at the Assign stage — so nobody
	 * could say whose desk a ticket was sitting on, and those hours never
	 * reached costing or technician performance.
	 */
	_bind_diagnosis_custody(d) {
		const self = this;
		this._init_link_field("#goh-diag-tech-field", "Employee", __("Search technician..."), {
			query: "gofix.gofix_services.api.technician_query",
			sr_name: d.name,
		}, "_diag_tech");

		this.parent.find("#goh-diag-assign").on("click", () => {
			const tech = this._diag_tech && this._diag_tech.get_value();
			if (!tech) return frappe.show_alert({ message: __("Select a technician."), indicator: "orange" });
			frappe.xcall(`${API}.assign_diagnosis_technician`, { sr_name: d.name, technician: tech })
				.then((r) => {
					frappe.show_alert({
						message: r.reassigned
							? __("Handed over to {0}.", [r.technician_name || tech])
							: __("Assigned to {0}. Time is now being recorded.", [r.technician_name || tech]),
						indicator: "green",
					});
					self._refresh_all();
				});
		});

		this.parent.find("#goh-diag-release").on("click", () => {
			frappe.xcall(`${API}.release_diagnosis_technician`, { sr_name: d.name })
				.then(() => {
					frappe.show_alert({ message: __("Clock stopped."), indicator: "blue" });
					self._refresh_all();
				});
		});

		this._start_diagnosis_timer();
	}

	/**
	 * Tick the elapsed-time label from the SERVER clock.
	 *
	 * The offset between server and browser is measured once on render, so a
	 * wrong workstation clock cannot inflate or hide a technician's time. Only
	 * the label re-renders each second — no request per tick.
	 */
	_start_diagnosis_timer() {
		clearInterval(this._diag_timer);
		const $el = this.parent.find(".goh-diag-elapsed");
		if (!$el.length) return;

		const start = frappe.datetime.str_to_obj($el.data("start"));
		const serverNow = frappe.datetime.str_to_obj($el.data("server-now"));
		if (!start || !serverNow) return;
		const skew = serverNow.getTime() - Date.now();
		const banked = parseFloat($el.data("banked")) || 0;

		const tick = () => {
			const live = (Date.now() + skew) - start.getTime();
			if (live < 0) return;
			let secs = Math.floor(live / 1000) + Math.round(banked * 3600);
            const h = Math.floor(secs / 3600);
            const m = Math.floor((secs % 3600) / 60);
            const s = secs % 60;
			const pad = (n) => String(n).padStart(2, "0");
			// Text, not colour alone.
			$el.text(__("held {0}", [`${pad(h)}:${pad(m)}:${pad(s)}`]));
		};
		tick();
		this._diag_timer = setInterval(tick, 1000);
	}

	/**
	 * Cumulative technician time on the ticket, shown on every working stage.
	 *
	 * Analysis and Repair are separate Job Assignments, so each screen used to
	 * show only its own clock: Repair opened at 00:00:00 even when the same
	 * technician had already spent an hour diagnosing the same device, and the
	 * Analysis figure vanished once the ticket moved on. This is the one number
	 * that answers "how long has this ticket actually taken", which is what a
	 * promise to a customer is measured against.
	 */
	/**
	 * Time remaining against the promise given to the customer.
	 *
	 * Rendered on every working stage, because the deadline does not belong to
	 * one step — it is what the whole ticket is measured against, and a
	 * technician picking up a job at Repair needs to know how much of the
	 * customer's time is already gone.
	 *
	 * Ticks from the SERVER clock: the offset is measured once here, so a
	 * workstation with a wrong clock cannot make a late job look on time.
	 */
	_html_countdown(d) {
		const c = d.countdown || {};
		const esc = frappe.utils.escape_html;
		if (c.state === "unset") {
			return `<span class="goh-badge badge-muted" title="${esc(c.message || "")}">
				<i class="fa fa-hourglass-o"></i> ${__("No promise set")}</span>`;
		}
		const cls = { overdue: "badge-red", missed: "badge-red", due_soon: "badge-orange",
			on_track: "badge-green", met: "badge-green" }[c.state] || "badge-muted";
		const label = { met: __("Delivered on time"), missed: __("Promise missed") }[c.state];
		return `<span class="goh-badge ${cls} goh-countdown"
				data-promised="${esc(c.promised || "")}"
				data-server-now="${esc(c.server_now || "")}"
				data-stopped="${c.stopped ? 1 : 0}"
				title="${__("Promised {0}", [esc(c.promised || "")])}">
				<i class="fa fa-clock-o"></i> <span class="goh-countdown-text">${
					label || __("calculating…")}</span></span>${
			c.revision_count ? `<span class="text-muted small ml-1" title="${
				__("The promise has been moved {0} time(s)", [c.revision_count])}">
				(${__("revised {0}×", [c.revision_count])})</span>` : ""}`;
	}

	/** One interval for every countdown on screen, driven by the server offset. */
	_start_countdown_timer() {
		clearInterval(this._countdown_timer);
		const $els = this.parent.find(".goh-countdown");
		if (!$els.length) return;

		const first = $els.first();
		const serverNow = frappe.datetime.str_to_obj(first.data("server-now"));
		if (!serverNow) return;
		const skew = serverNow.getTime() - Date.now();

		const tick = () => {
			$els.each(function () {
				const $el = $(this);
				if (String($el.data("stopped")) === "1") return;   // frozen: job is over
				const target = frappe.datetime.str_to_obj($el.data("promised"));
				if (!target) return;
				let secs = Math.round((target.getTime() - (Date.now() + skew)) / 1000);
                const overdue = secs < 0;
                secs = Math.abs(secs);
				const d2 = Math.floor(secs / 86400);
				const h = Math.floor((secs % 86400) / 3600);
				const m = Math.floor((secs % 3600) / 60);
				const sec = secs % 60;
				const pad = (n) => String(n).padStart(2, "0");
				const clock = (d2 ? `${d2}d ` : "") + `${pad(h)}:${pad(m)}:${pad(sec)}`;
				// Text carries the meaning, not colour alone.
				$el.find(".goh-countdown-text").text(
					overdue ? __("{0} OVERDUE", [clock]) : __("{0} left", [clock]));
				$el.toggleClass("badge-red", overdue).toggleClass("badge-green", !overdue);
			});
		};
		tick();
		this._countdown_timer = setInterval(tick, 1000);
	}

	/** The ticket-age clock — counts UP from when the job was raised. */
	_start_age_timer() {
		clearInterval(this._age_timer);
		const $el = this.parent.find(".goh-age");
		if (!$el.length || String($el.data("running")) !== "1") return;
		const opened = frappe.datetime.str_to_obj($el.data("opened"));
		const serverNow = frappe.datetime.str_to_obj($el.data("server-now"));
		if (!opened || !serverNow) return;
		const skew = serverNow.getTime() - Date.now();
		const tick = () => {
			const mins = Math.max(0, Math.floor(((Date.now() + skew) - opened.getTime()) / 60000));
			const d2 = Math.floor(mins / 1440);
			const h = Math.floor((mins % 1440) / 60);
			$el.text((d2 ? `${d2}d ` : "") + `${h}h ${String(mins % 60).padStart(2, "0")}m`);
		};
		tick();
		this._age_timer = setInterval(tick, 30000);   // minutes granularity
	}

	/**
	 * Condition evidence: what the device looked like coming in, and going out.
	 *
	 * Shown on every stage rather than only at billing, because the intake
	 * photos are the thing a technician needs to SEE before they touch the
	 * device. The stage of a NEW photo follows the ticket: before repair is
	 * finished it is more intake evidence, at QC and billing it is the outgoing
	 * record the customer is charged against.
	 */
	/**
	 * What happened before the ticket was closed.
	 *
	 * A closed ticket used to render one line — "Ticket is Rejected. No further
	 * action needed." — and threw away everything the payload already carries:
	 * which faults were found, what was tried, by whom, how long it took, and
	 * what it cost in parts. That is exactly the record someone needs when the
	 * customer asks why, or when the same device comes back. Nothing here is
	 * editable; the ticket is closed.
	 */
	_html_closed_history(d) {
		const esc = frappe.utils.escape_html;
		const hm = (h) => {
			const m = Math.max(0, Math.round((h || 0) * 60));
			return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
		};

		// How far the work actually got, from the recorded stage moves.
		const moves = (d.status_log || []).filter(r => r.event_type === "Operations Stage");
		const reached = moves.length ? esc(moves[moves.length - 1].to_status) : __("not started");

		const issues = (d.issue_lines || []).filter(i => i.issue_category);
		const sols = d.solution_lines || [];
		const spares = d.spare_lines || [];
		const t = d.time_summary || {};

		const row = (cells) => `<tr>${cells.map(c => `<td style="padding:4px 8px">${c}</td>`).join("")}</tr>`;
		const table = (head, rows) => rows.length ? `
			<table class="table table-sm" style="margin:6px 0 0">
				<thead><tr>${head.map(h => `<th style="padding:4px 8px;font-size:11px;text-transform:uppercase;color:var(--text-muted)">${h}</th>`).join("")}</tr></thead>
				<tbody>${rows.join("")}</tbody>
			</table>` : `<div class="text-muted small" style="margin-top:4px">${__("None recorded")}</div>`;

		return `
			<div class="goh-section p-3">
				<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
					<b>${__("Ticket is")} ${esc(d.decision)}</b>
					${d.rejection_reason ? `<span class="goh-badge badge-red">${esc(d.rejection_reason)}</span>` : ""}
					<span class="text-muted small">${__("Reached")}: <b>${reached}</b></span>
					<span class="text-muted small">${__("Worked")}: <b>${hm(t.total_hours)}</b></span>
				</div>
				<div class="text-muted small" style="margin-bottom:10px">
					${__("Closed — kept as the record of what was attempted.")}
				</div>

				${this._html_device_photos(d)}

				<b class="small">${__("Faults identified")}</b>
				${table([__("Issue"), __("Reported by"), __("Status")],
					issues.map(i => row([esc(i.issue_category), esc(i.reported_by || "—"),
						`<span class="goh-badge badge-muted">${esc(i.status || "")}</span>`])))}

				<b class="small" style="display:block;margin-top:12px">${__("Work attempted")}</b>
				${table([__("Solution"), __("Technician"), __("Status")],
					sols.map(s => row([
						`<span title="${esc(s.repair_solution || "")}">${esc(s.solution_name || s.repair_solution || "")}</span>`,
						esc(s.technician_name || "—"),
						`<span class="goh-badge badge-muted">${esc(s.status || "")}</span>`])))}

				<b class="small" style="display:block;margin-top:12px">${__("Parts consumed")}</b>
				${table([__("Spare"), __("Qty"), __("Status")],
					spares.map(s => row([esc(s.spare_item_name || s.spare_item || ""),
						esc(String(s.qty || "")),
						`<span class="goh-badge badge-muted">${esc(s.status || "")}</span>`])))}

				${(t.by_technician || []).length ? `
					<b class="small" style="display:block;margin-top:12px">${__("Time by technician")}</b>
					<div style="margin-top:4px">${(t.by_technician || []).map(x =>
						`<span class="goh-badge badge-muted ml-1">${esc(x.technician_name)} ${hm(x.hours)}</span>`).join("")}</div>` : ""}
			</div>`;
	}

	_html_device_photos(d) {
		const p = d.device_photos || { intake: [], outtake: [] };
		const stage = ["qc", "invoice", "done"].includes(d.ops_stage) ? "Outtake" : "Intake";
		const esc = frappe.utils.escape_html;
		const thumb = (row) => `
			<div style="position:relative;width:64px;height:64px;border-radius:6px;overflow:hidden;border:1px solid var(--border-color)"
				title="${esc(row.stage)} · ${esc(row.captured_at || "")}${row.remarks ? " · " + esc(row.remarks) : ""}">
				<a href="${esc(row.photo)}" target="_blank" rel="noopener">
					<img src="${esc(row.photo)}" style="width:100%;height:100%;object-fit:cover">
				</a>
				<button class="goh-photo-drop" data-row="${esc(row.name)}" title="${__("Remove")}"
					style="position:absolute;top:1px;right:1px;padding:0 4px;background:rgba(0,0,0,0.6);color:#fff;border:0;border-radius:3px;font-size:11px">&times;</button>
			</div>`;
		const group = (label, rows) => `
			<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
				<b class="text-muted small" style="min-width:60px">${label}</b>
				${rows.length ? rows.map(thumb).join("")
					: `<span class="text-muted small">${__("None taken")}</span>`}
			</div>`;

		return `
			<div class="goh-section goh-photos" style="margin-bottom:10px">
				<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
					<b><i class="fa fa-camera"></i> ${__("Device Photos")}</b>
					<button class="btn btn-xs btn-default goh-photo-add" data-stage="${stage}"
						style="margin-left:auto">
						<i class="fa fa-plus"></i> ${__("Add {0} Photo", [__(stage)])}
					</button>
					<input type="file" class="goh-photo-input" accept="image/*" capture="environment"
						multiple style="display:none">
				</div>
				${group(__("In"), p.intake || [])}
				<div style="height:6px"></div>
				${group(__("Out"), p.outtake || [])}
			</div>`;
	}

	_html_time_on_ticket(d) {
		const esc = frappe.utils.escape_html;
		const t = d.time_summary || {};
		const hm = (h) => {
			const mins = Math.max(0, Math.round((h || 0) * 60));
			return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
		};
		const hasTime = t.by_technician && t.by_technician.length;
		const hasPromise = (d.countdown || {}).state && d.countdown.state !== "unset";
		// A ticket with no time logged still has a deadline to show.
		if (!hasTime && !hasPromise) return "";

		const stages = Object.entries(t.by_stage || {})
			.filter(([, h]) => h > 0)
			.map(([stage, h]) => `<span class="goh-badge badge-muted ml-1">${esc(stage)} ${hm(h)}</span>`)
			.join("");
		const techs = (t.by_technician || [])
			.map(x => `<span class="ml-2">${esc(x.technician_name)} <b>${hm(x.hours)}</b></span>`)
			.join(" · ");

		const run = t.running;
		const runningBit = run
			? `<span class="goh-badge badge-blue ml-2"><i class="fa fa-play"></i> ${esc(run.technician_name)} — ${esc(run.job_type || "")}</span>`
			// "clock stopped" sat beside the promise countdown and read as though
			// the DEADLINE had stopped. Name which clock and why: technician time
			// only accrues while a job is open, and analysis hours are banked
			// when the analysis is confirmed.
			: `<span class="text-muted small ml-2"
					title="${__("Technician time only runs while a job is open. Analysis hours are banked when Confirm Analysis is pressed; repair time starts when the work is assigned.")}">${
					__("nobody working now")}</span>`;

		// Text, not colour alone.
		const stale = run && run.stale
			? `<div style="margin-top:4px;color:var(--red-600,#c0392b);font-size:11px">
					<i class="fa fa-exclamation-triangle"></i>
					${__("Running for {0} without a pause — check whether this job was left open.",
						[hm(run.elapsed_hours)])}
				</div>`
			: "";

		return `
			<div class="goh-section" style="padding:8px 12px;margin-bottom:10px;
					border-left:3px solid var(--purple-500,#7c5cff);background:var(--bg-light-gray,#f7f9fc)">
				<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px">
					${/* Two clocks, side by side and never merged: how long the
					     customer has waited, and how much work has gone in. */ ""}
					<span title="${__("Wall-clock since the ticket was raised")}">
						<b>${__("Open")}</b>
						<b style="font-size:14px" class="goh-age"
							data-opened="${esc((d.ticket_age || {}).opened || "")}"
							data-server-now="${esc((d.ticket_age || {}).server_now || "")}"
							data-running="${(d.ticket_age || {}).running ? 1 : 0}">${
								hm((d.ticket_age || {}).hours)}</b>
					</span>
					<span class="text-muted">|</span>
					<span title="${__("Hands-on technician time, every stage and technician")}">
						<b>${__("Worked")}</b>
						<b style="font-size:14px">${hm(t.total_hours)}</b>
					</span>
					${stages}
					${this._html_countdown(d)}
					${runningBit}
					<div style="flex:1"></div>
					<span class="text-muted small">${techs}</span>
				</div>
				${stale}
			</div>`;
	}

	_html_diagnosis_custody(d) {
		const esc = frappe.utils.escape_html;
		const a = d.diagnosis_assignment || {};
		const held = a.assigned
			? `<span class="goh-badge badge-blue"><i class="fa fa-user"></i> ${esc(a.technician_name || a.technician)}</span>
			   <span class="goh-diag-elapsed text-muted small ml-2"
			         data-start="${esc(a.start_datetime || "")}"
			         data-server-now="${esc(a.server_now || "")}"
			         data-banked="${a.actual_hours || 0}"></span>`
			: (() => {
				// "Unassigned" read as "nobody was ever assigned", directly under a
				// bar showing that a named technician had already put 18 minutes
				// into this ticket from the POS counter. Distinguish never-assigned
				// from finished-and-handed-back, or the counter believes the POS
				// assignment failed to map.
				const done = (((d.time_summary || {}).by_technician) || [])
					.filter(x => x.stages && x.stages.includes("Diagnosis"));
				if (done.length) {
					// State the fact, not the absence. Once diagnosis is done, who
					// did it is the useful information; "nobody working on it now"
					// re-reported the same thing the bar above already shows and
					// read like a problem. Every technician who diagnosed is named,
					// so a fault found later by someone else is credited too.
					const who = done.map(x => esc(x.technician_name)).join(", ");
					return `<span class="text-muted small">${
						__("Diagnosed by {0}", [who])}</span>`;
				}
				return `<span class="text-muted small">${
					__("Not yet assigned — nobody has worked on this ticket.")}</span>`;
			})();

		return `
			<div class="goh-section" style="padding:8px 12px;margin-bottom:10px;
					border-left:3px solid var(--blue-500,#4a8cf7);background:var(--bg-light-gray,#f7f9fc)">
				<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
					<b style="font-size:12px">${__("Pending with")}</b>
					${held}
					<div style="flex:1"></div>
					<div id="goh-diag-tech-field" style="min-width:210px"></div>
					<button class="btn btn-xs btn-primary" id="goh-diag-assign">
						<i class="fa fa-user-plus"></i> ${a.assigned ? __("Reassign") : __("Assign")}
					</button>
					${a.assigned ? `<button class="btn btn-xs btn-default" id="goh-diag-release"
						title="${__("Stop the clock without handing over")}"><i class="fa fa-pause"></i></button>` : ""}
				</div>
			</div>`;
	}

	_html_analysis(d) {
		const esc = frappe.utils.escape_html;

		const activeIssues = (d.issue_lines || []).filter(r => r.status !== "Deleted");
		const deletedIssues = (d.issue_lines || []).filter(r => r.status === "Deleted");

		// Analysis lists ISSUES; the Solutions step lists SOLUTIONS. They are
		// different counts by nature — one issue can carry several solutions and
		// some carry none — which read as work going missing ("I added 4, this
		// shows 3"). Showing each issue's solutions here makes the two views
		// tell the same story.
		const SOL_BADGE = {
			Planned: "badge-muted", "In Progress": "badge-blue", "On Hold": "badge-orange",
			Completed: "badge-green", Skipped: "badge-yellow", Cancelled: "badge-red",
		};
		const solsByCategory = {};
		for (const s of (d.solution_lines || [])) {
			(solsByCategory[s.issue_category] = solsByCategory[s.issue_category] || []).push(s);
		}
		const solutionCell = (cat) => {
			const list = solsByCategory[cat] || [];
			if (!list.length) {
				return `<span class="text-muted small">${__("None assigned")}</span>`;
			}
			return list.map(s => `<span class="goh-badge ${SOL_BADGE[s.status] || "badge-muted"} mr-1"
				title="${esc(s.repair_solution)} · ${esc(s.status)}${s.technician_name ? " · " + esc(s.technician_name) : ""}">${esc(s.solution_name || s.repair_solution)}</span>`).join(" ");
		};

		const issueRows = activeIssues.map((row, i) => `
			<tr data-name="${esc(row.name)}" data-idx="${i}">
				<td><select class="form-control input-xs goh-issue-cat" data-selected="${esc(row.issue_category)}"><option value="">${__("Issue Category")}</option></select></td>
				<td>
					<select class="form-control input-xs goh-issue-reporter">
						<option value="Technician" ${row.reported_by === "Technician" ? "selected" : ""}>${__("Technician")}</option>
						<option value="Customer" ${row.reported_by === "Customer" ? "selected" : ""}>${__("Customer")}</option>
					</select>
				</td>
				<td><input class="form-control input-xs goh-issue-desc" value="${esc(row.description)}" placeholder="${__("Description")}"></td>
				<td>${solutionCell(row.issue_category)}</td>
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
			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-search"></i> ${__("Technical Analysis")}
					<span class="text-muted small ml-2">${__("Identify all issues with the device")}</span>
				</div>

				${this._html_time_on_ticket(d)}
				${this._html_device_photos(d)}
				${this._html_diagnosis_custody(d)}

				${d.issue_description ? `
					<div class="goh-complaint-block">
						<div class="goh-note-label"><i class="fa fa-comment"></i> ${__("Customer Complaint")}</div>
						<div class="goh-note-text">${d.issue_description}</div>
					</div>
				` : ""}

				<table class="goh-table" id="goh-issue-table">
					<thead>
						<tr><th>${__("Issue Category")}</th><th style="width:130px">${__("Reported By")}</th><th>${__("Description")}</th><th style="width:200px">${__("Solutions")}</th><th style="width:80px">${__("Status")}</th><th style="width:40px"></th></tr>
					</thead>
					<tbody id="goh-issue-tbody">
						${issueRows || `<tr><td colspan="6" class="text-muted text-center">${__("No issues added yet. Click + to add.")}</td></tr>`}
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

				<div id="goh-est-breakdown" class="goh-est-breakdown">
					<span class="text-muted">${__("Pricing the chosen repairs…")}</span>
				</div>

				<div class="goh-confirm-cost" style="display:flex;align-items:center;gap:10px">
					<span class="goh-kv-label">${__("Estimated Cost")}</span>
					<span style="font-size:18px;font-weight:600">₹</span>
					<input type="number" class="form-control" id="goh-est-cost" value="${flt(d.estimated_cost)}" min="0" step="100" style="max-width:180px;font-size:18px;font-weight:700">
					<button class="btn btn-xs btn-default" id="goh-save-est-cost" style="white-space:nowrap"><i class="fa fa-save"></i> ${__("Save")}</button>
					<button class="btn btn-xs btn-primary" id="goh-use-calc" style="white-space:nowrap;display:none"><i class="fa fa-calculator"></i> ${__("Use calculated")}</button>
				</div>

				<!-- Coupons are produced at the counter, but which repairs the job
				     needs is only known after analysis -- so a coupon meant for a
				     display replacement can only be aimed at one here. -->
				<div class="goh-confirm-coupon" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px">
					<span class="goh-kv-label"><i class="fa fa-ticket"></i> ${__("Coupon")}</span>
					<input type="text" class="form-control" id="goh-coupon-code" style="max-width:190px"
						value="${frappe.utils.escape_html(d.coupon_code || "")}" placeholder="${__("Coupon code")}">
					<select class="form-control" id="goh-coupon-scope" style="max-width:190px">
						<option value="Entire Invoice"${d.coupon_scope === "Entire Invoice" ? " selected" : ""}>${__("Entire Invoice")}</option>
						<option value="Specific Repair"${d.coupon_scope === "Specific Repair" ? " selected" : ""}>${__("Specific Repair")}</option>
					</select>
					<select class="form-control" id="goh-coupon-solution" style="max-width:230px;display:${d.coupon_scope === "Specific Repair" ? "" : "none"}"></select>
					<button class="btn btn-xs btn-default" id="goh-apply-coupon"><i class="fa fa-check"></i> ${__("Apply")}</button>
					${flt(d.coupon_discount_amount) ? `<span class="goh-badge badge-green">− ₹${format_number(d.coupon_discount_amount)}</span>` : ""}
				</div>

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
				<span class="goh-badge badge-green" title="${esc(s.repair_solution)}">${esc(s.solution_name || s.repair_solution)}</span>
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
		// Match the server's definition of assignable: a Skipped row is not
		// active work, and offering it pre-checked only sets the operator up
		// for "Invalid Solution Selection" on submit. Skipped rows are listed
		// separately below with a pointer to Restart on the Repair step.
		const all = (d.solution_lines || []).filter(s => s.status !== "Cancelled");
		const skipped = all.filter(s => s.status === "Skipped");
		const sols = all.filter(s => s.status !== "Skipped");
		const assigned = sols.filter(s => s.technician);
		const unassigned = sols.filter(s => !s.technician);
		const allDone = sols.length > 0 && unassigned.length === 0;

		// Group assigned by technician
		const techMap = {};
		assigned.forEach(s => {
			const key = s.technician;
			if (!techMap[key]) techMap[key] = { name: s.technician_name || s.technician, solutions: [] };
			techMap[key].solutions.push(s);
		});

		const assignedHtml = Object.entries(techMap).map(([tech, info]) => `
			<div class="goh-tech-group" style="background:var(--fg-color);border:1px solid var(--border-color);border-radius:8px;padding:10px 14px;margin-bottom:8px">
				<div style="font-weight:600;font-size:13px;margin-bottom:6px">
					<i class="fa fa-user-check text-success" style="margin-right:4px"></i>${esc(info.name)}
					<span class="goh-badge badge-green" style="margin-left:6px">${info.solutions.length} solution${info.solutions.length > 1 ? "s" : ""}</span>
				</div>
				${info.solutions.map(s => `
					<div style="display:flex;align-items:center;gap:8px;padding:3px 0 3px 20px;font-size:12px">
						<span class="goh-badge badge-muted" style="font-size:11px">${esc(s.issue_category || "")}</span>
						<span style="font-weight:500" title="${esc(s.repair_solution)}">${esc(s.solution_name || s.repair_solution)}</span>
						<span class="text-muted">${s.estimated_minutes || 0}min</span>
						${s.requires_spare ? '<span class="goh-badge badge-orange" style="font-size:10px">Spare</span>' : ""}
						<button class="btn btn-xs btn-link text-danger goh-unassign-sol" data-row="${esc(s.name)}" style="padding:0;margin-left:auto;font-size:11px"><i class="fa fa-times"></i></button>
					</div>
				`).join("")}
			</div>
		`).join("");

		// Group unassigned by issue category
		const issueMap = {};
		unassigned.forEach(s => {
			const cat = s.issue_category || __("General");
			if (!issueMap[cat]) issueMap[cat] = [];
			issueMap[cat].push(s);
		});

		const unassignedHtml = Object.entries(issueMap).map(([issue, items]) => `
			<div style="margin-bottom:8px">
				<div style="font-weight:600;font-size:12px;color:var(--text-muted);margin-bottom:4px">
					<i class="fa fa-tag" style="margin-right:4px"></i>${esc(issue)}
				</div>
				${items.map(s => `
					<label style="display:flex;align-items:center;gap:8px;padding:4px 8px 4px 20px;cursor:pointer;border-radius:4px;margin:0" class="goh-sol-assign-row" onmouseover="this.style.background='var(--bg-light-gray)'" onmouseout="this.style.background=''">
						<input type="checkbox" class="goh-assign-check" data-row="${esc(s.name)}" checked>
						<span style="font-weight:500;font-size:13px" title="${esc(s.repair_solution)}">${esc(s.solution_name || s.repair_solution)}</span>
						<span class="text-muted" style="font-size:12px">${s.estimated_minutes || 0}min</span>
						${s.requires_spare ? '<span class="goh-badge badge-orange" style="font-size:10px">Spare</span>' : ""}
					</label>
				`).join("")}
			</div>
		`).join("");

		const skippedHtml = skipped.length ? `
			<div class="text-muted" style="font-size:12px;margin:6px 0 0 4px">
				<i class="fa fa-forward"></i> ${__("Skipped (not assignable)")}:
				${skipped.map(s => esc(s.solution_name || s.repair_solution)).join(", ")}
				— ${__("use Restart on the Repair step to bring one back")}
			</div>` : "";

		const progressPct = sols.length ? Math.round((assigned.length / sols.length) * 100) : 0;

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-user-plus"></i> ${__("Assign Technicians to Solutions")}</div>

				<!-- Progress bar -->
				<div style="margin-bottom:12px">
					<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
						<span>${assigned.length}/${sols.length} ${__("solutions assigned")}</span>
						<span>${progressPct}%</span>
					</div>
					<div style="height:6px;background:var(--border-color);border-radius:3px;overflow:hidden">
						<div style="height:100%;width:${progressPct}%;background:var(--green-500);border-radius:3px;transition:width 0.3s"></div>
					</div>
				</div>

				${(() => {
					// The per-technician badge reads "3 solutions" while a fourth
					// sits unassigned below, so a technician who saved four counts
					// three and thinks one was dropped. State the total once.
					const live = (d.solution_lines || []).filter(s => s.status !== "Cancelled");
					return live.length ? `<div class="text-muted small" style="margin-bottom:10px">
						${__("{0} solutions on this ticket", [live.length])}
					</div>` : "";
				})()}

				${assigned.length ? `
					<div style="margin-bottom:14px">
						<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;color:var(--green-600);margin-bottom:6px">
							<i class="fa fa-check-circle"></i> ${__("Assigned")} (${(d.solution_lines || []).filter(s => s.status !== "Cancelled" && s.technician).length})
						</div>
						${assignedHtml}
					</div>
				` : ""}

				${unassigned.length ? `
					<div style="background:var(--bg-color);border:1px solid var(--border-color);border-radius:8px;padding:12px 14px;margin-bottom:14px">
						<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;color:var(--orange-600);margin-bottom:8px">
							<i class="fa fa-clock-o"></i> ${__("Unassigned")} (${unassigned.length})
							<span style="font-weight:400;font-size:11px;color:var(--text-muted);margin-left:8px">${__("Select solutions & assign a technician")}</span>
						</div>
						${unassignedHtml}
				${skippedHtml}
						<div style="display:flex;gap:10px;align-items:flex-end;margin-top:12px;padding-top:10px;border-top:1px solid var(--border-color)">
							<div style="flex:2">
								<label class="goh-field-label" style="font-size:11px">${__("Technician")}</label>
								<div id="goh-tech-field"></div>
							</div>
							<div style="flex:0 0 90px">
								<label class="goh-field-label" style="font-size:11px">${__("Est. Hours")}</label>
								<input class="form-control input-sm" id="goh-est-hours" type="number" value="2" min="0.5" step="0.5">
							</div>
							<div style="flex:0 0 auto">
								<button class="btn btn-sm btn-primary" id="goh-do-assign"><i class="fa fa-check"></i> ${__("Assign")}</button>
							</div>
						</div>
					</div>
				` : ""}

				<div class="goh-section-actions" style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
					<button class="btn btn-xs btn-default" id="goh-back-to-solutions"><i class="fa fa-arrow-left"></i> ${__("Back to Solutions")}</button>
					${allDone ? `<button class="btn btn-sm btn-success" id="goh-proceed-repair"><i class="fa fa-arrow-right"></i> ${__("Proceed to Repair")}</button>` : ""}
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 5 — Repair Execution                                            */
	/* ═══════════════════════════════════════════════════════════════════════ */
	/* ── Device transfer control ─────────────────────────────────────────
	   Shown wherever the device is being worked. A repair that turns out to be
	   beyond this bench should not have to go back to the counter to be moved,
	   and a device that has been sent away has to be able to come home. The two
	   directions are deliberately different actions: sending needs a
	   destination and a reason, returning has neither — it goes back to the
	   store that sent it. */
	_transfer_button(d) {
		const away = d.transfer_status && d.transfer_status !== "Returned";
		if (away) {
			const at = d.transferred_to_store ? String(d.transferred_to_store).split(" - ")[0] : "";
			return `
				<span class="goh-badge badge-orange" title="${__("Device is away from its origin store")}">
					<i class="fa fa-truck"></i> ${frappe.utils.escape_html(d.transfer_status)}${at ? " · " + frappe.utils.escape_html(at) : ""}
				</span>
				${d.transfer_status === "Received at Service Center" ? `
					<button class="btn btn-xs btn-default" id="goh-return-store"
						title="${__("Send the device back to the store that raised the ticket")}">
						<i class="fa fa-undo"></i> ${__("Return to Store")}
					</button>` : ""}
				${d.transfer_status === "In Transit" ? `
					<button class="btn btn-xs btn-default" id="goh-cancel-transfer"
						title="${__("Call the dispatch back — only while the device has not been picked up")}">
						<i class="fa fa-times"></i> ${__("Cancel Dispatch")}
					</button>` : ""}`;
		}
		return `
			<button class="btn btn-xs btn-default" id="goh-send-hub"
				title="${__("This bench cannot finish the repair — send the device to another location")}">
				<i class="fa fa-truck"></i> ${__("Transfer for Repair")}
			</button>`;
	}

	_html_repair(d) {
		const esc = frappe.utils.escape_html;
		const sols = d.solution_lines || [];
		const activeSols = sols.filter(s => s.status !== "Cancelled");
		const cancelledSols = sols.filter(s => s.status === "Cancelled");
		const allDone = activeSols.length > 0 && activeSols.every(s => s.status === "Completed" || s.status === "Skipped");
		const doneCount = activeSols.filter(s => s.status === "Completed" || s.status === "Skipped").length;

		// Rework detection
		const isRework = (d.rework_count || 0) > 0;
		const reworkIteration = d.rework_count || 0;

		// Build QC fail reason index: solution → fail reasons
		const qcFailMap = {};
		if (isRework && d.qc_checklist) {
			for (const check of d.qc_checklist) {
				if (check.result === "Fail") {
					const key = check.linked_solution || "__general__";
					if (!qcFailMap[key]) qcFailMap[key] = [];
					qcFailMap[key].push({
						check: check.check_name,
						reason: check.fail_reason || check.remarks || "",
					});
				}
			}
		}

		const STATUS_CLS = { Planned: "badge-muted", "In Progress": "badge-blue", "On Hold": "badge-orange", Completed: "badge-green", Skipped: "badge-yellow", Cancelled: "badge-red" };

		// ERP-standard compact rows: active work first, done work collapsed,
		// one emphasised action per row, everything else quiet icons.
		const ROW_STATE = { "In Progress": "active", "On Hold": "hold", Planned: "queued", Completed: "done", Skipped: "done" };
		const solRow = (sol) => {
			const isReworkItem = sol.technician_remarks && sol.technician_remarks.includes("[Rework]");
			const failReasons = isReworkItem ? (qcFailMap[sol.repair_solution] || qcFailMap["__general__"] || []) : [];
			const state = ROW_STATE[sol.status] || "queued";
			const unassigned = !sol.technician && !["Completed", "Skipped", "Cancelled"].includes(sol.status);
			const lastRemark = (sol.technician_remarks || "").trim().split("\n").filter(Boolean).pop() || "";

			const primary =
				sol.status === "In Progress" ? `<button class="btn btn-xs btn-success goh-sol-complete" data-row="${esc(sol.name)}"><i class="fa fa-check"></i> ${__("Done")}</button>` :
				sol.status === "On Hold" && !unassigned ? `<button class="btn btn-xs btn-primary goh-sol-start" data-row="${esc(sol.name)}"><i class="fa fa-play"></i> ${__("Resume")}</button>` :
				sol.status === "Planned" && !unassigned ? `<button class="btn btn-xs btn-primary goh-sol-start" data-row="${esc(sol.name)}"><i class="fa fa-play"></i> ${__("Start")}</button>` :
				(sol.status === "Completed" || sol.status === "Skipped") ? `<button class="btn btn-xs btn-default goh-sol-restart" data-row="${esc(sol.name)}"><i class="fa fa-undo"></i> ${__("Restart")}</button>` : "";

			const quiet = ["Completed", "Skipped"].includes(sol.status) ? "" : `
				${sol.status === "In Progress" ? `<button class="btn btn-xs btn-default goh-sol-hold" data-row="${esc(sol.name)}" title="${__("Hold — release the device (e.g. waiting for parts)")}"><i class="fa fa-pause"></i></button>` : ""}
				${!unassigned && ["Planned", "In Progress", "On Hold"].includes(sol.status) ? `<button class="btn btn-xs btn-default goh-sol-reassign" data-row="${esc(sol.name)}" data-solution="${esc(sol.repair_solution || "")}" title="${__("Hand off this solution to another technician")}"><i class="fa fa-exchange"></i></button>` : ""}
				<button class="btn btn-xs btn-default goh-sol-skip" data-row="${esc(sol.name)}" title="${__("Skip this solution")}"><i class="fa fa-step-forward"></i></button>
				<button class="btn btn-xs btn-default goh-sol-cancel" data-row="${esc(sol.name)}" title="${__("Cancel this solution")}"><i class="fa fa-times text-danger"></i></button>`;

			return `
			<div class="goh-sol-row goh-sol-row--${state} ${isReworkItem ? "goh-sol-row--rework" : ""}" data-row="${esc(sol.name)}">
				<span class="goh-sol-dot goh-sol-dot--${state}"></span>
				<div class="goh-sol-main">
					<div class="goh-sol-line1">
						<span class="goh-sol-name" title="${esc(sol.repair_solution || "")}">${esc(sol.solution_name || sol.repair_solution || "—")}</span>
						<span class="goh-badge ${STATUS_CLS[sol.status] || "badge-muted"}">${__(sol.status)}</span>
						${isReworkItem ? `<span class="goh-badge badge-orange" title="${__("This item failed QC and needs rework")}"><i class="fa fa-refresh"></i> ${__("Rework")}</span>` : ""}
					</div>
					<div class="goh-sol-meta text-muted">
						${sol.issue_category ? `<span><i class="fa fa-tag"></i> ${esc(sol.issue_category)}</span>` : ""}
						${sol.estimated_minutes ? `<span><i class="fa fa-clock-o"></i> ${sol.estimated_minutes}min</span>` : ""}
						${sol.technician_name ? `<span><i class="fa fa-user"></i> ${esc(sol.technician_name)}</span>` : ""}
						${unassigned ? `<span class="indicator-pill orange">${__("Unassigned — assign in the Assign step")}</span>` : ""}
						${lastRemark ? `<span class="goh-sol-remark" title="${esc(sol.technician_remarks)}"><i class="fa fa-comment-o"></i> ${esc(lastRemark)}</span>` : ""}
					</div>
					${failReasons.length ? `
					<div class="goh-qc-fail-context small text-danger">
						<strong><i class="fa fa-exclamation-triangle"></i> ${__("QC Failed")}:</strong>
						${failReasons.map(f => `<span class="ml-1">• ${esc(f.check)}${f.reason ? ": " + esc(f.reason) : ""}</span>`).join("")}
					</div>` : ""}
				</div>
				<div class="goh-sol-actions">${primary}${quiet}</div>
			</div>`;
		};

		const activeRows = activeSols.filter(s => ["In Progress", "On Hold"].includes(s.status)).map(solRow).join("");
		const queuedRows = activeSols.filter(s => s.status === "Planned").map(solRow).join("");
		const doneSols = activeSols.filter(s => ["Completed", "Skipped"].includes(s.status));
		const doneRows = doneSols.map(solRow).join("");

		const solList = `
			${activeRows ? `<div class="goh-sol-group-label">${__("In Progress")}</div>${activeRows}` : ""}
			${queuedRows ? `<div class="goh-sol-group-label">${__("Queued")}</div>${queuedRows}` : ""}
			${doneRows ? `
				<details class="goh-sol-done-details" ${activeRows || queuedRows ? "" : "open"}>
					<summary class="goh-sol-group-label" style="cursor:pointer">
						<span class="goh-caret"></span><i class="fa fa-check-circle text-success"></i> ${__("Done")} (${doneSols.length})
					</summary>
					${doneRows}
				</details>` : ""}
		`;

		const cancelledCards = cancelledSols.length ? `
			<details class="goh-sol-done-details">
				<summary class="goh-sol-group-label" style="cursor:pointer"><span class="goh-caret"></span><i class="fa fa-ban"></i> ${__("Cancelled")} (${cancelledSols.length})</summary>
				${cancelledSols.map(sol => `
					<div class="goh-sol-row goh-sol-row--cancelled">
						<span class="goh-sol-dot"></span>
						<div class="goh-sol-main">
							<div class="goh-sol-line1"><span class="goh-sol-name" style="text-decoration:line-through" title="${esc(sol.repair_solution || "")}">${esc(sol.solution_name || sol.repair_solution || "—")}</span></div>
							<div class="goh-sol-meta text-danger"><i class="fa fa-comment"></i> ${esc(sol.cancel_reason || sol.technician_remarks || __("No reason provided"))}</div>
						</div>
					</div>
				`).join("")}
			</details>
		` : "";

		// Spare parts — separate active and damaged
		const activeSpares = (d.spare_lines || []).filter(sp => sp.status !== "Damaged");
		const damagedSpares = (d.spare_lines || []).filter(sp => sp.status === "Damaged");
		const awaitingCount = (d.spare_lines || []).filter(sp => ["Awaiting Procurement", "In Transit"].includes(sp.status)).length;

		const spare_badge = (st) => {
			const map = {
				"Reserved": "badge-blue", "Consumed": "badge-green", "Issued": "badge-blue",
				"Awaiting Procurement": "badge-orange", "In Transit": "badge-blue", "Pending": "badge-muted", "Returned": "badge-grey",
			};
			return `<span class="goh-badge ${map[st] || "badge-muted"}">${__(st)}</span>`;
		};

		const spareRows = activeSpares.map(sp => {
			const removable = ["Reserved", "Awaiting Procurement", "In Transit", "Pending"].includes(sp.status);
			const actionBtn = removable
				? `<button class="btn btn-xs btn-outline-secondary goh-spare-remove" data-row="${esc(sp.name)}" title="${__("Remove")}"><i class="fa fa-times"></i></button>`
				: `<button class="btn btn-xs btn-outline-danger goh-spare-damage" data-row="${esc(sp.name)}" title="${__("Mark Damaged")}"><i class="fa fa-exclamation-triangle"></i></button>`;
			const arrived = ["Reserved", "Issued", "Pending"].includes(sp.status);
			const needsGenealogy = (sp.status === "Consumed" && (!sp.removed_part_serial || !sp.removed_part_condition || !sp.installed_part_serial)) || arrived;
			const genBtn = sp.status === "Consumed" || arrived || sp.status === "Awaiting Procurement"
				? `<button class="btn btn-xs ${needsGenealogy ? "btn-warning" : "btn-outline-secondary"} goh-spare-genealogy"
					data-row="${esc(sp.name)}" data-serial="${esc(sp.removed_part_serial || "")}" data-mode="${arrived ? "install" : "edit"}"
					data-installed="${esc(sp.installed_part_serial || "")}" data-condition="${esc(sp.removed_part_condition || "")}"
					title="${arrived ? __("Install part — record old + new serials") : __("Part serial details (required before close)")}"><i class="fa fa-pencil"></i></button>`
				: "";
			// A part that has not landed is the usual reason a ticket stalls, so
			// say where it is and when it is due rather than just "awaiting".
			const waiting = ["Awaiting Procurement", "In Transit"].includes(sp.status);
			const eta = sp.expected_date ? frappe.datetime.str_to_user(sp.expected_date) : "";
            const overdue = sp.expected_date && sp.expected_date < frappe.datetime.get_today();
			const etaPill = waiting
				? ` <span class="indicator-pill ${overdue ? "red" : "blue"}" style="font-size:10px">${
					sp.status === "In Transit"
						? (eta ? __("in transit — due {0}", [eta]) : __("in transit"))
						: (eta ? __("not ordered yet — needed by {0}", [eta]) : __("not ordered yet"))
				}${overdue ? " · " + __("overdue") : ""}</span>`
				: "";
			const pill = arrived
				? ` <span class="indicator-pill blue" style="font-size:10px">${__("arrived — install & record new serial")}</span>`
				: (needsGenealogy ? ` <span class="indicator-pill orange" style="font-size:10px">${__("serial details missing")}</span>` : etaPill);
			return `
			<tr data-spare-row="${esc(sp.name)}">
				<td>${esc(sp.item_name || sp.spare_item)}${pill}</td>
				<td class="text-center">${sp.qty} ${esc(sp.uom || "")}</td>
				<td class="text-right">₹${format_number(sp.rate)}</td>
				<td class="text-center">${spare_badge(sp.status)}</td>
				<td class="text-center">${genBtn} ${actionBtn}</td>
			</tr>`;
		}).join("");

		const damagedRows = damagedSpares.map(sp => `
			<tr style="opacity:0.6; text-decoration:line-through">
				<td>${esc(sp.item_name || sp.spare_item)}</td>
				<td class="text-center">${sp.qty} ${esc(sp.uom || "")}</td>
				<td class="text-right">₹${format_number(sp.rate)}</td>
				<td class="text-center"><span class="goh-badge badge-red">${__("Damaged")}</span></td>
				<td></td>
			</tr>
			<tr style="opacity:0.6"><td colspan="5" class="text-danger small"><i class="fa fa-comment"></i> ${esc(sp.remarks || "")}</td></tr>
		`).join("");

		// Technician info — the current device holder glows; others queue
		const activeAssigns = (d.assignments || []).filter(a => a.assignment_status !== "Cancelled");

		// Hands-on hours per technician, from the custody log — the spells when
		// they actually had the device, not the elapsed age of the ticket. A
		// still-open spell counts up to now so a job in hand shows live time.
		const heldHours = {};
		(d.custody_log || []).forEach(c => {
			const who = c.technician || c.technician_name;
			if (!who) return;
			// an open spell has no stored hours yet — count it up to now
			const hrs = c.released_at
				? flt(c.hours)
				: (c.taken_at ? Math.max(0, moment().diff(moment(c.taken_at), "minutes") / 60) : 0);
			heldHours[who] = (heldHours[who] || 0) + hrs;
		});

		const techInfo = activeAssigns.map(a => {
			const holds = a.assignment_status === "In Progress";
			const hrs = heldHours[a.service_engineer] || flt(a.actual_hours);
			const hoursBadge = hrs
				? ` <span class="goh-badge badge-muted" title="${__("Hands-on time recorded against this technician")}">${hrs.toFixed(1)}h</span>`
				: "";

			// Assigned but not taken on yet: the clock is NOT running, and the
			// chip says so rather than looking like work in progress. The wait
			// counts up so a job sitting unaccepted is visible at a glance.
			if (a.awaiting_accept) {
				const waited = flt(a.waiting_hours);
				const waitBadge = waited >= 0.02
					? ` <span class="goh-badge badge-orange" title="${__("Waiting for this technician to accept")}">${waited.toFixed(1)}h ${__("waiting")}</span>`
					: "";
				return `
				<span class="goh-assign-chip-sm" style="background:var(--orange-100,#ffedd5);border:1.5px dashed var(--orange-600,#ea580c)">
					<i class="fa fa-hourglass-half" style="color:var(--orange-600,#ea580c)"></i>
					${esc(a.engineer_display)}
					<span class="goh-badge badge-orange">${__("pending accept")}</span>
					${waitBadge}
					${a.can_accept ? `<button class="btn btn-xs btn-primary goh-accept-ja" data-ja="${esc(a.name)}" title="${__("Take this job on — the clock starts now")}"><i class="fa fa-check"></i> ${__("Accept")}</button>` : ""}
				</span>`;
			}

			const acceptedBadge = flt(a.accept_wait_hours) >= 0.02
				? ` <span class="goh-badge badge-muted" title="${__("Time between assignment and the technician accepting")}">${__("accepted after")} ${flt(a.accept_wait_hours).toFixed(1)}h</span>`
				: "";
			return `
			<span class="goh-assign-chip-sm" style="${holds ? "background:var(--green-100,#dcfce7);border:1.5px solid var(--green-600,#16a34a);font-weight:600" : ""}">
				<i class="fa ${holds ? "fa-mobile" : "fa-user"}" ${holds ? 'style="color:var(--green-600,#16a34a)"' : ""}></i>
				${esc(a.engineer_display)}
				${holds ? `<span class="goh-badge badge-green">${__("has device")}</span>` : `<span class="goh-badge badge-muted">${esc(a.assignment_status)}</span>`}
				${acceptedBadge}
				${hoursBadge}
			</span>`;
		}).join("");

		const totalHeld = Object.values(heldHours).reduce((a, b) => a + b, 0)
			|| activeAssigns.reduce((sum, a) => sum + flt(a.actual_hours), 0);

		const custodyRows = (d.custody_log || []).slice(0, 6).map(c => `
			<div class="small" style="padding:1px 0">
				<i class="fa fa-mobile text-muted"></i> <b>${esc(c.technician_name || c.technician)}</b>
				${frappe.datetime.str_to_user(c.taken_at)} → ${c.released_at ? frappe.datetime.str_to_user(c.released_at) : `<span class="text-success">${__("holding now")}</span>`}
				${c.released_at ? `<span class="text-muted">(${(c.hours || 0).toFixed(1)}h)</span>` : ""}
				${c.note ? `<span class="text-muted">— ${esc(c.note)}</span>` : ""}
			</div>`).join("");

		return `
			${this._html_time_on_ticket(d)}
			${this._html_device_photos(d)}
			<div class="goh-section">
				<div class="goh-section-title" style="display:flex;align-items:center;gap:8px">
					<span><i class="fa fa-users"></i> ${__("Technicians")}</span>
					${/* The ticket total now lives in the Time-on-ticket bar above, which
					     is server-computed across every stage and is not capped at the
					     15 custody rows this figure was derived from. */ ""}
					<span style="flex:1"></span>
					${activeAssigns.length ? `<button class="btn btn-xs btn-default" id="goh-device-handover" title="${__("Move the physical device to another technician on this ticket (⇄ on a card moves the solution instead)")}"><i class="fa fa-mobile"></i> ${__("Hand Over Device")}</button>` : ""}
					${this._transfer_button(d)}
				</div>
				<div class="goh-tech-chips">${techInfo || `<span class="text-muted">${__("None assigned")}</span>`}</div>
				${custodyRows ? `
				<details class="goh-custody-details">
					<summary class="small text-muted goh-sol-group-label" style="cursor:pointer;margin:0"><span class="goh-caret"></span>${__("Device custody history")} (${(d.custody_log || []).length})</summary>
					<div style="padding-top:4px">${custodyRows}</div>
				</details>` : ""}
			</div>

			${isRework ? `
			<div class="goh-section">
				<div class="goh-rework-banner">
					<div class="goh-rework-banner-title">
						<i class="fa fa-refresh"></i> <strong>${__("Rework Round")} #${reworkIteration}</strong>
					</div>
					<div class="small">${__("QC failed on previous repair. Only failed items need rework — completed items can be restarted if needed.")}</div>
				</div>
			</div>
			` : ""}

			<div class="goh-section">
				<div class="goh-section-title" style="display:flex;align-items:center;gap:10px">
					<span><i class="fa fa-wrench"></i> ${__("Repair Progress")}</span>
					<div class="goh-progressbar" title="${doneCount}/${activeSols.length} ${__("done")}">
						<div class="goh-progressbar-fill" style="width:${activeSols.length ? Math.round((doneCount / activeSols.length) * 100) : 0}%"></div>
					</div>
					<span class="text-muted small" style="font-weight:400">${doneCount}/${activeSols.length} ${__("done")}</span>
				</div>
				${allDone ? `<div class="goh-all-done-banner"><i class="fa fa-check-circle"></i> ${__("All solutions completed — submit for QC below.")}</div>` : ""}
				<div class="goh-sol-list">${solList.trim() || `<p class="text-muted">${__("No solutions assigned")}</p>`}</div>
				${cancelledCards}
			</div>

			<div class="goh-section">
				<div class="goh-section-title">
					<i class="fa fa-cogs"></i> ${__("Spare Parts")}
					<button class="btn btn-xs btn-default ml-2" id="goh-add-spare-btn"><i class="fa fa-plus"></i> ${__("Add")}</button>
					${awaitingCount ? `<button class="btn btn-xs btn-warning ml-2" id="goh-raise-mr-btn"><i class="fa fa-shopping-cart"></i> ${__("Raise MR")} (${awaitingCount})</button>` : ""}
				</div>
				${spareRows || damagedRows ? `
					<table class="goh-table">
						<thead><tr><th>${__("Part")}</th><th class="text-center">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-center">${__("Status")}</th><th class="text-center" style="width:50px"></th></tr></thead>
						<tbody>${spareRows}${damagedRows}</tbody>
					</table>
				` : `<p class="text-muted">${__("No spare parts added")}</p>`}
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
			<span class="goh-badge ${s.status === "Completed" ? "badge-green" : "badge-yellow"}" title="${esc(s.repair_solution)}">${esc(s.solution_name || s.repair_solution)}</span>
		`).join(" ");

		// Group checks by the solution they verify (OEM-style: each repair is
		// QC'd on its own, then a final whole-device inspection).
		const checkRow = row => `
			<tr class="goh-qc-row" data-name="${esc(row.name)}">
				<td>${esc(row.check_name)}</td>
				<td>
					<select class="form-control input-xs goh-qc-result" data-name="${esc(row.name)}" data-check="${esc(row.check_name)}">
						<option value="">${__("—")}</option>
						<option value="Pass" ${row.result === "Pass" ? "selected" : ""}>${__("Pass")}</option>
						<option value="Fail" ${row.result === "Fail" ? "selected" : ""}>${__("Fail")}</option>
						<option value="NA" ${row.result === "NA" ? "selected" : ""}>${__("N/A")}</option>
					</select>
				</td>
				<td><input class="form-control input-xs goh-qc-remarks" data-name="${esc(row.name)}" data-check="${esc(row.check_name)}" value="${esc(row.remarks || "")}" placeholder="${__("Remarks")}"></td>
			</tr>`;
		const qcGroups = {};
		checklist.forEach(row => {
			const key = row.linked_solution || "";
			(qcGroups[key] = qcGroups[key] || []).push(row);
		});
		const groupHeader = label => `
			<tr class="goh-qc-group-row"><td colspan="3" style="background:var(--goh-bg);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;color:var(--goh-muted);padding:6px 10px">
				<i class="fa fa-wrench"></i> ${esc(label)}
			</td></tr>`;
		const checkRows = Object.keys(qcGroups).filter(k => k).map(sol =>
			groupHeader(sol) + qcGroups[sol].map(checkRow).join("")
		).join("") + (qcGroups[""] ? groupHeader(__("Final Inspection — whole device")) + qcGroups[""].map(checkRow).join("") : "");

		return `
			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-wrench"></i> ${__("Completed Solutions")}</div>
				<div class="goh-sol-chips">${solChips || `<span class="text-muted">${__("None")}</span>`}</div>
			</div>

			<div class="goh-section">
				<div class="goh-section-title" style="display:flex;align-items:center;gap:8px">
					<span><i class="fa fa-check-square-o"></i> ${__("QC Checklist")}</span>
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
				${d.service_invoice ? `<button class="btn btn-sm btn-default ml-2 goh-print-invoice" data-invoice="${frappe.utils.escape_html(d.service_invoice)}"><i class="fa fa-print"></i> ${__("Print Invoice")}</button>` : ""}
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  STEP 7b — Rework (QC Fail)                                           */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_html_rework(d) {
		const esc = frappe.utils.escape_html;
		const failed = (d.qc_checklist || []).filter(c => c.result === "Fail");
		const passed = (d.qc_checklist || []).filter(c => c.result === "Pass");
		const allChecks = (d.qc_checklist || []).map(row => `
			<tr>
				<td>${esc(row.check_name)}</td>
				<td><span class="goh-badge ${row.result === "Pass" ? "badge-green" : row.result === "Fail" ? "badge-red" : "badge-muted"}">${esc(row.result || "—")}</span></td>
				<td class="text-muted small">${esc(row.remarks || "")}</td>
				<td class="text-muted small">${esc(row.fail_reason || "")}</td>
			</tr>
		`).join("");

		const failItems = failed.map(r => `
			<div class="goh-fail-item"><i class="fa fa-times-circle text-danger"></i> ${esc(r.check_name)} ${r.fail_reason ? `— <span class="text-muted">${esc(r.fail_reason)}</span>` : ""} ${r.linked_solution ? `<small class="text-primary">[${esc(r.linked_solution)}]</small>` : ""}</div>
		`).join("");

		// Show which solutions will be reworked vs kept
		const solutions = d.solution_lines || [];
		const failedSolutions = new Set(failed.filter(c => c.linked_solution).map(c => c.linked_solution));
		const solRows = solutions.map(s => {
			const willRework = failedSolutions.has(s.repair_solution) || (failedSolutions.size === 0 && s.status === "Completed");
			return `<tr>
				<td title="${esc(s.repair_solution || "")}">${esc(s.solution_name || s.repair_solution || "")}</td>
				<td>${esc(s.issue_category || "")}</td>
				<td><span class="goh-badge ${willRework ? "badge-red" : s.status === "Completed" ? "badge-green" : "badge-muted"}">${willRework ? __("Will Rework") : esc(s.status)}</span></td>
				<td>${esc(s.technician_name || "")}</td>
			</tr>`;
		}).join("");

		return `
			<div class="goh-section goh-rework-alert">
				<div class="goh-section-title"><i class="fa fa-exclamation-triangle text-danger"></i> ${__("QC Failed — Rework Only Failed Items")}</div>
				<p class="text-muted small">${__("Only the failed solutions will go back to repair. Passed items are kept intact.")}</p>
				${failItems ? `<div class="goh-fail-list">${failItems}</div>` : `<p class="text-muted">${__("QC result: Fail")}</p>`}
				${passed.length ? `<div class="mt-2"><small class="text-success"><i class="fa fa-check-circle"></i> ${passed.length} ${__("check(s) passed — these will not be affected")}</small></div>` : ""}
			</div>

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-bolt"></i> ${__("Solution Impact")}</div>
				<table class="goh-table">
					<thead><tr><th>${__("Solution")}</th><th>${__("Issue")}</th><th>${__("Status")}</th><th>${__("Technician")}</th></tr></thead>
					<tbody>${solRows || `<tr><td colspan="4" class="text-muted text-center">${__("No solutions")}</td></tr>`}</tbody>
				</table>
			</div>

			<div class="goh-section">
				<div class="goh-section-title"><i class="fa fa-list"></i> ${__("Full QC Checklist")}</div>
				<table class="goh-table">
					<thead><tr><th>${__("Check")}</th><th>${__("Result")}</th><th>${__("Remarks")}</th><th>${__("Fail Reason")}</th></tr></thead>
					<tbody>${allChecks || `<tr><td colspan="4" class="text-muted text-center">${__("No checklist")}</td></tr>`}</tbody>
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
		const hasInvoice = !!d.service_invoice;
		return `
			<div class="goh-done-state">
				<i class="fa fa-check-circle fa-3x" style="color:#10b981"></i>
				<h4 class="mt-2">${__("Repair Complete")}</h4>
				<p class="text-muted">${__("Status")}: <b>${esc(d.decision)}</b></p>
				${hasInvoice ? `
					<div class="goh-section mt-3" id="goh-done-invoice-summary">
						<div class="text-center p-2"><i class="fa fa-spinner fa-spin"></i> ${__("Loading invoice…")}</div>
					</div>
				` : ""}
				<div class="mt-3">
					${hasInvoice
						? `<a href="/app/sales-invoice/${encodeURIComponent(d.service_invoice)}" target="_blank" class="btn btn-primary btn-sm"><i class="fa fa-file-text-o"></i> ${__("View Invoice")} — ${esc(d.service_invoice)}</a>`
						: ""}
					${hasInvoice ? `<button class="btn btn-default btn-sm ml-2 goh-print-invoice" data-invoice="${esc(d.service_invoice)}"><i class="fa fa-print"></i> ${__("Print Invoice")}</button>` : ""}
					<a href="/app/service-request/${encodeURIComponent(d.name)}" target="_blank" class="btn btn-default btn-sm ${hasInvoice ? 'ml-2' : ''}"><i class="fa fa-external-link"></i> ${__("View Service Request")}</a>
				</div>
			</div>
		`;
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Event Binding                                                        */
	/* ═══════════════════════════════════════════════════════════════════════ */
	/**
	 * Populate a past stage for viewing only.
	 *
	 * Reached only once a ticket is INVOICED or DONE. The data still has to
	 * load — a technician looking back at Analysis needs to see which
	 * categories were recorded — but nothing here may be edited, so every
	 * control is disabled rather than left live and unwired.
	 */
	_render_readonly_stage(d, stage) {
		const self = this;
		const content = this.parent.find("#goh-tab-work");

		const lock = () => {
			content.find("select, input, textarea").prop("disabled", true);
			content.find("button").not("#goh-back-to-current").prop("disabled", true)
				.attr("title", __("This step is closed — the ticket has been invoiced."));
			if (!content.find(".goh-readonly-note").length) {
				content.find(".goh-section").first().prepend(
					`<div class="goh-readonly-note text-muted small" style="margin-bottom:6px">
						<i class="fa fa-lock"></i> ${__("Read-only — this step is complete.")}
					</div>`);
			}
		};

		if (stage === "analysis") {
			frappe.xcall(`${API}.get_issue_categories`).then((cats) => {
				self._issue_categories = cats || [];
				self._fill_issue_category_selects();
			}).catch(() => {}).then(lock);
			return;
		}

		if (stage === "solutions") {
			// async — lock only once the picker has actually rendered, or the
			// controls would be re-created live after being disabled.
			self._load_solutions_for_categories(d).then(lock, lock);
			return;
		}

		lock();
	}

	_bind_step_events(d) {
		const content = this.parent.find("#goh-tab-work");
		const self = this;

		/* Determine which stage's events to bind */
		const activeStage = d._view_stage || d.ops_stage;
		const isViewing = !!d._view_stage;

		// The promise clock belongs to the ticket, not to a stage, so it starts
		// before any stage-specific binding — including the read-only return
		// below, which a completed ticket always takes.
		this._start_countdown_timer();
		this._start_age_timer();

		/* ── Device photos: bound before the read-only return, because the
		   evidence strip stays viewable on a closed ticket. ───────────── */
		content.off("click.gohphoto").on("click.gohphoto", ".goh-photo-add", (e) => {
			const stage = $(e.currentTarget).data("stage");
			const input = content.find(".goh-photo-input");
			input.data("stage", stage).trigger("click");
		});
		content.on("change.gohphoto", ".goh-photo-input", (e) => {
			const stage = $(e.currentTarget).data("stage") || "Intake";
			const files = Array.from(e.currentTarget.files || []);
			e.currentTarget.value = "";
			if (!files.length) return;
			const uploads = files.map((file) => {
				const fd = new FormData();
				fd.append("file", file, file.name);
				fd.append("is_private", 1);
				fd.append("doctype", "Service Request");
				fd.append("docname", d.name);
				return fetch("/api/method/upload_file", {
					method: "POST",
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
					body: fd,
				}).then((r) => r.json()).then((r) => {
					const url = r && r.message && r.message.file_url;
					if (!url) throw new Error("upload failed");
					return frappe.xcall(`${API}.add_device_photo`, {
						sr_name: d.name, file_url: url, stage: stage,
					});
				});
			});
			Promise.allSettled(uploads).then((rs) => {
				const bad = rs.filter((r) => r.status === "rejected").length;
				if (bad) {
					frappe.msgprint({
						title: __("Photos Not Attached"),
						message: __("{0} of {1} photo(s) could not be attached.", [bad, rs.length]),
						indicator: "orange",
					});
				}
				self._refresh_all();
			});
		});
		content.on("click.gohphoto", ".goh-photo-drop", (e) => {
			e.preventDefault();
			const row = $(e.currentTarget).data("row");
			frappe.confirm(__("Remove this photo? The removal is recorded on the ticket."), () => {
				frappe.xcall(`${API}.remove_device_photo`, { sr_name: d.name, row_name: row })
					.then(() => self._refresh_all())
					.catch((err) => frappe.show_alert({
						message: err.message || __("Could not remove the photo"), indicator: "red" }));
			});
		});

		/* Once INVOICED or DONE, previous steps are read-only — changing the work
		   after the customer has been billed would desync the invoice.
		   A ticket sitting AT qc is NOT finished: QC can fail into rework, and a
		   technician who spots a further fault must be able to add a solution for
		   it. Locking qc here is what made "add a solution after reaching QC"
		   impossible, and moving back and forth between steps is routine.

		   Returning here without rendering anything left the panel LOOKING
		   editable but empty: the Issue Category selects are filled by an async
		   load that never ran, so every row showed only its placeholder, and the
		   Solutions picker sat on "Loading solutions..." forever. Show the real
		   values, then visibly lock the controls. */
		if (isViewing && ["invoice", "done"].includes(d.ops_stage)) {
			this._render_readonly_stage(d, activeStage);
			return;
		}

		/* ── Draft: accept & create the Service Order from the hub ───── */
		if (activeStage === "draft") {
			content.find("#goh-open-job").on("click", (e) => {
				const btn = $(e.currentTarget);
				btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Opening…")}`);
				frappe.xcall(`${API}.open_walkin_job`, { sr_name: d.name })
					.then(() => {
						frappe.show_alert({ message: __("Job opened — ticket is in Analysis."), indicator: "green" });
						self._refresh_all();
					})
					.catch((err) => {
						frappe.msgprint({ title: __("Could not open the job"), message: err.message || String(err), indicator: "red" });
						btn.prop("disabled", false).html(`<i class="fa fa-inbox"></i> ${__("Take In — start Analysis")}`);
					});
			});

			content.find("#goh-accept-create-so").on("click", (e) => {
				const btn = $(e.currentTarget);
				btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Accepting…")}`);
				frappe.xcall(`${API}.accept_and_create_service_order`, { sr_name: d.name })
					.then((r) => {
						frappe.show_alert({
							message: __("Accepted — Service Order {0} created.", [r.service_order]),
							indicator: "green",
						});
						self._refresh_all();
					})
					.catch((err) => {
						frappe.msgprint({ title: __("Could not accept"), message: err.message || String(err), indicator: "red" });
						btn.prop("disabled", false).html(`<i class="fa fa-check"></i> ${__("Accept & Create Service Order")}`);
					});
			});
		}

		/* ── Analysis ────────────────────────────────────────────────── */
		// Any stage change kills the analysis clock; re-armed below if we are
		// back on Analysis. Without this the interval survives navigation and
		// ticks against a detached element for the rest of the session.
		clearInterval(this._diag_timer);

		if (activeStage === "analysis") {
			// Load categories
			// A native <datalist> popup is drawn by the browser, not the page, so
			// it ignores the desk theme entirely and renders dark on a light page.
			// A <select> is browser-themed consistently, and Issue Category is a
			// closed list anyway — free text only invited typos.
			frappe.xcall(`${API}.get_issue_categories`).then(cats => {
				self._issue_categories = cats || [];
				self._fill_issue_category_selects();
			});

			content.find("#goh-add-issue").on("click", () => {
				const tbody = this.parent.find("#goh-issue-tbody");
				tbody.find("td[colspan]").closest("tr").remove();
				const idx = tbody.find("tr").length;
				tbody.append(`
					<tr data-idx="${idx}">
						<td><select class="form-control input-xs goh-issue-cat"><option value="">${__("Issue Category")}</option></select></td>
						<td><select class="form-control input-xs goh-issue-reporter"><option value="Technician">${__("Technician")}</option><option value="Customer">${__("Customer")}</option></select></td>
						<td><input class="form-control input-xs goh-issue-desc" placeholder="${__("Description")}"></td>
						<td><span class="text-muted small">${__("None assigned")}</span></td>
						<td></td>
						<td><button class="btn btn-xs btn-danger goh-issue-remove"><i class="fa fa-trash"></i></button></td>
					</tr>
				`);
				self._fill_issue_category_selects();
				tbody.find("tr:last .goh-issue-cat").focus();
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

			// Wired LAST, and isolated: the technician-custody widget is an
			// addition to this panel, not a prerequisite for it. Binding it
			// first meant any failure inside it aborted the rest of the block —
			// including the Issue Category load — leaving every category
			// dropdown showing nothing but its placeholder.
			try {
				this._bind_diagnosis_custody(d);
			} catch (e) {
				console.error("GoFix: diagnosis custody widget failed to bind", e);
				frappe.show_alert({
					message: __("Technician assignment is unavailable on this ticket."),
					indicator: "orange",
				});
			}

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
			// Price the ticket from the chosen repairs rather than leaving the
			// operator to guess. The typed field stays authoritative so a
			// negotiated price can still override the rate card.
			frappe.xcall(`${API}.get_estimate_breakdown`, { sr_name: d.name }).then(est => {
				// Kept so the Save handler knows what the rate card says without
				// re-pricing, and can tell a match from a deviation before calling.
				self._last_estimate_total = flt(est.total);
				self._last_estimate_lines = est.lines || [];
				const box = content.find("#goh-est-breakdown");
				if (!box.length) return;
				if (!est.priced) {
					box.html(`<span class="text-muted"><i class="fa fa-info-circle"></i> ${frappe.utils.escape_html(est.reason || "")}</span>`);
					return;
				}
				const rows = (est.lines || []).map(l => `
					<tr>
						<td title="${frappe.utils.escape_html(l.repair_solution || "")}">${frappe.utils.escape_html(l.solution_name || l.repair_solution || "")}</td>
						<td class="text-right">₹${format_number(l.labor || 0)}</td>
						<td class="text-right">${l.spare_item ? `₹${format_number(l.spare || 0)}` : "—"}</td>
						<td class="text-muted small">${l.spare_item ? frappe.utils.escape_html(`${l.spare_item} · ${l.spare_grade || ""}`) : ""}</td>
					</tr>`).join("");
				box.html(`
					<table class="goh-est-table">
						<thead><tr><th>${__("Repair")}</th><th class="text-right">${__("Labour")}</th><th class="text-right">${__("Part")}</th><th></th></tr></thead>
						<tbody>${rows}</tbody>
						<tfoot><tr>
							<th>${__("Calculated total")}</th>
							<th class="text-right">₹${format_number(est.labour)}</th>
							<th class="text-right">₹${format_number(est.parts)}</th>
							<th class="text-right">₹${format_number(est.total)}</th>
						</tr></tfoot>
					</table>`);
				const input = content.find("#goh-est-cost");
				if (!flt(input.val())) input.val(est.total);
				else if (flt(input.val()) !== flt(est.total)) content.find("#goh-use-calc").show();
				content.find("#goh-use-calc").off("click").on("click", () => input.val(est.total).trigger("change"));
			});

			content.find("#goh-send-wa").on("click", () => {
				frappe.xcall(`${API}.send_confirmation_whatsapp`, { sr_name: d.name })
					.then(r => {
						frappe.show_alert({ message: r.whatsapp_sent ? __("WhatsApp sent!") : __("WhatsApp not configured. Mark manually."), indicator: r.whatsapp_sent ? "green" : "orange" });
						self._load_detail(d.name);
					});
			});

			content.find("#goh-mark-confirmed").on("click", (e) => {
				const btn = $(e.currentTarget);
				btn.prop("disabled", true);
				frappe.xcall(`${API}.mark_customer_confirmed`, { sr_name: d.name })
					.then((r) => {
						frappe.show_alert({
							message: r && r.service_order
								? __("Customer confirmed — Service Order {0} raised.", [r.service_order])
								: __("Customer confirmed."),
							indicator: "green",
						});
						self._refresh_all();
					})
					.catch((err) => {
						frappe.msgprint({ title: __("Could not confirm"), message: err.message || String(err), indicator: "red" });
						btn.prop("disabled", false);
					});
			});

			content.find("#goh-back-to-analysis").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "analysis" })
					.then(() => self._refresh_all());
			});

			// Populate the repair picker from what was actually chosen, so a
			// coupon can only be pointed at work on this ticket.
			const $scope = content.find("#goh-coupon-scope");
			const $sol = content.find("#goh-coupon-solution");
			const fillSolutions = () => {
				const lines = (self._last_estimate_lines || []);
				$sol.html(lines.map((l) =>
					`<option value="${frappe.utils.escape_html(l.repair_solution || "")}"${
						d.coupon_solution === l.repair_solution ? " selected" : ""
					}>${frappe.utils.escape_html(l.solution_name || l.repair_solution || "")}</option>`
				).join(""));
			};
			fillSolutions();
			$scope.on("change", () => {
				const specific = $scope.val() === "Specific Repair";
				$sol.toggle(specific);
				if (specific) fillSolutions();
			});

			content.find("#goh-apply-coupon").on("click", () => {
				frappe.xcall(`${API}.set_service_coupon`, {
					sr_name: d.name,
					coupon_code: content.find("#goh-coupon-code").val().trim(),
					scope: $scope.val(),
					solution: $sol.val() || "",
				}).then((r) => {
					frappe.show_alert({
						message: r && r.discount
							? __("Coupon {0} applied — {1} off.", [r.coupon_code, format_currency(r.discount)])
							: __("Coupon cleared."),
						indicator: r && r.discount ? "green" : "blue",
					});
					self._refresh_all();
				});
			});

			content.find("#goh-save-est-cost").on("click", () => {
				const cost = parseFloat(content.find("#goh-est-cost").val()) || 0;
				const calculated = flt(self._last_estimate_total);
				// Matching the rate card needs no ceremony. Departing from it is
				// an exception, so the reason is collected here rather than
				// letting the server reject the click with nothing to send.
				const save = (reason) => frappe.xcall(`${API}.set_estimated_cost`, {
					sr_name: d.name, estimated_cost: cost, reason: reason || "",
				}).then((r) => {
					if (r && r.override && r.exception) {
						const approved = flt(r.estimated_cost) === cost;
						frappe.msgprint({
							title: approved ? __("Override Approved") : __("Sent for Approval"),
							indicator: approved ? "green" : "orange",
							message: approved
								? __("Approved under exception {0}. The customer is quoted {1}.",
									[r.exception, format_currency(r.estimated_cost)])
								: __("Exception {0} is awaiting approval. Until it is approved the estimate stays at the rate-card price {1} — quote that to the customer.",
									[r.exception, format_currency(r.calculated)]),
						});
					} else {
						frappe.show_alert({ message: __("Estimated cost updated."), indicator: "green" });
					}
					self._refresh_all();
				});

				if (Math.abs(cost - calculated) < 0.01) {
					save("");
					return;
				}
				frappe.prompt(
					[{
						fieldname: "reason", fieldtype: "Small Text", reqd: 1,
						label: __("Why is this different from the rate-card price of {0}?",
							[format_currency(calculated)]),
					}],
					(v) => save(v.reason),
					__("Price Change Needs Approval"),
					__("Send for Approval"),
				);
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
				query: "gofix.gofix_services.api.technician_query",
				sr_name: d.name,
			});

			content.find("#goh-back-to-solutions").on("click", () => {
				frappe.xcall(`${API}.go_back_to_stage`, { sr_name: d.name, target_stage: "solutions" })
					.then(() => self._refresh_all());
			});

			// Assign selected solutions to technician
			content.find("#goh-do-assign").on("click", () => {
				const tech = this._tech_field && this._tech_field.get_value();
				if (!tech) return frappe.show_alert({ message: __("Select a technician."), indicator: "orange" });

				const selectedRows = [];
				content.find(".goh-assign-check:checked").each(function () {
					selectedRows.push($(this).data("row"));
				});
				if (!selectedRows.length) return frappe.show_alert({ message: __("Select at least one solution."), indicator: "orange" });

				frappe.xcall(`${API}.assign_solutions_to_technician`, {
					sr_name: d.name,
					solution_rows_json: JSON.stringify(selectedRows),
					technician: tech,
					estimated_hours: parseFloat(content.find("#goh-est-hours").val() || 2),
				}).then((r) => {
					frappe.show_alert({ message: __("Technician assigned to {0} solution(s)!", [selectedRows.length]), indicator: "green" });
					self._refresh_all();
				});
			});

			// Remove assignment
			content.on("click", ".goh-unassign-sol", function (e) {
				e.preventDefault();
				const rowName = $(this).data("row");
				frappe.xcall(`${API}.unassign_solution`, { sr_name: d.name, solution_row_name: rowName })
					.then(() => { frappe.show_alert({ message: __("Assignment removed."), indicator: "blue" }); self._load_detail(d.name); });
			});

			// Proceed to repair (only when all assigned)
			content.find("#goh-proceed-repair").on("click", () => {
				frappe.xcall(`${API}.advance_to_repair`, { sr_name: d.name })
					.then(() => { frappe.show_alert({ message: __("Moving to Repair stage."), indicator: "green" }); self._refresh_all(); });
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

			content.on("click", ".goh-sol-hold", function () {
				const rowName = $(this).data("row");
				// Pick the coded reason from a dropdown rather than sending a
				// bare "On Hold" the server has to reject. Reasons are grouped
				// by type so the list reads at a glance, and the note field
				// appears only for a reason that needs one.
				frappe.xcall(`${API}.get_pause_reasons`).then(reasons => {
					reasons = reasons || [];
					const needsNote = {};
					const options = reasons.map(r => {
						needsNote[r.name] = !!r.requires_note;
						return { label: `${r.reason_name} — ${r.reason_type}`, value: r.name };
					});
					const dlg = new frappe.ui.Dialog({
						title: __("Put Solution On Hold"),
						fields: [
							{ fieldname: "pause_reason", label: __("Reason"), fieldtype: "Select",
								options: options, reqd: 1,
								description: __("The device is released — another technician can work their own solution meanwhile.") },
							{ fieldname: "remarks", label: __("Note"), fieldtype: "Small Text",
								depends_on: "eval:doc.pause_reason",
								description: __("What actually happened — required for some reasons.") },
						],
						primary_action_label: __("Hold"),
						primary_action: v => {
							if (!v.pause_reason) { frappe.msgprint(__("Choose a reason for the hold.")); return; }
							if (needsNote[v.pause_reason] && !(v.remarks || "").trim()) {
								frappe.msgprint(__("This reason needs a note saying what happened.")); return;
							}
							frappe.xcall(`${API}.update_solution_status`, {
								sr_name: d.name, solution_row_name: rowName, status: "On Hold",
								pause_reason: v.pause_reason, remarks: v.remarks || "",
							}).then(() => { dlg.hide(); self._load_detail(d.name); });
						},
					});
					// Surface the picked reason's own description under the field.
					dlg.fields_dict.pause_reason.$input.on("change", function () {
						const r = reasons.find(x => x.name === dlg.get_value("pause_reason"));
						dlg.fields_dict.pause_reason.set_description((r && r.description) || "");
					});
					dlg.show();
				});
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

			content.on("click", ".goh-sol-restart", function () {
				const rowName = $(this).data("row");
				const dlg = new frappe.ui.Dialog({
					title: __("Restart Solution"),
					fields: [{ fieldname: "remarks", label: __("Reason for restart"), fieldtype: "Small Text", description: __("Why does this need to be redone?") }],
					primary_action_label: __("Restart"),
					primary_action: v => {
						frappe.xcall(`${API}.restart_solution_line`, { sr_name: d.name, solution_row_name: rowName, remarks: v.remarks || "" })
							.then(() => { dlg.hide(); self._load_detail(d.name); });
					},
				});
				dlg.show();
			});

			// Removed-part genealogy (required before the ticket can close)
			content.on("click", ".goh-spare-genealogy", function () {
				const btn = $(this);
				const rowName = btn.data("row");
				const installMode = btn.data("mode") === "install";
				const dlg = new frappe.ui.Dialog({
					title: installMode ? __("Install Part — Record Serials") : __("Part Serial Details"),
					fields: [
						...(installMode ? [{ fieldname: "install_note", fieldtype: "HTML",
							options: `<div class="text-muted small" style="margin-bottom:8px">${__("The purchased spare has arrived. Recording the new serial installs it on this ticket (line becomes Consumed).")}</div>` }] : []),
						{ fieldname: "removed_part_serial", label: __("Removed Part Serial (old, KBB)"), fieldtype: "Data",
							reqd: 1, default: btn.data("serial") || "" },
						{ fieldname: "removed_part_condition", label: __("Condition"), fieldtype: "Select",
							options: "\nGood\nFaulty\nDamaged\nScrap", reqd: 1, default: btn.data("condition") || "" },
						{ fieldname: "installed_part_serial", label: __("Installed Part Serial / IMEI (new, KGB)"), fieldtype: "Data",
							reqd: installMode ? 1 : 0, default: btn.data("installed") || "" },
					],
					primary_action_label: installMode ? __("Install") : __("Save"),
					primary_action: v => {
						frappe.xcall(`${API}.update_spare_genealogy`, {
							sr_name: d.name, spare_row_name: rowName,
							removed_part_serial: v.removed_part_serial,
							installed_part_serial: v.installed_part_serial || "",
							removed_part_condition: v.removed_part_condition,
							consume: installMode ? 1 : 0,
						}).then(() => {
							dlg.hide();
							frappe.show_alert({ message: installMode ? __("Part installed — serials recorded.") : __("Part serial details saved."), indicator: "green" });
							self._load_detail(d.name);
						}).catch(err => frappe.msgprint({ title: __("Error"), message: err.message || String(err), indicator: "red" }));
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
				const warehouse = d.source_warehouse || "";
				// Scope the picker to the solution actually being worked on:
				// In Progress first, else assigned-and-active, else all active.
				const activeSols = (d.solution_lines || []).filter(s => !["Completed", "Skipped", "Cancelled"].includes(s.status));
				const inProgress = activeSols.filter(s => s.status === "In Progress");
				const scopeSols = inProgress.length ? inProgress : (activeSols.some(s => s.technician) ? activeSols.filter(s => s.technician) : activeSols);
				const scopeNames = scopeSols.map(s => s.repair_solution);
				const scopeCats = [...new Set(scopeSols.map(s => s.issue_category).filter(Boolean))];
				const dlg = new frappe.ui.Dialog({
					title: __("Add Spare Part"),
					fields: [
						{ fieldname: "spare_item", label: __("Spare Part"), fieldtype: "Link", options: "Item", reqd: 1,
							description: scopeNames.length ? __("Scoped to: {0}", [scopeNames.join(", ")]) : undefined,
							get_query: () => ({
								query: "gofix.gofix_services.api.get_compatible_spare_items",
								filters: { device_item: d.device_item || "", item_group: "Spares",
									solutions: scopeNames, issue_categories: scopeCats }
							}),
							change: () => {
								const item = dlg.get_value("spare_item");
								if (item && warehouse) {
									frappe.xcall(`${API}.get_spare_availability`, { item_code: item, warehouse })
										.then(r => {
											const avail = r.available_qty || 0;
											const cls = avail > 0 ? "green" : "red";
											dlg.fields_dict.stock_html.$wrapper.html(
												`<div class="text-${cls}" style="font-weight:600; margin-bottom:8px">
													<i class="fa fa-${avail > 0 ? 'check-circle' : 'warning'}"></i>
													${__("Available in store")}: ${avail}
													${avail <= 0 ? ` — <span class="text-muted">${__("will be added to procurement cart")}</span>` : ""}
												</div>`
											);
										});
								} else {
									dlg.fields_dict.stock_html.$wrapper.html("");
								}
							},
						},
						{ fieldname: "stock_html", fieldtype: "HTML" },
						{ fieldname: "qty", label: __("Qty"), fieldtype: "Float", default: 1, reqd: 1 },
						{ fieldname: "removed_part_serial", label: __("Removed Part Serial (old, KBB)"), fieldtype: "Data",
							description: __("Serial/IMEI of the part taken OUT — needed for defective-return credit") },
						{ fieldname: "installed_part_serial", label: __("Installed Part Serial (new, KGB)"), fieldtype: "Data" },
						{ fieldname: "removed_part_condition", label: __("Removed Part Condition"), fieldtype: "Select",
							options: "\nGood\nFaulty\nDamaged\nScrap" },
						{ fieldname: "rate", label: __("Rate"), fieldtype: "Currency", default: 0 },
					],
					primary_action_label: __("Add"),
					primary_action: v => {
						frappe.xcall(`${API}.add_spare_to_ticket`, { sr_name: d.name, spare_item: v.spare_item, qty: v.qty, rate: v.rate || 0,
							repair_solution: (scopeSols[0] || {}).repair_solution || "",
							removed_part_serial: v.removed_part_serial || "", installed_part_serial: v.installed_part_serial || "",
							removed_part_condition: v.removed_part_condition || "" })
							.then(r => {
								dlg.hide();
								if (r.status === "Awaiting Procurement") {
									frappe.show_alert({ message: __("Spare not in stock — added to procurement cart"), indicator: "orange" });
								} else {
									frappe.show_alert({ message: __("Spare reserved successfully"), indicator: "green" });
								}
								self._load_detail(d.name);
							});
					},
				});
				dlg.show();
			});

			// Remove spare (reserved / awaiting procurement)
			content.on("click", ".goh-spare-remove", function () {
				const rowName = $(this).data("row");
				frappe.confirm(__("Remove this spare from the ticket?"), () => {
					frappe.xcall(`${API}.release_spare_reservation`, { sr_name: d.name, spare_row_name: rowName })
						.then(() => { self._load_detail(d.name); });
				});
			});

			// Raise Material Request
			content.find("#goh-raise-mr-btn").on("click", () => {
				frappe.confirm(
					__("Create a Material Request for all spares awaiting procurement?"),
					() => {
						frappe.xcall(`${API}.raise_material_request`, { sr_name: d.name })
							.then(r => {
								frappe.show_alert({ message: __("MR {0} created for {1} spare(s)", [r.material_request, r.count]), indicator: "green" });
								self._load_detail(d.name);
							});
					}
				);
			});

			// Send the device somewhere that can finish the repair. Destinations
			// come from the location hierarchy, not the warehouse tree, so a
			// Damaged bin is never a place to send a customer's phone.
			content.find("#goh-send-hub").on("click", () => {
				frappe.xcall("gofix.gofix_services.api.get_repair_destinations", { service_request: d.name })
					.then((dests) => {
						if (!(dests || []).length) {
							frappe.msgprint({
								title: __("Nowhere to send it"),
								message: __("No other service-enabled store is available in this company."),
								indicator: "orange",
							});
							return;
						}
						const opts = dests.map((x) =>
							`${x.label}${x.is_hub ? " — " + __("Hub") : ""}${x.city ? " · " + x.city : ""}`);
						const dlg = new frappe.ui.Dialog({
							title: __("Transfer {0} for repair", [d.name]),
							fields: [
								{ fieldname: "dest", fieldtype: "Select", label: __("Send to"), reqd: 1, options: opts },
								{ fieldname: "reason", fieldtype: "Small Text", reqd: 1,
								  label: __("Why can this location not finish it?") },
							],
							primary_action_label: __("Dispatch"),
							primary_action: (v) => {
								const picked = dests[opts.indexOf(v.dest)];
								if (!picked) return;
								dlg.get_primary_btn().prop("disabled", true);
								frappe.xcall("gofix.gofix_services.api.create_service_transfer", {
									service_request: d.name, to_store: picked.warehouse, reason: v.reason,
								}).then(() => {
									dlg.hide();
									frappe.show_alert({
										message: __("{0} dispatched to {1}.", [d.name, picked.label]),
										indicator: "green",
									});
									self._refresh_all();
								}).catch((err) => {
									dlg.get_primary_btn().prop("disabled", false);
									frappe.msgprint({ title: __("Could not dispatch"),
										message: err.message || String(err), indicator: "red" });
								});
							},
						});
						dlg.show();
					});
			});

			// Send it home. No destination to choose — it goes back to the store
			// that raised the ticket, which is where the customer will collect it
			// and where the invoice is raised.
			content.find("#goh-return-store").on("click", () => {
				const home = d.source_warehouse ? String(d.source_warehouse).split(" - ")[0] : __("the origin store");
				frappe.confirm(
					__("Send this device back to {0} for handover and invoicing?", [home]),
					() => {
						frappe.xcall("gofix.gofix_services.api.return_service_transfer", {
							service_request: d.name,
						}).then(() => {
							frappe.show_alert({
								message: __("{0} is on its way back to {1}.", [d.name, home]),
								indicator: "green",
							});
							self._refresh_all();
						}).catch((err) => {
							frappe.msgprint({ title: __("Could not return the device"),
								message: err.message || String(err), indicator: "red" });
						});
					}
				);
			});

			// Call back a dispatch that has not left the shelf yet.
			content.find("#goh-cancel-transfer").on("click", () => {
				const dlg = new frappe.ui.Dialog({
					title: __("Cancel dispatch of {0}", [d.name]),
					fields: [
						{ fieldname: "note", fieldtype: "HTML",
						  options: `<p class="text-muted small">${__("Only possible while the device has not been picked up. It stays at {0} and any redirected spares come back with it.", [(d.source_warehouse || "").split(" - ")[0]])}</p>` },
						{ fieldname: "reason", fieldtype: "Small Text", reqd: 1,
						  label: __("Why is it being called back?") },
					],
					primary_action_label: __("Cancel Dispatch"),
					primary_action: (v) => {
						dlg.get_primary_btn().prop("disabled", true);
						frappe.xcall("gofix.gofix_services.api.cancel_service_transfer", {
							service_request: d.name, reason: v.reason,
						}).then(() => {
							dlg.hide();
							frappe.show_alert({ message: __("Dispatch cancelled."), indicator: "green" });
							self._refresh_all();
						}).catch((err) => {
							dlg.get_primary_btn().prop("disabled", false);
							frappe.msgprint({ title: __("Could not cancel"),
								message: err.message || String(err), indicator: "red" });
						});
					},
				});
				dlg.show();
			});

			// Technician takes the job on. Assigning them was an offer; this is
			// where the clock actually starts, so the wait until this click is
			// what the Assignment timeline track measures.
			content.find(".goh-accept-ja").on("click", (e) => {
				const ja = $(e.currentTarget).data("ja");
				if (!ja) return;
				frappe.call({
					method: "gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.accept_job_assignment",
					args: { ja_name: ja },
					freeze: true,
					freeze_message: __("Accepting…"),
					callback: (r) => {
						if (!r.message) return;
						if (r.message.already) {
							frappe.show_alert({ message: __("Already accepted."), indicator: "orange" });
						} else {
							const waited = flt(r.message.accept_wait_hours);
							frappe.show_alert({
								message: waited >= 0.02
									? __("Job accepted after {0}h waiting. The clock starts now.", [waited.toFixed(1)])
									: __("Job accepted. The clock starts now."),
								indicator: "green",
							});
						}
						self._load_detail(d.name);
					},
				});
			});

			// Device handover — custody moves, solution assignments stay
			content.find("#goh-device-handover").on("click", () => {
				const holder = d.device_holder || "";
				const opts = {};
				(d.assignments || []).filter(a => a.assignment_status !== "Cancelled" && a.assignment_status !== "Completed").forEach(a => {
					if (a.service_engineer && a.service_engineer !== holder) opts[a.service_engineer] = a.engineer_display;
				});
				(d.solution_lines || []).forEach(s => {
					if (s.technician && s.technician !== holder && s.status !== "Cancelled") opts[s.technician] = s.technician_name || s.technician;
				});
				const keys = Object.keys(opts);
				if (!keys.length) {
					return frappe.show_alert({ message: __("No other technician on this ticket — assign them a solution first."), indicator: "orange" });
				}
				const holderName = (d.assignments || []).find(a => a.service_engineer === holder)?.engineer_display;
				const dlg = new frappe.ui.Dialog({
					title: __("Hand Over Device"),
					fields: [
						{ fieldname: "info", fieldtype: "HTML", options: `<div class="small text-muted" style="margin-bottom:8px">${holderName ? __("Device is currently with {0}.", [`<b>${holderName}</b>`]) : __("Nobody holds the device right now.")}</div>` },
						{ fieldname: "to_technician", label: __("Hand over to"), fieldtype: "Select", reqd: 1,
							options: keys.map(k => ({ label: opts[k], value: k })) },
						{ fieldname: "remarks", label: __("Note"), fieldtype: "Small Text" },
					],
					primary_action_label: __("Hand Over"),
					primary_action: v => {
						frappe.xcall(`${API}.handover_device`, { sr_name: d.name, to_technician: v.to_technician, remarks: v.remarks || "" })
							.then(() => { dlg.hide(); self._load_detail(d.name); frappe.show_alert({ message: __("Device handed over."), indicator: "green" }); });
					},
				});
				dlg.show();
			});

			// Per-solution handoff (⇄ on the card) — operation-level reassignment
			content.on("click", ".goh-sol-reassign", function () {
				const btn = $(this);
				const rowName = btn.data("row");
				const solName = btn.data("solution") || __("this solution");
				const dlg = new frappe.ui.Dialog({
					title: __("Hand Off: {0}", [solName]),
					fields: [
						{ fieldname: "technician", label: __("New Technician"), fieldtype: "Link", options: "Employee", reqd: 1,
							get_query: () => ({ query: "gofix.gofix_services.api.technician_query", filters: { sr_name: d.name } }) },
						{ fieldname: "reason", label: __("Reason"), fieldtype: "Small Text",
							description: __("The solution returns to Planned — the new technician takes the device when they press Start.") },
					],
					primary_action_label: __("Hand Off"),
					primary_action: v => {
						frappe.xcall(`${API}.reassign_solution_to_technician`, {
							sr_name: d.name, solution_row_name: rowName, technician: v.technician, reason: v.reason || "",
						}).then(() => { dlg.hide(); self._load_detail(d.name); frappe.show_alert({ message: __("Handed off."), indicator: "green" }); });
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
				const cc = s.company_cost || { total: 0, parts_cost: 0, damaged_parts_cost: 0, labour_cost: 0, labour_hours: 0 };

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
					${s.items_source === "service_order" ? `<div class="text-muted small mt-1"><i class="fa fa-info-circle"></i> ${__("Amounts pulled from Service Order (estimate) — no billing lines logged on the SR.")}</div>` : ""}
					${s.discount ? `<div class="text-muted mt-2">${__("Discount")}: -₹${format_number(s.discount)}</div>` : ""}
					<div class="goh-grand-total mt-3">
						<h4>${__("Cost to Customer")}: <span style="color:#059669">₹${format_number(s.customer_total)}</span>
							${s.final_cost ? `<small class="text-muted">(${__("Final Cost override — base")} ₹${format_number(s.base_total)})</small>` : ""}
						</h4>
						<div class="mt-2" style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:8px 12px;display:inline-block;">
							<h5 style="margin:0">${__("Cost to Company")}: <span style="color:#dc2626">₹${format_number(cc.total)}</span></h5>
							<div class="text-muted small mt-1">
								${__("Parts (at cost)")}: ₹${format_number(cc.parts_cost)}
								&nbsp;·&nbsp; ${__("Damaged parts")}: ₹${format_number(cc.damaged_parts_cost)}
								&nbsp;·&nbsp; ${__("Labour")} (${cc.labour_hours || 0}h): ₹${format_number(cc.labour_cost)}
							</div>
						</div>
						<h5 class="mt-2">${__("Margin")}: <span style="color:${s.margin >= 0 ? "#059669" : "#dc2626"}">₹${format_number(s.margin)}</span></h5>
					</div>
					${!s.service_invoice ? `
						<div class="mt-3" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
							<label class="text-muted" style="margin:0">${__("Final Cost to Customer")}</label>
							<input type="number" min="0" step="0.01" id="goh-final-cost" class="form-control input-sm" style="width:140px" value="${s.final_cost || ""}" placeholder="${format_number(s.base_total)}">
							<button class="btn btn-xs btn-default" id="goh-set-final-cost">${__("Set Final Cost")}</button>
							<span class="text-muted small">${__("Leave empty to bill the item total. Below Cost-to-Company needs an approved exception.")}</span>
						</div>
					` : ""}
					${s.below_cost ? (
						["Approved", "Auto-Approved"].includes(s.below_cost_exception_status)
							? `<div class="mt-2" style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:6px 12px;">
								<i class="fa fa-check-circle" style="color:#16a34a"></i> ${__("Below-cost billing approved via")}
								<a href="/app/ch-exception-request/${encodeURIComponent(s.below_cost_exception)}" target="_blank">${esc(s.below_cost_exception)}</a>
							   </div>`
							: `<div class="mt-2" style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:6px 12px;">
								<i class="fa fa-exclamation-triangle" style="color:#d97706"></i>
								${__("Billing total is below Cost to Company.")}
								${s.below_cost_exception
									? `${__("Exception")} <a href="/app/ch-exception-request/${encodeURIComponent(s.below_cost_exception)}" target="_blank">${esc(s.below_cost_exception)}</a> — <b>${esc(s.below_cost_exception_status || __("Pending"))}</b>. ${__("Invoice creation stays blocked until it is approved.")}`
									: __("Set a Final Cost to raise a below-cost exception for approval.")}
							   </div>`
					) : ""}
					${s.service_invoice
						? `<div class="mt-2">
							<span class="goh-badge badge-green">${__("Invoiced")}</span>
							<a href="/app/sales-invoice/${encodeURIComponent(s.service_invoice)}" target="_blank">${esc(s.service_invoice)}</a>
							<button class="btn btn-xs btn-default ml-2 goh-print-invoice" data-invoice="${esc(s.service_invoice)}"><i class="fa fa-print"></i> ${__("Print")}</button>
						</div>`
						: `<div class="mt-3">
							<!-- The hub owns the PRICE and the device's LOCATION; the POS
							     owns the tender. Both have to be settleable here, or the
							     counter is handed a ticket it cannot bill: a price nobody
							     agreed, or a device sitting in another store. -->
							<div class="goh-invoice-prep" style="margin-bottom:10px">
								<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
									<b class="small">${__("Final Price")}</b>
									<input type="number" step="0.01" min="0" class="form-control input-sm"
										id="goh-final-price" style="max-width:150px"
										value="${s.customer_total || 0}">
									<input type="text" class="form-control input-sm" id="goh-final-price-reason"
										style="max-width:260px" placeholder="${__("Reason (required to change)")}">
									<button class="btn btn-sm btn-default" id="goh-set-final-price">
										<i class="fa fa-check"></i> ${__("Set Price")}
									</button>
								</div>
								<div class="text-muted small" style="margin-top:4px">
									${__("This is what the counter will charge. Below cost-to-company it needs an approved exception before it can be billed.")}
								</div>
								${d.transferred_to_store && d.transferred_to_store !== d.source_warehouse ? `
									<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border-color)">
										<span class="goh-badge badge-orange"><i class="fa fa-truck"></i>
											${__("Device is at")} ${esc(d.transferred_to_store)}</span>
										<span class="text-muted small ml-2">
											${__("It has to come back before the customer can collect and pay.")}
										</span>
										<button class="btn btn-xs btn-default ml-2" id="goh-invoice-return-store">
											<i class="fa fa-undo"></i> ${__("Return To Store")}
										</button>
									</div>` : ""}
							</div>

							<div class="goh-bill-at-pos" style="padding:10px 12px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-color)">
								<div><i class="fa fa-shopping-cart"></i> <b>${__("Bill this repair at the POS counter")}</b></div>
								<div class="text-muted small" style="margin-top:4px">
									${__("Add the ticket to the POS cart to take payment. It can go on one invoice with accessories, plans, discounts and vouchers — and the money reaches the till settlement.")}
								</div>
								<button class="btn btn-sm btn-primary mt-2" id="goh-open-pos-billing">
									<i class="fa fa-external-link"></i> ${__("Open POS Billing")}
								</button>
							</div>
							<span class="text-muted ml-2">${__("Or invoice at POS during handover.")}</span>
						   </div>`
					}
				`);

				// Bind set-final-cost button
				this.parent.find("#goh-set-final-cost").on("click", () => {
					const val = flt(this.parent.find("#goh-final-cost").val() || 0);
					const args = { sr_name: d.name, final_cost: val };
					const call = () => frappe.xcall(`${API}.set_final_cost`, args)
						.then(r => {
							if (r.below_cost && !["Approved", "Auto-Approved"].includes(r.exception_status)) {
								frappe.show_alert({ message: __("Final cost saved — below-cost exception {0} awaiting approval.", [r.exception]), indicator: "orange" });
							} else {
								frappe.show_alert({ message: __("Final cost saved."), indicator: "green" });
							}
							self._refresh_all();
						});
					if (val && val < cc.total) {
						frappe.prompt(
							{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (below Cost to Company ₹{0})", [format_number(cc.total)]), reqd: 1 },
							v => { args.reason = v.reason; call(); },
							__("Below-Cost Final Price"), __("Save")
						);
					} else {
						call();
					}
				});

				// Billing moved to the POS counter. An invoice raised here carries no
				// pos_profile and no payment rows, so the settlement query cannot see
				// it and the cash never reaches the drawer. Send the user to the till
				// instead of quietly creating an unreconcilable invoice.
				this.parent.find("#goh-set-final-price").on("click", (e) => {
					const btn = $(e.currentTarget);
					const price = parseFloat(this.parent.find("#goh-final-price").val());
					const reason = (this.parent.find("#goh-final-price-reason").val() || "").trim();
					if (!(price >= 0)) {
						frappe.show_alert({ message: __("Enter a price"), indicator: "orange" });
						return;
					}
					btn.prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i>');
					frappe.xcall(`${API}.set_final_cost`, {
						sr_name: d.name, final_cost: price, reason: reason,
					}).then((r) => {
						frappe.show_alert({
							message: __("Final price set to {0}", [format_currency(price)]),
							indicator: "green",
						});
						self._refresh_all();
					}).catch((err) => {
						btn.prop("disabled", false).html('<i class="fa fa-check"></i> ' + __("Set Price"));
						frappe.msgprint({
							title: __("Price Not Set"),
							message: err.message || __("The final price could not be recorded."),
							indicator: "red",
						});
					});
				});

				this.parent.find("#goh-invoice-return-store").on("click", () => {
					const home = d.source_warehouse
						? String(d.source_warehouse).split(" - ")[0] : __("the origin store");
					frappe.confirm(
						__("Send this device back to {0} so the customer can collect and pay there?", [home]),
						() => {
							frappe.xcall("gofix.gofix_services.api.return_service_transfer",
								{ service_request: d.name })
								.then(() => {
									frappe.show_alert({
										message: __("{0} is on its way back to {1}.", [d.name, home]),
										indicator: "green",
									});
									self._refresh_all();
								})
								.catch((err) => frappe.msgprint({
									title: __("Could not return the device"),
									message: err.message || __("The transfer could not be raised."),
									indicator: "red",
								}));
						}
					);
				});

				if (!s.service_invoice) {
					this.parent.find("#goh-open-pos-billing").on("click", () => {
						// Carry the ticket so the counter does not search for it again.
						try {
							localStorage.setItem("ch_pos_pending_repair_bill", d.name);
						} catch (e) {
							// A private window without storage is not a reason to block
							// billing — the counter can still find the ticket by number.
						}
						frappe.set_route("ch-pos-app", "repair");
					});
				}
			});
		}

		/* ── Rework ──────────────────────────────────────────────────── */
		if (activeStage === "rework") {
			this._init_link_field("#goh-rework-tech-field", "Employee", __("Select technician..."), {
				query: "gofix.gofix_services.api.technician_query",
				sr_name: d.name,
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

		/* ── Done (load invoice summary if applicable) ───────────────── */
		if (activeStage === "done" && d.service_invoice) {
			frappe.xcall(`${API}.get_invoice_summary`, { sr_name: d.name }).then(s => {
				const esc = frappe.utils.escape_html;
				const makeRows = items => items.map(i => `
					<tr><td>${esc(i.item_name || i.item_code)}</td><td class="text-right">${i.qty}</td><td class="text-right">₹${format_number(i.rate)}</td><td class="text-right">₹${format_number(i.amount)}</td></tr>
				`).join("");
				this.parent.find("#goh-done-invoice-summary").html(`
					${s.service_items.length ? `
						<h6>${__("Service Charges")}</h6>
						<table class="goh-table"><thead><tr><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-right">${__("Amount")}</th></tr></thead>
						<tbody>${makeRows(s.service_items)}</tbody></table>
					` : ""}
					${s.spare_items.length ? `
						<h6 class="mt-2">${__("Spare Parts")}</h6>
						<table class="goh-table"><thead><tr><th>${__("Item")}</th><th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th><th class="text-right">${__("Amount")}</th></tr></thead>
						<tbody>${makeRows(s.spare_items)}</tbody></table>
					` : ""}
					<div class="goh-grand-total mt-2">
						<h5>${__("Total")}: <span style="color:#059669">₹${format_number(s.customer_total)}</span></h5>
					</div>
				`);
			}).catch(() => {
				this.parent.find("#goh-done-invoice-summary").html("");
			});
		}

		/* ── Hide stage-transition buttons when viewing a previous step ── */
		if (isViewing) {
			const hideSel = [
				"#goh-confirm-analysis", "#goh-send-wa", "#goh-mark-confirmed",
				"#goh-back-to-analysis", "#goh-save-solutions", "#goh-back-to-confirm",
				"#goh-back-to-solutions", "#goh-submit-qc",
				"#goh-back-to-assign", "#goh-rework-assign", "#goh-device-handover",
				"#goh-send-hub", "#goh-return-store", "#goh-cancel-transfer",
			];
			// Assigning MORE technicians mid-repair is legitimate (a ticket can be
			// split across L1/L2/L4) — keep the Assign action live while the
			// ticket is still in repair; hide it once work has moved past that.
			if (d.ops_stage !== "repair") hideSel.push("#goh-do-assign", "#goh-proceed-repair");
			// Same reasoning for the work itself: a fault found during repair or
			// at QC needs a solution added, which means stepping back to
			// Solutions and SAVING. Hiding Save made that step look editable but
			// left no way to commit it — tick the boxes, lose the change.
			if (activeStage === "solutions") {
				const i = hideSel.indexOf("#goh-save-solutions");
				if (i > -1) hideSel.splice(i, 1);
			}
			content.find(hideSel.join(", ")).hide();
		}
	}

	/* ═══════════════════════════════════════════════════════════════════════ */
	/*  Helper Methods                                                       */
	/* ═══════════════════════════════════════════════════════════════════════ */
	_fill_issue_category_selects() {
		/* Options come from the server once, then every Issue Category <select>
		   on screen is (re)filled. data-selected carries the row's saved value so
		   refilling never silently blanks an existing issue. */
		const cats = this._issue_categories || [];
		this.parent.find("select.goh-issue-cat").each(function () {
			const $sel = $(this);
			const chosen = $sel.val() || $sel.data("selected") || "";
			const placeholder = $sel.find('option[value=""]').text() || __("Issue Category");
			$sel.html(
				`<option value="">${frappe.utils.escape_html(placeholder)}</option>` +
				cats.map(c => {
					const v = frappe.utils.escape_html(c);
					return `<option value="${v}"${c === chosen ? " selected" : ""}>${v}</option>`;
				}).join("")
			);
			if (chosen && !cats.includes(chosen)) {
				// keep a value the catalogue no longer offers rather than dropping it
				$sel.append(`<option value="${frappe.utils.escape_html(chosen)}" selected>${frappe.utils.escape_html(chosen)}</option>`);
			}
		});
	}

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
		// filters.query switches to a custom server-side Link query;
		// remaining keys are passed through as its filters.
		const get_query = () => {
			if (filters && filters.query) {
				const { query, ...rest } = filters;
				return { query, filters: rest };
			}
			return { filters };
		};
		const ctrl = frappe.ui.form.make_control({
			df: { fieldname: propName || "tech", fieldtype: "Link", options: doctype, placeholder, get_query },
			parent: wrapper, render_input: true,
		});
		if (propName) this[propName] = ctrl;
		else this._tech_field = ctrl;
	}

	_html_draft(d) {
		const esc = frappe.utils.escape_html;
		return `
		<div class="goh-section p-3">
			<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
				<i class="fa fa-inbox" style="font-size:20px;color:var(--text-muted)"></i>
				<div>
					<div style="font-weight:700">${__("New ticket — awaiting acceptance")}</div>
					<div class="text-muted" style="font-size:12px">
						${__("Issue")}: ${esc(d.issue_category || "—")}
						${d.estimated_cost ? ` · ${__("Est.")} ₹${format_number(d.estimated_cost)}` : ""}
					</div>
				</div>
			</div>
			<p class="text-muted" style="font-size:12px">
				${__("Tickets raised at a counter open themselves — the device is already in hand. This queue is for requests raised remotely, where taking the job is a decision.")}
			</p>
			<p class="text-muted" style="font-size:12px">
				<b>${__("Take In")}</b> ${__("opens the job and sends it to Analysis. Nothing is quoted or ordered yet — the Service Order is raised once the customer confirms the estimate.")}
				<br>
				<b>${__("Accept & Create Service Order")}</b> ${__("additionally records estimate v1 as customer-approved and raises the Service Order up front. Use it only when the price is already agreed.")}
			</p>
			<button class="btn btn-primary" id="goh-open-job">
				<i class="fa fa-inbox"></i> ${__("Take In — start Analysis")}
			</button>
			<button class="btn btn-default" id="goh-accept-create-so" style="margin-left:6px">
				<i class="fa fa-check"></i> ${__("Accept & Create Service Order")}
			</button>
		</div>`;
	}

	async _load_solutions_for_categories(d) {
		const self = this;
		const esc = frappe.utils.escape_html;
		const activeIssues = (d.issue_lines || []).filter(r => r.status !== "Deleted");
		const categories = [...new Set(activeIssues.map(i => i.issue_category).filter(Boolean))];
		if (!categories.length) {
			this.parent.find("#goh-sol-picker").html(`<p class="text-muted">${__("No issues documented yet.")}</p>`);
			return;
		}

		// Solutions borrowed from another Issue Category. The picker asks the
		// server for one category at a time, so a reused solution would vanish
		// on the next reload unless it is carried here. Scoped to one job.
		if (this._extraSolutionsFor !== d.name) {
			this._extraSolutionsFor = d.name;
			this._extraSolutions = {};
		}

		// Reloading the picker (after adding a solution) used to wipe every tick
		// the user had already made. Carry them across the rebuild.
		const wasChecked = new Set();
		this.parent.find(".goh-sol-check:checked").each(function () {
			wasChecked.add(`${$(this).data("category")} ${$(this).data("solution")}`);
		});

		// Seed from what is ALREADY on the ticket. Without this the picker
		// opened with every box empty even though the solutions were assigned
		// and being worked: the "Already Assigned" chips said one thing and the
		// list underneath said another, so re-saving looked like it would drop
		// them.
		const assignedStatus = {};
		for (const s of (d.solution_lines || [])) {
			if (s.status === "Cancelled") continue;
			wasChecked.add(`${s.issue_category} ${s.repair_solution}`);
			assignedStatus[`${s.issue_category} ${s.repair_solution}`] = s.status;
		}
		const STATUS_BADGE = {
			Planned: "badge-muted", "In Progress": "badge-blue", "On Hold": "badge-orange",
			Completed: "badge-green", Skipped: "badge-yellow",
		};

		const solRow = (s, cat, borrowedFrom) => {
			const key = `${cat} ${s.name}`;
			const checked = (wasChecked.has(key) || borrowedFrom) ? " checked" : "";
			const live = assignedStatus[key];
			return `
					<label class="goh-sol-option">
						<input type="checkbox" class="goh-sol-check"${checked}
							data-solution="${esc(s.name)}" data-category="${esc(cat)}"
							data-code="${esc(s.solution_code || "")}" data-minutes="${s.estimated_minutes || 0}"
							data-requires-spare="${s.requires_spare ? 1 : 0}">
						<span class="goh-sol-name">${esc(s.solution_name || s.name)}</span>
						<span class="text-muted small ml-2">${s.estimated_minutes || 0}min</span>
						${live ? `<span class="goh-badge ${STATUS_BADGE[live] || "badge-muted"} ml-1" title="${__("Already on this ticket")}">${esc(live)}</span>` : ""}
						${s.requires_spare ? `<span class="goh-badge badge-yellow ml-1">${__("Spare")}</span>` : ""}
						${borrowedFrom ? `<span class="goh-badge badge-blue ml-1" title="${__("Borrowed from another issue category")}">${esc(borrowedFrom)}</span>` : ""}
					</label>`;
		};

		let allHtml = "";
		for (const cat of categories) {
			let rows = "";
			let failed = false;
			try {
				const sols = await frappe.xcall(`${API}.get_solutions_for_issue`, {
					issue_category: cat,
					device_item: d.device_item || null,
				});
				rows = (sols || []).map(s => solRow(s, cat, null)).join("");
			} catch (e) {
				failed = true;
			}

			const borrowed = (this._extraSolutions[cat] || [])
				.map(s => solRow(s, cat, s.owner_issue_category || __("Reused")))
				.join("");

			const body = (rows + borrowed) || (failed
				? `<p class="text-muted small">${__("Could not load solutions.")}</p>`
				: `<p class="text-muted small">${__("No solutions apply to this device in this category.")}</p>`);

			allHtml += `
					<div class="goh-sol-category" data-category="${esc(cat)}">
						<h6 class="goh-sol-cat-title"><i class="fa fa-tag"></i> ${esc(cat)}</h6>
						${body}
						<button class="btn btn-xs btn-default goh-add-sol-btn mt-1" data-category="${esc(cat)}"><i class="fa fa-plus"></i> ${__("Add Solution")}</button>
					</div>`;
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
					const call = (on_duplicate) => frappe.xcall(`${API}.quick_create_solution`, {
						solution_name: v.solution_name,
						issue_category: cat,
						estimated_minutes: v.estimated_minutes || 30,
						requires_spare: v.requires_spare ? 1 : 0,
						description: v.description || "",
						on_duplicate: on_duplicate || null,
					});

					call(null).then(r => {
						dlg.hide();
						if (r.status === "exists_elsewhere") {
							self._resolve_duplicate_solution(d, cat, r, call);
							return;
						}
						self._announce_solution_result(r, cat);
						self._load_solutions_for_categories(d);
					});
				},
			});
			dlg.show();
		});
	}

	/** Tell the user exactly what the server did — never claim more than that. */
	_announce_solution_result(r, cat) {
		const label = r.solution_name || r.name;
		if (r.status === "created") {
			frappe.show_alert({ message: __("Solution '{0}' created and selected.", [label]), indicator: "green" });
		} else if (r.status === "reactivated") {
			frappe.show_alert({ message: __("Solution '{0}' already existed here but was inactive — reactivated and selected.", [label]), indicator: "orange" });
		} else if (r.status === "reused") {
			frappe.show_alert({ message: __("Reusing '{0}' from {1} on this job.", [label, r.owner_issue_category]), indicator: "blue" });
		} else {
			frappe.show_alert({ message: __("Solution '{0}' already exists in {1} — tick it below.", [label, cat]), indicator: "blue" });
		}
	}

	/**
	 * The label is taken by a solution filed under a different Issue Category.
	 * Name the category and let the user choose, rather than silently doing
	 * nothing and reporting success.
	 */
	_resolve_duplicate_solution(d, cat, r, call) {
		const self = this;
		const esc = frappe.utils.escape_html;
		const owners = (r.existing || []).map(e => e.issue_category);
		const dlg = new frappe.ui.Dialog({
			title: __("'{0}' already exists", [r.solution_name]),
			fields: [{
				fieldtype: "HTML",
				options: `
					<p>${__("A repair solution called <b>{0}</b> is already filed under <b>{1}</b>.",
						[esc(r.solution_name), esc(owners.join(", "))])}</p>
					<p class="text-muted small">${__("<b>Reuse</b> puts that same solution on this job — one catalogue entry, one service item, one price. <b>Create separate</b> makes a second solution owned by {0}, which is right only when the work genuinely differs.", [esc(cat)])}</p>`,
			}],
			primary_action_label: __("Reuse existing"),
			primary_action: () => {
				call("reuse").then(res => {
					dlg.hide();
					self._extraSolutions[cat] = (self._extraSolutions[cat] || [])
						.filter(x => x.name !== res.name)
						.concat([res]);
					self._announce_solution_result(res, cat);
					self._load_solutions_for_categories(d);
				});
			},
			secondary_action_label: __("Create separate for {0}", [cat]),
			secondary_action: () => {
				call("duplicate").then(res => {
					dlg.hide();
					self._announce_solution_result(res, cat);
					self._load_solutions_for_categories(d);
				});
			},
		});
		dlg.show();
	}

	_refresh_all() {
		this._load_queue();
		if (this.selectedSR) this._load_detail(this.selectedSR);
	}

	/* ── Not Repairable Flow ─────────────────────────────────────────── */
	_bind_not_repairable(d) {
		const self = this;
		// Hide for terminal states
		if (["done", "closed", "draft"].includes(d.ops_stage)) {
			this.parent.find(".goh-not-repairable-btn").hide();
			return;
		}
		this.parent.find(".goh-not-repairable-btn").on("click", () => {
			// All four ways a job ends without a repair, with the coded reasons
			// the counter sees. The workshop closing a job and the counter
			// closing the same job must record it identically, so both read the
			// one list from the server rather than carrying their own.
			frappe.xcall("gofix.gofix_services.api.get_repair_close_options",
				{ service_request: d.name })
				.then((opts) => {
					const outcomes = (opts && opts.outcomes) || [];
					if (!outcomes.length) {
						frappe.msgprint({
							title: __("Nothing configured"), indicator: "orange",
							message: __("No closing reasons are set up. Add them under Withdrawal Reason."),
						});
						return;
					}
					const by_outcome = {};
					outcomes.forEach((o) => { by_outcome[o.outcome] = o.reasons || []; });

					const refresh_reasons = () => {
						const rows = by_outcome[dlg.get_value("outcome")] || [];
						dlg.set_df_property("reason", "options",
							rows.map((r) => ({ value: r.name, label: r.reason_name || r.name })));
						dlg.set_value("reason", rows.length ? rows[0].name : "");
						dlg.set_df_property("note", "reqd",
							rows.length && rows[0].requires_note ? 1 : 0);
					};

					const dlg = new frappe.ui.Dialog({
						title: __("Close without repair"),
						fields: [
							{
								fieldname: "outcome", label: __("How did it end?"),
								fieldtype: "Select", reqd: 1,
								options: outcomes.map((o) => o.outcome),
								default: outcomes[0].outcome,
								description: __("BER = beyond economic repair: fixable, but not for what the device is worth."),
								onchange: () => refresh_reasons(),
							},
							{
								fieldname: "reason", label: __("Reason"),
								fieldtype: "Select", reqd: 1,
								description: __("Coded, so it can be counted later."),
							},
							{
								fieldname: "note", label: __("What happened?"),
								fieldtype: "Small Text",
								description: __("Shown on the customer receipt. Required for reasons that mean nothing on their own."),
							},
						],
						primary_action_label: __("Close Job"),
						primary_action: (v) => {
							dlg.disable_primary_action();
							frappe.xcall(`${API}.mark_not_repairable`, {
								sr_name: d.name, status: v.outcome,
								reason: v.note, reason_code: v.reason,
							}).then((r) => {
								dlg.hide();
								frappe.show_alert({
									message: r.handback_entry
										? __("Closed as {0}. Device issued back to the customer.", [v.outcome])
										: __("Closed as {0}", [v.outcome]),
									indicator: "orange",
								});
								if (r.needs_spare_recovery && (r.pending_spares || []).length) {
									self._show_spare_recovery_dialog(d.name, r.pending_spares);
								} else {
									self._refresh_all();
								}
							}).catch(() => dlg.enable_primary_action());
						},
					});
					dlg.show();
					refresh_reasons();
				});
		});
	}

	_show_spare_recovery_dialog(sr_name, spares) {
		const self = this;
		const esc = frappe.utils.escape_html;
		const DISPOSITIONS = [
			{ value: "Good - Back to Stock", label: __("Good → Back to Stock") },
			{ value: "Faulty - Supplier Return", label: __("Faulty → Supplier Return") },
			{ value: "Damaged by Technician", label: __("Damaged → Damaged Stock") },
		];
		const opts = DISPOSITIONS.map(d => `<option value="${d.value}">${d.label}</option>`).join("");

		const rows = spares.map(sp => `
			<tr data-spu="${esc(sp.name)}">
				<td>${esc(sp.item_name || sp.spare_part_item)}</td>
				<td class="text-center">${sp.qty_used}</td>
				<td>
					<select class="form-control input-xs goh-recovery-disp" data-spu="${esc(sp.name)}">
						<option value="">${__("— Select —")}</option>
						${opts}
					</select>
				</td>
				<td>
					<input class="form-control input-xs goh-recovery-remarks" data-spu="${esc(sp.name)}" placeholder="${__("Remarks")}">
				</td>
				<td class="text-center goh-recovery-status">
					<span class="text-warning"><i class="fa fa-clock-o"></i></span>
				</td>
			</tr>
		`).join("");

		const dlg = new frappe.ui.Dialog({
			title: __("⚠ Spare Recovery Required"),
			size: "large",
			fields: [{
				fieldname: "html", fieldtype: "HTML",
				options: `
					<div class="mb-3 text-muted small">
						${__("These consumed spares must be recovered before returning the device. Select a disposition for each spare.")}
					</div>
					<table class="table table-sm" id="goh-recovery-table">
						<thead><tr>
							<th>${__("Part")}</th>
							<th class="text-center" style="width:60px">${__("Qty")}</th>
							<th style="width:220px">${__("Disposition")}</th>
							<th>${__("Remarks")}</th>
							<th class="text-center" style="width:50px"></th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table>
				`,
			}],
			primary_action_label: __("Recover All"),
			primary_action: () => {
				const entries = [];
				let valid = true;
				dlg.$wrapper.find("#goh-recovery-table tbody tr").each(function () {
					const spu = $(this).data("spu");
					const disp = $(this).find(".goh-recovery-disp").val();
					const remarks = $(this).find(".goh-recovery-remarks").val();
					if (!disp) {
						$(this).find(".goh-recovery-disp").css("border-color", "red");
						valid = false;
					} else {
						$(this).find(".goh-recovery-disp").css("border-color", "");
					}
					entries.push({ spu, disp, remarks });
				});
				if (!valid) {
					frappe.show_alert({ message: __("Select a disposition for every spare."), indicator: "orange" });
					return;
				}
				dlg.disable_primary_action();
				self._process_spare_recoveries(sr_name, entries, dlg, 0);
			},
			secondary_action_label: __("Skip for Now"),
			secondary_action: () => {
				dlg.hide();
				frappe.show_alert({
					message: __("Spares not recovered yet. You can recover them later from the Service Request."),
					indicator: "yellow",
				});
				self._refresh_all();
			},
		});
		dlg.show();
	}

	_process_spare_recoveries(sr_name, entries, dlg, idx) {
		if (idx >= entries.length) {
			dlg.hide();
			frappe.show_alert({ message: __("All spares recovered!"), indicator: "green" });
			this._refresh_all();
			return;
		}
		const entry = entries[idx];
		const statusCell = dlg.$wrapper.find(`tr[data-spu="${entry.spu}"] .goh-recovery-status`);
		statusCell.html('<i class="fa fa-spinner fa-spin text-muted"></i>');

		frappe.xcall(`${API}.recover_spare_from_ops_hub`, {
			sr_name, spu_name: entry.spu, disposition: entry.disp, remarks: entry.remarks || "",
		}).then(() => {
			statusCell.html('<i class="fa fa-check text-success"></i>');
			this._process_spare_recoveries(sr_name, entries, dlg, idx + 1);
		}).catch(e => {
			statusCell.html('<i class="fa fa-times text-danger"></i>');
			frappe.show_alert({ message: __("Recovery failed for spare: {0}", [e.message || e]), indicator: "red" });
			this._process_spare_recoveries(sr_name, entries, dlg, idx + 1);
		});
	}
}
