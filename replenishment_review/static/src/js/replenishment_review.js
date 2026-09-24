import { Component, onWillStart, useState, useExternalListener } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const BATCH_SIZE = 30;
const SNOOZE_CHOICES = { d: 1, w: 7, m: 30 };
const INPUT_TAGS = ["INPUT", "TEXTAREA", "SELECT"];

export class ReplenishmentReview extends Component {
    static template = "replenishment_review.ReplenishmentReview";
    static props = {
        action: { type: Object, optional: true },
        actionId: { type: [Number, Boolean], optional: true },
        className: { type: String, optional: true },
        state: { type: Object, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.actionService = useService("action");
        this.state = useState({
            loading: true,
            cards: [],
            cardById: {},
            loaded: 0,
            total: 0,
            offset: 0,
            batchSize: BATCH_SIZE,
            staged: [],
            summaryOpen: false,
            pendingSnooze: false,
            pendingOrder: false,
            customOrderQty: "",
            busy: false,
            error: null,
        });
        this.history = [];
        useExternalListener(document, "keydown", this.onKeydown.bind(this));
        onWillStart(() => this.loadBatch());
    }

    get current() {
        return this.state.cards[0] || null;
    }

    get stagedCounts() {
        const counts = { order: 0, snooze: 0, archive: 0, stop_reorder: 0 };
        for (const staged of this.state.staged) {
            counts[staged.action] = (counts[staged.action] || 0) + 1;
        }
        return counts;
    }

    get remaining() {
        return this.state.cards.length + Math.max(0, this.state.total - this.state.loaded);
    }

    get finished() {
        return !this.state.loading && !this.state.error && !this.current && this.remaining === 0;
    }

    get progressLabel() {
        const staged = this.state.staged.length;
        if (this.finished) {
            return _t("%s staged", staged);
        }
        return _t("%(staged)s staged · %(remaining)s left", { staged, remaining: this.remaining });
    }

    async loadBatch() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const data = await this.orm.call(
                "stock.warehouse.orderpoint",
                "get_review_data",
                [this.state.batchSize, this.state.offset]
            );
            for (const card of data.cards) {
                this.state.cardById[card.id] = card;
            }
            this.state.cards = this.state.cards.concat(data.cards);
            this.state.total = data.total;
            this.state.loaded += data.cards.length;
            this.state.offset += data.cards.length;
        } catch (error) {
            this.state.error = error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    async ensureNextBatch() {
        if (this.state.cards.length === 0 && this.state.loaded < this.state.total) {
            await this.loadBatch();
        }
    }

    stage(action, extra = {}) {
        const card = this.current;
        if (!card) {
            return;
        }
        this.state.cards.shift();
        this.state.staged.push(
            Object.assign({ orderpoint_id: card.id, action, name: card.name }, extra)
        );
        this.history.push({ orderpoint_id: card.id });
        if (this.state.cards.length === 0) {
            this.ensureNextBatch();
        }
    }

    orderDefault() {
        this.orderQty(2);
    }

    orderQty(qty) {
        const quantity = Number(qty);
        if (!quantity || quantity <= 0) {
            return;
        }
        this.stage("order", { qty: quantity });
        this.state.pendingOrder = false;
        this.state.customOrderQty = "";
    }

    openOrderPrompt() {
        if (this.current) {
            this.state.pendingOrder = true;
        }
    }

    orderCustom() {
        this.orderQty(this.state.customOrderQty);
    }

    onCustomOrderKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.orderCustom();
        }
    }

    openRecord(model, resId, ev) {
        if (ev && (ev.ctrlKey || ev.metaKey)) {
            return;
        }
        if (ev) {
            ev.preventDefault();
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
            target: "new",
        });
    }

    openProduct(ev) {
        this.openRecord("product.template", this.current.product_tmpl_id, ev);
    }

    openSaleOrder(ev) {
        this.openRecord("sale.order", this.current.reason_detail.sale_order_id, ev);
    }

    archiveCurrent() {
        this.stage("archive");
    }

    stopReorder() {
        this.stage("stop_reorder");
    }

    beginSnooze() {
        const card = this.current;
        if (!card) {
            return;
        }
        if (card.trigger !== "manual") {
            this.notification.add(
                _t("Automatic rules cannot be snoozed. Use Never reorder, or archive the product."),
                { type: "warning" }
            );
            return;
        }
        this.state.pendingSnooze = true;
    }

    applySnooze(days) {
        this.stage("snooze", { days });
        this.state.pendingSnooze = false;
    }

    skip() {
        const card = this.state.cards.shift();
        if (card) {
            this.state.cards.push(card);
        }
    }

    undo() {
        if (!this.history.length) {
            return;
        }
        this.history.pop();
        const lastStaged = this.state.staged[this.state.staged.length - 1];
        if (!lastStaged) {
            return;
        }
        this.state.staged.pop();
        const card = this.state.cardById[lastStaged.orderpoint_id];
        if (card) {
            this.state.cards.unshift(card);
        }
    }

    openSummary() {
        this.state.summaryOpen = true;
    }

    closeSummary() {
        this.state.summaryOpen = false;
    }

    async confirm() {
        if (this.state.busy) {
            return;
        }
        if (!this.state.staged.length) {
            this.closeSummary();
            return;
        }
        this.state.busy = true;
        try {
            const summary = await this.orm.call(
                "stock.warehouse.orderpoint",
                "execute_review_actions",
                [this.state.staged.slice()]
            );
            this.notification.add(this.formatSummary(summary), { type: "success" });
            for (const error of summary.errors || []) {
                this.notification.add(error, { type: "warning" });
            }
            this.reset();
            await this.loadBatch();
        } catch (error) {
            this.notification.add(error.message || String(error), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    formatSummary(summary) {
        const parts = [];
        if (summary.ordered) {
            parts.push(_t("%s ordered", summary.ordered));
        }
        if (summary.snoozed) {
            parts.push(_t("%s snoozed", summary.snoozed));
        }
        if (summary.archived) {
            parts.push(_t("%s archived", summary.archived));
        }
        if (summary.stopped) {
            parts.push(_t("%s marked never reorder", summary.stopped));
        }
        return parts.length ? parts.join(", ") : _t("No changes applied");
    }

    reset() {
        this.state.cards = [];
        this.state.cardById = {};
        this.state.loaded = 0;
        this.state.total = 0;
        this.state.offset = 0;
        this.state.staged = [];
        this.state.summaryOpen = false;
        this.state.pendingSnooze = false;
        this.state.pendingOrder = false;
        this.state.customOrderQty = "";
        this.history = [];
    }

    cancelAndClose() {
        this.actionService.doAction({ type: "ir.actions.act_window_close" });
    }

    onKeydown(ev) {
        const tag = (ev.target && ev.target.tagName) || "";
        if (INPUT_TAGS.includes(tag)) {
            return;
        }
        if (this.state.summaryOpen) {
            if (ev.key === "Escape") {
                this.closeSummary();
            }
            return;
        }
        if (this.state.pendingSnooze) {
            const key = (ev.key || "").toLowerCase();
            if (key in SNOOZE_CHOICES) {
                this.applySnooze(SNOOZE_CHOICES[key]);
            } else if (ev.key === "Escape") {
                this.state.pendingSnooze = false;
            }
            return;
        }
        if (this.state.pendingOrder) {
            const key = (ev.key || "").toLowerCase();
            if (["1", "2", "3"].includes(key)) {
                this.orderQty(Number(key));
            } else if (ev.key === "Escape") {
                this.state.pendingOrder = false;
                this.state.customOrderQty = "";
            }
            return;
        }
        if (this.state.loading || this.state.busy || this.state.error) {
            return;
        }
        switch (ev.key) {
            case "Enter":
                ev.preventDefault();
                this.orderDefault();
                break;
            case "o":
            case "O":
                this.openOrderPrompt();
                break;
            case "s":
            case "S":
                this.beginSnooze();
                break;
            case "a":
            case "A":
                this.archiveCurrent();
                break;
            case "n":
            case "N":
                this.stopReorder();
                break;
            case "ArrowRight":
                ev.preventDefault();
                this.skip();
                break;
            case "ArrowLeft":
                ev.preventDefault();
                this.undo();
                break;
            case "Escape":
                this.openSummary();
                break;
            default:
                break;
        }
    }
}

registry.category("actions").add("replenishment_review", ReplenishmentReview);
