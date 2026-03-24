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

        return max(0, min(100, health))