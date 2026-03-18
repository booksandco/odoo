from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class VendorReturnLine(models.Model):
    _name = 'vendor.return.line'
    _description = 'Vendor Return Planning Line'
    _order = 'invoice_date asc, vendor_id'

    product_id = fields.Many2one('product.product', string='Product', required=True, ondelete='cascade')
    barcode = fields.Char(related='product_id.barcode', string='ISBN')
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True, ondelete='cascade')
    on_hand_qty = fields.Float('On Hand', compute='_compute_on_hand_qty')
    invoice_date = fields.Date('Invoice Date')
    age_days = fields.Integer('Age (Days)', compute='_compute_age_days')
    return_window_end = fields.Date('Window End', compute='_compute_return_window', store=True)
    window_status = fields.Selection([
        ('too_early', 'Too Early'),
        ('within_window', 'Returnable'),
        ('expired', 'Expired'),
        ('no_policy', 'No Policy'),
    ], string='Status', compute='_compute_return_window', store=True)
    return_qty = fields.Float('Return Qty')

    @api.depends('product_id')
    def _compute_on_hand_qty(self):
        if not self.product_id:
            self.on_hand_qty = 0
            return
        quant_data = self.env['stock.quant']._read_group(
            [('product_id', 'in', self.product_id.ids), ('location_id.usage', '=', 'internal')],
            ['product_id'],
            ['quantity:sum'],
        )
        qty_map = {product.id: qty for product, qty in quant_data}
        for line in self:
            line.on_hand_qty = qty_map.get(line.product_id.id, 0)

    @api.depends('invoice_date')
    def _compute_age_days(self):
        today = date.today()
        for line in self:
            line.age_days = (today - line.invoice_date).days if line.invoice_date else 0

    @api.depends('invoice_date', 'vendor_id', 'product_id.product_tmpl_id.x_publication_date')
    def _compute_return_window(self):
        today = date.today()
        vendor_ids = self.vendor_id.ids
        policies = self.env['vendor.return.policy'].search([('partner_id', 'in', vendor_ids)])
        policy_map = {p.partner_id.id: p for p in policies}

        for line in self:
            policy = policy_map.get(line.vendor_id.id)
            if not policy:
                line.return_window_end = False
                line.window_status = 'no_policy'
                continue

            basis_date = False
            if policy.date_basis == 'publication':
                basis_date = line.product_id.product_tmpl_id.x_publication_date
            if not basis_date:
                basis_date = line.invoice_date
            if not basis_date:
                line.return_window_end = False
                line.window_status = 'no_policy'
                continue

            window_start = basis_date + timedelta(days=policy.min_days)
            window_end = basis_date + timedelta(days=policy.max_days)
            line.return_window_end = window_end

            if today < window_start:
                line.window_status = 'too_early'
            elif today <= window_end:
                line.window_status = 'within_window'
            else:
                line.window_status = 'expired'

    @api.model
    def action_refresh(self):
        """Refresh return planning lines from current stock and purchase data."""
        # Products with positive on-hand stock
        quant_data = self.env['stock.quant']._read_group(
            [('location_id.usage', '=', 'internal')],
            ['product_id'],
            ['quantity:sum'],
        )
        product_ids = [product.id for product, qty in quant_data if qty > 0]

        # Oldest receipt date per product
        move_data = self.env['stock.move']._read_group(
            [
                ('product_id', 'in', product_ids),
                ('state', '=', 'done'),
                ('location_dest_id.usage', '=', 'internal'),
                ('location_id.usage', '=', 'supplier'),
            ],
            ['product_id'],
            ['date:min'],
        )
        oldest_date = {product.id: dt.date() for product, dt in move_data if dt}

        # Preserve existing lines the user may have edited
        existing = {(l.product_id.id, l.vendor_id.id): l for l in self.search([])}
        seen_keys = set()
        to_create = []

        products = self.env['product.product'].browse(product_ids)
        for product in products:
            vendor = product.seller_ids[:1].partner_id
            if not vendor:
                continue
            key = (product.id, vendor.id)
            seen_keys.add(key)
            if key not in existing:
                to_create.append({
                    'product_id': product.id,
                    'vendor_id': vendor.id,
                    'invoice_date': oldest_date.get(product.id),
                })

        # Remove lines for products no longer in stock
        stale = self.browse([l.id for key, l in existing.items() if key not in seen_keys])
        stale.unlink()

        if to_create:
            self.create(to_create)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Return Planning'),
            'res_model': 'vendor.return.line',
            'view_mode': 'list',
            'target': 'current',
            'context': {'search_default_group_vendor': 1},
        }

    def action_generate_returns(self):
        """Generate return pickings for selected lines with return_qty > 0."""
        lines = self.filtered(lambda l: l.return_qty > 0)
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

        lines.unlink()

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
