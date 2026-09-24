# Replenishment Review — Card-Based Replenishment Workflow

> **⚠ SHIP TOGETHER WITH THE BOOKSTORE REPLENISHMENT FIXES**
>
> This module is intentionally bundled with `fix/bookstore-orderpoint-creation`
> (merged into the `feature/replenishment-review` branch). That branch fixed
> reordering-rule creation in the `bookstore` module (19.0.1.4.3 / 19.0.1.4.4):
> it backfills ~2,700 rules for sold ISBN titles, makes the incoming-picking
> automation always create a rule, and turns on `replenish_location` so books
> **and giftware** auto-create `min=1/max=1` rules.
>
> On its own that produces a large replenishment list. Shipping the review UI
> and the bookstore changes in the **same** release gives staff the tool to work
> through it. Do **not** merge one side without the other — merge the whole
> `feature/replenishment-review` branch to `main` together.

## Motivation

The current replenishment view (`stock.warehouse.orderpoint` list, filtered by
"To Reorder") is a spreadsheet-style grid. For a bookstore where every title
needs individual judgment — is it a one-off customer order? is the supplier still
active? is the book even still in print? — the grid doesn't surface enough
context. Staff end up clicking into each product to see the cover, author,
vendor, and reason it's showing up. This makes the daily replenishment review
slow and skippable, which leads to the clutter problem: sold-out products
accumulate on the website with no reordering action.

A card-based review — one product at a time, with rich context and keyboard
shortcuts — turns replenishment from a spreadsheet chore into a fast,
decision-dense workflow.

---

## Existing Infrastructure (What We Build On)

### `stock.warehouse.orderpoint`

Every product has a reordering rule. In this bookstore, the defaults (set via
`ir.default` in the `bookstore` module) are:

| Field | Default | Meaning |
|---|---|---|
| `product_min_qty` | 1 | Trigger replenishment when forecast drops below 1 |
| `product_max_qty` | 1 | Replenish up to 1 |
| `trigger` | `auto` | The scheduler recomputes `qty_to_order` automatically |

Key fields and actions we reuse:

| Field / Action | What it does |
|---|---|
| `qty_to_order` | Computed quantity to order (max - forecast). If set manually, stored in `qty_to_order_manual`. |
| `snoozed_until` | Date field. When set to a future date, the orderpoint is hidden from "Not Snoozed" filter. Scheduler ignores it. |
| `action_replenish()` | Sets `qty_to_order`, calls `_procure_orderpoint_confirm()` which creates an RFQ/PO/manufacturing order via the procurement pipeline. |
| `action_replenish_auto()` | Same as above but also sets `trigger='auto'` so future replenishment is automatic. |

### Archive (`product.template.active`)

Odoo's `product.template` inherits `active` from `base.models`. Setting
`active=False` on a product archives it — the product disappears from:

- The website (regardless of `is_published`)
- The POS
- Inventory views (unless "Archived" filter is active)
- Replenishment (the related orderpoint becomes irrelevant)

This is the archive action in the card review. It's stronger than archiving
just the orderpoint — it removes the product from the system entirely (while
preserving data for reporting). This directly solves the original website
clutter problem: an archived product cannot show on the shop.

### `bookstore.purchase.suggestion` (Customer Order View)

A SQL view in the `customer_to_order` module that identifies sale order lines
with undelivered quantities. Statuses: `available`, `on_order`, `in_cart`,
`unordered`. Products with `status='unordered'` have customer demand with no
PO in progress — these are **higher priority** than regular stock depletion.

### Product Context Available

From `bookstore` and `book_data` modules:
- `x_author`, `x_publisher` — author/publisher names
- `image_1920` — book cover image
- `x_publication_date` — publication date
- `x_last_sale_date` — last sale date (computed from stock moves)
- `x_hardcover_rating` — Hardcover community rating
- `seller_ids` — vendor list with prices and lead times
- `list_price` — retail price
- Stock moves (via `stock.move`) — full sales history

---

## Module Design: `replenishment_review`

### Dependency Chain

