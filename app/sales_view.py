import logging
from datetime import timedelta

from psycopg2.extras import RealDictCursor
from nicegui import run, ui


def load_sales(connect, region_code, client_code, product_code, days):
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT max(sales_date) AS latest
                    FROM market.daily_sales
                """)
                latest = cur.fetchone()["latest"]
                if latest is None:
                    return {}, "", {}, "", [], None

                cur.execute("""
                    SELECT DISTINCT s.company_code, c.name
                    FROM market.daily_sales s
                    JOIN market.companies c ON c.code = s.company_code
                    WHERE s.region_code = %s
                      AND c.role = 'client'
                    ORDER BY s.company_code
                """, (region_code,))
                clients = {
                    row["company_code"]: row["name"]
                    for row in cur.fetchall()
                }
                chosen_client = (
                    client_code if client_code in clients
                    else next(iter(clients), "")
                )
                if not chosen_client:
                    return clients, "", {}, "", [], latest

                cur.execute("""
                    SELECT DISTINCT s.product_code, p.name
                    FROM market.daily_sales s
                    JOIN market.products p ON p.code = s.product_code
                    WHERE s.region_code = %s AND s.company_code = %s
                    ORDER BY s.product_code
                """, (region_code, chosen_client))
                products = {
                    row["product_code"]: row["name"]
                    for row in cur.fetchall()
                }
                chosen_product = (
                    product_code if product_code in products else ""
                )

                cur.execute("""
                    SELECT
                        sales_date,
                        sum(actual_qty) AS actual_qty,
                        sum(plan_qty) AS plan_qty,
                        sum(actual_revenue) AS revenue,
                        sum(plan_revenue) AS plan_revenue,
                        sum(actual_cogs) AS cogs,
                        sum(plan_cogs) AS plan_cogs
                    FROM market.daily_sales
                    WHERE region_code = %s
                      AND company_code = %s
                      AND (%s = '' OR product_code = %s)
                      AND sales_date BETWEEN %s AND %s
                    GROUP BY sales_date
                    ORDER BY sales_date
                """, (
                    region_code,
                    chosen_client,
                    chosen_product,
                    chosen_product,
                    latest - timedelta(days=days - 1),
                    latest,
                ))
                rows = cur.fetchall()
                return (
                    clients, chosen_client, products,
                    chosen_product, rows, latest,
                )
    finally:
        conn.close()


def margin(revenue, cogs):
    if not revenue:
        return None
    return round(float(100 * (revenue - cogs) / revenue), 2)


def rub(value):
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def percent(value):
    return "Нет выручки" if value is None else f"{value:.2f}%"


async def build_sales(region, connect, filters):
    product = filters.product
    client = filters.client
    with ui.card().classes("w-full"):
        ui.label("Продажи и маржа").classes("text-xl font-semibold")
        ui.label(
            "Синтетические продажи · завершенные дни UTC · без НДС"
        ).classes("text-sm text-slate-500")

        with ui.row().classes("w-full items-center gap-4"):
            period = ui.select(
                {7: "7 дней", 30: "30 дней", 90: "90 дней"},
                value=7, label="Период продаж",
            ).classes("w-40")

        status = ui.label().classes("text-sm text-slate-500")
        result = ui.column().classes("w-full gap-4")

        ui.label(
            "Маржа рассчитывается из суммарной выручки "
            "и себестоимости проданного товара. "
            "Фактическая цена реализации может включать скидки."
        ).classes("text-xs text-slate-500")

    revision = 0

    async def refresh():
        nonlocal revision

        revision += 1
        request = revision
        selection = filters.snapshot()
        selected_region = region.value

        if filters.loading or not region.value or not product.value or not client.value:
            result.clear()
            status.set_text("Выбери регион на карте или в общем фильтре.")
            return

        status.set_text("Загрузка продаж...")

        try:
            clients, chosen_client, products, chosen_product, rows, latest = (
                await run.io_bound(
                    load_sales, connect, selected_region,
                    client.value, product.value, int(period.value),
                )
            )
        except Exception:
            logging.exception("Sales load failed")
            if request == revision:
                result.clear()
                status.set_text("Не удалось загрузить продажи.")
            return

        if request != revision or selection != filters.snapshot():
            return

        if chosen_client != client.value or chosen_product != product.value:
            rows = []
        result.clear()
        if not rows:
            status.set_text("За выбранный период продаж нет.")
            return

        totals = {
            key: sum(row[key] for row in rows)
            for key in (
                "revenue", "plan_revenue", "cogs",
                "plan_cogs", "actual_qty", "plan_qty",
            )
        }
        actual_margin = margin(totals["revenue"], totals["cogs"])
        plan_margin = margin(totals["plan_revenue"], totals["plan_cogs"])
        gap = (
            actual_margin - plan_margin
            if actual_margin is not None and plan_margin is not None
            else None
        )

        status.set_text(
            f'{rows[0]["sales_date"]:%d.%m.%Y} — '
            f'{rows[-1]["sales_date"]:%d.%m.%Y} · '
            f'Дней с данными: {len(rows)}'
        )

        with result:
            cards = [
                (
                    "Выручка",
                    rub(totals["revenue"]),
                    "План: " + rub(totals["plan_revenue"]),
                ),
                (
                    "Валовая прибыль",
                    rub(totals["revenue"] - totals["cogs"]),
                    "План: " + rub(
                        totals["plan_revenue"] - totals["plan_cogs"]
                    ),
                ),
                (
                    "Валовая маржа",
                    percent(actual_margin),
                    "План: " + percent(plan_margin),
                ),
                (
                    "Отклонение маржи",
                    "Нет данных" if gap is None else f"{gap:+.2f} п.п.",
                    "Факт минус план за выбранный период",
                ),
            ]
            with ui.row().classes("w-full gap-4"):
                for title, value, detail in cards:
                    with ui.card().classes("min-w-48"):
                        ui.label(title).classes("text-slate-500")
                        ui.label(value).classes("text-2xl font-semibold")
                        ui.label(detail).classes("text-xs text-slate-500")

            ui.label("Маржа по дням: план и факт").classes(
                "text-lg font-semibold"
            )
            ui.echart({
                "animation": False,
                "tooltip": {"trigger": "axis"},
                "legend": {"top": 0},
                "grid": {
                    "left": 60, "right": 25,
                    "top": 45, "bottom": 80,
                },
                "xAxis": {
                    "type": "category",
                    "data": [
                        row["sales_date"].isoformat() for row in rows
                    ],
                },
                "yAxis": {
                    "type": "value",
                    "name": "Маржа, %",
                    "scale": True,
                },
                "dataZoom": [
                    {"type": "inside"},
                    {"type": "slider", "bottom": 10},
                ],
                "series": [
                    {
                        "name": "Факт",
                        "type": "line",
                        "showSymbol": False,
                        "lineStyle": {"width": 3},
                        "data": [
                            margin(row["revenue"], row["cogs"])
                            for row in rows
                        ],
                    },
                    {
                        "name": "План",
                        "type": "line",
                        "showSymbol": False,
                        "lineStyle": {"type": "dashed"},
                        "data": [
                            margin(row["plan_revenue"], row["plan_cogs"])
                            for row in rows
                        ],
                    },
                ],
            }).classes("w-full").style("height: 360px;")

    filters.subscribe(refresh)
    period.on_value_change(refresh)

    await refresh()
    ui.timer(60, refresh)
