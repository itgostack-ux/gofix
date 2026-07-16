frappe.pages["gofix-token-queue"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("GoFix Token Queue"),
		single_column: true,
	});
	page.main.html(`<div class="gofix-token-queue"><div class="gtq-body"></div></div>`);
	new GoFixTokenQueue(page);
};

const _API = "gofix.api.token_api";
const _TABS = [
	{ key: "Waiting",          label: __("Waiting"),          className: "gtq-status-Waiting" },
	{ key: "Called",           label: __("Called"),           className: "gtq-status-Called" },
	{ key: "Attending",        label: __("Attending"),        className: "gtq-status-Attending" },
	{ key: "Job Card Created", label: __("Job Card Created"), className: "gtq-status-JobCardCreated" },
	{ key: "Completed",        label: __("Completed"),        className: "gtq-status-Completed" },
	{ key: "Customer Left",    label: __("Customer Left"),    className: "gtq-status-CustomerLeft" },
	{ key: "Cancelled",        label: __("Cancelled"),        className: "gtq-status-Cancelled" },
	{ key: "__all__",          label: __("All Today"),        className: "" },
];
const _AUTOREFRESH_MS = 20000;

class GoFixTokenQueue {
	constructor(page) {
		this.page   = page;
		this.body   = page.main.find(".gtq-body");
		this.stores = [];
		this.store  = null;
		this.active_tab = "Waiting";
		this.rows   = [];
		this.timer  = null;
		this.init();
	}

	async init() {
		try {
			this.stores = await frappe.xcall(`${_API}.get_fde_stores`);
		} catch (e) {
			this.stores = [];
		}

		if (!this.stores.length) {
			this.body.html(`<div class="gtq-empty">
				${__("No GoFix-enabled stores are available to you.")}<br>
				<small>${__("Ask a System Manager to tick \"GoFix Token Enabled\" on your Company or grant Store Executive role.")}</small>
			</div>`);
			return;
		}

		this.store = this._default_store();
		this.render_toolbar();
		this.load();
		// Live refresh — cleared on page teardown.
		this.timer = setInterval(() => this.load({ silent: true }), _AUTOREFRESH_MS);
		this.page.wrapper.on("remove", () => clearInterval(this.timer));
	}

	_default_store() {
		const url_store = new URLSearchParams(window.location.search || "").get("store");
		const url_match = url_store && this.stores.find((s) => (
			s.warehouse === url_store || s.store_code === url_store || s.store_name === url_store
		));
		if (url_match) return url_match;

		const default_wh = frappe.defaults.get_user_default("warehouse");
		const match = default_wh && this.stores.find((s) => s.warehouse === default_wh);
		return match || {
			warehouse: "__all__",
			company: "",
			store_code: "ALL",
			store_name: __("All GoFix Stores"),
		};
	}

	// ------- Toolbar --------------------------------------------------------

	render_toolbar() {
		this.page.clear_fields();

		this.store_field = this.page.add_field({
			label: __("Store"),
			fieldtype: "Select",
			fieldname: "store",
			options: [{
				value: "__all__",
				label: __("All GoFix Stores"),
			}].concat(this.stores.map((s) => ({
				value: s.warehouse,
				label: `${s.store_name || s.warehouse} — ${s.store_code || ""}`,
			}))),
			default: this.store.warehouse,
			change: () => {
				const wh = this.store_field.get_value();
				this.store = wh === "__all__"
					? { warehouse: "__all__", company: "", store_code: "ALL", store_name: __("All GoFix Stores") }
					: (this.stores.find((s) => s.warehouse === wh) || this.store);
				this.load();
			},
		});

		this.page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
		this.page.set_primary_action(__("Open Tablet Page"), () => {
			if (!this.store || this.store.warehouse === "__all__") {
				frappe.msgprint(__("Select a specific store before opening the tablet page."));
				return;
			}
			window.open(`/gofix-token?store=${encodeURIComponent(this.store.store_code || this.store.warehouse)}`, "_blank");
		}, "external-link");
	}

	// ------- Load tokens ----------------------------------------------------

