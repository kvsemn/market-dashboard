import logging
from datetime import timedelta

from psycopg2.extras import RealDictCursor
from nicegui import run, ui


def load_history(connect, region_code, product_code, days, grain):
    if grain not in ("hour", "day"):
        raise ValueError("Недопустимая гранулярность")
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL TIME ZONE 'UTC'")
                cur.execute("""
                    SELECT max(period_at) AS latest
                    FROM market.pipeline_runs
                """)
                latest = cur.fetchone()["latest"]
                if latest is None:
                    return {}, None, [], None

                cur.execute("""
                    SELECT DISTINCT p.product_code, t.name
                    FROM market.prices p
                    JOIN market.products t ON t.code = p.product_code
                    WHERE p.region_code = %s
                      AND p.channel = 'wholesale'
                      AND p.observed_at <= %s
                    ORDER BY p.product_code
                """, (region_code, latest))
                products = {
                    row["product_code"]: row["name"]
                    for row in cur.fetchall()
                }

                selected = (
                    product_code if product_code in products
                    else next(iter(products), None)
                )
                if selected is None:
                    return products, None, [], latest

                cur.execute("""
                    SELECT
                        date_trunc(%s, p.observed_at) AS observed_at,
                        p.company_code,
                        c.name AS company_name,
                        c.role,
                        round(
                            avg(p.price_rub) FILTER (
                                WHERE p.is_available
                            ),
                            2
                        ) AS price_rub,
                        bool_or(p.is_available) AS is_available
                    FROM market.prices p
                    JOIN market.companies c ON c.code = p.company_code
                    WHERE p.region_code = %s
                      AND p.product_code = %s
                      AND p.channel = 'wholesale'
                      AND p.observed_at >= %s
                      AND p.observed_at <= %s
                    GROUP BY 1, p.company_code, c.name, c.role
                    ORDER BY 1, p.company_code
                """, (
                    grain,
                    region_code,
                    selected,
                    latest - timedelta(days=days),
                    latest,
                ))
                return products, selected, cur.fetchall(), latest
    finally:
        conn.close()


async def build_history(region, connect, filters):
    product = filters.product
    client = filters.client
    with ui.card().classes("w-full"):
        ui.label("История цен").classes("text-xl font-semibold")
        ui.label(
            "Опт · ₽ за упаковку · без НДС и доставки · время UTC"
        ).classes("text-sm text-slate-500")

        with ui.row().classes("w-full items-center gap-4"):
            period = ui.select(
                {7: "7 дней", 30: "30 дней", 90: "90 дней"},
                value=7,
                label="Период графика",
            ).classes("w-40")
            granularity = ui.select(
                {"hour": "По часам", "day": "По дням"},
                value="hour",
                label="Гранулярность",
            ).classes("w-44")

        status = ui.label().classes("text-sm text-slate-500")
        chart_area = ui.column().classes("w-full")
        ui.label(
            "Толстая линия — выбранный клиент, тонкие — конкуренты. "
            "Нажми название компании в легенде, чтобы скрыть ее линию. "
            "Точки — средние доступных наблюдений за час или день. "
            "Крайние дни выбранного периода могут быть неполными. "
            "Старая история содержит только дневные наблюдения."
        ).classes("text-xs text-slate-500")

    revision = 0

    async def refresh():
        nonlocal revision

        revision += 1
        request = revision
        selection = filters.snapshot()
        selected_region = region.value

        if filters.loading or not region.value or not product.value or not client.value:
            chart_area.clear()
            status.set_text(
                "Выбери регион на карте или в списке над картой."
            )
            return

        status.set_text("Загрузка истории...")

        try:
            products, selected, rows, latest = await run.io_bound(
                load_history,
                connect,
                selected_region,
                product.value,
                int(period.value),
                granularity.value,
            )
        except Exception:
            logging.exception("Price history load failed")
            if request == revision:
                chart_area.clear()
                status.set_text("Не удалось загрузить историю цен.")
            return

        # Если пользователь уже выбрал другой регион,
        # не отображаем результат старого запроса.
        if request != revision or selection != filters.snapshot():
            return

        chart_area.clear()
        if not rows:
            status.set_text("За выбранный период наблюдений нет.")
            return

        rows = [row for row in rows if row["role"] != "client"
                or row["company_code"] == client.value]
        times = sorted({
            int(row["observed_at"].timestamp() * 1000)
            for row in rows
        })
        companies = {}

        for row in rows:
            company = companies.setdefault(
                row["company_code"],
                {
                    "name": row["company_name"],
                    "role": row["role"],
                    "values": {},
                },
            )
            timestamp = int(row["observed_at"].timestamp() * 1000)
            company["values"][timestamp] = (
                float(row["price_rub"])
                if row["is_available"]
                else None
            )

        series = []
        for code, company in sorted(companies.items()):
            series.append({
                "id": code,
                "name": (
                    ("Клиент" if company["role"] == "client" else "Конкурент")
                    + " · " + company["name"]
                ),
                "type": "line",
                "showSymbol": False,
                "connectNulls": False,
                "smooth": False,
                "lineStyle": {
                    "width": 3 if company["role"] == "client" else 1.5
                },
                "data": [
                    [timestamp, company["values"].get(timestamp)]
                    for timestamp in times
                ],
            })

        with chart_area:
            ui.echart({
                "useUTC": True,
                "animation": False,
                "tooltip": {
                    "trigger": "axis",
                    "valueFormatter": None,
                },
                "legend": {
                    "type": "scroll",
                    "top": 0,
                },
                "grid": {
                    "left": 65,
                    "right": 25,
                    "top": 60,
                    "bottom": 85,
                },
                "xAxis": {
                    "type": "time",
                    "name": "UTC",
                },
                "yAxis": {
                    "type": "value",
                    "name": "₽ / упаковка",
                    "scale": True,
                },
                "dataZoom": [
                    {"type": "inside"},
                    {"type": "slider", "bottom": 15},
                ],
                "series": series,
            }).classes("w-full").style("height: 430px;")

        status.set_text(
            f"Компаний: {len(companies)} · "
            f"Последний период: {latest:%d.%m.%Y %H:%M} UTC"
        )

    filters.subscribe(refresh)
    period.on_value_change(refresh)
    granularity.on_value_change(refresh)

    await refresh()
    ui.timer(60, refresh)
