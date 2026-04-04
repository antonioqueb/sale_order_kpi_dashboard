## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
{
    'name': 'Sale Order KPI Dashboard (Odoo 19)',
    'version': '19.0.3.0.0',
    'category': 'Sales',
    'summary': 'Strategic KPIs: Margin, Credit Risk, DSO, Lead Time, Lot Fragmentation, Order Health Score',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'depends': ['sale', 'sale_management', 'stock', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/sale_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_order_kpi_dashboard/static/src/css/kpi_dashboard.css',
            'sale_order_kpi_dashboard/static/src/js/kpi_dashboard.js',
            'sale_order_kpi_dashboard/static/src/xml/kpi_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}```

## ./models/__init__.py
```py
from . import sale_order
from . import sale_order_line```

## ./models/sale_order.py
```py
from odoo import models, fields, api
from datetime import timedelta
import json


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    kpi_dashboard_data = fields.Text(
        string='KPI Dashboard Data',
        compute='_compute_kpi_dashboard_data',
    )

    kpi_order_health_score = fields.Float(
        string='Order Health Score',
        compute='_compute_kpi_dashboard_data',
    )
    kpi_amount_pending = fields.Monetary(
        string='Pendiente por Pagar',
        compute='_compute_kpi_dashboard_data',
        currency_field='currency_id',
    )

    @api.depends(
        'order_line.qty_delivered',
        'order_line.product_uom_qty',
        'order_line.move_ids',
        'order_line.move_ids.state',
        'order_line.price_unit',
        'order_line.kpi_cost_price',
        'invoice_ids',
        'invoice_ids.state',
        'invoice_ids.amount_residual',
        'invoice_ids.payment_state',
        'invoice_ids.line_ids.matched_credit_ids',
        'amount_total',
        'date_order',
        'commitment_date',
        'partner_id',
    )
    def _compute_kpi_dashboard_data(self):
        for order in self:
            data = order._build_kpi_data()
            order.kpi_dashboard_data = json.dumps(data)
            order.kpi_order_health_score = data.get('order_health_score', 0)
            order.kpi_amount_pending = data.get('payment', {}).get('amount_pending', 0)

    # ──────────────────────────────────────────────────────────────
    #  Currency conversion helper
    # ──────────────────────────────────────────────────────────────
    def _to_mxn(self, amount, from_currency, company, date=None):
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        if not mxn or from_currency == mxn:
            return amount
        return from_currency._convert(
            amount, mxn, company, date or fields.Date.today()
        )

    # ──────────────────────────────────────────────────────────────
    def _build_kpi_data(self):
        self.ensure_one()
        order = self

        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        target_currency = mxn or order.currency_id
        order_currency = order.currency_id
        company = order.company_id
        convert_date = (order.date_order and order.date_order.date()) or fields.Date.today()

        total_revenue = 0.0
        total_cost = 0.0
        total_returned_qty = 0.0
        total_returned_cost = 0.0
        total_returned_revenue = 0.0
        total_ordered_qty = 0.0
        total_delivered_qty = 0.0
        total_sqm_ordered = 0.0

        for line in order.order_line.filtered(lambda l: not l.display_type):
            qty_ordered = line.product_uom_qty
            qty_delivered = line.qty_delivered
            price = line.price_unit
            cost = line.kpi_cost_price or 0.0

            revenue_line = qty_delivered * price
            cost_line = qty_delivered * cost

            returned_qty = 0.0
            out_moves = line.move_ids.filtered(
                lambda m: m.state == 'done' and m.picking_code == 'outgoing'
            )
            if out_moves:
                returns = self.env['stock.move'].sudo().search([
                    ('origin_returned_move_id', 'in', out_moves.ids),
                    ('state', '=', 'done'),
                ])
                returned_qty = sum(returns.mapped('product_uom_qty'))

            total_revenue += revenue_line
            total_cost += cost_line
            total_returned_qty += returned_qty
            total_returned_revenue += returned_qty * price
            total_returned_cost += returned_qty * cost
            total_ordered_qty += qty_ordered
            total_delivered_qty += qty_delivered
            total_sqm_ordered += qty_ordered

        total_revenue = self._to_mxn(total_revenue, order_currency, company, convert_date)
        total_cost = self._to_mxn(total_cost, order_currency, company, convert_date)
        total_returned_revenue = self._to_mxn(total_returned_revenue, order_currency, company, convert_date)
        total_returned_cost = self._to_mxn(total_returned_cost, order_currency, company, convert_date)
        amount_total_mxn = self._to_mxn(order.amount_total, order_currency, company, convert_date)

        net_revenue = total_revenue - total_returned_revenue
        net_cost = total_cost - total_returned_cost
        gross_margin = net_revenue - net_cost
        margin_pct = (gross_margin / net_revenue * 100) if net_revenue else 0.0
        return_margin_impact = total_returned_revenue - total_returned_cost
        margin_per_sqm = (gross_margin / total_sqm_ordered) if total_sqm_ordered else 0.0

        # ── Payment metrics ───────────────────────────────────────
        invoices = order.invoice_ids.filtered(lambda inv: inv.state == 'posted' and inv.move_type == 'out_invoice')
        payment_ids_seen = set()
        payments_data = []
        total_paid = 0.0
        invoice_dates = []
        payment_dates = []

        for invoice in invoices:
            if invoice.invoice_date:
                invoice_dates.append(invoice.invoice_date)
            reconciled_partials = invoice.sudo().line_ids.mapped('matched_credit_ids')
            for partial in reconciled_partials:
                pay = partial.credit_move_id.payment_id
                if pay and pay.id not in payment_ids_seen:
                    payment_ids_seen.add(pay.id)
                    if pay.date:
                        payment_dates.append(pay.date)
                    pay_amount_mxn = self._to_mxn(
                        pay.amount, pay.currency_id, pay.company_id, pay.date
                    )
                    payments_data.append({
                        'id': pay.id,
                        'date': pay.date.strftime('%d/%m/%Y') if pay.date else '',
                        'name': pay.name or '',
                        'journal': pay.journal_id.name or '',
                        'amount': round(pay_amount_mxn, 2),
                        'currency': target_currency.symbol or '$',
                    })
                if pay:
                    partial_mxn = self._to_mxn(
                        partial.amount, company.currency_id, company, pay.date or fields.Date.today()
                    )
                    total_paid += partial_mxn

        amount_pending = amount_total_mxn - total_paid

        # ── DSO ───────────────────────────────────────────────────
        dso = 0.0
        if invoice_dates and payment_dates:
            dso_days = []
            for invoice in invoices:
                if not invoice.invoice_date:
                    continue
                for partial in invoice.sudo().line_ids.mapped('matched_credit_ids'):
                    pay = partial.credit_move_id.payment_id
                    if pay and pay.date:
                        delta = (pay.date - invoice.invoice_date).days
                        dso_days.append(max(delta, 0))
            dso = sum(dso_days) / len(dso_days) if dso_days else 0.0
        elif invoice_dates and not payment_dates:
            earliest = min(invoice_dates)
            dso = (fields.Date.today() - earliest).days

        # ── Exposición del Cliente ────────────────────────────────
        partner = order.partner_id.commercial_partner_id or order.partner_id
        client_open_orders = self.env['sale.order'].sudo().search([
            ('partner_id.commercial_partner_id', '=', partner.id),
            ('state', 'in', ['sale', 'done']),
        ])
        client_exposure = 0.0
        for o in client_open_orders:
            inv_posted = o.invoice_ids.filtered(lambda i: i.state == 'posted' and i.move_type == 'out_invoice')
            for inv in inv_posted:
                residual = inv.amount_residual
                if inv.currency_id != target_currency:
                    residual = inv.currency_id._convert(
                        residual, target_currency, inv.company_id,
                        inv.invoice_date or fields.Date.today(),
                    )
                client_exposure += residual

        credit_risk = self._compute_credit_risk_index(partner, client_exposure)

        # ── Lead Time ─────────────────────────────────────────────
        lead_time_days = 0
        if order.date_order:
            done_moves = order.order_line.mapped('move_ids').filtered(
                lambda m: m.state == 'done' and m.picking_code == 'outgoing'
            )
            if done_moves:
                last_delivery = max(done_moves.mapped('date'))
                lead_time_days = (last_delivery.date() - order.date_order.date()).days

        # ── Desviación ────────────────────────────────────────────
        deviation_days = 0
        if order.commitment_date:
            done_moves = order.order_line.mapped('move_ids').filtered(
                lambda m: m.state == 'done' and m.picking_code == 'outgoing'
            )
            if done_moves:
                last_delivery = max(done_moves.mapped('date'))
                deviation_days = (last_delivery.date() - order.commitment_date.date()).days

        fragmentation_index = self._compute_lot_fragmentation()
        projected_collection = self._compute_projected_collection(partner, amount_pending, dso)

        overall_fulfillment = 0.0
        if total_ordered_qty > 0:
            overall_fulfillment = round((total_delivered_qty / total_ordered_qty) * 100, 1)

        health_score = self._compute_health_score(
            margin_pct=margin_pct,
            credit_risk=credit_risk,
            dso=dso,
            deviation_days=deviation_days,
            return_margin_impact=return_margin_impact,
            net_revenue=net_revenue,
            overall_fulfillment=overall_fulfillment,
        )

        mxn_sym = target_currency.symbol or '$'

        return {
            'margin': {
                'gross_margin': round(gross_margin, 2),
                'margin_pct': round(margin_pct, 1),
                'return_margin_impact': round(return_margin_impact, 2),
                'margin_per_sqm': round(margin_per_sqm, 2),
                'net_revenue': round(net_revenue, 2),
                'net_cost': round(net_cost, 2),
            },
            'payment': {
                'dso': round(dso, 1),
                'total_paid': round(total_paid, 2),
                'amount_pending': round(amount_pending, 2),
                'amount_total': round(amount_total_mxn, 2),
                'payments': payments_data,
            },
            'client': {
                'client_exposure': round(client_exposure, 2),
                'client_exposure_currency': mxn_sym,
                'credit_risk_score': credit_risk['score'],
                'credit_risk_label': credit_risk['label'],
                'credit_risk_color': credit_risk['color'],
                'credit_risk_details': credit_risk['details'],
                'partner_name': partner.name,
            },
            'logistics': {
                'lead_time_days': lead_time_days,
                'deviation_days': deviation_days,
                'overall_fulfillment': overall_fulfillment,
                'total_ordered': total_ordered_qty,
                'total_delivered': total_delivered_qty,
            },
            'returns': {
                'total_returned_qty': total_returned_qty,
                'total_returned_revenue': round(total_returned_revenue, 2),
            },
            'inventory': {
                'fragmentation_index': round(fragmentation_index, 1),
            },
            'projections': {
                'projected_collection_date': projected_collection['date_str'],
                'projected_collection_days': projected_collection['days'],
            },
            'order_health_score': round(health_score, 0),
            'currency': mxn_sym,
            'has_margin_access': self.env.user.has_group('sale_order_kpi_dashboard.group_margin_viewer'),
        }

    # ══════════════════════════════════════════════════════════════
    #  HELPER: Credit Risk Index
    # ══════════════════════════════════════════════════════════════
    def _compute_credit_risk_index(self, partner, exposure):
        score = 100
        details = []

        sudo_env = self.env['account.move'].sudo()
        partner_sudo = partner.sudo()

        overdue_invoices = sudo_env.search([
            ('partner_id.commercial_partner_id', '=', partner.id),
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice'),
            ('payment_state', 'in', ['not_paid', 'partial']),
            ('invoice_date_due', '<', fields.Date.today()),
        ])
        overdue_amount = sum(overdue_invoices.mapped('amount_residual'))
        overdue_count = len(overdue_invoices)

        if overdue_count > 0:
            penalty = min(overdue_count * 8, 30)
            score -= penalty
            details.append(f"{overdue_count} factura(s) vencida(s) (${overdue_amount:,.2f})")

        if overdue_invoices:
            avg_overdue = sum(
                (fields.Date.today() - inv.invoice_date_due).days
                for inv in overdue_invoices if inv.invoice_date_due
            ) / len(overdue_invoices)
            if avg_overdue > 90:
                score -= 25
                details.append(f"Promedio {avg_overdue:.0f} días de atraso")
            elif avg_overdue > 60:
                score -= 18
                details.append(f"Promedio {avg_overdue:.0f} días de atraso")
            elif avg_overdue > 30:
                score -= 12
                details.append(f"Promedio {avg_overdue:.0f} días de atraso")
            elif avg_overdue > 0:
                score -= 5
                details.append(f"Promedio {avg_overdue:.0f} días de atraso")

        credit_limit = partner_sudo.credit_limit if hasattr(partner_sudo, 'credit_limit') and partner_sudo.credit_limit else 0
        if credit_limit > 0:
            usage = exposure / credit_limit
            if usage > 1.0:
                score -= 20
                details.append(f"Sobre-expuesto: {usage:.0%} del límite")
            elif usage > 0.8:
                score -= 10
                details.append(f"Uso de crédito: {usage:.0%}")
        else:
            if exposure > 0:
                score -= 5
                details.append("Sin límite de crédito definido")

        twelve_months_ago = fields.Date.today() - timedelta(days=365)
        paid_invoices = sudo_env.search([
            ('partner_id.commercial_partner_id', '=', partner.id),
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice'),
            ('payment_state', '=', 'paid'),
            ('invoice_date', '>=', twelve_months_ago),
        ], limit=50)
        if paid_invoices:
            late_count = 0
            for inv in paid_invoices:
                if inv.invoice_date_due:
                    last_payment_date = None
                    for ml in inv.line_ids:
                        for partial in ml.matched_credit_ids:
                            pay = partial.credit_move_id.payment_id
                            if pay and pay.date:
                                if not last_payment_date or pay.date > last_payment_date:
                                    last_payment_date = pay.date
                    if last_payment_date and last_payment_date > inv.invoice_date_due:
                        late_count += 1
            if paid_invoices:
                late_ratio = late_count / len(paid_invoices)
                if late_ratio > 0.5:
                    score -= 15
                    details.append(f"{late_ratio:.0%} de pagos atrasados en 12 meses")
                elif late_ratio > 0.25:
                    score -= 8
                    details.append(f"{late_ratio:.0%} de pagos atrasados en 12 meses")

        score = max(0, min(100, score))

        if score >= 80:
            label, color = 'Bajo Riesgo', '#10B981'
        elif score >= 60:
            label, color = 'Riesgo Moderado', '#F59E0B'
        elif score >= 40:
            label, color = 'Riesgo Alto', '#F97316'
        else:
            label, color = 'Riesgo Crítico', '#EF4444'

        return {
            'score': score,
            'label': label,
            'color': color,
            'details': details,
        }

    # ══════════════════════════════════════════════════════════════
    #  HELPER: Lot Fragmentation
    # ══════════════════════════════════════════════════════════════
    def _compute_lot_fragmentation(self):
        self.ensure_one()
        done_moves = self.order_line.mapped('move_ids').filtered(
            lambda m: m.state == 'done' and m.picking_code == 'outgoing'
        )
        lot_ids = done_moves.mapped('lot_ids')
        if not lot_ids:
            lot_ids = done_moves.mapped('move_line_ids.lot_id')

        if not lot_ids:
            return 0.0

        total_original = 0.0
        total_remnant_small = 0.0
        threshold_sqm = 1.0

        sudo_quant = self.env['stock.quant'].sudo()
        sudo_sml = self.env['stock.move.line'].sudo()

        for lot in lot_ids:
            quants = sudo_quant.search([
                ('lot_id', '=', lot.id),
                ('location_id.usage', '=', 'internal'),
            ])
            current_qty = sum(quants.mapped('quantity'))
            outgoing = sudo_sml.search([
                ('lot_id', '=', lot.id),
                ('state', '=', 'done'),
                ('move_id.picking_code', '=', 'outgoing'),
            ])
            incoming = sudo_sml.search([
                ('lot_id', '=', lot.id),
                ('state', '=', 'done'),
                ('move_id.picking_code', '=', 'incoming'),
            ])
            original_qty = sum(incoming.mapped('quantity')) if incoming else current_qty + sum(outgoing.mapped('quantity'))
            total_original += original_qty

            if 0 < current_qty < threshold_sqm:
                total_remnant_small += current_qty

        if total_original <= 0:
            return 0.0

        return (total_remnant_small / total_original) * 100

    # ══════════════════════════════════════════════════════════════
    #  HELPER: Projected Collection
    # ══════════════════════════════════════════════════════════════
    def _compute_projected_collection(self, partner, amount_pending, current_dso):
        if amount_pending <= 0:
            return {'date_str': 'Cobrado', 'days': 0}

        twelve_months_ago = fields.Date.today() - timedelta(days=365)
        paid_invoices = self.env['account.move'].sudo().search([
            ('partner_id.commercial_partner_id', '=', partner.id),
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice'),
            ('payment_state', '=', 'paid'),
            ('invoice_date', '>=', twelve_months_ago),
        ], limit=100)

        historical_dso = 30
        if paid_invoices:
            dso_list = []
            for inv in paid_invoices:
                if not inv.invoice_date:
                    continue
                last_pay_date = None
                for ml in inv.line_ids:
                    for partial in ml.matched_credit_ids:
                        pay = partial.credit_move_id.payment_id
                        if pay and pay.date:
                            if not last_pay_date or pay.date > last_pay_date:
                                last_pay_date = pay.date
                if last_pay_date:
                    dso_list.append((last_pay_date - inv.invoice_date).days)
            if dso_list:
                historical_dso = sum(dso_list) / len(dso_list)

        invoices = self.invoice_ids.filtered(
            lambda i: i.state == 'posted' and i.move_type == 'out_invoice'
        )
        if invoices:
            latest_inv = max(invoices, key=lambda i: i.invoice_date or fields.Date.today())
            base_date = latest_inv.invoice_date or fields.Date.today()
        else:
            base_date = fields.Date.today()

        projected_date = base_date + timedelta(days=int(historical_dso))
        remaining_days = (projected_date - fields.Date.today()).days
        remaining_days = max(remaining_days, 0)

        return {
            'date_str': projected_date.strftime('%d/%m/%Y'),
            'days': remaining_days,
        }

    # ══════════════════════════════════════════════════════════════
    #  HELPER: Order Health Score (0-100)
    # ══════════════════════════════════════════════════════════════
    def _compute_health_score(self, margin_pct, credit_risk, dso,
                               deviation_days, return_margin_impact,
                               net_revenue, overall_fulfillment):
        if margin_pct >= 30:
            margin_score = 100
        elif margin_pct <= -10:
            margin_score = 0
        else:
            margin_score = (margin_pct + 10) / 40 * 100

        risk_score = credit_risk['score']

        if dso <= 0:
            dso_score = 100
        elif dso >= 120:
            dso_score = 0
        else:
            dso_score = (1 - dso / 120) * 100

        fulfillment_score = min(overall_fulfillment, 100)
        deviation_score = 100
        if deviation_days > 0:
            deviation_score = max(0, 100 - deviation_days * 5)
        logistics_score = fulfillment_score * 0.5 + deviation_score * 0.5

        if net_revenue > 0:
            return_ratio = abs(return_margin_impact) / net_revenue
            returns_score = max(0, (1 - return_ratio * 2) * 100)
        else:
            returns_score = 100 if return_margin_impact == 0 else 50

        health = (
            margin_score * 0.25 +
            risk_score * 0.20 +
            dso_score * 0.15 +
            logistics_score * 0.20 +
            returns_score * 0.20
        )

        return max(0, min(100, health))```

## ./models/sale_order_line.py
```py
from odoo import models, fields, api


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    kpi_cost_price = fields.Float(
        string='Costo KPI',
        digits='Product Price',
        help='Costo unitario usado internamente por el módulo KPI Dashboard para cálculo de margen.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if not line.kpi_cost_price and line.product_id:
                line.kpi_cost_price = line.product_id.standard_price or 0.0
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'product_id' in vals and 'kpi_cost_price' not in vals:
            for line in self:
                if line.product_id and not line.kpi_cost_price:
                    super(SaleOrderLine, line).write({
                        'kpi_cost_price': line.product_id.standard_price or 0.0
                    })
        return res```

## ./security/security.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <record id="module_category_sale_kpi" model="ir.module.category">
        <field name="name">KPI Dashboard</field>
        <field name="sequence">50</field>
    </record>

    <record id="group_margin_viewer" model="res.groups">
        <field name="name">Ver Márgenes y Rentabilidad</field>
        <field name="comment">Usuarios con este permiso pueden ver los KPIs de margen bruto, % margen, margen por m² e impacto de devoluciones en margen dentro del dashboard de la orden de venta.</field>
    </record>

</odoo>
```

## ./static/src/js/kpi_dashboard.js
```js
/** @odoo-module **/

import { Component, useState, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

class SaleKpiDashboard extends Component {
    static template = "sale_order_kpi_dashboard.SaleKpiDashboard";
    static props = { ...standardFieldProps };

    setup() {
        console.log("[KPI Dashboard] setup() called");

        const raw = this.props.record.data[this.props.name];
        console.log("[KPI Dashboard] raw data:", typeof raw, raw ? String(raw).substring(0, 100) + "..." : "empty");
        const data = this._parse(raw);

        this.state = useState({
            ...data,
            hasMarginAccess: data.has_margin_access || false,
            loaded: true,
        });

        console.log("[KPI Dashboard] hasMarginAccess:", this.state.hasMarginAccess);

        onWillUpdateProps((next) => {
            console.log("[KPI Dashboard] onWillUpdateProps triggered");
            const d = this._parse(next.record.data[next.name]);
            Object.assign(this.state, d);
            this.state.hasMarginAccess = d.has_margin_access || false;
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
                client_exposure_currency: "$",
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
            has_margin_access: false,
        };
    }

    _parse(value) {
        const empty = this._defaults();
        try {
            if (!value || value === "false" || value === false) {
                console.log("[KPI Dashboard] _parse: empty/false value");
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
            console.error("[KPI Dashboard] parse error:", e);
            return empty;
        }
    }

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

    getFulfillmentFill(pct) {
        if (pct >= 100) return "kpi-fill-green";
        if (pct > 50) return "kpi-fill-blue";
        if (pct > 0) return "kpi-fill-amber";
        return "kpi-fill-red";
    }

    getFulfillmentWidth() {
        return Math.min(this.state.logistics.overall_fulfillment, 100);
    }

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

    getPaymentBarWidth() {
        var t = this.state.payment.amount_total;
        if (t <= 0) return 0;
        return Math.min((this.state.payment.total_paid / t) * 100, 100);
    }

    hasPending() {
        return this.state.payment.amount_pending > 0;
    }

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

    isFragHigh() {
        return this.state.inventory.fragmentation_index > 15;
    }

    hasRiskDetails() {
        return (
            this.state.client.credit_risk_details &&
            this.state.client.credit_risk_details.length > 0
        );
    }

    openPayment(id) {
        console.log("[KPI Dashboard] openPayment:", id);
        window.location.href = `/odoo/payments/${id}`;
    }
}

registry.category("fields").add("sale_kpi_dashboard", {
    component: SaleKpiDashboard,
});```

## ./static/src/xml/kpi_dashboard.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">

<t t-name="sale_order_kpi_dashboard.SaleKpiDashboard">
<div class="kpi-root" t-if="state.loaded">

    <!-- ═══════════ HEADER + HEALTH SCORE ═══════════ -->
    <div class="kpi-header">
        <div class="kpi-header-left">
            <div class="kpi-header-icon">
                <i class="fa fa-dashboard"/>
            </div>
            <div>
                <div class="kpi-header-title">KPI Dashboard</div>
                <div class="kpi-header-subtitle">Indicadores clave de rendimiento de la orden</div>
            </div>
        </div>
        <div t-att-class="'kpi-health-badge ' + getHealthClass(state.order_health_score)">
            <span>Order Health</span>
            <span class="kpi-health-score-num"><t t-esc="fmtInt(state.order_health_score)"/></span>
            <span>/100</span>
        </div>
    </div>

    <!-- ═══════════ ROW 1: HEALTH + LEAD TIME + DESVIACIÓN + DSO ═══════════ -->
    <div class="kpi-section-row-4">

        <!-- KPI 12: Health Gauge -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-indigo"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-indigo">
                    <i class="fa fa-heartbeat"/>
                </div>
                <span class="kpi-card-number-tag">KPI 12</span>
            </div>
            <div class="kpi-card-label">Salud de la Orden</div>
            <div class="kpi-gauge-wrap">
                <div class="kpi-gauge-ring">
                    <svg viewBox="0 0 64 64">
                        <circle class="kpi-gauge-bg" cx="32" cy="32" r="27"/>
                        <circle class="kpi-gauge-fill" cx="32" cy="32" r="27"
                            t-att-stroke="getHealthColor(state.order_health_score)"
                            t-att-stroke-dasharray="'' + getGaugeCircumference()"
                            t-att-stroke-dashoffset="'' + getGaugeDashoffset(state.order_health_score)"/>
                    </svg>
                    <div class="kpi-gauge-center">
                        <t t-esc="fmtInt(state.order_health_score)"/>
                    </div>
                </div>
                <div>
                    <div class="kpi-gauge-label"><t t-esc="getHealthLabel(state.order_health_score)"/></div>
                    <div class="kpi-gauge-detail">Margen + Riesgo + DSO + Logística + Devoluciones</div>
                </div>
            </div>
        </div>

        <!-- KPI 7: Lead Time Total -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-blue"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-blue">
                    <i class="fa fa-truck"/>
                </div>
                <span class="kpi-card-number-tag">KPI 7</span>
            </div>
            <div class="kpi-card-label">Lead Time Total</div>
            <div class="kpi-card-value">
                <t t-esc="state.logistics.lead_time_days"/>
                <span class="kpi-unit-label"> días</span>
            </div>
            <div class="kpi-card-sub">Confirmación → Última entrega</div>
            <div class="kpi-progress-bar">
                <div t-att-class="'kpi-progress-fill ' + getFulfillmentFill(state.logistics.overall_fulfillment)"
                     t-att-style="'width:' + getFulfillmentWidth() + '%'"/>
            </div>
            <div class="kpi-card-sub" style="margin-top:4px;">
                Cumplimiento: <b><t t-esc="fmtPct(state.logistics.overall_fulfillment)"/>%</b>
            </div>
        </div>

        <!-- KPI 8: Desviación vs Fecha Prometida -->
        <div class="kpi-card">
            <div t-att-class="'kpi-card-accent ' + (isDeviationLate() ? 'kpi-accent-red' : 'kpi-accent-green')"/>
            <div class="kpi-card-top">
                <div t-att-class="'kpi-card-icon-wrap ' + (isDeviationLate() ? 'kpi-icon-red' : 'kpi-icon-green')">
                    <i class="fa fa-calendar-check-o"/>
                </div>
                <span class="kpi-card-number-tag">KPI 8</span>
            </div>
            <div class="kpi-card-label">Desviación vs Promesa</div>
            <div t-att-class="'kpi-card-value ' + getDeviationClass(state.logistics.deviation_days)">
                <t t-esc="getDeviationText(state.logistics.deviation_days)"/>
            </div>
            <div class="kpi-card-sub">vs fecha comprometida</div>
        </div>

        <!-- KPI 4: DSO -->
        <div class="kpi-card">
            <div t-att-class="'kpi-card-accent ' + getDsoAccent()"/>
            <div class="kpi-card-top">
                <div t-att-class="'kpi-card-icon-wrap ' + getDsoIcon()">
                    <i class="fa fa-hourglass-half"/>
                </div>
                <span class="kpi-card-number-tag">KPI 4</span>
            </div>
            <div class="kpi-card-label">DSO (Días de Cartera)</div>
            <div class="kpi-card-value">
                <t t-esc="fmtPct(state.payment.dso)"/>
                <span class="kpi-unit-label"> días</span>
            </div>
            <div class="kpi-card-sub">Promedio facturación → pago</div>
        </div>
    </div>

    <!-- ═══════════ SECTION: PAGOS Y COBRANZA ═══════════ -->
    <div class="kpi-section-divider">
        <span class="kpi-section-divider-label">Pagos y Cobranza</span>
        <div class="kpi-section-divider-line"/>
    </div>

    <div class="kpi-section-row-4">

        <!-- Pagado -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-green"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-green">
                    <i class="fa fa-check-circle"/>
                </div>
            </div>
            <div class="kpi-card-label">Total Pagado</div>
            <div class="kpi-card-value">
                <t t-esc="state.currency"/><t t-esc="fmt(state.payment.total_paid)"/>
            </div>
            <div class="kpi-card-sub">
                de <t t-esc="state.currency"/><t t-esc="fmt(state.payment.amount_total)"/>
            </div>
            <div class="kpi-progress-bar">
                <div class="kpi-progress-fill kpi-fill-green"
                     t-att-style="'width:' + getPaymentBarWidth() + '%'"/>
            </div>
        </div>

        <!-- Pendiente -->
        <div class="kpi-card">
            <div t-att-class="'kpi-card-accent ' + (hasPending() ? 'kpi-accent-red' : 'kpi-accent-green')"/>
            <div class="kpi-card-top">
                <div t-att-class="'kpi-card-icon-wrap ' + (hasPending() ? 'kpi-icon-orange' : 'kpi-icon-green')">
                    <i t-att-class="hasPending() ? 'fa fa-clock-o' : 'fa fa-check'"/>
                </div>
            </div>
            <div class="kpi-card-label">Pendiente por Pagar</div>
            <div t-att-class="'kpi-card-value ' + (hasPending() ? 'kpi-deviation-positive' : '')">
                <t t-esc="state.currency"/><t t-esc="fmt(state.payment.amount_pending)"/>
            </div>
        </div>

        <!-- KPI 5: Exposición Total (siempre MXN) -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-orange"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-orange">
                    <i class="fa fa-building"/>
                </div>
                <span class="kpi-card-number-tag">KPI 5</span>
            </div>
            <div class="kpi-card-label">Exposición del Cliente (MXN)</div>
            <div class="kpi-card-value kpi-card-value-sm">
                <t t-esc="state.client.client_exposure_currency"/><t t-esc="fmt(state.client.client_exposure)"/>
            </div>
            <div class="kpi-card-sub">
                <t t-esc="state.client.partner_name"/> — todas las órdenes
            </div>
        </div>

        <!-- KPI 11: Cobro Proyectado -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-cyan"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-cyan">
                    <i class="fa fa-line-chart"/>
                </div>
                <span class="kpi-card-number-tag">KPI 11</span>
            </div>
            <div class="kpi-card-label">Cobro Total Proyectado</div>
            <div class="kpi-card-value kpi-card-value-sm">
                <t t-esc="state.projections.projected_collection_date"/>
            </div>
            <div class="kpi-card-sub">
                ~<t t-esc="state.projections.projected_collection_days"/> días restantes
            </div>
        </div>
    </div>

    <!-- ═══════════ SECTION: RIESGO + DEVOLUCIONES ═══════════ -->
    <div class="kpi-section-divider">
        <span class="kpi-section-divider-label">Análisis de Riesgo y Devoluciones</span>
        <div class="kpi-section-divider-line"/>
    </div>

    <div class="kpi-section-row-2">

        <!-- KPI 6: Índice de Riesgo Crediticio -->
        <div class="kpi-card">
            <div class="kpi-card-accent" t-att-style="'background-color:' + state.client.credit_risk_color"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap"
                     t-att-style="'background:' + state.client.credit_risk_color + '15;color:' + state.client.credit_risk_color">
                    <i class="fa fa-shield"/>
                </div>
                <span class="kpi-card-number-tag">KPI 6</span>
            </div>
            <div class="kpi-card-label">Índice de Riesgo Crediticio</div>
            <div class="kpi-card-value">
                <t t-esc="state.client.credit_risk_score"/>
                <span class="kpi-unit-label"> /100</span>
            </div>
            <div class="kpi-risk-badge"
                 t-att-style="'background:' + state.client.credit_risk_color + '15;color:' + state.client.credit_risk_color">
                <div class="kpi-risk-dot" t-att-style="'background:' + state.client.credit_risk_color"/>
                <t t-esc="state.client.credit_risk_label"/>
            </div>
            <t t-if="hasRiskDetails()">
                <div class="kpi-risk-details">
                    <t t-foreach="state.client.credit_risk_details" t-as="detail" t-key="detail_index">
                        <div class="kpi-risk-detail-item"><t t-esc="detail"/></div>
                    </t>
                </div>
            </t>
        </div>

        <!-- Devoluciones + KPI 10: Fragmentación -->
        <div class="kpi-card">
            <div class="kpi-card-accent kpi-accent-amber"/>
            <div class="kpi-card-top">
                <div class="kpi-card-icon-wrap kpi-icon-amber">
                    <i class="fa fa-undo"/>
                </div>
            </div>
            <div class="kpi-card-label">Devoluciones e Inventario</div>
            <div class="kpi-dual-metrics">
                <div class="kpi-dual-left">
                    <div class="kpi-dual-sublabel">Devuelto</div>
                    <div class="kpi-dual-value kpi-color-amber">
                        <t t-esc="state.currency"/><t t-esc="fmt(state.returns.total_returned_revenue)"/>
                    </div>
                    <div class="kpi-dual-sub">
                        <t t-esc="fmtQty(state.returns.total_returned_qty)"/> uds
                    </div>
                </div>
                <div class="kpi-dual-divider"/>
                <div class="kpi-dual-right">
                    <div class="kpi-dual-sublabel">
                        Fragmentación Lote
                        <span class="kpi-card-number-tag">KPI 10</span>
                    </div>
                    <div t-att-class="'kpi-dual-value ' + (isFragHigh() ? 'kpi-deviation-positive' : '')">
                        <t t-esc="fmtPct(state.inventory.fragmentation_index)"/>%
                    </div>
                    <div class="kpi-dual-sub">m² remanentes pequeños</div>
                </div>
            </div>
        </div>
    </div>

    <!-- ═══════════ SECTION: MÁRGENES (RESTRINGIDO) ═══════════ -->
    <div class="kpi-section-divider">
        <span class="kpi-section-divider-label">
            <i class="fa fa-lock" style="margin-right:4px;"/> Margen y Rentabilidad
        </span>
        <div class="kpi-section-divider-line"/>
    </div>

    <t t-if="state.hasMarginAccess">
        <div class="kpi-section-row-4">

            <!-- KPI 1: Margen Bruto Real -->
            <div class="kpi-card">
                <div t-att-class="'kpi-card-accent ' + getMarginAccent()"/>
                <div class="kpi-card-top">
                    <div t-att-class="'kpi-card-icon-wrap ' + getMarginIcon()">
                        <i class="fa fa-dollar"/>
                    </div>
                    <span class="kpi-card-number-tag">KPI 1</span>
                </div>
                <div class="kpi-card-label">Margen Bruto Real</div>
                <div t-att-class="'kpi-card-value ' + (isMarginNegative() ? 'kpi-deviation-positive' : '')">
                    <t t-esc="state.currency"/><t t-esc="fmt(state.margin.gross_margin)"/>
                </div>
                <div class="kpi-card-sub">
                    Venta neta: <t t-esc="state.currency"/><t t-esc="fmt(state.margin.net_revenue)"/>
                    — Costo: <t t-esc="state.currency"/><t t-esc="fmt(state.margin.net_cost)"/>
                </div>
            </div>

            <!-- KPI 2: % Margen Real -->
            <div class="kpi-card">
                <div t-att-class="'kpi-card-accent ' + getMarginAccent()"/>
                <div class="kpi-card-top">
                    <div t-att-class="'kpi-card-icon-wrap ' + getMarginIcon()">
                        <i class="fa fa-percent"/>
                    </div>
                    <span class="kpi-card-number-tag">KPI 2</span>
                </div>
                <div class="kpi-card-label">% Margen Real</div>
                <div t-att-class="'kpi-card-value ' + (isMarginNegative() ? 'kpi-deviation-positive' : '')">
                    <t t-esc="fmtPct(state.margin.margin_pct)"/>%
                </div>
                <div class="kpi-progress-bar">
                    <div t-att-class="'kpi-progress-fill ' + getMarginFill()"
                         t-att-style="'width:' + getMarginBarWidth() + '%'"/>
                </div>
            </div>

            <!-- KPI 3: Impacto Devoluciones en Margen -->
            <div class="kpi-card">
                <div t-att-class="'kpi-card-accent ' + (hasReturnImpact() ? 'kpi-accent-red' : 'kpi-accent-green')"/>
                <div class="kpi-card-top">
                    <div t-att-class="'kpi-card-icon-wrap ' + (hasReturnImpact() ? 'kpi-icon-red' : 'kpi-icon-green')">
                        <i class="fa fa-exchange"/>
                    </div>
                    <span class="kpi-card-number-tag">KPI 3</span>
                </div>
                <div class="kpi-card-label">Impacto Devol. en Margen</div>
                <div class="kpi-card-value kpi-card-value-sm">
                    -<t t-esc="state.currency"/><t t-esc="fmt(absReturnImpact())"/>
                </div>
                <div class="kpi-card-sub">Margen perdido por devoluciones</div>
            </div>

            <!-- KPI 9: Margen por m² -->
            <div class="kpi-card">
                <div t-att-class="'kpi-card-accent ' + getMarginAccent()"/>
                <div class="kpi-card-top">
                    <div class="kpi-card-icon-wrap kpi-icon-purple">
                        <i class="fa fa-th"/>
                    </div>
                    <span class="kpi-card-number-tag">KPI 9</span>
                </div>
                <div class="kpi-card-label">Margen por m²</div>
                <div class="kpi-card-value kpi-card-value-sm">
                    <t t-esc="state.currency"/><t t-esc="fmt(state.margin.margin_per_sqm)"/>
                </div>
                <div class="kpi-card-sub">por m² vendido en esta orden</div>
            </div>
        </div>
    </t>

    <t t-if="!state.hasMarginAccess">
        <div class="kpi-card" style="text-align:center;padding:32px;">
            <div class="kpi-restricted-msg">
                <i class="fa fa-lock kpi-restricted-icon"/>
                <div class="kpi-restricted-text">
                    Los KPIs de margen y rentabilidad requieren el permiso
                    <b>"Ver Márgenes y Rentabilidad"</b> en Ventas
                </div>
            </div>
        </div>
    </t>

</div>
</t>

</templates>```

## ./views/sale_order_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- ═══════════════════════════════════════════════════════════
         KPI Dashboard inside sale order form
         ═══════════════════════════════════════════════════════════ -->
    <record id="view_order_form_kpi_dashboard" model="ir.ui.view">
        <field name="name">sale.order.form.kpi.dashboard</field>
        <field name="model">sale.order</field>
        <field name="inherit_id" ref="sale.view_order_form"/>
        <field name="priority">70</field>
        <field name="arch" type="xml">

            <xpath expr="//page[@name='order_lines']" position="inside">
                <div class="mt-4 pt-3 border-top">
                    <div class="h3 mb-3 text-muted ps-2">Análisis de Rendimiento (KPIs)</div>
                    <field name="kpi_dashboard_data" widget="sale_kpi_dashboard" nolabel="1"/>
                </div>
            </xpath>

        </field>
    </record>

</odoo>```

