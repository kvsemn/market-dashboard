import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from psycopg2.extras import RealDictCursor
from nicegui import run, ui


def money(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def rub(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def load_comparisons(connect, region_code):
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT *
                    FROM market.v_price_comparison
                    WHERE region_code = %s AND channel = 'wholesale'
                    ORDER BY client_code, product_code, competitor_code
                """, (region_code,))
                return cur.fetchall()
    finally:
        conn.close()


def render_calculator(row):
    client_price = row["client_price_rub"]
    competitor_price = row["competitor_price_rub"]

    ui.label(
        f'Наблюдение: {row["observed_at"]} · '
        f'Клиент: {rub(client_price)} · '
        f'Конкурент: {rub(competitor_price)}'
    ).classes("font-medium")

    ui.label(
        "Все суммы — за одну упаковку, без НДС. "
        "Закупка не включает логистику; прочие затраты "
        "не должны повторять закупку и логистику."
    ).classes("text-sm text-slate-500")

    # Стартовые допущения, не сведения о конкуренте.
    defaults = [
        money(client_price * Decimal("0.70")),
        money(client_price * Decimal("0.08")),
        money(client_price * Decimal("0.07")),
    ]

    ui.label(
        "Начальные допущения: закупка — 70%, логистика — 8%, "
        "прочие затраты — 7% от цены клиента. "
        "Эти доли выбраны только для демонстрации."
    ).classes("text-sm text-slate-500")

    with ui.row().classes("w-full items-center gap-4"):
        purchase = ui.number(
            "Закупка, ₽", value=float(defaults[0]),
            min=0, precision=2,
        ).classes("w-48")
        logistics = ui.number(
            "Логистика, ₽", value=float(defaults[1]),
            min=0, precision=2,
        ).classes("w-48")
        overhead = ui.number(
            "Прочие затраты, ₽", value=float(defaults[2]),
            min=0, precision=2,
        ).classes("w-48")
        target = ui.number(
            "Целевая маржа, %", value=10,
            min=0, max=99, precision=2,
        ).classes("w-48")

    output = ui.column().classes("w-full gap-4")

    def calculate():
        output.clear()
        fields = [purchase, logistics, overhead, target]
        try:
            values = [Decimal(str(field.value)) for field in fields]
            if any(not value.is_finite() for value in values):
                raise ValueError()
            buy, shipping, other, target_margin = values
            if min(buy, shipping, other) < 0:
                raise ValueError()
            if not 0 <= target_margin < 100:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            with output:
                ui.label(
                    "Заполни затраты неотрицательными числами. "
                    "Целевая маржа должна быть от 0 до 99%."
                ).classes("text-red-700")
            return

        costs = buy + shipping + other
        residual = competitor_price - costs
        scenario_margin = 100 * residual / competitor_price
        purchase_limit = (
            competitor_price * (1 - target_margin / 100)
            - shipping - other
        )
        required_price = costs / (1 - target_margin / 100)

        with output:
            with ui.row().classes("w-full gap-4"):
                for title, value in [
                    ("Заданные затраты", rub(costs)),
                    ("Остаток после затрат", rub(residual)),
                    ("Сценарная маржа", f"{scenario_margin:.2f}%"),
                    ("Цена для целевой маржи", rub(required_price)),
                ]:
                    with ui.card().classes("min-w-48"):
                        ui.label(title).classes("text-xs text-slate-500")
                        ui.label(value).classes("text-2xl font-semibold")

            if purchase_limit < 0:
                ui.label(
                    "Целевая маржа недостижима при заданной логистике "
                    "и прочих затратах даже при нулевой закупочной цене."
                ).classes("text-red-700")
            else:
                ui.label(
                    "Для целевой маржи закупочная цена должна быть "
                    f"не выше {rub(purchase_limit)} за упаковку."
                ).classes("font-semibold")

                if buy > purchase_limit:
                    ui.label(
                        "Необходимое снижение закупочной цены: "
                        + rub(buy - purchase_limit)
                    ).classes("text-orange-700")

            if residual < 0:
                ui.label(
                    "При этих допущениях цена не покрывает затраты. "
                    "Это результат сценария, а не доказательство "
                    "убыточности конкурента."
                ).classes("text-red-700")

            ui.table(
                columns=[
                    {"name": "part", "field": "part",
                     "label": "Компонент", "align": "left"},
                    {"name": "amount", "field": "amount",
                     "label": "₽ за упаковку", "align": "right"},
                ],
                rows=[
                    {"part": "Закупка", "amount": float(money(buy))},
                    {"part": "Логистика", "amount": float(money(shipping))},
                    {"part": "Прочие затраты", "amount": float(money(other))},
                    {"part": "Остаток после затрат",
                     "amount": float(money(residual))},
                    {"part": "Цена конкурента",
                     "amount": float(competitor_price)},
                ],
                row_key="part",
            ).classes("w-full")

            ui.label(
                "Сценарная маржа учитывает все введенные затраты. "
                "Она не тождественна валовой марже в блоке продаж. "
                "Реальные закупки, расходы и прибыль конкурента неизвестны."
            ).classes("text-xs text-slate-500")

    def preset(kind):
        purchase.set_value(float(defaults[0]))
        logistics.set_value(float(defaults[1]))
        overhead.set_value(float(defaults[2]))
        if kind == "purchase":
            purchase.set_value(float(money(defaults[0] * Decimal("0.85"))))
        elif kind == "logistics":
            logistics.set_value(float(money(defaults[1] * Decimal("0.60"))))
        calculate()

    with ui.row().classes("gap-3"):
        ui.button("Рассчитать", on_click=calculate)
        ui.button(
            "Сценарий: закупка −15%",
            on_click=lambda: preset("purchase"),
        ).props("outline")
        ui.button(
            "Сценарий: логистика −40%",
            on_click=lambda: preset("logistics"),
        ).props("outline")
        ui.button(
            "Сбросить затраты",
            on_click=lambda: preset("reset"),
        ).props("flat")

    ui.label(
        "Кнопки сценариев применяют изменения к начальным допущениям. "
        "После ручного ввода нажми «Рассчитать»."
    ).classes("text-xs text-slate-500")

    calculate()


async def build_price_model(region, connect, filters):
    client, product = filters.client, filters.product
    with ui.card().classes("w-full") as card:
        ui.label("Структура цены конкурента").classes("text-xl font-semibold")
        ui.badge("Сценарий · допущения", color="amber")
        ui.label(
            "Клиент и товар выбраны в общих фильтрах. "
            "Расчет использует текущие цены, а не цены на дату события."
        ).classes("text-sm text-slate-500")
        selection_label = ui.label().classes("font-medium")
        competitor = ui.select({}, label="Конкурент для расчета").classes("w-80")
        status = ui.label().classes("text-sm text-slate-500")
        body = ui.column().classes("w-full gap-4")

    revision = 0
    updating_controls = False

    async def refresh(preferred=None, event_title=None):
        nonlocal revision, updating_controls
        revision += 1
        request = revision
        selection = filters.snapshot()
        body.clear()
        selection_label.set_text("")
        updating_controls = True
        previous = competitor.value
        try:
            competitor.set_options({}, value=None)
            competitor.disable()
        finally:
            updating_controls = False
        if filters.loading or not region.value or not client.value or not product.value:
            status.set_text("Выберите регион, клиента и товар в общих фильтрах.")
            return False
        status.set_text("Загрузка текущих цен…")
        try:
            rows = await run.io_bound(load_comparisons, connect, region.value)
        except Exception:
            logging.exception("Price model load failed")
            if request == revision:
                status.set_text("Не удалось загрузить цены.")
            return False
        if request != revision or selection != filters.snapshot():
            return False
        rows = [r for r in rows if r['client_code'] == client.value
                and r['product_code'] == product.value]
        choices = {r['competitor_code']: r['competitor_name'] for r in rows}
        if not choices:
            status.set_text("Для выбранных клиента и товара нет текущих предложений.")
            return False
        if preferred is not None and preferred not in choices:
            status.set_text("У конкурента из события больше нет текущего предложения. Другой конкурент не подставлен.")
            return False
        chosen = preferred if preferred is not None else (
            previous if previous in choices else next(iter(choices)))
        updating_controls = True
        try:
            competitor.set_options(choices, value=chosen)
            competitor.enable()
        finally:
            updating_controls = False
        row = next(r for r in rows if r['competitor_code'] == chosen)
        selection_label.set_text(
            f"{row['client_name']} → {row['product_name']} → {row['competitor_name']}"
        )
        prefix = f"Переход из события: {event_title}. " if event_title else ""
        status.set_text(prefix + "При смене выбора затраты сбрасываются. Сценарий не сохраняется в базе.")
        with body:
            render_calculator(row)
        return True

    def competitor_changed(_):
        if not updating_controls:
            return refresh(preferred=competitor.value)

    async def sync():
        await refresh()

    async def open_competitor(code, title):
        ok = await refresh(preferred=code, event_title=title)
        if ok:
            ui.run_javascript(
                f'document.getElementById("c{card.id}")?.scrollIntoView({{behavior:"smooth",block:"start"}})'
            )
        return ok

    competitor.on_value_change(competitor_changed)
    filters.subscribe(sync)
    await refresh()
    # No timer: do not erase manually entered scenario costs.
    return open_competitor
