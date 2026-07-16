from odoo import api, fields, models


class BookPublisher(models.Model):
    _name = 'bookstore.publisher'
    _description = 'Book Publisher'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    slug = fields.Char(string='Slug', required=True, index=True)
    hardcover_id = fields.Integer(string='Hardcover ID', index=True)

    _slug_uniq = models.Constraint(
        'unique(slug)',
        'Publisher slug must be unique.',
    )
    _hardcover_id_uniq = models.Constraint(
        'unique(hardcover_id)',
        'Hardcover ID must be unique.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('slug'):
                vals['slug'] = self._generate_unique_slug(vals.get('name'))
        return super().create(vals_list)

    def write(self, vals):
        if 'name' in vals and 'slug' not in vals:
            vals['slug'] = self._generate_unique_slug(vals['name'], exclude_id=self.id)
        return super().write(vals)

    @api.model
    def _hardcover_get_or_create(self, name, hardcover_id=False):
        """Find or create a publisher by Hardcover ID or by name."""
        if hardcover_id:
            publisher = self.search([('hardcover_id', '=', hardcover_id)], limit=1)
            if publisher:
                if name and publisher.name != name:
                    publisher.name = name
                return publisher
        if name:
            publisher = self.search([('name', '=ilike', name)], limit=1)
            if publisher:
                if hardcover_id and not publisher.hardcover_id:
                    publisher.hardcover_id = hardcover_id
                return publisher
            vals = {'name': name}
            if hardcover_id:
                vals['hardcover_id'] = hardcover_id
            return self.create(vals)
        return self.browse()

    @api.model
    def _generate_unique_slug(self, name, exclude_id=None):
        if not name:
            return 'unknown'
        base_slug = self.env['ir.http']._slugify_one(name)
        slug = base_slug
        counter = 1
        while self.search_count([('slug', '=', slug), ('id', '!=', exclude_id or 0)]):
            slug = f'{base_slug}-{counter}'
            counter += 1
        return slug

    @api.depends('name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.name
