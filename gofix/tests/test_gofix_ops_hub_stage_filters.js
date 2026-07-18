const assert = require("node:assert/strict");
const path = require("node:path");

global.frappe = {
	pages: {
		"gofix-ops-hub": {},
	},
};

const {
	format_assignment_hours,
	get_ops_stage_list_filters,
} = require(path.resolve(
	__dirname,
	"../gofix_services/page/gofix_ops_hub/gofix_ops_hub.js"
));

const queue = [
	{ name: "SR-REPAIR-1", decision: "In Service", ops_stage: "repair" },
	{ name: "SR-REPAIR-2", decision: "In Service", ops_stage: "repair" },
	{ name: "SR-QC-1", decision: "In Service", ops_stage: "qc" },
	{ name: "SR-INVOICE-1", decision: "Completed", ops_stage: "invoice" },
	{ name: "SR-DONE-1", decision: "Invoiced", ops_stage: "done" },
];

assert.deepEqual(get_ops_stage_list_filters(queue, "repair"), {
	name: ["in", ["SR-REPAIR-1", "SR-REPAIR-2"]],
});
assert.deepEqual(get_ops_stage_list_filters(queue, "qc"), {
	name: ["in", ["SR-QC-1"]],
});
assert.deepEqual(get_ops_stage_list_filters(queue, "invoice"), {
	name: ["in", ["SR-INVOICE-1"]],
});
assert.deepEqual(get_ops_stage_list_filters(queue, "done"), {
	name: ["in", ["SR-DONE-1"]],
});
assert.equal(get_ops_stage_list_filters(queue, "closed"), null);

assert.equal(format_assignment_hours(null), "—");
assert.equal(format_assignment_hours(0), "0h");
assert.equal(format_assignment_hours(0.25), "15m");
assert.equal(format_assignment_hours(1.05), "1h 3m");
assert.equal(format_assignment_hours(3.84), "3h 50m");

console.log("GoFix Ops Hub stage filters and assignment hours: PASS");
