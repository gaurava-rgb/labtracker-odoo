/** @odoo-module **/
// Odoo 20 ships Owl 3: state is a `proxy`, props are declared with `useProps`, templates use `this.`
import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const TILES = [
    { key: "open", label: "Open requests", model: "project.task" },
    { key: "unassigned", label: "Unassigned", model: "project.task" },
    { key: "overdue", label: "Overdue", model: "project.task" },
    { key: "blocked", label: "Blocked", model: "project.task" },
    { key: "equipment_down", label: "Equipment down", model: "maintenance.equipment" },
];

export class LabHome extends Component {
    static template = "lab_dashboard.LabHome";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.tiles = TILES;
        this.state = proxy({ data: null, error: "", loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            this.state.data = await this.orm.call("lab.dashboard", "get_data", []);
        } catch (e) {
            this.state.error = (e.data && e.data.message) || e.message || "Could not load the dashboard.";
        }
        this.state.loading = false;
    }

    count(key) {
        return this.state.data ? this.state.data.counts[key] : "";
    }

    openTile(tile) {
        const d = this.state.data;
        if (!d) {
            return;
        }
        const ctx = tile.model === "project.task" ? { default_project_id: d.project_id } : {};
        this.action.doAction({
            type: "ir.actions.act_window",
            name: tile.label,
            res_model: tile.model,
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain: d.domains[tile.key],
            context: ctx,
            target: "current",
        });
    }

    openList(key, label) {
        this.openTile({ key, label, model: "project.task" });
    }

    openRecord(model, id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    conditionLabel(c) {
        return { available: "Available", under_repair: "Under repair", out_of_service: "Out of service" }[c] || c || "Unknown";
    }
}

registry.category("actions").add("lab_dashboard.home", LabHome);
