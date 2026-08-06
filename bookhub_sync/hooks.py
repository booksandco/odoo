"""Post-install setup that cannot be expressed in data XML.

``is_published`` is injected onto ``product.template`` through the
``website.published.mixin``, so no external id exists for the
``ir.model.fields`` row and it cannot be referenced with ``ref()`` in
``base_automation.xml``.  Look it up by model/name and attach it as a
trigger field here instead.
"""


def post_init_hook(env):
    field = env['ir.model.fields'].sudo()._get('product.template', 'is_published')
    automation = env.ref('bookhub_sync.automation_product_template_updated', raise_if_not_found=False)
    if field and automation:
        automation.trigger_field_ids = [(4, field.id)]
