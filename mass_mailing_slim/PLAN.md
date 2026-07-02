# Plan: `mass_mailing_slim` — custom addon to shrink Odoo mass-mailing emails below Gmail's 102 KB clip limit

## Destination & conventions
- **Location:** `booksandco/odoo/mass_mailing_slim/`
- **Current state = "Plan + scaffold":** this doc plus an installable **no-op skeleton** (manifest, `__init__` files, stub models/tools/views). No transformation logic yet — the module installs and does nothing until the stubs are filled.
- **Conventions** (from `booksandco/odoo/AGENTS.md` + `partner_mailing/__manifest__.py`):
  - Odoo **19.0**, deployed via odoo.sh. Core at `~/work/odoo` **must not be modified** (this addon is standalone).
  - Manifest: `version` = `19.0.1.0.0`, `license` = `OEEL-1`, `author` = `Harry Bird`, `category` = `Marketing`.
  - Layout: `__manifest__.py`, `models/`, `views/`, `security/` (only if new models — this module adds none, so no `security/`).
  - **Version bump rule:** every commit touching the module bumps `MINOR`/`PATCH`.
  - No local test runner; tests run on odoo.sh build after push.

## Goal
Reduce the HTML that Email Marketing sends (fix the CSS-in-plain-text leak + move the end-of-body tracking pixel) **without editing Odoo core**, keeping rendering correct in Gmail / Outlook / Apple Mail / Yahoo.

## Why server-side post-processing
The bloat is produced at edit time by the JS inliner (`convert_inline.js`), but the *final* oversized HTML is fully reachable server-side at one clean chokepoint before the MIME message is built. Post-processing in Python is low-risk (no touching the fragile JS), theme/snippet-agnostic, testable, and consistent with core (core already round-trips this HTML through lxml incl. `[if mso]` handling at `mass_mailing/models/mailing.py:1346`).

## Verified integration points (Odoo 19.0 core, read-only reference)
- Send path: `mass_mailing/models/mailing.py:1092 _action_send_mail` → `mail.compose.message` (mass_mail) → `wizard/mail_compose_message.py:40 _prepare_mail_values` wraps `body_html` in `mass_mailing.mass_mailing_mail_layout` + injects `<style>{mass_mailing_mail.scss}</style>` (lines 48-54).
- **Primary chokepoint:** `mass_mailing/models/mail_mail.py:31 _prepare_outgoing_body` — full wrapped HTML, appends tracking pixel at *end*, called once per `mail.mail`.
- **Plain-text chokepoint:** `mail/models/mail_mail.py:512` `body_alternative = tools.html2plaintext(body_personalized)`; `html2plaintext` (`odoo/tools/mail.py:537`, tag-strip regex at line 601) never removes `<style>/<script>` **text** → CSS leaks into text/plain.
- Reuse: `tools.mail.prepend_html_content` (`odoo/tools/mail.py:701`); size-guard pattern `mail/models/mail_mail.py:484 _estimate_email_size` + `ir.mail_server._get_max_email_size`.
- `lxml` already a dependency.

---

## Scaffold files (installable no-op)

```
mass_mailing_slim/
├── PLAN.md                         # this document
├── __init__.py                     # from . import models
├── __manifest__.py                 # 19.0.1.0.0 / OEEL-1 / Harry Bird; depends ['mass_mailing']
├── models/
│   ├── __init__.py
│   ├── mail_mail.py                # _inherit mail.mail: stub overrides call super() only
│   ├── mailing.py                  # _inherit mailing.mailing: TODO size fields
│   └── res_config_settings.py      # _inherit res.config.settings: TODO toggles
├── tools/
│   ├── __init__.py
│   └── html_slim.py                # pure-function stubs, identity no-ops + TODO
├── data/
│   └── config_params.xml           # ir.config_parameter defaults
├── views/
│   ├── mailing_views.xml           # placeholder <odoo/>
│   └── res_config_settings_views.xml
└── tests/
    ├── __init__.py
    └── test_html_slim.py           # TransactionCase skeleton (skipped)
```

Scaffold rules: stubs are **safe no-ops** (`html_slim.*` return input unchanged; model overrides only call `super()`); all XML is valid; module installs and changes nothing until logic is added.

---

## Design to implement later — `tools/html_slim.py`
Pure `str -> str` functions, each toggleable via `ir.config_parameter`:
1. `minify_email_html(html)` — collapse newline+indent runs **between tags** (`re.sub(r'>\s*\n\s*<', '><', html)`); strip HTML comments **except** `[if…]/[endif]`; skip `<pre>/<textarea>`; never touch attribute values/text.
2. `strip_dead_classes(html)` *(aggressive)* — lxml parse; keep-set = class/id tokens referenced by every `<style>` text **and** every HTML comment (covers `[if mso]` `<style>`); plus config allowlist; on each `@class` keep only referenced/allowlisted; drop empty attr; re-attach DOCTYPE line.
3. `trim_redundant_inline_defaults(html)` *(aggressive, default off)* — delete exact no-op declarations force-injected by `_getMatchedCSSRules` (`convert_inline.js:1820-1847`): `box-sizing:border-box`, `border-radius:0px`, `border-style:none`, `border-width:0px`, inert `text-align:inherit;font-size:unset;line-height:inherit`.
4. `html_to_text_no_css(html)` — `etree.strip_elements(tree, 'style','script', with_tail=False)` then delegate to `tools.html2plaintext`. Fixes plain-text CSS leak.
5. `relocate_tracking_pixel(html, pixel_html)` — insert open-tracking `<img>` right after `<body>` via `prepend_html_content`.

## Design to implement later — models
- `mail_mail.py`: `_prepare_outgoing_body` → (mailing mails, config-gated) relocate pixel → optional strip/trim → always minify. `_prepare_outgoing_list` → recompute `vals['body_alternative'] = html_slim.html_to_text_no_css(vals['body'])` for mailing mails only.
- `mailing.py`: computed non-stored `email_size_kb` + `email_size_warning` (threshold `mass_mailing_slim.warn_kb`, default 100); log warning at send when over.
- `res_config_settings.py` + `data/config_params.xml`: flags `enabled`/`minify`/`fix_plaintext`/`move_pixel`/`strip_classes`/`trim_defaults`/`warn_kb`/`class_allowlist`.

## Tiers (choose when implementing logic)
- **Safe:** minify + plaintext fix + pixel move + size warning (no class removal).
- **Aggressive:** Safe + dead-class stripping (+ optional default trim) — the big HTML win; test in Gmail/Outlook first.
- **Full:** Aggressive + client-side `convert_inline` patch so `body_html` is slim in DB/previews.

## Tests (run on odoo.sh) — `tests/test_html_slim.py`
Skeletons now; assertions when logic lands: minify keeps `[if mso]`/single spaces, drops indentation; dead-class strip removes unreferenced `btn`/`card-*` but keeps `@media`/`:hover`/allowlisted/mso-comment-referenced; default-trim removes no-ops but keeps real borders; `html_to_text_no_css` yields no `{`/`}`/`px;`; integration on a heavy mailing asserts size drop, clean text part, pixel-before-content, `[if mso]` intact, `<style>` media/hover survive.

## Safety
Config-gated, idempotent, preserves `[if mso]`/VML, head `<style>` media/hover rules, `xmlns:v`/`xmlns:o`, DOCTYPE, `data-o-mail-quote*`. Bump manifest version on every commit per AGENTS.md.
