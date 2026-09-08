// The queue of requests that have not become tickets yet.
//
// Two panes on purpose: the list answers "what is waiting and how long has it
// waited", the detail answers "what did this person actually say". A raw list
// view could only ever do the first, which is why the requests were invisible
// in practice even though they were being stored.

frappe.pages["service-inbox"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Service Inbox"),
		single_column: true,
	});
	page.main.html(`<div id="service-inbox-app"></div>`);
	new ServiceInbox(page);
};

const API = "gofix.gofix_services.page.service_inbox.service_inbox";

class ServiceInbox {
	constructor(page) {
		this.page = page;
		this.wrapper = page.main.find("#service-inbox-app");
		this.company = "";
		this.status = "Open";
		this.channel = "";
		this.search = "";
		this.assigned = "";
		this.selected = null;
		this.rows = [];
		this.init();
	}

	// The company pill is the same one every other screen obeys.
	active_company() {
		const lock = window.ch_erp15 && window.ch_erp15.company_lock;
		if (lock && typeof lock.active_company === "function") {
			return lock.active_company() || "";
		}
		return frappe.defaults.get_user_default("Company") || "";
	}

	async init() {
		this.company = this.active_company();
		const ctx = await frappe.xcall(`${API}.get_context_data`, { company: this.company });
		this.company = ctx.company || this.company || "";
		this.channels = ctx.channels || [];
		this.statuses = ctx.statuses || [];
		this.can_write = ctx.can_write;
		this.render_toolbar();
		this.render_layout();
		this.load();
	}

	// The filter bar is rendered here rather than through page.add_field.
	// On this bench add_field puts nothing in the DOM at all -- Store Queue has
	// the same silent problem -- so its controls would have looked present in
	// the code and been missing on the screen.
	render_toolbar() {
		this.page.set_primary_action(__("Log A Request"), () => this.log_dialog(), "add");
		this.page.add_menu_item(__("Refresh"), () => this.load());
		this.page.add_menu_item(__("Open the full list"), () =>
			frappe.set_route("List", "GoFix Service Inbox"));
	}

	render_filters() {
		const opt = (v, label, sel) =>
			`<option value="${v}" ${sel === v ? "selected" : ""}>${label}</option>`;

		this.wrapper.find("#si-filters").html(`
			<select class="form-control input-sm si-f" data-f="status">
				${opt("Open", __("Open"), this.status)}
				${this.statuses.map((s) => opt(s, __(s), this.status)).join("")}
			</select>
			<select class="form-control input-sm si-f" data-f="channel">
				${opt("", __("All Channels"), this.channel)}
				${this.channels.map((c) => opt(c, __(c), this.channel)).join("")}
			</select>
			<select class="form-control input-sm si-f" data-f="assigned">
				${opt("", __("Anyone"), this.assigned)}
				${opt("me", __("Mine"), this.assigned)}
				${opt("unassigned", __("Unassigned"), this.assigned)}
			</select>
			<input type="text" class="form-control input-sm si-f" data-f="search"
			       placeholder="${__("Phone or name")}" value="${frappe.utils.escape_html(this.search || "")}">
			<button class="btn btn-default btn-sm" id="si-refresh">${__("Refresh")}</button>
		`);

		this.wrapper.find("select.si-f").on("change", (e) => {
			this[$(e.currentTarget).data("f")] = e.currentTarget.value;
			this.load();
		});
		// Search on Enter and on blur: typing a phone number should not fire a
		// query per keystroke.
		const $s = this.wrapper.find('input.si-f[data-f="search"]');
		$s.on("keydown", (e) => { if (e.key === "Enter") { this.search = $s.val(); this.load(); } });
		$s.on("blur", () => {
			if ((this.search || "") !== $s.val()) { this.search = $s.val(); this.load(); }
		});
		this.wrapper.find("#si-refresh").on("click", () => this.load());
	}

	render_layout() {
		this.wrapper.html(`
			<div class="si-filters" id="si-filters"></div>
			<div class="si-stats" id="si-stats"></div>
			<div class="si-grid">
				<div class="si-list" id="si-list"></div>
				<div class="si-detail" id="si-detail"></div>
			</div>`);
		this.render_filters();
		this.$stats = this.wrapper.find("#si-stats");
		this.$list = this.wrapper.find("#si-list");
		this.$detail = this.wrapper.find("#si-detail");
	}

