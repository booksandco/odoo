# Part of the booksandco custom addons. See LICENSE.
{
    'name': 'Mass Mailing Slim',
    'version': '19.0.1.4.0',
    'category': 'Marketing',
    'summary': 'Shrink Email Marketing HTML below Gmail\'s 102 KB clip limit',
    'description': """
Mass Mailing Slim
=================

Post-processes the HTML that Email Marketing sends so newsletters stay under
Gmail's ~102 KB clipping threshold, and fixes two related defects:

* the ``text/plain`` alternative leaking raw CSS (``html2plaintext`` never
  removes ``<style>``/``<script>`` text);
* the open-tracking pixel sitting at the very end of the body, so a clipped
  mail never fires it.

All transforms run server-side, are gated by ``ir.config_parameter`` flags, and
never modify Odoo core. See ``PLAN.md`` for the full design.
""",
    'depends': [
        'mass_mailing',
    ],
    'data': [
        'views/res_config_settings_views.xml',
        'views/mailing_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'license': 'OEEL-1',
    'author': 'Harry Bird',
    'installable': True,
}
