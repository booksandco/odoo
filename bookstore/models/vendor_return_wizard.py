from odoo import _, fields, models
from odoo.exceptions import UserError


class VendorReturnWizard(models.TransientModel):
    _name = 'vendor.return.wizard'
    _description = 'Vendor Return Wizard'

    line_ids = fields.One2many('vendor.return.wizard.line', 'wizard_id')

    def action_confirm(self):
        lines = self.line_ids.filtered(lambda l: l.return_qty > 0)
        if not lines:
            raise UserError(_("Please set a return quantity on at least one selected line."))

        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        picking_type = warehouse.out_type_id
        supplier_location = self.env.ref('stock.stock_location_suppliers')

        pickings = self.env['stock.picking']
        for vendor in lines.vendor_id:
            vendor_lines = lines.filtered(lambda l: l.vendor_id == vendor)
            picking = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'partner_id': vendor.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': supplier_location.id,
                'origin': _('Vendor Return'),
            })
            for line in vendor_lines:
                self.env['stock.move'].create({
                    'name': line.product_id.display_name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.return_qty,
                    'product_uom': line.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                })
            picking.action_confirm()
            picking.action_assign()
            pickings |= picking

        if len(pickings) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Return'),
                'res_model': 'stock.picking',
                'res_id': pickings.id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Returns'),
            'res_model': 'stock.picking',
            'domain': [('id', 'in', pickings.ids)],
            'view_mode': 'list,form',
        }


class VendorReturnWizardLine(models.TransientModel):
    _name = 'vendor.return.wizard.line'
    _description = 'Vendor Return Wizard Line'

    wizard_id = fields.Many2one('vendor.return.wizard', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', readonly=True)
    vendor_id = fields.Many2one('res.partner', readonly=True)
    on_hand_qty = fields.Float(readonly=True)
    return_qty = fields.Float()