```
replenishment_review
├── stock                    (orderpoint model + replenishment actions)
├── bookstore                (product fields: author, publisher, cover, etc.)
├── book_data                (Hardcover ratings, data score)
└── customer_to_order        (purchase.suggestion view for priority)
```

### Architecture

Two layers (no persistent model needed — the review is a session-level workflow):

1. **Backend** — methods on `stock.warehouse.orderpoint` for data assembly and
   action execution. A controller serves the SPA page and JSON endpoints.
2. **Frontend** — an OWL component rendering the card stack with keyboard
   bindings. Accumulates actions client-side until the user confirms.

### Two-Phase Commit

All actions during the review session are staged client-side. Nothing hits the
database until the user exits with a confirmation:

```
┌──────────────────────────────────────────────────┐
│  Review Complete                                  │
│                                                   │
│  You processed 47 items:                          │
│    ✓ 12 purchase orders created (4 vendors)        │
│    ✓ 8 items snoozed                              │
│    ✓ 5 products archived                          │
│    → 22 skipped (will appear next session)         │
│                                                   │
│  [Confirm & Exit]    [Cancel, Keep Reviewing]     │
└──────────────────────────────────────────────────┘
```

On Confirm, the backend executes all staged actions in a single transaction.
This solves the undo problem — nothing is real until confirmed. It also gives
staff a moment to review their decisions before committing.

### Card Data Shape

Each card represents one `stock.warehouse.orderpoint` with enriched context:

```python
{
    "id": orderpoint.id,
    "product_id": product.id,
    "product_tmpl_id": template.id,
    "name": template.name,                    # book title
    "author": template.x_author,              # author name(s)
    "publisher": template.x_publisher,        # publisher name
    "image_url": "/web/image/product.template/{id}/image_1920",
    "list_price": template.list_price,
    "qty_on_hand": orderpoint.qty_on_hand,    # current stock
    "qty_forecast": orderpoint.qty_forecast,  # virtual available
    "qty_to_order_computed": orderpoint.qty_to_order_computed,
    "vendor_name": primary_vendor.name,       # first active vendor
    "vendor_code": primary_vendor.product_code,  # vendor's SKU
    "last_sale_date": template.x_last_sale_date,
    "publication_date": template.x_publication_date,
    "hardcover_rating": template.x_hardcover_rating,

    # -- Reason for appearing --
    "reason": "customer_order" | "stock_depleted",
    "reason_detail": {
        # if customer_order:
        "customer_name": "Sarah M.",
        "qty_ordered": 2,
        "sale_order_id": 123,
        # if stock_depleted:
        "days_below_min": 3,
    },

    # -- Decision-support context --
    "has_open_po": True,                     # someone already ordered this?
    "open_po_qty": 3,                        # how many on order
    "sales_last_30_days": 2,                 # recent sales velocity
    "sales_last_90_days": 8,
    "sales_last_365_days": 8,                # all-time or annual
    "avg_days_on_shelf": 23,                 # days between receiving and selling
                                             #   (avg across all units sold)
    "category_avg_days_on_shelf": 45,        # category baseline for comparison
    "is_below_baseline": True,               # sells faster than category avg?
    "deadline_date": orderpoint.deadline_date,
}
```

### Helpful Hints on the Card

The card displays:

**Stock & ordering status:**
- Current stock / forecast
- "3 on order (PO #42)" — if there's already an open PO
- Vendor name and vendor SKU

**Sales context:**
- "Sold 8 in the last year, 2 in the last month"
- "Avg 23 days on shelf (category avg: 45 days)"
- This makes it immediately obvious whether a book is a good seller or dead
  stock. A book that's sold 0 in 12 months with an avg shelf time of 300 days
  is an obvious archive candidate, without needing to click into reports.

**Reason banner:**
- ⚠ "Customer order — Sarah M. ordered 2" (high priority, different color)
- "Stock depleted — below minimum for 3 days" (routine)

### Priority Sorting

Cards are sorted by priority, then by urgency:

1. **Customer-order-driven** (higher priority)
   - Product appears in `bookstore.purchase.suggestion` with `status='unordered'`
   - The customer is waiting — this needs action now
   - Secondary sort: earliest `sale_order.date_order` first

