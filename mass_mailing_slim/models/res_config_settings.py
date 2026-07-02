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
        string="Strip Dead CSS Classes",
        config_parameter="mass_mailing_slim.strip_classes",
    )
    mass_mailing_slim_trim_defaults = fields.Boolean(
        string="Trim Redundant Inline Defaults",
        config_parameter="mass_mailing_slim.trim_defaults",
    )
    mass_mailing_slim_normalize_css = fields.Boolean(
        string="Normalize CSS Values",
        config_parameter="mass_mailing_slim.normalize_css",
        help="Shorten rgb() colors, hex values, and 0px units in inline styles.",
    )
    mass_mailing_slim_compress_shorthands = fields.Boolean(
        string="Compress Shorthand Properties",
        config_parameter="mass_mailing_slim.compress_shorthands",
        help="Collapse padding/margin/border longhands into shorter shorthands.",
    )
    mass_mailing_slim_minify_style_blocks = fields.Boolean(
        string="Minify <style> Blocks",
        config_parameter="mass_mailing_slim.minify_style_blocks",
        help="Remove comments and whitespace from CSS blocks shipped in the email.",
    )
    mass_mailing_slim_strip_inherited = fields.Boolean(
        string="Strip Inherited Inline Declarations",
        config_parameter="mass_mailing_slim.strip_inherited",
        help="Drop inline font-family/etc. that merely repeat the value already "
             "set on .o_layout and inherited by every descendant.",
    )
    mass_mailing_slim_warn_kb = fields.Integer(
        string="Size Warning Threshold (KB)",
        config_parameter="mass_mailing_slim.warn_kb",
    )
