from odoo import models, fields, api


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    purchase_price = fields.Float(
        string='Costo',
        digits='Product Price',
    )

    @api.onchange('product_id', 'product_uom')
    def _onchange_product_set_purchase_price(self):
        if self.product_id:
            self.purchase_price = self.product_id.standard_price or 0.0