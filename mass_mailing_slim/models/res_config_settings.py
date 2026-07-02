# Part of the booksandco custom addons. See LICENSE.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Expose the mass_mailing_slim transforms as Settings toggles.

    Boolean values are stored as ``"True"``/``"False"`` strings by
    ``res.config.settings`` (via ``set_param``), matching the direct
    ``get_param(...) == 'True'`` checks in the model overrides.
    """

    _inherit = "res.config.settings"

    mass_mailing_slim_enabled = fields.Boolean(
        string="Slim Outgoing Mailings",
        config_parameter="mass_mailing_slim.enabled",
        help="Post-process newsletter HTML to reduce size and avoid Gmail clipping.",
    )
    mass_mailing_slim_minify = fields.Boolean(
        string="Minify Email HTML",
        config_parameter="mass_mailing_slim.minify",
    )
    mass_mailing_slim_fix_plaintext = fields.Boolean(
        string="Strip CSS from Plain-Text Part",
        config_parameter="mass_mailing_slim.fix_plaintext",
    )
    mass_mailing_slim_move_pixel = fields.Boolean(
        string="Move Tracking Pixel to Top",
        config_parameter="mass_mailing_slim.move_pixel",
    )
    mass_mailing_slim_strip_classes = fields.Boolean(
        string="Strip Dead CSS Classes (aggressive)",
        config_parameter="mass_mailing_slim.strip_classes",
    )
    mass_mailing_slim_trim_defaults = fields.Boolean(
        string="Trim Redundant Inline Defaults (aggressive)",
        config_parameter="mass_mailing_slim.trim_defaults",
    )
    mass_mailing_slim_warn_kb = fields.Integer(
        string="Size Warning Threshold (KB)",
        config_parameter="mass_mailing_slim.warn_kb",
    )
