"""Read-side MCP tools.

Contributes the read tools to :class:`McpMixin` via ``_inherit = 'mcp.mixin'``:
``list_models``, ``get_record``, ``read_field``, ``get_fields``,
``search_records``, ``aggregate_records``, ``list_resource_templates`` and
``get_current_context``.
The LLM-friendly text output reuses the
``tools.formatters`` / ``tools.uri_schema`` / ``tools.smart_fields`` helpers.

Every tool routes through the mixin chokepoints: :meth:`McpMixin._resolve_model`
applies the coarse model gate, then :meth:`McpMixin._check_op` applies the
per-operation (``read``) gate. The tools run as the calling user (``self.env``
is already user-scoped), so Odoo's ``ir.model.access`` and record
rules remain the real enforcement boundary.

**Tool-result shape.** Each tool returns a dict in MCP ``CallToolResult`` form::

    {"content": [{"type": "text", "text": <formatted text>}],
     "structuredContent": {<machine-readable result>}}

The ``/mcp`` controller's ``tools/call`` handler forwards this verbatim,
adding ``isError: false``.
"""

import ast
import json

from odoo import _, api, models
from odoo.exceptions import AccessError, MissingError, UserError

from ..controllers import utils
from ..tools.formatters import DatasetFormatter, RecordFormatter
from ..tools.smart_fields import (
    DEFAULT_MAX_SMART_FIELDS,
    get_smart_default_fields,
    is_sensitive_field_name,
)
from ..tools.uri_schema import build_attachment_uri, build_field_uri
from .mcp_mixin import _BINARY_FIELD_TYPES, mcp_tool

# Pagination fallback defaults. The live values come from the MCP settings
# (``mcp_server.default_limit`` / ``mcp_server.max_limit``); these apply only
# when the corresponding setting is unset.
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

# Deep pagination is query-cost amplification: Postgres still walks (and discards)
# every skipped row, so a huge offset is expensive even though limit is capped.
# Bound the offset to this many max-size pages -- generous for real paging, but
# it stops an ``offset=999999999`` forcing an unbounded skip.
MAX_OFFSET_PAGES = 1000

# Fallback for the maximum number of related records rendered inline by
# ``get_record`` (live value: ``mcp_server.max_related_items``). Kept low: each
# shown collection costs one extra name-resolution read, and larger ones just
# collapse to a count plus a search hint.
DEFAULT_MAX_RELATED_ITEMS = 3

# ``read_field`` page-size fallbacks (live values:
# ``mcp_server.read_field_default_length`` / ``mcp_server.read_field_max_length``).
# Text slices are cheap reads, so these sit far above the record-list limits;
# the max still bounds the size of a single response.
DEFAULT_READ_FIELD_LENGTH = 20000
MAX_READ_FIELD_LENGTH = 100000

# ``fields`` sentinel: explicit request for every field on the model.
_ALL_FIELDS_SENTINEL = "__all__"

# Compact attribute set ``get_fields`` returns when the caller does not request
# specific attributes -- enough to discover a model's schema without the noise.
_CURATED_FIELD_ATTRIBUTES = [
    "type",
    "string",
    "required",
    "readonly",
    "relation",
    "selection",
]

# get_record / search_records need a few more attributes than get_fields exposes:
# smart-field scoring reads ``store``/``searchable``, and the formatter reads
# ``digits`` (float precision) and ``relation_field`` (a one2many's inverse, used
# to build the "view all" search_records hint). Restricting fields_get to this
# superset -- rather than requesting the full field description -- skips the
# costly per-field sortable/groupable/aggregator work while keeping selection +
# formatting behaviour identical.
_RECORD_FIELD_ATTRIBUTES = _CURATED_FIELD_ATTRIBUTES + [
    "store",
    "searchable",
    "digits",
    "relation_field",
]


def _tool_result(text, structured_content=None):
    """Build the shared MCP tool-result dict (see module docstring)."""
    result = {"content": [{"type": "text", "text": text}]}
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return result


