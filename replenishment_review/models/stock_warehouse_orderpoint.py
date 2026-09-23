import logging
from collections import defaultdict, deque

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 30
MAX_BATCH_SIZE = 100
CATEGORY_BASELINE_SAMPLE = 150


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    # ------------------------------------------------------------------
    # Public RPC API
    # ------------------------------------------------------------------
    @api.model
    def get_review_data(self, limit=DEFAULT_BATCH_SIZE, offset=0):
        """Return a page of enriched replenishment review cards."""
        self.check_access_rights('read')
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_BATCH_SIZE
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 0
        limit = max(1, min(limit, MAX_BATCH_SIZE))
        offset = max(0, offset)

        today = fields.Date.context_today(self)
        domain = [
            ('product_id.active', '=', True),
            ('qty_to_order', '>', 0.0),
            '|',
            ('snoozed_until', '=', False),
            ('snoozed_until', '<=', today),
        ]
        orderpoints = self.search(domain)
        ordered = self._sort_review_orderpoints(orderpoints)
        total = len(ordered)
        page = ordered[offset:offset + limit]
        cards = self._build_review_cards(page)
        return {
            'cards': cards,
            'total': total,
            'offset': offset,
            'limit': limit,
            'remaining': max(0, total - offset - len(cards)),
        }

    @api.model
    def execute_review_actions(self, actions):
        """Execute the staged review actions in a single transaction."""
        if not isinstance(actions, list):
            raise UserError(_("Invalid review payload."))
        self.check_access_rights('write')

        summary = {
            'ordered': 0,
            'snoozed': 0,
            'archived': 0,
            'skipped': 0,
            'po_ids': [],
            'po_names': [],
            'errors': [],
        }
        orderpoints_to_order = self.browse()
        order_qty = {}
        snooze_pairs = []
        templates_to_archive = self.env['product.template']
        seen = set()

        for raw in actions:
            orderpoint_id = self._safe_int(raw.get('orderpoint_id'))
            action = raw.get('action')
            if orderpoint_id is None:
                summary['errors'].append(_("Skipped an action with an invalid orderpoint id."))
                continue
            if orderpoint_id in seen:
                continue
            seen.add(orderpoint_id)
            orderpoint = self.browse(orderpoint_id).exists()
            if not orderpoint:
                summary['errors'].append(
                    _("Orderpoint %(id)s no longer exists.", id=orderpoint_id)
                )
                continue

            if action == 'order':
                qty = self._safe_float(raw.get('qty'), orderpoint.qty_to_order_computed or 0.0)
                if qty <= 0:
                    summary['skipped'] += 1
                    continue
                order_qty[orderpoint.id] = qty
                orderpoints_to_order |= orderpoint
            elif action == 'snooze':
                if orderpoint.trigger != 'manual':
                    summary['errors'].append(
                        _(
                            "%(product)s uses an automatic rule and cannot be snoozed; archive it instead.",
                            product=orderpoint.product_id.display_name,
                        )
                    )
                    continue
                days = self._safe_int(raw.get('days')) or 7
                snooze_pairs.append((orderpoint, fields.Date.context_today(self) + relativedelta(days=days)))
            elif action == 'archive':
                templates_to_archive |= orderpoint.product_tmpl_id
            elif action == 'skip':
                summary['skipped'] += 1
            else:
                summary['errors'].append(_("Unknown action %(action)s.", action=action))

        if orderpoints_to_order:
            execute_start = fields.Datetime.now()
            ordered_count = len(orderpoints_to_order)
            ordered_product_ids = orderpoints_to_order.product_id.ids
            for orderpoint in orderpoints_to_order:
                orderpoint.qty_to_order_manual = order_qty[orderpoint.id]
            orderpoints_to_order.action_replenish()
            purchase_orders = self.env['purchase.order'].search([
                ('order_line.product_id', 'in', ordered_product_ids),
                ('state', 'in', ['draft', 'sent', 'to approve', 'purchase']),
                ('write_date', '>=', execute_start),
            ])
            summary['ordered'] = ordered_count
            summary['po_ids'] = purchase_orders.ids
            summary['po_names'] = purchase_orders.mapped('name')

        for orderpoint, snooze_date in snooze_pairs:
            orderpoint.snoozed_until = snooze_date
            summary['snoozed'] += 1

        if templates_to_archive:
            templates_to_archive.write({'active': False})
            summary['archived'] = len(templates_to_archive)

        return summary

    # ------------------------------------------------------------------
    # Card assembly
    # ------------------------------------------------------------------
    def _sort_review_orderpoints(self, orderpoints):
        if not orderpoints:
            return orderpoints

        demand_dates = self._get_customer_demand_dates(orderpoints.product_tmpl_id)
        customer = orderpoints.filtered(lambda o: o.product_tmpl_id.id in demand_dates)
        stock = orderpoints - customer

        demand_sentinel = fields.Datetime.to_datetime('9999-12-31 23:59:59')
        deadline_sentinel = fields.Date.to_date('9999-12-31')

        customer_sorted = customer.sorted(
            key=lambda o: (demand_dates.get(o.product_tmpl_id.id) or demand_sentinel, o.id)
        )
        stock_sorted = stock.sorted(
            key=lambda o: (o.deadline_date or deadline_sentinel, o.id)
        )
        return self.browse(customer_sorted.ids + stock_sorted.ids)

    def _get_customer_demand_dates(self, templates):
        """Map template id -> earliest sale order date with unordered demand."""
        if not templates:
            return {}
        suggestions = self.env['bookstore.purchase.suggestion'].sudo().search([
            ('status', '=', 'unordered'),
            ('product_tmpl_id', 'in', templates.ids),
        ])
        demand_dates = {}
        for suggestion in suggestions:
            template_id = suggestion.product_tmpl_id.id
            date = suggestion.sale_order_id.date_order
            if (
                template_id not in demand_dates
                or not demand_dates[template_id]
                or (date and date < demand_dates[template_id])
            ):
                demand_dates[template_id] = date
        return demand_dates

    def _build_review_cards(self, orderpoints):
        if not orderpoints:
            return []

        products = orderpoints.product_id
        templates = orderpoints.product_tmpl_id
        product_ids = products.ids
        template_ids = templates.ids

        demand = self._get_customer_demand(template_ids)
        open_po = self._get_open_po(product_ids)
        velocity = self._safe_enrich(self._get_sales_velocity, product_ids, {})
        shelf_days = self._safe_enrich(self._get_avg_days_on_shelf, product_ids, {})
        baselines = self._safe_enrich(
            self._get_category_baselines, templates.categ_id.ids, {}
        )

        cards = []
        for orderpoint in orderpoints:
            product = orderpoint.product_id
            template = orderpoint.product_tmpl_id
            seller = product._select_seller(quantity=max(orderpoint.qty_to_order or 1.0, 1.0))
            demand_info = demand.get(template.id)
            po_info = open_po.get(product.id) or {}
            product_velocity = velocity.get(product.id, {})
            avg_days = shelf_days.get(product.id)
            baseline = baselines.get(template.categ_id.id)

            if demand_info:
                reason = 'customer_order'
                reason_detail = {
                    'customer_name': demand_info['customer_name'],
                    'qty_ordered': demand_info['qty'],
                    'sale_order_id': demand_info['sale_order_id'],
                    'sale_order_name': demand_info['sale_order_name'],
                }
            else:
                reason = 'stock_depleted'
                reason_detail = {
                    'deadline_date': fields.Date.to_string(orderpoint.deadline_date)
                    if orderpoint.deadline_date else False,
                }

            cards.append({
                'id': orderpoint.id,
                'orderpoint_name': orderpoint.name,
                'product_id': product.id,
                'product_tmpl_id': template.id,
                'name': template.name,
                'author': template.x_author or '',
                'publisher': template.x_publisher or '',
                'image_url': '/web/image/product.template/%s/image_1920' % template.id,
                'list_price': template.list_price,
                'currency_symbol': template.currency_id.symbol or '',
                'trigger': orderpoint.trigger,
                'product_uom_name': orderpoint.product_uom_name or '',
                'qty_on_hand': orderpoint.qty_on_hand,
                'qty_forecast': orderpoint.qty_forecast,
                'qty_to_order_computed': orderpoint.qty_to_order_computed,
                'qty_to_order': orderpoint.qty_to_order,
                'deadline_date': fields.Date.to_string(orderpoint.deadline_date)
                if orderpoint.deadline_date else False,
                'vendor_name': seller.partner_id.display_name if seller else '',
                'vendor_code': seller.product_code or '' if seller else '',
                'vendor_delay': seller.delay if seller else 0,
                'last_sale_date': fields.Date.to_string(template.x_last_sale_date)
                if template.x_last_sale_date else False,
                'publication_date': fields.Date.to_string(template.x_publication_date)
                if template.x_publication_date else False,
                'hardcover_rating': template.x_hardcover_rating or 0.0,
                'reason': reason,
                'reason_detail': reason_detail,
                'has_open_po': bool(po_info.get('po_name')),
                'open_po_qty': po_info.get('qty', 0.0),
                'open_po_name': po_info.get('po_name', ''),
                'sales_last_30_days': product_velocity.get('sales_last_30_days', 0.0),
                'sales_last_90_days': product_velocity.get('sales_last_90_days', 0.0),
                'sales_last_365_days': product_velocity.get('sales_last_365_days', 0.0),
                'avg_days_on_shelf': avg_days,
                'category_avg_days_on_shelf': baseline,
                'is_below_baseline': bool(
                    avg_days is not None and baseline is not None and avg_days < baseline
                ),
            })
        return cards

    # ------------------------------------------------------------------
    # Enrichment helpers
    # ------------------------------------------------------------------
    def _get_customer_demand(self, template_ids):
        demand = {}
        if not template_ids:
            return demand
        suggestions = self.env['bookstore.purchase.suggestion'].sudo().search([
            ('status', '=', 'unordered'),
            ('product_tmpl_id', 'in', template_ids),
        ])
        for suggestion in suggestions:
            template_id = suggestion.product_tmpl_id.id
            info = demand.setdefault(template_id, {
                'qty': 0.0,
                'customer_name': suggestion.partner_id.display_name or '',
                'sale_order_id': suggestion.sale_order_id.id,
                'sale_order_name': suggestion.sale_order_id.name,
                'date': suggestion.sale_order_id.date_order,
            })
            info['qty'] += suggestion.qty_to_deliver or 0.0
            if suggestion.sale_order_id.date_order and (
                not info['date'] or suggestion.sale_order_id.date_order < info['date']
            ):
                info['date'] = suggestion.sale_order_id.date_order
                info['customer_name'] = suggestion.partner_id.display_name or ''
                info['sale_order_id'] = suggestion.sale_order_id.id
                info['sale_order_name'] = suggestion.sale_order_id.name
        return demand

    def _get_open_po(self, product_ids):
        open_po = {}
        if not product_ids:
            return open_po
        lines = self.env['purchase.order.line'].sudo().search_read([
            ('product_id', 'in', product_ids),
            ('state', 'in', ['draft', 'sent', 'to approve', 'purchase']),
        ], ['product_id', 'product_qty', 'qty_received', 'order_id'])
        for line in lines:
            if line['qty_received'] >= line['product_qty']:
                continue
            info = open_po.setdefault(line['product_id'][0], {'qty': 0.0, 'po_name': ''})
            info['qty'] += line['product_qty'] - line['qty_received']
            if not info['po_name']:
                info['po_name'] = line['order_id'][1] if line['order_id'] else ''
        return open_po

    def _get_sales_velocity(self, product_ids):
        velocity = {
            product_id: {
                'sales_last_30_days': 0.0,
                'sales_last_90_days': 0.0,
                'sales_last_365_days': 0.0,
            }
            for product_id in product_ids
        }
        if not product_ids:
            return velocity
        sale_report = self.env['sale.report'].sudo()
        done_states = sale_report._get_done_states()
        now = fields.Datetime.now()
        windows = (
            (30, 'sales_last_30_days'),
            (90, 'sales_last_90_days'),
            (365, 'sales_last_365_days'),
        )
        for days, key in windows:
            cutoff = now - relativedelta(days=days)
            grouped = sale_report._read_group([
                ('state', 'in', done_states),
                ('product_id', 'in', product_ids),
                ('date', '>=', cutoff),
            ], ['product_id'], ['product_uom_qty:sum'])
            for product, qty in grouped:
                if product.id in velocity:
                    velocity[product.id][key] = qty
        return velocity

    def _get_avg_days_on_shelf(self, product_ids):
        """FIFO approximation of the average number of days a sold unit spent in stock."""
        result = {product_id: None for product_id in product_ids}
        if not product_ids:
            return result
        moves = self.env['stock.move'].sudo().search_read([
            ('product_id', 'in', product_ids),
            ('state', '=', 'done'),
            '|',
            '&', ('location_id.usage', '=', 'supplier'), ('location_dest_id.usage', '=', 'internal'),
            '&', ('location_id.usage', '=', 'internal'), ('location_dest_id.usage', '=', 'customer'),
        ], ['product_id', 'location_usage', 'location_dest_usage', 'quantity', 'date'],
            order='date asc, id asc')
        stock = defaultdict(deque)
        totals = defaultdict(lambda: [0.0, 0.0])
        for move in moves:
            quantity = move['quantity'] or 0.0
            if quantity <= 0:
                continue
            product_id = move['product_id'][0]
            date = move['date']
            if move['location_usage'] == 'supplier' and move['location_dest_usage'] == 'internal':
                stock[product_id].append([quantity, date])
            elif move['location_usage'] == 'internal' and move['location_dest_usage'] == 'customer':
                remaining = quantity
                queue = stock[product_id]
                while remaining > 0 and queue:
                    lot = queue[0]
                    take = min(lot[0], remaining)
                    if lot[1] and date:
                        totals[product_id][0] += take * (date - lot[1]).days
                        totals[product_id][1] += take
                    lot[0] -= take
                    remaining -= take
                    if lot[0] <= 0:
                        queue.popleft()
        for product_id, (age_sum, quantity) in totals.items():
            if quantity:
                result[product_id] = round(age_sum / quantity, 1)
        return result

    def _get_category_baselines(self, categ_ids):
        """Median avg-days-on-shelf across recently sold products, per category."""
        baselines = {}
        if not categ_ids:
            return baselines
        cutoff = fields.Date.context_today(self) - relativedelta(days=365)
        template_model = self.env['product.template'].sudo()
        for categ in self.env['product.category'].sudo().browse(categ_ids):
            templates = template_model.search([
                ('categ_id', '=', categ.id),
                ('x_last_sale_date', '>=', cutoff),
            ], limit=CATEGORY_BASELINE_SAMPLE)
            if not templates:
                continue
            values = [
                value
                for value in self._get_avg_days_on_shelf(
                    templates.product_variant_id.ids
                ).values()
                if value is not None
            ]
            if not values:
                continue
            values.sort()
            middle = len(values) // 2
            baselines[categ.id] = (
                values[middle]
                if len(values) % 2
                else round((values[middle - 1] + values[middle]) / 2, 1)
            )
        return baselines

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_enrich(self, func, argument, default):
        try:
            return func(argument)
        except Exception:  # noqa: BLE001 - enrichment must never break the review
            _logger.exception("Replenishment review enrichment failed for %s", argument)
            return default
