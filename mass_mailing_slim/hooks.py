# Part of the booksandco custom addons. See LICENSE.
"""Seed default config parameters without colliding with UI-set values.

We deliberately do NOT ship ``<record model="ir.config_parameter">`` rows:
``res.config.settings`` toggles create ``ir.config_parameter`` keys via
``set_param`` with no external id, so a data-file record with an external id
would later try to ``CREATE`` a row whose ``key`` already exists and blow up
the module upgrade with a ``ir_config_parameter_key_uniq`` unique violation.

``set_param`` is an idempotent upsert, so seeding here is collision-proof. We
only seed a key when it is genuinely absent, to preserve any value the user
has already chosen.
"""

# Defaults mirror the fallbacks used by the get_param(...) reads in the models.
_DEFAULTS = {
    "mass_mailing_slim.enabled": "True",
    "mass_mailing_slim.minify": "True",
    "mass_mailing_slim.fix_plaintext": "True",
    "mass_mailing_slim.move_pixel": "True",
    "mass_mailing_slim.strip_classes": "False",
    "mass_mailing_slim.trim_defaults": "False",
    "mass_mailing_slim.normalize_css": "False",
    "mass_mailing_slim.compress_shorthands": "False",
    "mass_mailing_slim.minify_style_blocks": "False",
    "mass_mailing_slim.strip_inherited": "False",
    "mass_mailing_slim.warn_kb": "100",
    "mass_mailing_slim.class_allowlist": "o_layout,o_mail_snippet_general",
}

_MISSING = object()


def post_init_hook(env):
    """Seed missing mass_mailing_slim.* config parameters on fresh install."""
    icp = env["ir.config_parameter"].sudo()
    for key, value in _DEFAULTS.items():
        if icp.get_param(key, _MISSING) is _MISSING:
            icp.set_param(key, value)
