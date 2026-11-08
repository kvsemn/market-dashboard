import hashlib
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from airflow.sdk import Connection


def stable_fraction(key):
    value = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(value[:8], "big") / (2**64 - 1)


def money(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def generate_sales():
    config = Connection.get("dashboard_db")
    conn = psycopg2.connect(
        host=config.host,
        port=config.port or 5432,
        dbname=config.schema,
        user=config.login,
        password=config.password,
        connect_timeout=10,
    )

    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL TIME ZONE 'UTC'")
                cur.execute("SELECT pg_advisory_xact_lock(710012)")

                cur.execute("""
                    SELECT min(period_at) AS origin,
                           max(period_at) AS latest
                    FROM market.pipeline_runs
                """)
                bounds = cur.fetchone()

                if bounds["latest"] is None:
                    raise RuntimeError("Сначала необходимо загрузить цены")

                # Первый исторический день может быть неполным.
                # Сегодняшний день также исключаем.
                first_day = bounds["origin"].date() + timedelta(days=1)
                end_day = bounds["latest"].date()

                cur.execute("""
                    SELECT first_seen_at
                    FROM market.presence
                    WHERE company_code = 'R00C5'
                      AND region_code = 'RU-NVS'
                      AND channel = 'wholesale'
                """)
                entrant = cur.fetchone()
                entrant_at = entrant["first_seen_at"] if entrant else None

                # Дата сценария закреплена относительно начала истории.
                cost_increase_day = (
                    bounds["origin"].date() + timedelta(days=83)
                )

                cur.execute("""
                    SELECT
                        (p.observed_at AT TIME ZONE 'UTC')::date
                            AS sales_date,
                        p.company_code,
                        p.region_code,
                        p.channel,
                        p.product_code,
                        avg(p.price_rub) AS offered_price,
                        max(p.observed_at) AS source_prices_until
                    FROM market.prices p
                    JOIN market.companies c ON c.code = p.company_code
                    WHERE c.role = 'client'
                      AND p.channel = 'wholesale'
                      AND p.is_available
                      AND p.observed_at >= %s::date
                      AND p.observed_at < %s::date
                      AND NOT EXISTS (
                          SELECT 1
                          FROM market.daily_sales s
                          WHERE s.sales_date =
                              (p.observed_at AT TIME ZONE 'UTC')::date
                            AND s.company_code = p.company_code
                            AND s.region_code = p.region_code
                            AND s.channel = p.channel
                            AND s.product_code = p.product_code
                      )
                    GROUP BY 1, p.company_code, p.region_code,
                             p.channel, p.product_code
                    ORDER BY 1, p.company_code, p.product_code
                """, (first_day, end_day))
                observations = cur.fetchall()

                rows = []

                for row in observations:
                    day = row["sales_date"]
                    company = row["company_code"]
                    product = row["product_code"]

                    # Разбираем идентификаторы нашего синтетического набора.
                    region_index = int(company[1:3])
                    client_slot = int(company[4:])
                    product_index = int(product[1:])

                    plan_unit_price = money(
                        (300 + product_index * 27)
                        * (1 + region_index * 0.015)
                        * (1 + (client_slot - 2) * 0.015)
                    )

                    # Плановая маржа 22%.
                    plan_unit_cost = money(plan_unit_price * Decimal("0.78"))

                    base_qty = 35 + int(
                        65 * stable_fraction(f"{company}:{product}")
                    )
                    weekday_factor = 0.75 if day.weekday() >= 5 else 1
                    plan_qty = max(1, round(base_qty * weekday_factor))

                    noise = 0.96 + 0.08 * stable_fraction(
                        f"{company}:{product}:{day}"
                    )
                    volume_factor = noise
                    discount = 0.0

                    if row["region_code"] == "RU-NVS" and entrant_at:
                        day_end = datetime.combine(
                            day + timedelta(days=1),
                            time.min,
                            tzinfo=timezone.utc,
                        )
                        exposure = max(
                            0.0,
                            min(
                                1.0,
                                (day_end - entrant_at).total_seconds()
                                / 86400,
                            ),
                        )
                        volume_factor *= 1 - 0.25 * exposure
                        discount = 0.07 * exposure

                    actual_qty = max(0, round(plan_qty * volume_factor))
                    actual_unit_price = money(
                        row["offered_price"]
                        * Decimal(str(1 - discount))
                    )

                    cost_factor = Decimal("1")
                    if company == "R01C0" and day >= cost_increase_day:
                        cost_factor = Decimal("1.10")

                    actual_unit_cost = money(
                        plan_unit_cost * cost_factor
                    )

                    rows.append((
                        day,
                        company,
                        row["region_code"],
                        row["channel"],
                        product,
                        actual_qty,
                        money(actual_unit_price * actual_qty),
                        money(actual_unit_cost * actual_qty),
                        plan_qty,
                        money(plan_unit_price * plan_qty),
                        money(plan_unit_cost * plan_qty),
                        row["source_prices_until"],
                        "sales-v1",
                    ))

                if rows:
                    execute_values(cur, """
                        INSERT INTO market.daily_sales (
                            sales_date, company_code, region_code,
                            channel, product_code,
                            actual_qty, actual_revenue, actual_cogs,
                            plan_qty, plan_revenue, plan_cogs,
                            source_prices_until, generator_version
                        )
                        VALUES %s
                        ON CONFLICT (
                            sales_date, company_code, region_code,
                            channel, product_code
                        ) DO NOTHING
                    """, rows, page_size=1000)

                print(
                    f"Добавлено записей продаж: {len(rows)}. "
                    f"Обрабатываем дни до {end_day}, не включая его."
                )
    finally:
        conn.close()