2. **Stock-depleted** (lower priority)
   - Product dropped below `product_min_qty` through normal sales
   - Secondary sort: earliest `deadline_date` first

### Actions (Hotkeys)

| Key | Action | What Happens |
|---|---|---|
| `Enter` | Order (computed qty) | Accepts `qty_to_order_computed` as the order quantity. Staged for commit. Card dismissed. |
| `1` | Order 1 | Override: order exactly 1. Staged. Card dismissed. |
| `2` | Order 2 | Override: order exactly 2. Staged. Card dismissed. |
| `S` | Snooze | Opens sub-prompt: `D`=1 day, `W`=1 week, `M`=1 month. Sets `snoozed_until` on the orderpoint. Staged. Card dismissed. |
| `N` | Never reorder | **Archives the orderpoint/rule only** (`stock.warehouse.orderpoint.active=False`) for every rule on the product. The product stays on sale; only future replenishment stops. Staged. Card dismissed. This is the "we're never ordering that again" action. |
| `A` | Archive product | Sets `active=False` on `product.template`. This archives the **product itself** — it disappears from the website, POS, inventory, and replenishment. Use for items leaving the catalogue entirely. Staged. Card dismissed. |
| `→` | Skip | Move to next card. Current card goes to the back of the stack. Not staged — reappears next session. |
| `←` | Undo last | Reverses the last staged action in this session. The previous card reappears. This is trivial with two-phase commit — just pop the action from the client-side queue. |
| `Esc` | Exit | Opens the confirmation summary. If there are staged actions, shows the summary dialog. If no actions taken, returns to the replenishment list. |

### Archive Semantics

Archive means `product.template.active = False`. This is Odoo's built-in
product archival. Effects:

- **Website**: product disappears immediately (regardless of `is_published`).
  This directly solves the original clutter problem.
- **POS**: product is hidden.
- **Inventory**: product hidden from standard views (visible with "Archived"
  filter).
- **Replenishment**: the related orderpoint becomes irrelevant — archived
  products can't be ordered. The orderpoint itself doesn't need to be touched;
  the scheduler will skip it because the product is inactive.
- **Sales history**: preserved. Reports still include archived products.
- **Reversibility**: can be unarchived from the product list (Archived filter →
  set `active=True`).

### Never Reorder Semantics

"Never reorder" archives the product's reordering rules
(`stock.warehouse.orderpoint.active=False`) without touching the product:

- **Product**: remains active, on the website and in the POS. Remaining stock
  still sells normally.
- **Replenishment**: the product stops appearing in the review list.
- **Durability**: the rule is not recreated — the incoming-picking automation
  and the backfill both check for orderpoints *including archived ones*
  (`active_test=False`), so a deliberately stopped product stays stopped.
- **Reversibility**: re-enable from Inventory → Reordering Rules (Archived
  filter → set `active=True`), or create a new rule.

Use **Never reorder** for "we won't stock this again"; use **Archive product**
only when the item is leaving the catalogue entirely.

### Sales Velocity Computation

The "average days on shelf" metric is computed per-product from `stock.move`
history:

```
avg_days_on_shelf = average of (sale_date - receipt_date) across all units sold
```

Where `receipt_date` is the date the unit was received into stock (incoming
move completion) and `sale_date` is the date it was sold (outgoing move
completion). This requires tracing stock moves back to their source receipts.

For performance, this should be pre-computed and stored (cron or computed field
updated periodically), not computed live for every card. It could be a new
field `x_avg_days_on_shelf` on `product.template`, recomputed weekly.

The category baseline (`category_avg_days_on_shelf`) is the median of all
products in the same `categ_id` that have at least one sale. This is cheap to
compute as a single aggregated query.

### Batch Size and Session

- Load N cards at a time (default 30, configurable)
- Actions accumulate client-side in a queue
- On confirm, the backend executes them in one transaction
- A session counter shows: "12 of 47 remaining (3 staged)"
- Skipped cards reappear in the next session — they're not marked or modified

---

## Implementation Plan

### Step 1 — Backend: Card Data + Actions