def _parse_list_arg(text, kind):
    """Parse a JSON / Python-literal list argument for a coercer.

    Tries JSON first, then a Python literal; raises ``UserError`` (message keyed
    by ``kind`` -- ``"domain"`` or ``"fields"``) when neither parses. Shared by
    :meth:`McpToolsRead._coerce_domain` and :meth:`McpToolsRead._coerce_fields`.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as err:
            if kind == "domain":
                message = _("Invalid domain: expected a list, got %s", text[:100])
            else:
                message = _("Invalid fields: expected a list, got %s", text[:100])
            raise UserError(message) from err


class McpToolsRead(models.AbstractModel):
    """Read tools contributed to the ``mcp.mixin`` tool layer."""

    _inherit = "mcp.mixin"

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _mcp_int_config(self, key, default):
        """Read an integer tuning setting from the MCP configuration.

        sudo: ``ir.config_parameter`` reads are admin-only, but these are
        non-sensitive tuning values. Falls back to ``default`` when the
        parameter is unset or not an integer.
        """
        params = self.env["ir.config_parameter"].sudo()  # non-sensitive tuning param
        value = params.get_param(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _limit_bounds(self):
        """Return the effective ``(default, max)`` record limits from live config.

        Single source of truth for both :meth:`_effective_limit` and the
        advertised ``limit`` description, so the page size a fields-less call
        actually returns can never diverge from what ``tools/list`` promises.
        A misconfigured 0/negative falls back to the module defaults -- Odoo
        treats ``limit <= 0`` as "no limit", which would silently uncap a
        full-table fetch -- and the default is clamped to the max so the cap
        always wins (a Default set above the Maximum still returns at most
        Maximum rows).
        """
        default_limit = self._mcp_int_config("mcp_server.default_limit", DEFAULT_LIMIT)
        max_limit = self._mcp_int_config("mcp_server.max_limit", MAX_LIMIT)
        if default_limit <= 0:
            default_limit = DEFAULT_LIMIT
        if max_limit <= 0:
            max_limit = MAX_LIMIT
        return min(default_limit, max_limit), max_limit

    def _mcp_live_input_schema(self, schema):
        """Return ``schema`` with live tuning values filled into its arguments.

        Argument descriptions may carry ``%(default)s``/``%(max)s``
        placeholders so ``tools/list`` can reflect the configured values. Each
        fillable argument names its own bounds provider below (``limit`` uses
        the record-list bounds, ``length`` the ``read_field`` page-size
        bounds). Arguments without a placeholder are left untouched, and the
        cached schema is never mutated.
        """
        bounds_providers = {
            "limit": self._limit_bounds,
            "length": self._read_field_length_bounds,
        }
        props = schema.get("properties", {})
        new_props = dict(props)
        changed = False
        for arg_name, bounds in bounds_providers.items():
            prop = props.get(arg_name)
            description = prop.get("description", "") if isinstance(prop, dict) else ""
            if "%(default)s" not in description:
                continue
            default, maximum = bounds()
            filled = description % {"default": default, "max": maximum}
            new_props[arg_name] = {**prop, "description": filled}
            changed = True
        if not changed:
            return schema
        return {**schema, "properties": new_props}

    def _read_field_length_bounds(self):
        """Effective ``(default, max)`` page length for ``read_field``.

        Mirrors :meth:`_limit_bounds`: live config with module-default
        fallback, a misconfigured 0/negative falls back to the defaults, and
        the default is clamped to the max.
        """
        default_length = self._mcp_int_config(
            "mcp_server.read_field_default_length", DEFAULT_READ_FIELD_LENGTH
        )
        max_length = self._mcp_int_config(
            "mcp_server.read_field_max_length", MAX_READ_FIELD_LENGTH
        )
        if default_length <= 0:
            default_length = DEFAULT_READ_FIELD_LENGTH
        if max_length <= 0:
            max_length = MAX_READ_FIELD_LENGTH
        return min(default_length, max_length), max_length

    @staticmethod
    def _coerce_domain(domain):
        """Coerce a domain argument into an Odoo domain list.

        Accepts a list (passed through), a JSON / Python-literal string, or
        ``None`` (-> ``[]``). Tolerant parsing so LLM
        clients that stringify the domain still work.
        """
        if domain is None:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        if isinstance(domain, str):
            text = domain.strip()
            if not text:
                return []
            parsed = _parse_list_arg(text, "domain")
            if not isinstance(parsed, (list, tuple)):
                raise UserError(_("Domain must be a list."))
            return list(parsed)
        raise UserError(_("Domain must be a list."))

    @staticmethod
    def _coerce_fields(fields):
        """Coerce a ``fields`` argument into a list, ``None`` or the sentinel.

        Accepts a list, a JSON / Python-literal string, or ``None``. Returns
        ``None`` for an absent/empty selection (caller applies smart defaults).
        """
        if fields is None:
            return None
        if isinstance(fields, str):
            text = fields.strip()
            if not text:
                return None
            fields = _parse_list_arg(text, "fields")
        if isinstance(fields, (list, tuple)):
            fields = list(fields)
            return fields or None
        raise UserError(_("Fields must be a list of field names."))

    def _resolve_fields(self, fields, fields_metadata):
        """Decide which fields to read and how the selection was made.

        :return: ``(selection_method, fields_to_read)`` where ``selection_method``
            is one of ``smart_defaults``/``all``/``explicit`` and
            ``fields_to_read`` is a list (or ``None`` to read all fields).
        """
        fields = self._coerce_fields(fields)
        if fields is None:
            max_fields = self._mcp_int_config(
                "mcp_server.max_smart_fields", DEFAULT_MAX_SMART_FIELDS
            )
            return "smart_defaults", get_smart_default_fields(
                fields_metadata, max_fields=max_fields
            )
        if fields == [_ALL_FIELDS_SENTINEL]:
            return "all", None
        return "explicit", fields

    def _effective_limit(self, limit):
        """Apply the default/cap policy to a requested ``limit`` (live config)."""
        # ``_limit_bounds`` already applies the 0/negative fallback and clamps
        # the default down to the max, so the no-limit path below stays capped.
        default_limit, max_limit = self._limit_bounds()
        if not limit:  # None / 0 / "" -> use the (already max-clamped) default
            return default_limit
        # Coerce before comparing: a client may send a string limit, and
        # ``"abc" <= 0`` would raise a raw TypeError. A non-integer is a clean
        # client error (surfaced as an isError result), not a -32602.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise UserError(_("'limit' must be an integer.")) from None
        if limit <= 0:
            return default_limit
        return min(limit, max_limit)

    def _effective_offset(self, offset):
        """Coerce and upper-bound a requested pagination ``offset`` (live config).

        Mirrors ``_effective_limit``'s coercion -- a non-integer offset is a clean
        client error (surfaced as an isError result), not a raw -32602 -- then
        caps the offset at ``MAX_OFFSET_PAGES`` max-size pages so a caller cannot
        force Postgres to skip an unbounded row count (query-cost amplification).
        """
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise UserError(_("'offset' must be an integer.")) from None
        _default_limit, max_limit = self._limit_bounds()
        return min(offset, max_limit * MAX_OFFSET_PAGES)

    @staticmethod
    def _strip_sensitive_fields(record):
        """Drop credential-named fields from a read result in place.

        Applied only on the *bulk* read paths -- the smart-default selection
        (which already scores these to 0) and the ``["__all__"]`` sentinel -- so a
        field named like a credential (``*_api_key``, ``*password``,
        ``webhook_secret`` ...) is not surfaced by a caller that did not ask for
        it by name. An explicitly-named field is honored (never stripped): Odoo
        field-level ``groups=`` is the real ACL there, this is best-effort
        defense in depth. Mutates ``record`` in place; returns nothing.
        """
        for name in [name for name in record if is_sensitive_field_name(name)]:
            del record[name]

    @staticmethod
    def _binary_field_names(fields_metadata):
        """Names of binary/image fields in ``fields_metadata``."""
        return {
            name
            for name, meta in fields_metadata.items()
            if (meta or {}).get("type") in _BINARY_FIELD_TYPES
        }

    # ------------------------------------------------------------------
    # list_models
    # ------------------------------------------------------------------
    @mcp_tool(
        name="list_models",
        title="List Models",
        description=(
            "List all models enabled for MCP access with their allowed "
            "operations (read, create, write, unlink)."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def list_models(self):
        """Return the MCP-enabled models with their allowed operations."""
        # get_enabled_models already returns dicts with model/name/operations.
        enriched = utils.get_enabled_models(self.env)

        lines = [
            "=" * 60,
            _("MCP-enabled models (%s)", len(enriched)),
            "=" * 60,
        ]
        if not enriched:
            lines.append(_("No models are currently enabled for MCP access."))
        for entry in enriched:
            ops = entry["operations"] or {}
            allowed = ", ".join(
                op for op in ("read", "create", "write", "unlink") if ops.get(op)
            )
            ops_label = allowed or _("no operations")
            lines.append(f"- {entry['name']} ({entry['model']}) [{ops_label}]")

        return _tool_result("\n".join(lines), {"models": enriched})

    # ------------------------------------------------------------------
    # get_record
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_record",
        title="Get Record",
        description=(
            "Get a single record by ID with smart field selection. Omit "
            "'fields' for a smart default selection, pass a list of field "
            'names for specific fields, or ["__all__"] for every field.'
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record ID to retrieve.",
                },
                "fields": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Field selection: omit/null for smart defaults, a list "
                        'of field names, or ["__all__"] for all fields.'
                    ),
                },
            },
            "required": ["model", "record_id"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_record(self, model, record_id, fields=None):
        """Read one record, formatting it for LLM consumption."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        fields_metadata = model_rs.fields_get(attributes=_RECORD_FIELD_ATTRIBUTES)
        selection_method, fields_to_read = self._resolve_fields(fields, fields_metadata)

        record_rs = self._browse_record_or_raise(model, model_rs, record_id)

        # Runs as the calling user -> ORM enforces read ACLs / record rules.
        # bin_size: binary fields are only ever turned into odoo:// URIs below
        # (never returned inline), so load their size placeholder rather than the
        # full base64 blob -- the bytes are re-fetched separately on resources/read.
        data = record_rs.with_context(bin_size=True).read(fields_to_read)[0]
        # Guard the bulk paths only (smart defaults + the ``["__all__"]``
        # sentinel); an explicit field list is honored -- see
        # _strip_sensitive_fields.
        if selection_method != "explicit":
            self._strip_sensitive_fields(data)
        render_metadata = self._replace_binaries(
            model, int(record_id), data, fields_metadata
        )

        max_related_items = self._mcp_int_config(
            "mcp_server.max_related_items", DEFAULT_MAX_RELATED_ITEMS
        )
        related_summaries = self._resolve_related_summaries(
            data, fields_metadata, max_related_items
        )
        enabled_relations = self._enabled_relation_models(data, fields_metadata)
        text = RecordFormatter(model).format_record(
            data,
            render_metadata,
            related_summaries=related_summaries,
            enabled_relations=enabled_relations,
        )

        structured = {"record": data}
        if selection_method == "smart_defaults":
            structured["metadata"] = {
                "fields_returned": len(data),
                "field_selection_method": selection_method,
                "total_fields_available": len(fields_metadata),
                "note": _(
                    'Smart field selection used. Pass fields=["__all__"] for '
                    "every field, or a list of field names for a specific subset."
                ),
            }
        return _tool_result(text, structured)

    def _replace_binaries(self, model, record_id, data, fields_metadata):
        """Swap populated binary values for ``odoo://`` URIs (in place).

        Returns the field metadata to hand the formatter: a shallow copy with
        the replaced fields re-typed to ``char`` so the URI string renders
        plainly instead of the generic ``[Binary data]`` placeholder. Untouched
        when the record carries no populated binary field.
        """
        render_metadata = fields_metadata
        for name in self._binary_field_names(fields_metadata):
            if data.get(name):
                data[name] = build_field_uri(model, record_id, name)
                if render_metadata is fields_metadata:
                    render_metadata = dict(fields_metadata)
                render_metadata[name] = {**fields_metadata[name], "type": "char"}
        return render_metadata

    def _resolve_related_summaries(self, data, fields_metadata, max_related_items):
        """Resolve display names for small x2many collections (inline preview).

        For each one2many/many2many field in ``data`` holding between 1 and
        ``max_related_items`` ids, read the related records' ``display_name``
        under the calling user's env (so record rules apply) and return
        ``{field_name: [(id, name), ...]}`` for the formatter to list inline.
        A field the caller cannot read is omitted -- the formatter falls back to
        the count plus a search hint. Larger collections are skipped so the read
        stays cheap and the output short.

        The relation is first gated on the same per-model opt-in as
        ``search_records`` (model enabled + ``read`` allowed): an inline preview
        must never expose display names from a model the admin did not expose via
        MCP, even when the caller's Odoo ACL alone would permit the read.
        """
        summaries = {}
        if max_related_items <= 0:
            return summaries
        for name, value in data.items():
            meta = fields_metadata.get(name) or {}
            if meta.get("type") not in ("one2many", "many2many"):
                continue
            relation = meta.get("relation")
            if not relation or not isinstance(value, list):
                continue
            if not 0 < len(value) <= max_related_items:
                continue
            if not self._relation_mcp_read_enabled(relation):
                continue
            try:
                related = self.env[relation].browse(value).read(["display_name"])
            except (AccessError, MissingError):
                continue
            summaries[name] = [
                (rec["id"], rec.get("display_name") or f"id {rec['id']}")
                for rec in related
            ]
        return summaries

    def _enabled_relation_models(self, data, fields_metadata):
        """Return the x2many target models in ``data`` that are MCP-read-enabled.

        The formatter uses this to decide whether a collapsed relation may offer
        a ``search_records`` "view all" hint: a model that is not exposed via MCP
        would only error, so no hint is advertised for it. Mirrors the gate
        ``search_records`` itself applies (model enabled + ``read`` allowed).
        """
        enabled = set()
        for name, value in data.items():
            meta = fields_metadata.get(name) or {}
            if meta.get("type") not in ("one2many", "many2many"):
                continue
            relation = meta.get("relation")
            if not relation or relation in enabled or not value:
                continue
            if not self._relation_mcp_read_enabled(relation):
                continue
            enabled.add(relation)
        return enabled

    def _relation_mcp_read_enabled(self, relation):
        """Whether ``relation`` is exposed for MCP read (enabled + ``read`` op).

        Mirrors the gate ``search_records`` applies. Shared by the inline x2many
        preview (``_related_summaries``) and the "view all" hint gate
        (``_enabled_relation_models``): a model not exposed via MCP would only
        error, so neither touches it.
        """
        try:
            self._resolve_model(relation)
            self._check_op(relation, "read")
        except (AccessError, UserError):
            return False
        return True

    # ------------------------------------------------------------------
    # read_field
    # ------------------------------------------------------------------
    @mcp_tool(
        name="read_field",
        title="Read Field",
        description=(
            "Read the raw value of a single text-like field (char, text or "
            "html) on one record, paged with 'offset' and 'length'. Intended "
            "for long values (e.g. website page or view arch) that get_record "
            "truncates for display."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'ir.ui.view').",
                },
                "record_id": {
                    "type": "integer",
                    "description": "The record ID to read from.",
                },
                "field": {
                    "type": "string",
                    "description": "Field name (must be char, text or html).",
                },
                "offset": {
                    "type": ["integer", "null"],
                    "description": "Start character offset (default 0).",
                },
                "length": {
                    "type": ["integer", "null"],
                    "description": (
                        "Characters to return (default %(default)s, max "
                        "%(max)s). Re-call with offset += returned chars "
                        "while has_more is true to page through the value."
                    ),
                },
            },
            "required": ["model", "record_id", "field"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def read_field(self, model, record_id, field, offset=0, length=None):
        """Read a slice of one text-like field, with explicit paging metadata."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        field_obj = model_rs._fields.get(field)
        if field_obj is None:
            raise UserError(
                _(
                    "Unknown field '%(field)s' on model '%(model)s'.",
                    field=field,
                    model=model,
                )
            )
        if field_obj.type not in ("char", "text", "html"):
            raise UserError(
                _(
                    "read_field supports char/text/html fields; '%(field)s' "
                    "on '%(model)s' is of type '%(type)s'.",
                    field=field,
                    model=model,
                    type=field_obj.type,
                )
            )

        record_rs = self._browse_record_or_raise(model, model_rs, record_id)
        # Runs as the calling user -> ORM enforces read ACLs / record rules.
        value = str(record_rs.read([field])[0].get(field) or "")

        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise UserError(_("'offset' must be an integer.")) from None
        default_length, max_length = self._read_field_length_bounds()
        if length is None:
            length = default_length
        try:
            length = int(length)
        except (TypeError, ValueError):
            raise UserError(_("'length' must be an integer.")) from None
        if length <= 0:
            raise UserError(_("'length' must be positive."))
        length = min(length, max_length)

        total = len(value)
        chunk = value[offset : offset + length]
        has_more = offset + len(chunk) < total

        lines = [
            "=" * 60,
            _("Field: %(model)s/%(id)s %(field)s", model=model, id=record_id, field=field),
            "=" * 60,
            _("Total length: %s chars", total),
            _("Showing: chars %(start)s-%(end)s", start=offset, end=offset + len(chunk)),
        ]
        if has_more:
            lines.append(
                _("More available: re-call with offset=%s", offset + len(chunk))
            )
        lines.append("")
        lines.append(chunk)

        return _tool_result(
            "\n".join(lines),
            {
                "model": model,
                "record_id": int(record_id),
                "field": field,
                "total": total,
                "offset": offset,
                "returned": len(chunk),
                "has_more": has_more,
                "value": chunk,
            },
        )

    # ------------------------------------------------------------------
    # get_fields
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_fields",
        title="Get Fields",
        description=(
            "Describe a model's fields: type, label, required/readonly, "
            "relation target, and selection options. Use it to discover a "
            "model's schema before reading or writing records."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "field_names": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Restrict the result to these field names. Omit/null "
                        "to describe every field on the model."
                    ),
                },
                "attributes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Which field attributes to return. Omit/null for the "
                        "curated default set (type, string, required, readonly, "
                        "relation, selection). Pass an explicit list to request "
                        "more, e.g. help or store."
                    ),
                },
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_fields(self, model, field_names=None, attributes=None):
        """Describe a model's fields for schema discovery."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        selected_attributes = attributes or _CURATED_FIELD_ATTRIBUTES
        # Runs as the calling user (no sudo) -> field-level group ACLs apply.
        fields_metadata = model_rs.fields_get(
            field_names or None, attributes=list(selected_attributes)
        )

        fields = [
            {"name": fname, **meta} for fname, meta in sorted(fields_metadata.items())
        ]
        structured = {"model": model, "fields": fields, "total": len(fields)}
        return _tool_result(self._format_fields_text(model, fields), structured)

    @staticmethod
    def _format_fields_text(model, fields):
        """Render field definitions as concise, LLM-friendly text."""
        lines = [
            "=" * 60,
            _("Fields: %(model)s (%(count)s)", model=model, count=len(fields)),
            "=" * 60,
        ]
        for field in fields:
            ftype = field.get("type") or "?"
            relation = field.get("relation")
            type_part = f"{ftype}→{relation}" if relation else ftype
            line = f"{field['name']} ({type_part})"
            label = field.get("string")
            if label:
                line += f" — {label}"
            flags = []
            if field.get("required"):
                flags.append(_("required"))
            if field.get("readonly"):
                flags.append(_("readonly"))
            if flags:
                line += f" [{', '.join(flags)}]"
            selection = field.get("selection")
            if selection:
                line += ": " + ", ".join(str(option[0]) for option in selection)
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # search_records
    # ------------------------------------------------------------------
    @mcp_tool(
        name="search_records",
        title="Search Records",
        description=(
            "Search for records in a model. Returns a smart field selection by "
            "default with pagination. Use 'domain' to filter, 'fields' to pick "
            "columns, and 'limit'/'offset'/'order' to page and sort."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'res.partner').",
                },
                "domain": {
                    "type": ["array", "string", "null"],
                    "description": (
                        "Odoo domain filter as a list (e.g. "
                        '[["is_company", "=", true]]) or JSON string. '
                        "Omit for all records."
                    ),
                },
                "fields": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Field selection: omit/null for smart defaults, a list "
                        'of field names, or ["__all__"] for all fields.'
                    ),
                },
                "limit": {
                    "type": ["integer", "null"],
                    # Live default/max filled in at tools/list serve time.
                    "description": (
                        "Max records to return. Defaults to %(default)s, capped "
                        "at %(max)s."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Number of records to skip (pagination).",
                },
                "order": {
                    "type": ["string", "null"],
                    "description": "Sort order, e.g. 'name asc'.",
                },
            },
            "required": ["model"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def search_records(
        self, model, domain=None, fields=None, limit=None, offset=0, order=None
    ):
        """Search records and format the page for LLM consumption."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        parsed_domain = self._coerce_domain(domain)
        limit = self._effective_limit(limit)
        offset = self._effective_offset(offset)

        fields_metadata = model_rs.fields_get(attributes=_RECORD_FIELD_ATTRIBUTES)
        selection_method, fields_to_read = self._resolve_fields(fields, fields_metadata)

        # bin_size: any binary field is replaced by an odoo:// URI below, so load
        # size placeholders instead of full blobs (the bytes are re-fetched on
        # resources/read). Only affects binary fields; others read normally.
        rows = model_rs.with_context(bin_size=True).search_read(
            parsed_domain,
            fields_to_read,
            offset=offset,
            limit=limit,
            order=order or None,
        )
        # Guard the bulk paths only (smart defaults + the ``["__all__"]``
        # sentinel); an explicit field list is honored -- see
        # _strip_sensitive_fields.
        if selection_method != "explicit":
            for row in rows:
                self._strip_sensitive_fields(row)

        # Skip the extra count query when the page already reveals the total: a
        # first page that came back short of its limit holds every match.
        # Otherwise an exact count is needed for the pagination math.
        if offset == 0 and (not limit or len(rows) < limit):
            total = len(rows)
        else:
            total = model_rs.search_count(parsed_domain)

        binary_names = self._binary_field_names(fields_metadata)
        if binary_names:
            for row in rows:
                row_id = row.get("id")
                for name in binary_names:
                    if row_id and row.get(name):
                        row[name] = build_field_uri(model, row_id, name)

        total_pages = (total + limit - 1) // limit if limit else 1
        current_page = (offset // limit) + 1 if limit else 1
        next_hint = prev_hint = None
        if offset + len(rows) < total:
            next_hint = _(
                "search_records with offset=%(offset)s, limit=%(limit)s",
                offset=offset + limit,
                limit=limit,
            )
        if offset > 0:
            prev_hint = _(
                "search_records with offset=%(offset)s, limit=%(limit)s",
                offset=max(0, offset - limit),
                limit=limit,
            )

        text = DatasetFormatter(model).format_search_results(
            rows,
            domain=parsed_domain or None,
            fields=fields_to_read,
            limit=limit,
            offset=offset,
            total_count=total,
            fields_metadata=fields_metadata,
            next_hint=next_hint,
            prev_hint=prev_hint,
            current_page=current_page,
            total_pages=total_pages,
        )

        structured = {
            "records": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "model": model,
        }
        return _tool_result(text, structured)

    # ------------------------------------------------------------------
    # aggregate_records
    # ------------------------------------------------------------------
    @mcp_tool(
        name="aggregate_records",
        title="Aggregate Records",
        description=(
            "Aggregate records server-side (totals/counts/groupings) via Odoo's "
            "grouping engine. Use this instead of search_records when the "
            "question is about quantities per group rather than a list of rows."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Technical model name (e.g. 'sale.order').",
                },
                "groupby": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Group expressions. Field names, optionally with a "
                        "date/datetime granularity suffix, e.g. "
                        '["date_order:month"], ["partner_id"].'
                    ),
                },
                "aggregates": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        'Aggregate expressions "field:operator" (e.g. '
                        '["amount_total:sum"]). Defaults to ["__count"].'
                    ),
                },
                "domain": {
                    "type": ["array", "string", "null"],
                    "description": "Odoo domain filter as a list or JSON string.",
                },
                "order": {
                    "type": ["string", "null"],
                    "description": "Sort over groupby keys / aggregates.",
                },
                "limit": {
                    "type": ["integer", "null"],
                    # Live default/max filled in at tools/list serve time.
                    "description": (
                        "Max number of groups. Defaults to %(default)s, capped "
                        "at %(max)s."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Number of groups to skip.",
                },
            },
            "required": ["model", "groupby"],
            "additionalProperties": False,
        },
        operation="read",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    @api.model
    def aggregate_records(
        self,
        model,
        groupby,
        aggregates=None,
        domain=None,
        order=None,
        limit=None,
        offset=0,
    ):
        """Group records server-side via Odoo 19 ``formatted_read_group``."""
        model_rs = self._resolve_model(model)
        self._check_op(model, "read")

        if not groupby:
            raise UserError(
                _("groupby must not be empty (use search_records for a flat list).")
            )
        if isinstance(groupby, str):
            groupby = [groupby]

        # Coerce a bare string just like ``groupby`` above: a client may send
        # ``"amount:sum"`` instead of ``["amount:sum"]``; without this
        # ``list(...)`` would explode it into a list of characters.
        if isinstance(aggregates, str):
            aggregates = [aggregates]

        parsed_domain = self._coerce_domain(domain)
        limit = self._effective_limit(limit)
        offset = self._effective_offset(offset)
        effective_aggregates = list(aggregates) if aggregates else ["__count"]

        # Runs as the calling user -> raises AccessError when read is not
        # permitted. formatted_read_group offers no cheap "count of groups", so
        # peek one group past the page (limit+1): if it comes back, the result is
        # truncated and we signal has_more rather than pass off a partial "top
        # N" as complete. Mirrors search_records' next_hint.
        groups = model_rs.formatted_read_group(
            parsed_domain,
            groupby=list(groupby),
            aggregates=effective_aggregates,
            offset=offset,
            limit=limit + 1 if limit else None,
            order=order or None,
        )
        has_more = bool(limit) and len(groups) > limit
        if has_more:
            groups = groups[:limit]

        # Drop the grouping engine's internal per-group keys (e.g.
        # ``__extra_domain``, ``__fold``) so they never reach the client. The
        # requested aggregates are kept even when ``__``-prefixed (the default
        # ``__count`` is the count value the caller asked for).
        cleaned_groups = [
            {
                key: value
                for key, value in group.items()
                if not key.startswith("__") or key in effective_aggregates
            }
            for group in groups
        ]

        next_hint = None
        if has_more:
            next_hint = _(
                "aggregate_records with offset=%(offset)s, limit=%(limit)s",
                offset=offset + limit,
                limit=limit,
            )

        text = self._format_aggregate_text(
            model, list(groupby), effective_aggregates, cleaned_groups, next_hint
        )
        structured = {
            "groups": cleaned_groups,
            "model": model,
            "groupby": list(groupby),
            "aggregates": effective_aggregates,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }
        return _tool_result(text, structured)

    @staticmethod
    def _format_aggregate_text(model, groupby, aggregates, groups, next_hint=None):
        """Render aggregation buckets as compact LLM-friendly text."""
        lines = [
            "=" * 60,
            _("Aggregate: %s", model),
            "=" * 60,
            _("Group by: %s", ", ".join(groupby)),
            _("Aggregates: %s", ", ".join(aggregates)),
            _("Groups: %s", len(groups)),
            "",
        ]
        for idx, group in enumerate(groups, 1):
            parts = []
            for key in groupby:
                value = group.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    value = f"{value[1]} (ID: {value[0]})"
                elif value is False or value is None:
                    value = _("None")
                parts.append(f"{key}={value}")
            for key in aggregates:
                parts.append(f"{key}={group.get(key)}")
            lines.append(f"[{idx}] " + " | ".join(parts))
        if next_hint:
            lines.append("")
            lines.append(_("More groups available -- next page: %s", next_hint))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # list_resource_templates
    # ------------------------------------------------------------------
    @mcp_tool(
        name="list_resource_templates",
        title="List Resource Templates",
        description=(
            "List the available odoo:// resource URI templates (record binary "
            "fields and attachments) usable with resources/read."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def list_resource_templates(self):
        """Advertise the native ``odoo://`` resource templates."""
        models_info = utils.get_enabled_models(self.env)
        model_names = [info["model"] for info in models_info]

        # Emit the LITERAL template strings (matching the controller's
        # resources/templates/list); do NOT route the {placeholder} forms
        # through build_field_uri, which validates the model name and would
        # reject the literal "{model}" token.
        templates = [
            {
                "uri_template": "odoo://record/{model}/{id}/{field}",
                "description": _(
                    "Fetch a record's binary/image field (e.g. an attachment "
                    "or image) instead of inlining base64."
                ),
                "parameters": {
                    "model": _("Odoo model name (e.g. res.partner)"),
                    "id": _("Record ID (e.g. 10)"),
                    "field": _("Binary field name (e.g. image_1920)"),
                },
                "example": build_field_uri("res.partner", 10, "image_1920"),
            },
            {
                "uri_template": "odoo://attachment/{id}",
                "description": _("Fetch an ir.attachment by ID."),
                "parameters": {
                    "id": _("ir.attachment record ID"),
                },
                "example": build_attachment_uri(42),
            },
        ]

        note = _(
            "Resource URIs do not support query parameters. Use the "
            "search_records / get_record tools for filtering, pagination and "
            "field selection."
        )
        text_lines = ["=" * 60, _("Resource templates"), "=" * 60]
        for template in templates:
            text_lines.append(
                f"- {template['uri_template']}: {template['description']}"
            )
        text_lines.append("")
        text_lines.append(note)

        structured = {
            "templates": templates,
            "enabled_models": model_names[:10],
            "total_models": len(model_names),
            "note": note,
        }
        return _tool_result("\n".join(text_lines), structured)

    # ------------------------------------------------------------------
    # get_current_context
    # ------------------------------------------------------------------
    @mcp_tool(
        name="get_current_context",
        title="Get Current Context",
        description=(
            "Return the current session context: the connected user, their "
            "timezone, the active company plus any other allowed companies, and "
            "UTC datetime-handling guidance. Call it when unsure which user or "
            "company a request runs as, or how to interpret datetimes. "
            "Spec-compliant clients also receive this via the initialize "
            "response."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        operation=None,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    @api.model
    def get_current_context(self):
        """Return the calling user's session context.

        Not model-gated: it routes through neither ``_resolve_model`` nor
        ``_check_op``, so it is reachable whenever the global MCP switch is on
        regardless of the ``mcp.enabled.model`` registry. It exposes only the
        caller's own user/company info -- no new data surface.
        """
        return _tool_result(utils.build_user_context(self.env))
