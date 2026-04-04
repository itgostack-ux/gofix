app_name = "gofix"
app_title = "GoFix"
app_publisher = "GoStack"
app_description = "Repairs & Services"
app_email = "contact@gostack.in"
app_license = "custom"
required_apps = ["frappe/erpnext"]

# Fixtures
fixtures = [
    {
        "dt": "Role",
        "filters": [
            ["name", "in", ["Service Manager", "Service Engineer", "Service Viewer"]]
        ]
    },
    {
        "dt": "Workflow State",
        "filters": [
            ["name", "in", [
                "Draft", "Submitted", "Work in Progress",
                "QC Awaiting", "QC Pass", "QC Fail",
                "Not Repairable", "Customer Cancelled", "Closed"
            ]]
        ]
    },
    {
        "dt": "Workflow Action Master",
        "filters": [
            ["name", "in", [
                "Submit", "Start Work", "Complete Job",
                "Mark Not Repairable", "Customer Cancelled",
                "QC Pass", "QC Fail", "Rework", "Close"
            ]]
        ]
    },
    {
        "dt": "Workflow",
        "filters": [
            ["name", "in", ["Service Order Workflow"]]
        ]
    },
]

# Apps
# ------------------

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "gofix",
		"logo": "/assets/gofix/icon.svg",
		"title": "GoFix",
		"route": "/app/gofix-services",
		"has_permission": "gofix.gofix.utils.has_app_permission"
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/gofix/css/gofix.css"
# app_include_js = "/assets/gofix/js/gofix.js"

# include js, css files in header of web template
# web_include_css = "/assets/gofix/css/gofix.css"
# web_include_js = "/assets/gofix/js/gofix.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "gofix/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {
#     "Company": "public/js/warehouse_quick_setup.js",
#     "Warehouse": "public/js/warehouse_quick_setup.js",
#     "User": "public/js/warehouse_quick_setup.js"
# }
doctype_list_js = {
    "Sales Order": "public/js/sales_order_list.js"
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "gofix/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "gofix.utils.jinja_methods",
# 	"filters": "gofix.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "gofix.install.before_install"
after_install = "gofix.setup.install.after_install"
after_migrate = [
	"gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields",
    "gofix.setup.notifications.create_notifications",
    "gofix.setup.workflow.ensure_service_order_workflow",
    "gofix.setup.employee_custom_fields.create_employee_custom_fields",
]

# Uninstallation
# ------------

# before_uninstall = "gofix.uninstall.before_uninstall"
# after_uninstall = "gofix.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "gofix.utils.before_app_install"
# after_app_install = "gofix.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "gofix.utils.before_app_uninstall"
# after_app_uninstall = "gofix.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "gofix.notifications.get_notification_config"

# Permissions
# -----------
# Company-wise visibility for service data
permission_query_conditions = {
	"Service Request": "gofix.security.get_service_request_query",
}

has_permission = {
	"Service Request": "gofix.security.has_service_request_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Sales Order": "gofix.overrides.sales_order.CustomSalesOrder"
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Sales Order": {
		"validate": "gofix.overrides.sales_order.validate_service_order_before_submit",
		"on_update": "gofix.overrides.sales_order.update_service_request_on_qc",
        "on_update_after_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_cancel": "gofix.overrides.sales_order.update_service_request_on_qc"
	},
	"Sales Invoice": {
		"on_submit": "gofix.overrides.sales_invoice.update_service_request_on_invoice",
		"on_cancel": "gofix.overrides.sales_invoice.update_service_request_on_invoice"
	},
	"Delivery Note": {
		"on_submit": "gofix.overrides.delivery_note.update_service_request_on_delivery",
		"on_cancel": "gofix.overrides.delivery_note.update_service_request_on_delivery"
	},
	"Service Request": {
		"on_update": "gofix.gofix_services.whatsapp_notifications.on_service_request_update",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"gofix.gofix_services.doctype.service_request.service_request.flag_unclaimed_devices",
		"gofix.gofix_services.api.expire_pending_estimates",
	],
	"cron": {
		"*/15 * * * *": [
			"gofix.gofix_services.doctype.gofix_sla_rule.gofix_sla_rule.check_gofix_sla_breach"
		]
	},
}

# Testing
# -------

# before_tests = "gofix.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "gofix.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "gofix.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["gofix.utils.before_request"]
# after_request = ["gofix.utils.after_request"]

# Job Events
# ----------
# before_job = ["gofix.utils.before_job"]
# after_job = ["gofix.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"gofix.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

