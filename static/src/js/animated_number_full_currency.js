/** @odoo-module **/

/**
 * En Odoo 19 el componente AnimatedNumber del resumen de kanban formatea los
 * importes con `humanReadable: true`, por lo que el "Ingreso esperado" del CRM
 * se muestra abreviado (p. ej. "$ 10,000k"). Aquí se parchea `format()` para
 * que, SOLO en el kanban de crm.lead y para la columna de ingreso esperado, use
 * el formato monetario completo ("$ 10,000,000.00").
 *
 * El patch solo importa de @web (no de crm); el filtro por resModel es en
 * runtime, así que puede vivir en cualquier módulo de backend.
 */
import { patch } from "@web/core/utils/patch";
import { AnimatedNumber } from "@web/views/view_components/animated_number";
import { formatInteger, formatMonetary } from "@web/views/fields/formatters";

patch(AnimatedNumber.prototype, {
    format(value) {
        const isCrmExpectedRevenue =
            this.env.searchModel?.resModel === "crm.lead" &&
            ["Ingreso esperado", "Expected Revenue"].includes(this.props.title);

        if (isCrmExpectedRevenue) {
            if (this.currencyId) {
                return formatMonetary(value, {
                    currencyId: this.currencyId,
                    humanReadable: false,
                    digits: [null, 2],
                    trailingZeros: true,
                });
            }
            return formatInteger(value, { humanReadable: false });
        }

        return super.format(value);
    },
});