	async load({ silent } = {}) {
		if (!silent) this.body.addClass("loading");
		try {
			const statuses = _TABS.filter((t) => t.key !== "__all__").map((t) => t.key);
			const rows = await frappe.xcall(`${_API}.list_active_tokens`, {
				store: this.store && this.store.warehouse !== "__all__" ? this.store.warehouse : null,
				statuses: JSON.stringify(statuses),
			});
			this.rows = rows || [];
			this.render();
		} catch (e) {
			if (!silent) frappe.show_alert({ message: e.message || __("Failed to load tokens"), indicator: "red" });
		} finally {
			this.body.removeClass("loading");
		}
	}

	// ------- Render ---------------------------------------------------------

	render() {
		const counts = {};
		this.rows.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
		counts["__all__"] = this.rows.length;

		const tabs_html = _TABS.map((t) => {
			const c = counts[t.key] || 0;
			return `<div class="gtq-tab ${this.active_tab === t.key ? "active" : ""}" data-tab="${frappe.utils.escape_html(t.key)}">
				${frappe.utils.escape_html(t.label)}<span class="count">${c}</span>
			</div>`;
		}).join("");

		const filtered = this.active_tab === "__all__"
			? this.rows
			: this.rows.filter((r) => r.status === this.active_tab);

		this.body.html(`
			<div class="gtq-tabs">${tabs_html}</div>
			<div class="gtq-list">${this._table_html(filtered)}</div>
		`);

		this.body.find(".gtq-tab").on("click", (e) => {
			this.active_tab = $(e.currentTarget).data("tab");
			this.render();
		});
		this._bind_actions();
	}

	_table_html(rows) {
		if (!rows.length) {
			return `<div class="gtq-empty">
				${__("No tokens in this stage.")}
			</div>`;
		}
		return `
			<table class="gtq-table">
				<thead>
					<tr>
						<th style="width:110px">${__("Token")}</th>
						<th style="width:80px">${__("Waiting")}</th>
						<th>${__("Customer")}</th>
						<th>${__("Visit / Device")}</th>
						<th>${__("Symptoms")}</th>
						<th style="width:120px">${__("Status")}</th>
						<th style="width:220px;text-align:right">${__("Actions")}</th>
					</tr>
				</thead>
				<tbody>
					${rows.map((r) => this._row_html(r)).join("")}
				</tbody>
			</table>
		`;
	}

	_row_html(r) {
		const status_cls = "gtq-status-" + (r.status || "").replace(/\s+/g, "");
		const wait = this._format_wait(r.waiting_seconds);
		const wait_cls = r.status === "Waiting"
			? (r.waiting_seconds > 900 ? "danger" : r.waiting_seconds > 600 ? "warn" : "")
			: "";
		const device = r.device_type
			? [r.device_type, r.device_brand, r.device_model].filter(Boolean).join(" · ")
			: `<em class="gtq-muted">${__("No device")}</em>`;
		const symptoms = (r.symptoms || []).map(
			(s) => `<span class="gtq-symptom">${frappe.utils.escape_html(s)}</span>`
		).join("");
		const visit = r.visit_reason || "";
		const actions = this._actions_html(r);
		const store_label = [r.store_name, r.store_code].filter(Boolean).join(" — ") || r.store || "";
		return `
			<tr data-name="${frappe.utils.escape_html(r.name)}" data-status="${frappe.utils.escape_html(r.status)}">
				<td>
					<div class="gtq-token-num">${frappe.utils.escape_html(r.token_number || "")}</div>
					<div class="gtq-muted">${frappe.utils.escape_html(r.name)}</div>
					${this.store && this.store.warehouse === "__all__" && store_label ? `<div class="gtq-muted">${frappe.utils.escape_html(store_label)}</div>` : ""}
				</td>
				<td>
					<span class="gtq-wait ${wait_cls}">${wait}</span>
				</td>
				<td>
					<div>${frappe.utils.escape_html(r.customer_name || "")}</div>
					<div class="gtq-muted gtq-phone">${frappe.utils.escape_html(r.customer_phone || "")}</div>
				</td>
				<td>
					<div>${frappe.utils.escape_html(visit)}</div>
					<div class="gtq-muted">${device}</div>
				</td>
				<td>
					<div class="gtq-symptoms">${symptoms}</div>
					${r.additional_notes ? `<div class="gtq-muted" style="margin-top:4px">${frappe.utils.escape_html(r.additional_notes)}</div>` : ""}
				</td>
				<td>
					<span class="gtq-status-pill ${status_cls}">${frappe.utils.escape_html(r.status || "")}</span>
					${r.assigned_fde ? `<div class="gtq-muted" style="margin-top:2px">${frappe.utils.escape_html(r.assigned_fde)}</div>` : ""}
					${r.service_request ? `<div class="gtq-muted" style="margin-top:2px"><a href="/app/service-request/${encodeURIComponent(r.service_request)}">${frappe.utils.escape_html(r.service_request)}</a></div>` : ""}
				</td>
				<td class="gtq-actions">${actions}</td>
			</tr>
		`;
	}

