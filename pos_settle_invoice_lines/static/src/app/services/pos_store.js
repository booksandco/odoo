/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

patch(PosStore.prototype, {
    async onClickSettleInvoices(invoiceIds, partnerId, commercialPartnerId) {
        // Let the standard module create the settle line that carries the residual amount.
        await super.onClickSettleInvoices(...arguments);

        const order = this.getOrder();
        if (!order) {
            return;
        }

        // Fetch the product lines for each selected invoice.
        const invoiceLineMap = {};
        for (const invoiceId of invoiceIds) {
            const lines = await this.data.call(
                "account.move",
                "get_pos_invoice_line_data",
                [invoiceId]
            );
            invoiceLineMap[invoiceId] = lines;
        }

        // Make sure the products are available in the POS client even if they are
        // not normally available_in_pos.
        const productTmplIds = new Set();
        for (const lines of Object.values(invoiceLineMap)) {
            for (const line of lines) {
                productTmplIds.add(line.product_tmpl_id);
            }
        }
        if (productTmplIds.size) {
            await this.loadNewProducts([["id", "in", [...productTmplIds]]]);
        }

        // Add a zero-price line for every invoice product line.
        for (const invoiceId of invoiceIds) {
            const invoice = this.models["account.move"].get(invoiceId);
            if (!invoice) {
                continue;
            }
            const lines = invoiceLineMap[invoiceId] || [];
            lines.sort((a, b) => a.sequence - b.sequence);
            for (const line of lines) {
                const productTemplate = this.models["product.template"].get(line.product_tmpl_id);
                const product = this.models["product.product"].get(line.product_id);
                if (!productTemplate || !product) {
                    continue;
                }
                const newLine = await this.addLineToOrder(
                    {
                        product_tmpl_id: productTemplate,
                        product_id: product,
                        qty: line.quantity,
                        price_unit: 0,
                        tax_ids: [],
                        discount: 0,
                        settled_invoice_id: invoice,
                    },
                    order,
                    {},
                    false
                );
                if (newLine) {
                    newLine.settledInvoiceLineName = line.name || product.display_name;
                    newLine.setFullProductName();
                }
            }
        }
    },
});

patch(PosOrderline.prototype, {
    setFullProductName() {
        // Use the invoice line description for the expanded invoice lines.
        if (
            this.settled_invoice_id &&
            !this.isSettleInvoiceLine() &&
            this.settledInvoiceLineName
        ) {
            this.full_product_name = this.settledInvoiceLineName;
            return;
        }
        super.setFullProductName(...arguments);
    },

    get canBeRemoved() {
        // Prevent removal of any line tied to a settled invoice so the receipt
        // stays consistent with the settlement.
        if (this.settled_invoice_id) {
            return false;
        }
        return super.canBeRemoved;
    },
});
