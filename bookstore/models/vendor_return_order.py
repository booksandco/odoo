from odoo import _, api, fields, models
from odoo.exceptions import UserError


class VendorReturnOrder(models.Model):
    _name = 'vendor.return.order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Vendor Return Order'
    _order = 'date_order desc, id desc'

    name = fields.Char(required=True, default='New', copy=False, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirmed', 'Confirmed'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], default='draft', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, tracking=True)
    date_order = fields.Datetime(string='Order Date', default=fields.Datetime.now)
    order_line = fields.One2many('vendor.return.order.line', 'order_id', string='Order Lines', copy=True)
    picking_ids = fields.Many2many(
        'stock.picking', compute='_compute_picking_ids', store=True, string='Transfers', copy=False,
    )
    picking_count = fields.Integer(compute='_compute_picking_ids', store=True)
    invoice_ids = fields.Many2many(
        'account.move', compute='_compute_invoice_ids', store=True, string='Credit Notes', copy=False,
    )
    invoice_count = fields.Integer(compute='_compute_invoice_ids', store=True)
    note = fields.Html(string='Notes')
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('vendor.return.order') or _('New')
        return super().create(vals_list)

    @api.depends('order_line.move_ids.picking_id')
    def _compute_picking_ids(self):
        for order in self:
            order.picking_ids = order.order_line.move_ids.picking_id
            order.picking_count = len(order.picking_ids)

    @api.depends('order_line.invoice_lines.move_id')
    def _compute_invoice_ids(self):
        for order in self:
            order.invoice_ids = order.order_line.invoice_lines.move_id
            order.invoice_count = len(order.invoice_ids)

    def action_send(self):
        self.write({'state': 'sent'})

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_generate_picks(self):
        for order in self:
            if not order.order_line:
                raise UserError(_("Please add at least one line before generating picks."))

            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', order.company_id.id),
            ], limit=1)
            picking_type = warehouse.out_type_id
            supplier_location = self.env.ref('stock.stock_location_suppliers')

            picking = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'partner_id': order.partner_id.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': supplier_location.id,
                'origin': order.name,
            })
            for line in order.order_line:
                move = self.env['stock.move'].create({
                    'name': line.product_id.display_name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.product_qty,
                    'product_uom': line.product_uom.id,
                    'picking_id': picking.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                })
                line.move_ids = [(4, move.id)]
            picking.action_confirm()
            picking.action_assign()

        self.write({'state': 'done'})

    def action_create_invoice(self):
        self.ensure_one()
        invoice = self.env['account.move'].create({
            'move_type': 'in_refund',
            'partner_id': self.partner_id.id,
            'invoice_origin': self.name,
            'invoice_line_ids': [(0, 0, {
                'product_id': line.product_id.id,
                'name': line.name or line.product_id.display_name,
                'quantity': line.product_qty,
                'price_unit': line.price_unit,
            }) for line in self.order_line],
        })
        for inv_line in invoice.invoice_line_ids:
            order_line = self.order_line.filtered(lambda l: l.product_id == inv_line.product_id)
            if order_line:
                order_line[0].invoice_lines = [(4, inv_line.id)]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Note'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
        }

    def action_view_picking(self):
        pickings = self.picking_ids
        action = self.env['ir.actions.actions']._for_xml_id('stock.action_picking_tree_all')
        if len(pickings) > 1:
            action['domain'] = [('id', 'in', pickings.ids)]
        elif len(pickings) == 1:
            form_view = [(self.env.ref('stock.view_picking_form').id, 'form')]
            action['views'] = form_view + [
                (state, view) for state, view in action.get('views', []) if view != 'form'
            ]
            action['res_id'] = pickings.id
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    def action_view_invoice(self):
        invoices = self.invoice_ids
        action = self.env['ir.actions.actions']._for_xml_id('account.action_move_in_refund_type')
        if len(invoices) > 1:
            action['domain'] = [('id', 'in', invoices.ids)]
        elif len(invoices) == 1:
            form_view = [(self.env.ref('account.view_move_form').id, 'form')]
            action['views'] = form_view + [
                (state, view) for state, view in action.get('views', []) if view != 'form'
            ]
            action['res_id'] = invoices.id
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})


class VendorReturnOrderLine(models.Model):
    _name = 'vendor.return.order.line'
    _description = 'Vendor Return Order Line'

    order_id = fields.Many2one('vendor.return.order', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    name = fields.Char(compute='_compute_name', store=True, readonly=False)
    product_qty = fields.Float(string='Quantity', default=1.0)
    product_uom = fields.Many2one('uom.uom', related='product_id.uom_id', store=True)
    price_unit = fields.Float(string='Unit Price')
    move_ids = fields.Many2many('stock.move')
    invoice_lines = fields.Many2many('account.move.line')

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            line.name = line.product_id.display_name or ''
