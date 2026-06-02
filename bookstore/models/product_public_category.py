from odoo import api, fields, models


class ProductPublicCategory(models.Model):
    _inherit = 'product.public.category'

    product_count = fields.Integer(
        string='# Products',
        compute='_compute_product_count',
        help="The number of products directly linked to this category",
    )

    complete_name = fields.Char(
        'Complete Name',
        compute='_compute_complete_name',
        recursive=True,
        store=True,
    )

    @api.depends('product_tmpl_ids')
    def _compute_product_count(self):
        for category in self:
            category.product_count = len(category.product_tmpl_ids)

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for category in self:
            if category.parent_id:
                category.complete_name = '%s / %s' % (category.parent_id.complete_name, category.name)
            else:
                category.complete_name = category.name

    def action_view_products(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Products',
            'res_model': 'product.template',
            'view_mode': 'kanban,list,form',
            'domain': [('public_categ_ids', 'in', self.ids)],
            'context': dict(self.env.context, default_public_categ_ids=[(6, 0, self.ids)]),
        }