	async load() {
		this.$list.html(`<div class="si-empty">${__("Loading…")}</div>`);
		const r = await frappe.xcall(`${API}.get_requests`, {
			company: this.company, status: this.status, channel: this.channel,
			search: this.search, assigned: this.assigned,
		});
		this.rows = r.rows || [];
		this.render_stats(r.counts || {});
		this.render_list();
		if (this.selected && !this.rows.find((x) => x.name === this.selected)) {
			this.selected = null;
		}
		if (this.selected) this.open(this.selected);
		else this.$detail.html(`<div class="si-empty">${
			__("Pick a request to see what the customer told us.")}</div>`);
	}

	render_stats(counts) {
		const tile = (label, value, key, tone) => `
			<div class="si-stat ${tone || ""} ${this.status === key ? "active" : ""}"
			     data-status="${key || ""}">
				<div class="si-stat-n">${value || 0}</div>
				<div class="si-stat-l">${label}</div>
			</div>`;
		this.$stats.html([
			tile(__("Open"), counts.Open, "Open", "hot"),
			tile(__("New"), counts.New, "New"),
			tile(__("Contacted"), counts.Contacted, "Contacted"),
			tile(__("Scheduled"), counts.Scheduled, "Scheduled"),
			tile(__("Converted"), counts.Converted, "Converted", "good"),
			tile(__("Came in today"), counts.Today, ""),
		].join(""));

		this.$stats.find(".si-stat[data-status]").on("click", (e) => {
			const s = $(e.currentTarget).data("status");
			if (!s) return;
			this.status = s;
			this.wrapper.find('select.si-f[data-f="status"]').val(s);
			this.load();
		});
	}

