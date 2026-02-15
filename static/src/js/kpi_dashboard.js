/** @odoo-module **/

import { Component, useState, onWillUpdateProps, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

class SaleKpiDashboard extends Component {
    static template = "sale_order_kpi_dashboard.SaleKpiDashboard";
    static props = { ...standardFieldProps };

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.user = useService("user");

        const raw = this.props.record.data[this.props.name];
        const data = this._parse(raw);

        this.state = useState({
            ...data,
            hasMarginAccess: false,
            loaded: false,
        });

        onWillStart(async () => {
            try {
                const hasGroup = await this.user.hasGroup(
                    "sale_order_kpi_dashboard.group_margin_viewer"
                );
                this.state.hasMarginAccess = hasGroup;
            } catch (e) {
                console.warn("KPI Dashboard: margin group check failed", e);
                this.state.hasMarginAccess = false;
            }
            this.state.loaded = true;
        });

        onWillUpdateProps((next) => {
            const d = this._parse(next.record.data[next.name]);
            Object.assign(this.state, d);
        });
    }

    _defaults() {
        return {
            margin: {
                gross_margin: 0,
                margin_pct: 0,
                return_margin_impact: 0,
                margin_per_sqm: 0,
                net_revenue: 0,
                net_cost: 0,
            },
            payment: {
                dso: 0,
                total_paid: 0,
                amount_pending: 0,
                amount_total: 0,
                payments: [],
            },
            client: {
                client_exposure: 0,
                credit_risk_score: 100,
                credit_risk_label: "N/A",
                credit_risk_color: "#9CA3AF",
                credit_risk_details: [],
                partner_name: "",
            },
            logistics: {
                lead_time_days: 0,
                deviation_days: 0,
                overall_fulfillment: 0,
                total_ordered: 0,
                total_delivered: 0,
            },
            returns: {
                total_returned_qty: 0,
                total_returned_revenue: 0,
            },
            inventory: {
                fragmentation_index: 0,
            },
            projections: {
                projected_collection_date: "-",
                projected_collection_days: 0,
            },
            order_health_score: 0,
            currency: "$",
        };
    }

    _parse(value) {
        const empty = this._defaults();
        try {
            if (!value || value === "false" || value === false) {
                return empty;
            }
            let parsed = value;
            if (typeof value === "string") {
                parsed = JSON.parse(value);
            }
            const result = {};
            for (const key of Object.keys(empty)) {
                if (
                    typeof empty[key] === "object" &&
                    empty[key] !== null &&
                    !Array.isArray(empty[key])
                ) {
                    result[key] = Object.assign({}, empty[key], parsed[key] || {});
                } else {
                    result[key] =
                        parsed[key] !== undefined ? parsed[key] : empty[key];
                }
            }
            return result;
        } catch (e) {
            console.error("KPI Dashboard parse error:", e);
            return empty;
        }
    }

    // ── Formatters ─────────────────────────────────────────
    fmt(val) {
        if (val === undefined || val === null) return "0.00";
        return parseFloat(val).toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    fmtInt(val) {
        if (val === undefined || val === null) return "0";
        return Math.round(parseFloat(val)).toLocaleString("es-MX");
    }

    fmtPct(val) {
        if (val === undefined || val === null) return "0.0";
        return parseFloat(val).toFixed(1);
    }

    fmtQty(val) {
        if (val === undefined || val === null) return "0";
        const n = parseFloat(val);
        if (n === Math.floor(n)) return n.toLocaleString("es-MX");
        return n.toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    // ── Health Score ───────────────────────────────────────
    getHealthClass(score) {
        if (score >= 80) return "kpi-health-excellent";
        if (score >= 60) return "kpi-health-good";
        if (score >= 40) return "kpi-health-warning";
        return "kpi-health-danger";
    }

    getHealthLabel(score) {
        if (score >= 80) return "Excelente";
        if (score >= 60) return "Buena";
        if (score >= 40) return "Atención";
        return "Crítica";
    }

    getHealthColor(score) {
        if (score >= 80) return "#10B981";
        if (score >= 60) return "#3B82F6";
        if (score >= 40) return "#F59E0B";
        return "#EF4444";
    }

    getGaugeCircumference() {
        return 2 * Math.PI * 27;
    }

    getGaugeDashoffset(score) {
        var c = 2 * Math.PI * 27;
        return c - (score / 100) * c;
    }

    // ── Fulfillment ────────────────────────────────────────
    getFulfillmentFill(pct) {
        if (pct >= 100) return "kpi-fill-green";
        if (pct > 50) return "kpi-fill-blue";
        if (pct > 0) return "kpi-fill-amber";
        return "kpi-fill-red";
    }

    getFulfillmentWidth() {
        return Math.min(this.state.logistics.overall_fulfillment, 100);
    }

    // ── Deviation ──────────────────────────────────────────
    getDeviationClass(days) {
        if (days > 0) return "kpi-deviation-positive";
        if (days < 0) return "kpi-deviation-negative";
        return "kpi-deviation-zero";
    }

    getDeviationText(days) {
        if (days > 0) return "+" + days + " días tarde";
        if (days < 0) return Math.abs(days) + " días antes";
        return "En tiempo";
    }

    isDeviationLate() {
        return this.state.logistics.deviation_days > 0;
    }

    // ── DSO ────────────────────────────────────────────────
    getDsoAccent() {
        var d = this.state.payment.dso;
        if (d <= 30) return "kpi-accent-green";
        if (d <= 60) return "kpi-accent-amber";
        return "kpi-accent-red";
    }

    getDsoIcon() {
        var d = this.state.payment.dso;
        if (d <= 30) return "kpi-icon-green";
        if (d <= 60) return "kpi-icon-amber";
        return "kpi-icon-red";
    }

    // ── Payment ────────────────────────────────────────────
    getPaymentBarWidth() {
        var t = this.state.payment.amount_total;
        if (t <= 0) return 0;
        return Math.min((this.state.payment.total_paid / t) * 100, 100);
    }

    hasPending() {
        return this.state.payment.amount_pending > 0;
    }

    // ── Margin ─────────────────────────────────────────────
    getMarginAccent() {
        var p = this.state.margin.margin_pct;
        if (p >= 20) return "kpi-accent-green";
        if (p >= 10) return "kpi-accent-blue";
        if (p >= 0) return "kpi-accent-amber";
        return "kpi-accent-red";
    }

    getMarginIcon() {
        var p = this.state.margin.margin_pct;
        if (p >= 20) return "kpi-icon-green";
        if (p >= 10) return "kpi-icon-blue";
        if (p >= 0) return "kpi-icon-amber";
        return "kpi-icon-red";
    }

    getMarginFill() {
        var p = this.state.margin.margin_pct;
        if (p >= 20) return "kpi-fill-green";
        if (p >= 10) return "kpi-fill-blue";
        if (p >= 0) return "kpi-fill-amber";
        return "kpi-fill-red";
    }

    getMarginBarWidth() {
        return Math.max(0, Math.min(this.state.margin.margin_pct, 50)) * 2;
    }

    isMarginNegative() {
        return this.state.margin.gross_margin < 0;
    }

    hasReturnImpact() {
        return this.state.margin.return_margin_impact > 0;
    }

    absReturnImpact() {
        return Math.abs(this.state.margin.return_margin_impact);
    }

    // ── Fragmentation ──────────────────────────────────────
    isFragHigh() {
        return this.state.inventory.fragmentation_index > 15;
    }

    // ── Risk ───────────────────────────────────────────────
    hasRiskDetails() {
        return (
            this.state.client.credit_risk_details &&
            this.state.client.credit_risk_details.length > 0
        );
    }

    // ── Navigation ─────────────────────────────────────────
    async openPayment(id) {
        await this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "account.payment",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("fields").add("sale_kpi_dashboard", {
    component: SaleKpiDashboard,
});