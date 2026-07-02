# Part of the booksandco custom addons. See LICENSE.
{
    'name': 'Mass Mailing Slim',
    'version': '19.0.1.1.1',
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

NOTE: this is a no-op scaffold. The transform logic in ``tools/html_slim.py``
and the model overrides are stubs until implemented.
""",
    'depends': [
        'mass_mailing',
    ],
    'data': [
        'data/config_params.xml',
        'views/res_config_settings_views.xml',
        'views/mailing_views.xml',
    ],
    'license': 'OEEL-1',
    'author': 'Harry Bird',
    'installable': True,
}