	_actions_html(r) {
		// Build allowed actions from the state machine mirror.
		const btns = [];
		const btn = (act, label, kind) => btns.push(
			`<button class="btn btn-xs btn-${kind || "default"} gtq-act" data-act="${act}" data-name="${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(label)}</button>`
		);
		switch (r.status) {
			case "Waiting":
				btn("Called", __("Call"), "primary");
				btn("Attending", __("Attend"), "default");
				btn("Customer Left", __("Left"), "default");
				btn("Cancelled", __("Cancel"), "default");
				break;
			case "Called":
				btn("Attending", __("Attend"), "primary");
				btn("Waiting", __("Reopen"), "default");
				btn("Customer Left", __("Left"), "default");
				btn("Cancelled", __("Cancel"), "default");
				break;
			case "Attending":
				if (!r.service_request) btn("__link__", __("Job Card"), "primary");
				else btn("Job Card Created", __("Mark Handed Off"), "primary");
				btn("Completed", __("Complete"), "default");
				btn("Customer Left", __("Left"), "default");
				btn("Cancelled", __("Cancel"), "default");
				break;
			case "Job Card Created":
				btn("Completed", __("Complete"), "primary");
				btn("Cancelled", __("Cancel"), "default");
				break;
			default:
				// Terminal — no actions
				break;
		}
		return btns.join(" ");
	}

	_format_wait(secs) {
		secs = Math.max(0, Math.floor(secs || 0));
		const h = Math.floor(secs / 3600);
		const m = Math.floor((secs % 3600) / 60);
		const s = secs % 60;
		if (h) return `${h}h ${m}m`;
		if (m) return `${m}m ${s}s`;
		return `${s}s`;
	}

	// ------- Action wire-up -------------------------------------------------

	_bind_actions() {
		this.body.find(".gtq-act").on("click", (e) => {
			const $b   = $(e.currentTarget);
			const act  = $b.data("act");
			const name = $b.data("name");
			const row  = this.rows.find((r) => r.name === name);
			if (!row) return;
			if (act === "__link__") return this._open_link_dialog(row);
			if (act === "Customer Left" || act === "Cancelled") return this._open_cancel_dialog(row, act);
			this._do_transition(name, act);
		});
	}

	async _do_transition(name, to_status, extra) {
		try {
			await frappe.xcall(`${_API}.transition_token`, Object.assign({
				name, to_status,
			}, extra || {}));
			frappe.show_alert({ message: __("Token updated"), indicator: "green" });
			this.load();
		} catch (e) {
			frappe.msgprint({ title: __("Cannot update token"), message: e.message || String(e), indicator: "red" });
		}
	}

