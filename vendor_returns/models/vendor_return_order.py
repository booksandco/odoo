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
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        default=lambda self: self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1),
        required=True,
    )
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
            template_id = ir_model_data._xmlid_lookup('vendor_returns.email_template_vendor_return')[1]
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

            # Lines with a source receipt: use Odoo's standard return wizard
            # so that warehouse routes, move chaining and lot handling are
            # applied exactly as for a normal picking return.
            for source_picking, lines in lines_by_picking.items():
                try:
                    wizard = self.env['stock.return.picking'].create({
                        'picking_id': source_picking.id,
                    })
                except UserError as e:
                    raise UserError(_(
                        "Cannot return picking %(picking)s: %(error)s"
                    ) % {'picking': source_picking.name, 'error': str(e)}) from e

                for wizard_line in wizard.product_return_moves:
                    wizard_line.quantity = 0
                for line in lines:
                    wizard_line = wizard.product_return_moves.filtered(
                        lambda wl: wl.move_id == line.source_move_id
                    )
                    if wizard_line:
                        wizard_line.quantity = line.product_qty

                return_picking = wizard._create_return()
                for line in lines:
                    return_move = return_picking.move_ids.filtered(
                        lambda m: m.origin_returned_move_id == line.source_move_id
                    )
                    if return_move:
                        line.move_ids = [(4, m.id) for m in return_move]

            # Lines without a source receipt: create a simple outgoing picking
            # from the warehouse stock location to the supplier.
            if lines_without_source:
                warehouse = order.warehouse_id
                if not warehouse:
                    raise UserError(_(
                        "Please set a warehouse on the return order before generating picks."
                    ))
                picking_type = warehouse.out_type_id
                supplier_location = self.env.ref('stock.stock_location_suppliers')
                picking = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'partner_id': order.partner_id.id,
                    'location_id': warehouse.lot_stock_id.id,
                    'location_dest_id': supplier_location.id,
                    'origin': order.name,
                })
                for line in lines_without_source:
                    move = self.env['stock.move'].create({
                        'product_id': line.product_id.id,
                        'product_uom_qty': line.product_qty,
                        'product_uom': (line.product_uom or line.product_id.uom_id).id,
                        'picking_id': picking.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                        'warehouse_id': warehouse.id,
                        'procure_method': 'make_to_stock',
                    })
                    line.move_ids = [(4, move.id)]
                picking.action_confirm()
                picking.action_assign()

    def action_create_invoice(self):
        self.ensure_one()
        if self.invoice_ids:
            raise UserError(_("A credit note already exists for this return order."))

        lines_by_bill = defaultdict(lambda: self.env['vendor.return.order.line'])
        lines_no_bill = self.env['vendor.return.order.line']
        for line in self.order_line:
            bills = line.source_purchase_line_id.invoice_lines.move_id.filtered(
                lambda m: m.move_type == 'in_invoice'
            )
            if bills:
                lines_by_bill[bills[0]] |= line
            else:
                lines_no_bill |= line

        invoices = self.env['account.move']

        for bill, lines in lines_by_bill.items():
            invoice = self.env['account.move'].create({
                'move_type': 'in_refund',
                'partner_id': self.partner_id.id,
                'reversed_entry_id': bill.id,
                'ref': bill.ref,
                'invoice_origin': self.name,
                'invoice_line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'quantity': line.product_qty,
                    'price_unit': line.price_unit,
                }) for line in lines],
            })
            inv_lines = invoice.invoice_line_ids.filtered(lambda l: not l.display_type)
            for order_line, inv_line in zip(lines, inv_lines):
                order_line.invoice_lines = [(4, inv_line.id)]
            invoices |= invoice

        if lines_no_bill:
            invoice = self.env['account.move'].create({
                'move_type': 'in_refund',
                'partner_id': self.partner_id.id,
                'invoice_origin': self.name,
                'invoice_line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'quantity': line.product_qty,
                    'price_unit': line.price_unit,
                }) for line in lines_no_bill],
            })
            inv_lines = invoice.invoice_line_ids.filtered(lambda l: not l.display_type)
            for order_line, inv_line in zip(lines_no_bill, inv_lines):
                order_line.invoice_lines = [(4, inv_line.id)]
            invoices |= invoice

        if len(invoices) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Credit Note'),
                'res_model': 'account.move',
                'res_id': invoices.id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Notes'),
            'res_model': 'account.move',
            'domain': [('id', 'in', invoices.ids)],
            'view_mode': 'list,form',
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

    def _remove_replenishment_rules(self):
        """Remove active replenishment rules for returned products so reorders are not triggered."""
        Orderpoint = self.env['stock.warehouse.orderpoint']
        for order in self:
            products = order.order_line.product_id
            if not products:
                continue
            location_ids = order.picking_ids.move_ids.location_id.ids
            if not location_ids:
                continue
            orderpoints = Orderpoint.search([
                ('product_id', 'in', products.ids),
                ('location_id', 'parent_of', location_ids),
                ('company_id', '=', order.company_id.id),
            ])
            if orderpoints:
                descriptions = [
                    _("%s at %s") % (op.product_id.display_name, op.location_id.display_name)
                    for op in orderpoints
                ]
                order.message_post(
                    body=_("Replenishment rules removed for returned products: %s") % ', '.join(descriptions)
                )
                orderpoints.unlink()


class VendorReturnOrderLine(models.Model):
    _name = 'vendor.return.order.line'
    _description = 'Vendor Return Order Line'
    _order = 'order_id, id'

    order_id = fields.Many2one('vendor.return.order', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    product_qty = fields.Float(string='Quantity', default=1.0)
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure', required=True)
    price_unit = fields.Float(string='Unit Price')
    move_ids = fields.Many2many('stock.move', 'vendor_return_line_stock_move_rel', 'line_id', 'move_id')
    invoice_lines = fields.Many2many('account.move.line', 'vendor_return_line_invoice_line_rel', 'line_id', 'invoice_line_id')
    source_move_id = fields.Many2one('stock.move', string='Source Receipt', copy=False)
    source_purchase_line_id = fields.Many2one(
        'purchase.order.line', related='source_move_id.purchase_line_id',
        string='Source PO Line', store=True,
    )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom = self.product_id.uom_id
            if not self.price_unit:
                self.price_unit = self.product_id.standard_price
