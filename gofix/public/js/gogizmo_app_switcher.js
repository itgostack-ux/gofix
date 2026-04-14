/**
 * GoGizmo App Switcher — Role-based persistent navigation bar
 *
 * Shows only apps/dashboards the current user has permission to access.
 * Appears on all custom GoGizmo pages as a compact top bar.
 */
(function () {
	"use strict";

	// ── App Registry ─────────────────────────────────────────────────────
	// Each entry maps to a Page doctype. The switcher fetches allowed_pages
	// from the server so only pages the user can access are shown.
	const APP_REGISTRY = [
		// group: operations
		{ key: "ch-pos-app",        label: "POS",              icon: "fa-shopping-cart",  group: "operations", color: "#3b82f6" },
		{ key: "gofix-ops-hub",     label: "GoFix",            icon: "fa-wrench",         group: "operations", color: "#10b981" },
		{ key: "operations-hub",    label: "Ops Hub",          icon: "fa-cogs",           group: "operations", color: "#f59e0b" },
		{ key: "purchase-hub",      label: "Purchase",         icon: "fa-shopping-bag",   group: "operations", color: "#4f46e5" },
		{ key: "stock-hub",         label: "Stock Hub",        icon: "fa-cubes",          group: "operations", color: "#059669" },
		{ key: "logistics-hub",     label: "Logistics",        icon: "fa-map-signs",      group: "operations", color: "#d97706" },
		{ key: "scheme-hub",         label: "Schemes",           icon: "fa-tags",           group: "operations", color: "#0891b2" },
		// group: dashboards
		{ key: "ceo-command-center",         label: "CEO",              icon: "fa-line-chart",     group: "dashboards", color: "#8b5cf6" },
		{ key: "finance-dashboard",          label: "Finance",          icon: "fa-inr",            group: "dashboards", color: "#059669" },
		{ key: "operations-dashboard",       label: "Operations",       icon: "fa-truck",          group: "dashboards", color: "#d97706" },
		{ key: "compliance-dashboard",       label: "Compliance",       icon: "fa-shield",         group: "dashboards", color: "#dc2626" },
		{ key: "store-manager-dashboard",    label: "Store Mgr",       icon: "fa-building",       group: "dashboards", color: "#0891b2" },
		{ key: "category-manager-dashboard", label: "Category Mgr",    icon: "fa-tags",           group: "dashboards", color: "#7c3aed" },
		// group: tools
		{ key: "ch-customer-dashboard",      label: "Customer 360",    icon: "fa-user-circle",    group: "tools",      color: "#0d9488" },
	];

	// Pages where the switcher should appear (all custom GoGizmo pages)
	const SWITCHER_PAGES = new Set(APP_REGISTRY.map(a => a.key));

	// Workspace slugs where the switcher should also appear
	const SWITCHER_WORKSPACES = new Set([
		"buyback", "gofix", "gogizmo-erp", "ch-core", "ch-item-master", "ch-vas",
		"masters", "services",
	]);

	let _allowed_pages = null; // cached after first fetch
	let _bar_el = null;

	// ── Init on route change ─────────────────────────────────────────────
	$(document).on("page-change", function () {
		setTimeout(_maybe_show_switcher, 100);
	});

	// Also init on first load
	frappe.after_ajax(function () {
		setTimeout(_maybe_show_switcher, 300);
	});

	function _get_current_page() {
		// Frappe v16: route is like ["gofix-ops-hub"] or ["Form", "Sales Invoice", ...]
		const route = frappe.get_route();
		return route && route.length === 1 ? route[0] : null;
	}

	function _is_gogizmo_page(page_name) {
		if (!page_name) return false;
		if (SWITCHER_PAGES.has(page_name)) return true;
		if (SWITCHER_WORKSPACES.has(page_name)) return true;
		return false;
	}

	function _maybe_show_switcher() {
		const page_name = _get_current_page();

		if (!_is_gogizmo_page(page_name)) {
			// Not on a GoGizmo page — hide switcher if visible
			if (_bar_el) _bar_el.addClass("gg-switcher-hidden");
			$("body").removeClass("gg-switcher-active");
			return;
		}

		if (_allowed_pages) {
			_render(page_name);
		} else {
			_fetch_allowed_pages().then(() => _render(page_name));
		}
	}

	function _fetch_allowed_pages() {
		return frappe.xcall(
			"gofix.api.app_switcher.get_allowed_pages"
		).then(function (pages) {
			_allowed_pages = new Set(pages || []);
		}).catch(function () {
			// Fallback: show nothing rather than break
			_allowed_pages = new Set();
		});
	}

	function _render(current_page) {
		if (!_allowed_pages || _allowed_pages.size === 0) return;

		const visible_apps = APP_REGISTRY.filter(a => _allowed_pages.has(a.key));
		if (visible_apps.length <= 1) return; // No point showing switcher for 1 app

		// Group apps
		const groups = {};
		visible_apps.forEach(a => {
			if (!groups[a.group]) groups[a.group] = [];
			groups[a.group].push(a);
		});

		const group_order = ["operations", "dashboards", "tools"];
		const group_labels = { operations: "Apps", dashboards: "Dashboards", tools: "Tools" };

		let pills_html = "";
		group_order.forEach(g => {
			if (!groups[g] || !groups[g].length) return;
			if (pills_html) {
				pills_html += '<span class="gg-sw-divider"></span>';
			}
			groups[g].forEach(app => {
				const active = app.key === current_page ? "gg-sw-active" : "";
				const style = app.key === current_page ? `background:${app.color};border-color:${app.color};color:#fff;` : "";
				pills_html += `<a class="gg-sw-pill ${active}" href="/desk/${app.key}" style="${style}" title="${app.label}">
					<i class="fa ${app.icon}"></i>
					<span class="gg-sw-pill-label">${app.label}</span>
				</a>`;
			});
		});

		if (!_bar_el) {
			_bar_el = $(`<div class="gg-app-switcher" id="gg-app-switcher"></div>`);
			$("body").append(_bar_el);
		}

		_bar_el.html(`
			<div class="gg-sw-inner">
				<span class="gg-sw-brand"><i class="fa fa-th"></i></span>
				${pills_html}
			</div>
		`);
		_bar_el.removeClass("gg-switcher-hidden");
		$("body").addClass("gg-switcher-active");

		// Navigate via frappe.set_route to avoid full reload
		_bar_el.find(".gg-sw-pill").on("click", function (e) {
			e.preventDefault();
			const href = $(this).attr("href");
			if (href) {
				const page_key = href.replace("/desk/", "");
				frappe.set_route(page_key);
			}
		});
	}
})();