Extend `stock.warehouse.orderpoint` with methods:

- `_get_review_data(limit=30)` — returns sorted list of card dicts with all
  context fields. Handles priority sorting and enrichment (sales velocity,
  open POs, customer order detection).

- `_execute_review_actions(actions)` — accepts a list of staged actions and
  executes them in a single transaction:
  ```python
  [
      {"orderpoint_id": 42, "action": "order", "qty": 1},
      {"orderpoint_id": 43, "action": "order", "qty": 2},
      {"orderpoint_id": 44, "action": "snooze", "days": 7},
      {"orderpoint_id": 45, "action": "archive"},
  ]
  ```
  Returns a summary: `{"ordered": 2, "snoozed": 1, "archived": 1, "skipped": 0,
  "po_ids": [12, 13]}`.

- `_compute_avg_days_on_shelf(product_ids)` — batch-computes the velocity
  metric. Uses stock move history: for each sold unit, find the originating
  receipt move, compute the difference.

### Step 2 — Sales Velocity Field (Optional but Recommended)

Add `x_avg_days_on_shelf` (Float) on `product.template` in the `bookstore`
module. Update it via a weekly cron. The card review reads this pre-computed
value rather than computing live. The category baseline is computed on the fly
since it's a single aggregated query.

### Step 3 — Controller

File: `controllers/main.py`

- `@http.route('/replenishment/review', auth='user')` — serves the SPA page
- `@http.route('/replenishment/review/data', auth='user', type='json')` —
  returns card batch as JSON
- `@http.route('/replenishment/review/execute', auth='user', type='json')` —
  accepts staged actions, executes, returns summary

### Step 4 — Frontend OWL Component

Files: `static/src/js/review_card.js`, `static/src/xml/review_card.xml`

- `ReplenishmentReview` component
- Card stack with CSS transitions (dismiss animation left/right depending on
  action type)
- Keyboard event listener on `document` for hotkeys
- Client-side action queue (staged actions + undo support)
- Confirmation modal on exit
- State machine: `loading → reviewing → confirming → done`

### Step 5 — Menu Item + View Registration

File: `views/review_templates.xml`

- Register OWL component as a client action
- Add menu item under Inventory → Replenishment Review (alongside the existing
  "Replenishment" menu, not replacing it)
- Register the SPA page template

### Step 6 — Manifest + Dependencies

File: `__manifest__.py`

- Depend on: `stock`, `bookstore`, `book_data`, `customer_to_order`
- Version: `19.0.1.0.0`

---

## Feasibility Assessment

### Straightforward Parts

- **Backend logic**: Extending `stock.warehouse.orderpoint` with data assembly
  and batch execution methods is standard Odoo. The batch endpoint is a single
  transaction wrapping calls to `action_replenish()`, `write()` on
  `snoozed_until`, and `write()` on `product.template.active`.
- **Archive**: `product.template.active = False` is Odoo's built-in mechanism.
  No custom field, no cascade logic beyond what Odoo already handles.
- **Snooze**: Already exists on the orderpoint model.
- **Order action**: Reuses `action_replenish()` directly. No new procurement
  logic.
- **Priority sorting**: A join with the `bookstore.purchase.suggestion` SQL
  view and a UNION-style sort. Simple query.

### Moderate Complexity

- **OWL component**: Card-stack UI with keyboard bindings and two-phase commit
  state machine. About 200-400 lines of JS + XML. Odoo's OWL framework
  supports this, but it's custom frontend work.
- **Sales velocity computation**: Tracing stock moves back to source receipts
  requires a moderately complex query. Pre-computing it into a stored field
  simplifies the card data endpoint.

### Accepted Risks

- **Concurrent access**: Two staff opening the review simultaneously get the
  same batch. In practice, this is a small bookstore with likely one person
  doing replenishment at a time. The worst case is a duplicate PO, which is
  easy to spot and cancel. Not mitigated in v1.
- **Dev iteration cycle**: All testing requires push to odoo.sh. The OWL
  frontend is the riskiest component for bugs. Mitigation: the existing
  replenishment list remains available, so a broken card review degrades
  gracefully.
