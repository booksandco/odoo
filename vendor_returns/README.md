# Vendor Returns — Known Error

## Issue: Email fails when sending vendor return order

### Symptom
When clicking **"Send to Vendor"** on a `vendor.return.order` (e.g. VR/00004), the user sees an error message. The record's state changes to **Sent**, but no email appears in the chatter and no PDF attachment is generated.

### Log Error
```
QWebError: Error while rendering the template:
    KeyError: 'name'
    Template: vendor_returns.report_vendor_return_order_document
    Element: <span t-field="line.name"/>
```

### Cause
The deployed server code (`/home/odoo/src/user/vendor_returns/`) contains an older report template that references fields which do not exist on the `vendor.return.order.line` model:

- `line.name` — field does not exist
- `line.vendor_invoice_ref` — field does not exist

When the email composer opens, it tries to generate the PDF attachment from the template's linked report (`vendor_returns.report_vendor_return_order`). The QWeb rendering crashes because it cannot resolve those fields.

### Why the state still changes
The server's version of `action_send()` writes `state = 'sent'` **before** attempting to open the mail composer. So even though the composer crashes, the state change has already been committed.

### Fix
Update the report template `report/vendor_return_order_templates.xml` to use fields that actually exist on the model:

```xml
<!-- BEFORE (broken on server) -->
<span t-field="line.name"/>
<span t-field="line.vendor_invoice_ref"/>

<!-- AFTER (correct) -->
<span t-field="line.product_id.display_name"/>
<span t-field="line.source_purchase_line_id.order_id.partner_ref"/>
```

The corrected template (with totals row and proper float formatting) is already present in this local copy of the module (`../odoo/vendor_returns/`).

### Additional Note
The server's deployed module (`/home/odoo/src/user/vendor_returns/`, version `1.0.3`) is out of sync with this local copy (`19.0.1.0.0`). Beyond the broken report template, the server version is missing several features and safety checks present here, including:

- `warehouse_id` field on return orders
- Separate `action_mark_sent()` method (server's `action_send()` combines send + state change)
- Proper use of Odoo's stock return wizard in `action_generate_picks()`
- `_remove_replenishment_rules()` on completion
- Various `check_company` constraints

**Recommendation:** Deploy this local copy to the server and run an Odoo module update (`-u vendor_returns`) to bring the code and schema in sync.