	async _open_cancel_dialog(row, to_status) {
		// Fetch reasons scoped for this transition.
		let reasons = [];
		try {
			reasons = await frappe.xcall(`${_API}.get_cancellation_reasons`, { scope: to_status });
		} catch (e) {
			reasons = [];
		}
		const options = reasons.map((r) => r.reason_name);
		if (!options.length) {
			frappe.msgprint({ title: __("No reasons configured"), message: __("Ask a System Manager to add GoFix Cancellation Reasons for this action."), indicator: "orange" });
			return;
		}

		const d = new frappe.ui.Dialog({
			title: to_status === "Cancelled" ? __("Cancel Token") : __("Mark as Customer Left"),
			fields: [
				{
					label: __("Reason"),
					fieldname: "reason",
					fieldtype: "Autocomplete",
					reqd: 1,
					options,
					description: __("Pick the closest reason. \"Other\" needs a note below."),
				},
				{
					label: __("Notes (required for Other)"),
					fieldname: "notes",
					fieldtype: "Small Text",
				},
			],
			primary_action_label: to_status === "Cancelled" ? __("Cancel Token") : __("Confirm"),
			primary_action: async (values) => {
				const reason_row = reasons.find((r) => r.reason_name === values.reason);
				if (reason_row && reason_row.requires_note && !(values.notes || "").trim()) {
					frappe.msgprint({ title: __("Note required"), message: __("Reason \"{0}\" requires a note.", [values.reason]), indicator: "orange" });
					return;
				}
				d.hide();
				await this._do_transition(row.name, to_status, {
					reason: values.reason,
					notes: values.notes || "",
				});
			},
		});
		d.show();
	}

	_open_link_dialog(row) {
		const d = new frappe.ui.Dialog({
			title: __("Job Card / Service Request"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "help",
					options: `<div class="text-muted" style="margin-bottom:8px">
						${__("Create a fresh Service Request pre-filled from this token, or link an existing one.")}
					</div>`,
				},
				{
					label: __("Existing Service Request"),
					fieldname: "service_request",
					fieldtype: "Link",
					options: "Service Request",
					description: __("Pick one only if the customer already has a Service Request open."),
				},
			],
			primary_action_label: __("Link Existing"),
			primary_action: async (values) => {
				if (!values.service_request) {
					frappe.msgprint(__("Pick a Service Request or use \"Create New\" below."));
					return;
				}
				d.hide();
				try {
					await frappe.xcall(`${_API}.link_service_request`, {
						name: row.name,
						service_request: values.service_request,
					});
					frappe.show_alert({ message: __("Service Request linked"), indicator: "green" });
					this.load();
				} catch (e) {
					frappe.msgprint({ title: __("Link failed"), message: e.message || String(e), indicator: "red" });
				}
			},
			secondary_action_label: __("Create New"),
			secondary_action: () => {
				d.hide();
				this._create_new_sr(row);
			},
		});
		d.show();
	}

	_create_new_sr(row) {
		// Navigate to Quick Intake with token pre-filled, if the page exists.
		// Fallback: open a new Service Request with query params ERPNext honours.
		const params = new URLSearchParams({
			customer_name:   row.customer_name || "",
			contact_number:  row.customer_phone || "",
			brand:           row.device_brand || "",
			issue_description: [
				(row.symptoms || []).join(", "),
				row.additional_notes || "",
			].filter(Boolean).join(" — "),
			gofix_token:     row.name,
		});
		// Frappe's route API doesn't take POST-style params, so we stash them
		// in localStorage for Quick Intake to pick up, then navigate.
		try {
			localStorage.setItem("gofix_token_prefill", JSON.stringify({
				token_name: row.name,
				token_number: row.token_number,
				customer_name: row.customer_name,
				customer_phone: row.customer_phone,
				visit_reason: row.visit_reason,
				device_type: row.device_type,
				device_brand: row.device_brand,
				device_model: row.device_model,
				symptoms: row.symptoms || [],
				additional_notes: row.additional_notes,
				at: Date.now(),
			}));
		} catch (_e) { /* private mode */ }
		// Try Quick Intake first (existing GoFix page); fall back to SR new.
		frappe.db.exists("Page", "quick-intake").then((yes) => {
			if (yes) {
				frappe.set_route("quick-intake");
			} else {
				window.location.href = "/app/service-request/new?" + params.toString();
			}
			// Follow-up: user will complete the SR in that page; when they come
			// back, the queue will show "Job Card Created" once the token has
			// been linked via link_service_request().
		});
	}
}
