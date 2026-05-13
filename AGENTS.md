# Development Guidelines

## Environment

These are custom Odoo 19.0 modules developed and deployed via **odoo.sh**.

The Odoo source code is available at `~/work/odoo/` and includes both Community and Enterprise apps. It must not be modified.

## Branching

All changes must be implemented on `feature/` or `fix/` branches. Never commit directly to `production`.

## Module Structure

Modules follow standard Odoo module layout:

- `__manifest__.py` — module metadata, version, and dependencies
- `models/` — Python model definitions
- `views/` — XML view definitions
- `security/` — access control rules (`ir.model.access.csv`)

## Testing

There is no local test runner. Testing requires a **commit and push** to the branch, which triggers an odoo.sh build. The build installs/upgrades the changed modules and runs them on a staging environment.

## Version Bumps

Every commit that modifies a module **must** bump the version number in that module's `__manifest__.py`. odoo.sh uses the version number to detect which modules need upgrading. If the version is not bumped, the changes will not be applied.