	render_list() {
		if (!this.rows.length) {
			this.$list.html(`<div class="si-empty">${
				__("Nothing waiting. Requests from the website, the app, WhatsApp or a phone call land here.")
			}</div>`);
			return;
		}
		const esc = frappe.utils.escape_html;
		this.$list.html(this.rows.map((r) => {
			const age = r.age_hours >= 24
				? __("{0}d", [Math.floor(r.age_hours / 24)])
				: __("{0}h", [Math.round(r.age_hours)]);
			const stale = r.awaiting_response && r.age_hours > 4;
			const bits = [r.device_brand, r.device_model, r.issue_category]
				.filter(Boolean).join(" · ");
			return `
			<div class="si-card ${this.selected === r.name ? "sel" : ""} ${stale ? "stale" : ""}"
			     data-name="${r.name}">
				<div class="si-card-top">
					<span class="si-chan si-chan-${(r.channel || "").replace(/\s/g, "")}">${
						esc(r.channel || "")}</span>
					<span class="si-age" title="${__("Waiting since it arrived")}">${age}</span>
				</div>
				<div class="si-who">${esc(r.customer_name || __("Unknown caller"))}
					<span class="si-phone">${esc(r.contact_number || "")}</span></div>
				${bits ? `<div class="si-bits">${esc(bits)}</div>` : ""}
				${r.issue_description ? `<div class="si-said">"${
					esc(r.issue_description.slice(0, 110))}"</div>` : ""}
				<div class="si-card-foot">
					<span class="si-status si-status-${r.status}">${esc(r.status)}</span>
					${r.assigned_to ? `<span class="si-own">${esc(r.assigned_to)}</span>` : ""}
					${r.service_request ? `<span class="si-conv">→ ${esc(r.service_request)}</span>` : ""}
				</div>
			</div>`;
		}).join(""));

		this.$list.find(".si-card").on("click", (e) =>
			this.open($(e.currentTarget).data("name")));
	}

	async open(name) {
		this.selected = name;
		this.$list.find(".si-card").removeClass("sel");
		this.$list.find(`.si-card[data-name="${name}"]`).addClass("sel");
		this.$detail.html(`<div class="si-empty">${__("Loading…")}</div>`);

		const d = await frappe.xcall(`${API}.get_request`, { name });
		const esc = frappe.utils.escape_html;
		const row = (label, value) => value
			? `<tr><th>${label}</th><td>${esc(String(value))}</td></tr>` : "";

		// Everything the customer gave us, in the order they gave it. This is
		// the half a list view can never show.
		const said = `
			<table class="si-table">
				${row(__("Channel"), d.channel)}
				${row(__("Received"), d.received_at)}
				${row(__("Name"), d.customer_name)}
				${row(__("Phone"), d.contact_number)}
				${row(__("Alternate"), d.alternate_number)}
				${row(__("Email"), d.email)}
				${row(__("Known customer"), d.customer)}
				${row(__("Category"), d.device_category)}
				${row(__("Brand"), d.device_brand)}
				${row(__("Model"), d.device_model)}
				${row(__("IMEI / Serial"), d.serial_no)}
				${row(__("Issue"), d.issue_category)}
				${row(__("Preferred store"), d.preferred_store)}
				${row(__("Preferred slot"), d.preferred_datetime)}
				${row(__("Heard about us via"), d.referral_source)}
				${row(__("City"), d.city)}
				${row(__("Channel reference"), d.external_ref)}
			</table>`;

		const notes = (d.notes || []).length
			? `<ul class="si-notes">${d.notes.map((n) => `
				<li><span class="si-note-meta">${esc(n.note_datetime.slice(0, 16))} ·
					${esc(n.noted_by || "")}</span>${esc(n.note)}</li>`).join("")}</ul>`
			: `<p class="text-muted">${__("Nothing recorded yet.")}</p>`;

		const history = (d.repairs || []).length
			? `<ul class="si-hist">${d.repairs.map((r) => `
				<li><a href="/app/service-request/${r.name}" target="_blank">${r.name}</a>
					<span class="text-muted">${esc(r.decision || "")}${
						r.device_model ? " · " + esc(r.device_model) : ""}</span></li>`).join("")}</ul>`
			: `<p class="text-muted">${__("No repairs on this number yet.")}</p>`;

		const others = (d.other_requests || []).length
			? `<p class="si-others">${__("Also contacted us:")} ${d.other_requests.map((o) =>
				`<b>${esc(o.channel)}</b> (${esc(o.status)})`).join(", ")}</p>` : "";

		this.$detail.html(`
			<div class="si-head">
				<div>
					<h4>${esc(d.customer_name || __("Unknown caller"))}
						<span class="si-status si-status-${d.status}">${esc(d.status)}</span></h4>
					<div class="text-muted">${esc(d.name)} · ${esc(d.contact_number || "")}</div>
				</div>
				<div class="si-actions" id="si-actions"></div>
			</div>
			${others}
			${d.issue_description ? `<div class="si-quote">"${esc(d.issue_description)}"</div>` : ""}
			<h6>${__("What the customer told us")}</h6>
			${said}
			<h6>${__("Conversation")}</h6>
			${notes}
			<h6>${__("This number's repairs")}</h6>
			${history}
			${d.service_request ? `<div class="si-converted">${
				__("Booked in as")} <a href="/app/service-request/${d.service_request}"
				target="_blank">${esc(d.service_request)}</a></div>` : ""}
		`);
		this.render_actions(d);
	}

	render_actions(d) {
		const $a = this.$detail.find("#si-actions");
		if (!this.can_write) return;

		const btn = (label, cls, fn) => $(
			`<button class="btn btn-xs ${cls}">${label}</button>`).on("click", fn).appendTo($a);

		btn(__("Add Note"), "btn-default", () => this.note_dialog(d));

		if (!["Converted", "Closed", "Spam", "Duplicate"].includes(d.status)) {
			btn(__("Schedule"), "btn-default", () => this.schedule_dialog(d));
			// The device is not here yet, so this hands the counter a prefilled
			// intake rather than pretending a ticket already exists.
			btn(__("Book The Device In"), "btn-primary", () => this.convert(d));
			btn(__("Close"), "btn-default", () => this.close_dialog(d));
		}
		if (!d.assigned_to || d.assigned_to !== frappe.session.user) {
			btn(__("Take It"), "btn-default", () => this.take(d));
		}
	}

	note_dialog(d) {
		const dl = new frappe.ui.Dialog({
			title: __("Record What Was Said"),
			fields: [
				{ fieldname: "note", fieldtype: "Small Text", label: __("Note"), reqd: 1 },
				{ fieldname: "channel", fieldtype: "Select", label: __("Via"),
				  options: this.channels.join("\n"), default: d.channel },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				frappe.xcall(`${API}.add_note`, {
					name: d.name, note: v.note, channel: v.channel,
				}).then(() => { dl.hide(); this.load(); });
			},
		});
		dl.show();
	}

	schedule_dialog(d) {
		const dl = new frappe.ui.Dialog({
			title: __("Agree A Time"),
			fields: [
				{ fieldname: "when", fieldtype: "Datetime", label: __("When"), reqd: 1,
				  default: d.preferred_datetime },
				{ fieldname: "note", fieldtype: "Small Text", label: __("Note") },
			],
			primary_action_label: __("Schedule"),
			primary_action: (v) => {
				frappe.xcall(`${API}.schedule`, {
					name: d.name, when: v.when, note: v.note,
				}).then(() => { dl.hide(); this.load(); });
			},
		});
		dl.show();
	}

	close_dialog(d) {
		const dl = new frappe.ui.Dialog({
			title: __("Close This Request"),
			fields: [
				{ fieldname: "status", fieldtype: "Select", label: __("Outcome"),
				  options: "Closed\nSpam\nDuplicate", default: "Closed", reqd: 1 },
				{ fieldname: "reason", fieldtype: "Small Text", reqd: 1,
				  label: __("Why"),
				  description: __("Not every request becomes a repair, and saying why is what keeps the inbox worth reading.") },
			],
			primary_action_label: __("Close"),
			primary_action: (v) => {
				frappe.xcall("gofix.gofix_services.inbox.set_status", {
					inbox: d.name, status: v.status, reason: v.reason,
				}).then(() => { dl.hide(); this.selected = null; this.load(); });
			},
		});
		dl.show();
	}

	take(d) {
		frappe.xcall(`${API}.assign`, { name: d.name }).then((r) => {
			frappe.show_alert({ message: __("Assigned to you"), indicator: "green" });
			this.load();
		});
	}

	convert(d) {
		// The counter books the device in; this screen only hands over what the
		// customer already said so nobody has to ask twice.
		frappe.confirm(
			__("Open the counter intake for {0} with their details filled in?<br><br><span class='text-muted'>The request stays open until the device is actually booked in.</span>",
				[frappe.utils.escape_html(d.customer_name || d.contact_number)]),
			() => {
				// The counter asks for a store before it shows the intake, so
				// the hand-off has to survive that step. route_options do not
				// reliably outlive an intermediate screen; sessionStorage does,
				// and is scoped to this tab so it cannot leak into someone
				// else's till.
				const payload = { request: d.name, phone: d.contact_number };
				try {
					sessionStorage.setItem("gofix_inbox_handoff", JSON.stringify(payload));
				} catch (e) { /* private mode: route_options still carry it */ }
				frappe.route_options = { inbox_request: d.name, phone: d.contact_number };
				frappe.set_route("ch-pos-app", "repair");
			});
	}

	log_dialog() {
		// Somebody rings the shop: this is where that call gets recorded.
		const dl = new frappe.ui.Dialog({
			title: __("Log A Request"),
			fields: [
				{ fieldname: "channel", fieldtype: "Select", label: __("Channel"),
				  options: this.channels.join("\n"), default: "Phone Call", reqd: 1 },
				{ fieldname: "contact_number", fieldtype: "Data", reqd: 1,
				  label: __("Contact Number"),
				  description: __("The number is how we recognise them when they walk in.") },
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Name") },
				{ fieldtype: "Column Break" },
				{ fieldname: "device_brand", fieldtype: "Link", options: "Brand",
				  label: __("Brand") },
				{ fieldname: "issue_category", fieldtype: "Link", options: "Issue Category",
				  label: __("Issue") },
				{ fieldname: "preferred_datetime", fieldtype: "Datetime",
				  label: __("Preferred Slot") },
				{ fieldtype: "Section Break" },
				{ fieldname: "issue_description", fieldtype: "Small Text",
				  label: __("What did they say?") },
			],
			primary_action_label: __("Log It"),
			primary_action: (v) => {
				frappe.xcall("gofix.gofix_services.inbox.push_request", {
					...v, company: this.company,
				}).then((r) => {
					dl.hide();
					frappe.show_alert({ message: r.message, indicator: "green" });
					this.selected = r.name;
					this.load();
				});
			},
		});
		dl.show();
	}
}
