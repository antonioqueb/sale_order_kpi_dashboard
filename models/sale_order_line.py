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
                # standard_price es company_dependent: leerlo con la compañía de la venta.
                product = line.product_id.with_company(line.company_id or line.order_id.company_id)
                line.kpi_cost_price = product.standard_price or 0.0
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'product_id' in vals and 'kpi_cost_price' not in vals:
            for line in self:
                if line.product_id and not line.kpi_cost_price:
                    product = line.product_id.with_company(line.company_id or line.order_id.company_id)
                    super(SaleOrderLine, line).write({
                        'kpi_cost_price': product.standard_price or 0.0
                    })
        return res