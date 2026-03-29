from collections import defaultdict

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
        self.ensure_one()
        self.write({'state': 'sent'})
        ir_model_data = self.env['ir.model.data']
        try:
            template_id = ir_model_data._xmlid_lookup('bookstore.email_template_vendor_return')[1]
        except ValueError:
            template_id = False
        try:
            compose_form_id = ir_model_data._xmlid_lookup('mail.email_compose_message_wizard_form')[1]
        except ValueError:
            compose_form_id = False
        ctx = dict(self.env.context or {})
        ctx.update({
            'default_model': 'vendor.return.order',
            'default_res_ids': self.ids,
            'default_template_id': template_id,
            'default_composition_mode': 'comment',
            'default_email_layout_xmlid': 'mail.mail_notification_layout_with_responsible_signature',
            'force_email': True,
        })
        return {
            'name': _('Compose Email'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form_id, 'form')],
            'view_id': compose_form_id,
            'target': 'new',
            'context': ctx,
        }

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_generate_picks(self):
        for order in self:
            if not order.order_line:
                raise UserError(_("Please add at least one line before generating picks."))
            if order.picking_ids.filtered(lambda p: p.state != 'cancel'):
                raise UserError(_("Transfers already exist for this return order."))

            lines_by_picking = defaultdict(lambda: self.env['vendor.return.order.line'])
            lines_without_source = self.env['vendor.return.order.line']
            for line in order.order_line:
                if line.source_move_id:
                    lines_by_picking[line.source_move_id.picking_id] |= line
                else:
                    lines_without_source |= line

            for source_picking, lines in lines_by_picking.items():
                return_type = source_picking.picking_type_id.return_picking_type_id
                picking = self.env['stock.picking'].create({
                    'picking_type_id': (return_type or source_picking.picking_type_id).id,
                    'partner_id': order.partner_id.id,
                    'location_id': source_picking.location_dest_id.id,
                    'location_dest_id': source_picking.location_id.id,
                    'origin': order.name,
                })
                for line in lines:
                    move = self.env['stock.move'].create({
                        'name': line.name or line.product_id.display_name,
                        'product_id': line.product_id.id,
                        'product_uom_qty': line.product_qty,
                        'product_uom': line.product_uom.id,
                        'picking_id': picking.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                        'origin_returned_move_id': line.source_move_id.id,
                    })
                    line.move_ids = [(4, move.id)]
                picking.action_confirm()
                picking.action_assign()

            if lines_without_source:
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
                for line in lines_without_source:
                    move = self.env['stock.move'].create({
                        'name': line.name or line.product_id.display_name,
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

    def action_create_invoice(self):
        self.ensure_one()
        if self.invoice_ids:
            raise UserError(_("A credit note already exists for this return order."))
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
        inv_lines = invoice.invoice_line_ids.filtered(lambda l: not l.display_type)
        for order_line, inv_line in zip(self.order_line, inv_lines):
            order_line.invoice_lines = [(4, inv_line.id)]
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
        for order in self:
            active_picks = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
            if active_picks:
                raise UserError(_("Please cancel or complete all transfers before cancelling this return order."))
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
    move_ids = fields.Many2many('stock.move', 'vendor_return_line_stock_move_rel', 'line_id', 'move_id')
    invoice_lines = fields.Many2many('account.move.line', 'vendor_return_line_invoice_line_rel', 'line_id', 'invoice_line_id')
    source_move_id = fields.Many2one('stock.move', string='Source Receipt', copy=False)
    source_purchase_line_id = fields.Many2one(
        'purchase.order.line', related='source_move_id.purchase_line_id',
        string='Source PO Line', store=True,
    )
    vendor_invoice_ref = fields.Char(
        compute='_compute_vendor_invoice_ref', string='Vendor Invoice #', store=True,
    )

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            line.name = line.product_id.display_name or ''

    @api.depends('source_purchase_line_id.invoice_lines.move_id.ref')
    def _compute_vendor_invoice_ref(self):
        for line in self:
            refs = line.source_purchase_line_id.invoice_lines.move_id.mapped('ref')
            line.vendor_invoice_ref = ', '.join(filter(None, refs)) or ''
