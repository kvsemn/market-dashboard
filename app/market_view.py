import logging
from datetime import timedelta
from decimal import Decimal

from psycopg2.extras import RealDictCursor
from nicegui import run, ui


def load_market(connect, region_code, product_code, days):
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT DISTINCT t.product_code, p.name
                    FROM market.daily_market_totals t
                    JOIN market.products p ON p.code = t.product_code
                    WHERE t.region_code = %s
                      AND t.channel = 'wholesale'
                    ORDER BY t.product_code
                """, (region_code,))
                products = {
                    row["product_code"]: row["name"]
                    for row in cur.fetchall()
                }
                selected = product_code if product_code in products else ""

                cur.execute("""
                    SELECT max(market_date) AS latest
                    FROM market.daily_market_totals
                    WHERE region_code = %s
                      AND channel = 'wholesale'
                      AND (%s = '' OR product_code = %s)
                """, (region_code, selected, selected))
                latest = cur.fetchone()["latest"]
                if latest is None:
                    return products, selected, [], []

                params = (
                    region_code, selected, selected,
                    latest - timedelta(days=days - 1), latest,
                )

                # Знаменатель считаем отдельно:
                # объем рынка не должен повторяться для каждого участника.
                cur.execute("""
                    SELECT market_date, sum(total_revenue) AS revenue
                    FROM market.daily_market_totals
                    WHERE region_code = %s
                      AND channel = 'wholesale'
                      AND (%s = '' OR product_code = %s)
                      AND market_date BETWEEN %s AND %s
                    GROUP BY market_date
                    ORDER BY market_date
                """, params)
                totals = cur.fetchall()

                cur.execute("""
                    SELECT
                        market_date,
                        participant_key,
                        participant_name,
                        participant_role,
                        sum(revenue) AS revenue
                    FROM market.v_market_shares
                    WHERE region_code = %s
                      AND channel = 'wholesale'
                      AND (%s = '' OR product_code = %s)
                      AND market_date BETWEEN %s AND %s
                    GROUP BY market_date, participant_key,
                             participant_name, participant_role
                    ORDER BY market_date, participant_key
                """, params)
                participants = cur.fetchall()

                return products, selected, totals, participants
    finally:
        conn.close()


async def build_market(region, connect, filters):
    product = filters.product
    client = filters.client
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-3"):
            ui.label("Доли рынка").classes("text-xl font-semibold")
            ui.badge("Демо · расчетные доли", color="amber")

        ui.label(
            "Доли по выручке · опт · завершенные дни UTC"
        ).classes("text-sm text-slate-500")

        ui.label('Доли рассчитаны на синтетических данных для демонстрации работы системы. Для оценки реального рынка потребуются данные об объемах продаж и согласованная методика расчета.').classes("text-sm text-slate-600")

        with ui.row().classes("w-full items-center gap-4"):
            period = ui.select(
                {7: "7 дней", 30: "30 дней", 90: "90 дней"},
                value=7, label="Период долей",
            ).classes("w-40")

        status = ui.label().classes("text-sm text-slate-500")
        result = ui.column().classes("w-full gap-4")

        with ui.expansion(
            "Допущения модели", icon="info"
        ).classes("w-full"):
            ui.label(
                "Объем рынка равен четырем суммарным плановым "
                "выручкам клиентов для того же товара, региона и дня. "
                "Прочий рынок занимает 25%. Остаток распределяется "
                "между конкурентами с учетом цены и времени присутствия."
            )
            ui.label(
                "Продажи клиентов взяты из синтетического блока продаж. "
                "Доли конкурентов не подтверждены внешними данными. "
                "Перераспределение долей — результат модели, "
                "а не доказательство перехода покупателей."
            )
            ui.label(
                "Для всех товаров доля считается из сумм выручки, "
                "а не средним арифметическим процентов."
            )

    revision = 0

    async def refresh():
        nonlocal revision

        revision += 1
        request = revision
        selection = filters.snapshot()

        if filters.loading or not region.value or not product.value or not client.value:
            result.clear()
            status.set_text("Выбери регион на карте или в общем фильтре.")
            return

        status.set_text("Загрузка долей...")
        try:
            products, selected, totals, participants = await run.io_bound(
                load_market, connect, region.value,
                product.value, int(period.value),
            )
        except Exception:
            logging.exception("Market shares load failed")
            if request == revision:
                result.clear()
                status.set_text("Не удалось загрузить модель рынка.")
            return

        if request != revision or selection != filters.snapshot():
            return

        if selected != product.value:
            totals = []
        result.clear()
        if not totals:
            status.set_text("Модельных данных пока нет.")
            return

        dates = [row["market_date"] for row in totals]
        denominators = {
            row["market_date"]: row["revenue"] for row in totals
        }
        daily_sum = {day: Decimal("0") for day in dates}
        companies = {}

        role_names = {
            "client": "Клиент",
            "competitor": "Конкурент",
            "other": "Прочий рынок",
        }

        for row in participants:
            day = row["market_date"]
            daily_sum[day] += row["revenue"]

            role = row["participant_role"]
            name = (
                "Прочий рынок" if role == "other"
                else role_names[role] + " · " + row["participant_name"]
            )
            if role == "client" and row["participant_key"] == client.value:
                name += " (выбран)"
            company = companies.setdefault(
                row["participant_key"],
                {"name": name, "role": role, "values": {}},
            )
            company["values"][day] = row["revenue"]

        if any(
            daily_sum[day] != denominators[day]
            or denominators[day] <= 0
            for day in dates
        ):
            status.set_text(
                "Нарушен баланс модели. График скрыт до проверки данных."
            )
            return

        latest = dates[-1]
        latest_total = denominators[latest]
        status.set_text(
            f"{dates[0]:%d.%m.%Y} — {latest:%d.%m.%Y} · "
            f"Объем выбранного рынка за последний день: "
            f"{latest_total:,.2f} ₽".replace(",", " ")
        )

        series = []
        table_rows = []
        for code, company in sorted(companies.items()):
            values = [
                float(
                    100 * company["values"].get(day, Decimal("0"))
                    / denominators[day]
                )
                for day in dates
            ]
            item = {
                "id": code,
                "name": company["name"],
                "type": "line",
                "stack": "market",
                "areaStyle": {},
                "showSymbol": False,
                "smooth": False,
                "data": values,
            }
            if company["role"] == "other":
                item["itemStyle"] = {"color": "#94a3b8"}
            series.append(item)

            revenue = company["values"].get(latest, Decimal("0"))
            table_rows.append({
                "id": code,
                "name": company["name"],
                "revenue": float(revenue),
                "share": round(float(100 * revenue / latest_total), 2),
            })

        table_rows.sort(key=lambda row: row["revenue"], reverse=True)

        with result:
            ui.echart({
                "animation": False,
                "tooltip": {"trigger": "axis"},
                "legend": {"type": "scroll", "top": 0},
                "grid": {
                    "left": 55, "right": 25,
                    "top": 60, "bottom": 80,
                },
                "xAxis": {
                    "type": "category",
                    "boundaryGap": False,
                    "data": [day.isoformat() for day in dates],
                },
                "yAxis": {
                    "type": "value",
                    "name": "Доля, %",
                    "min": 0,
                    "max": 100,
                },
                "dataZoom": [
                    {"type": "inside"},
                    {"type": "slider", "bottom": 10},
                ],
                "series": series,
            }).classes("w-full").style("height: 400px;")

            ui.label(
                f"Доли за {latest:%d.%m.%Y}"
            ).classes("text-lg font-semibold")

            ui.table(
                columns=[
                    {
                        "name": key, "field": key, "label": title,
                        "sortable": True, "align": "left",
                    }
                    for key, title in [
                        ("name", "Участник"),
                        ("revenue", "Модельная выручка, ₽"),
                        ("share", "Доля, %"),
                    ]
                ],
                rows=table_rows,
                row_key="id",
            ).classes("w-full")

    filters.subscribe(refresh)
    period.on_value_change(refresh)

    await refresh()
    ui.timer(60, refresh)
