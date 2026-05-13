from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestVendorReturnOrder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Vendor',
            'supplier_rank': 1,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'product',
            'standard_price': 10.0,
        })
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')

    def test_create_order(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        self.assertTrue(order.name)
        self.assertNotEqual(order.name, 'New')
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.company_id, self.env.company)

    def test_state_transitions(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        order.action_mark_sent()
        self.assertEqual(order.state, 'sent')
        order.action_confirm()
        self.assertEqual(order.state, 'confirmed')
        order.action_cancel()
        self.assertEqual(order.state, 'cancel')
        order.action_draft()
        self.assertEqual(order.state, 'draft')

    def test_invalid_transitions(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        with self.assertRaises(UserError):
            order.action_confirm()
        order.action_mark_sent()
        order.action_confirm()
        with self.assertRaises(UserError):
            order.action_draft()
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_action_generate_picks_smoke(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        self.env['vendor.return.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_qty': 1.0,
            'product_uom': self.uom_unit.id,
            'price_unit': 10.0,
        })
        order.action_mark_sent()
        order.action_confirm()
        order.action_generate_picks()
        self.assertTrue(order.picking_ids)

    def test_action_create_invoice_blocked_without_done_picking(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        self.env['vendor.return.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_qty': 1.0,
            'product_uom': self.uom_unit.id,
            'price_unit': 10.0,
        })
        order.action_mark_sent()
        order.action_confirm()
        with self.assertRaises(UserError):
            order.action_create_invoice()

    def test_duplicate_order(self):
        order = self.env['vendor.return.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
        })
        self.env['vendor.return.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_qty': 1.0,
            'product_uom': self.uom_unit.id,
            'price_unit': 10.0,
        })
        order.action_mark_sent()
        order.action_confirm()
        order.action_generate_picks()
        self.assertTrue(order.order_line.move_ids)
        copy = order.copy()
        self.assertFalse(copy.picking_ids)
        self.assertFalse(copy.invoice_ids)
        for line in copy.order_line:
            self.assertFalse(line.move_ids)
            self.assertFalse(line.invoice_lines)
