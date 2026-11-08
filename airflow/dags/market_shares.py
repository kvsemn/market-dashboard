import hashlib
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values, Json
from airflow.sdk import Connection


def money(value):
    return Decimal(value).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def distribute(amount, weights):
    """Распределение суммы с точностью до копейки."""
    cents = int(amount * 100)
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("Нет положительных весов конкурентов")

    exact = {
        code: Decimal(cents) * weight / total_weight
        for code, weight in weights.items()
    }
    result = {code: int(value) for code, value in exact.items()}
    remainder = cents - sum(result.values())

    order = sorted(
        weights,
        key=lambda code: (-(exact[code] - result[code]), code),
    )
    for code in order[:remainder]:
        result[code] += 1

    return {
        code: Decimal(value) / 100
        for code, value in result.items()
    }


def generate_market_shares():
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
                cur.execute("SELECT pg_advisory_xact_lock(710013)")

                cur.execute("""
                    SELECT s.*
                    FROM market.daily_sales s
                    JOIN market.companies c ON c.code = s.company_code
                    WHERE c.role = 'client'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM market.daily_market_totals t
                          WHERE t.market_date = s.sales_date
                            AND t.region_code = s.region_code
                            AND t.channel = s.channel
                            AND t.product_code = s.product_code
                      )
                    ORDER BY s.sales_date, s.region_code,
                             s.channel, s.product_code, s.company_code
                """)
                sales = cur.fetchall()

                if not sales:
                    print("Новых дней для модели рынка нет.")
                    return

                groups = {}
                for row in sales:
                    key = (
                        row["sales_date"],
                        row["region_code"],
                        row["channel"],
                        row["product_code"],
                    )
                    groups.setdefault(key, []).append(row)

                first_day = min(key[0] for key in groups)
                last_day = max(key[0] for key in groups)

                cur.execute("""
                    SELECT
                        (p.observed_at AT TIME ZONE 'UTC')::date
                            AS market_date,
                        p.region_code, p.channel, p.product_code,
                        p.company_code,
                        avg(p.price_rub) AS offered_price,
                        pr.first_seen_at
                    FROM market.prices p
                    JOIN market.companies c ON c.code = p.company_code
                    JOIN market.presence pr
                        ON pr.company_code = p.company_code
                       AND pr.region_code = p.region_code
                       AND pr.channel = p.channel
                    WHERE c.role = 'competitor'
                      AND p.is_available
                      AND p.observed_at >= %s::date
                      AND p.observed_at < %s::date
                    GROUP BY 1, p.region_code, p.channel,
                             p.product_code, p.company_code,
                             pr.first_seen_at
                """, (first_day, last_day + timedelta(days=1)))

                competitors = {}
                for row in cur.fetchall():
                    key = (
                        row["market_date"],
                        row["region_code"],
                        row["channel"],
                        row["product_code"],
                    )
                    if key in groups:
                        competitors.setdefault(key, []).append(row)

                totals_rows = []
                participant_rows = []

                for key, clients in sorted(groups.items()):
                    day, region, channel, product = key
                    total = money(
                        sum(row["plan_revenue"] for row in clients) * 4
                    )
                    if total <= 0:
                        raise ValueError(
                            f"Неположительный объем рынка: {key}"
                        )

                    client_total = sum(
                        row["actual_revenue"] for row in clients
                    )
                    other = money(total * Decimal("0.25"))
                    budget = total - client_total - other

                    if budget < 0:
                        raise ValueError(
                            f"Допущение объема рынка недостаточно: {key}"
                        )

                    day_end = datetime.combine(
                        day + timedelta(days=1),
                        time.min,
                        tzinfo=timezone.utc,
                    )
                    weights = {}

                    for row in competitors.get(key, []):
                        active_seconds = max(
                            0,
                            min(
                                86400,
                                int(
                                    (day_end - row["first_seen_at"])
                                    .total_seconds()
                                ),
                            ),
                        )
                        if active_seconds == 0:
                            continue

                        # Постоянное отличие компаний по модельному весу.
                        digest = hashlib.sha256(
                            row["company_code"].encode()
                        ).digest()
                        base_weight = (
                            Decimal("0.8")
                            + Decimal(int.from_bytes(digest[:2], "big"))
                            / Decimal(65535) * Decimal("0.4")
                        )

                        # При прочих равных меньшая цена повышает вес.
                        weights[row["company_code"]] = (
                            base_weight
                            * Decimal(active_seconds) / Decimal(86400)
                            / row["offered_price"]
                        )

                    allocations = (
                        distribute(budget, weights) if weights else {}
                    )
                    if not weights:
                        other += budget

                    assumptions = {
                        "market_multiplier": 4,
                        "basis": "sum_of_client_plan_revenue",
                        "other_target_share": 0.25,
                        "competitor_weights":
                            "stable_company_weight * presence_fraction / price",
                        "no_competitors":
                            "remaining_revenue_allocated_to_other",
                        "is_real_market_estimate": False,
                    }
                    totals_rows.append((
                        *key, total, "shares-v1", Json(assumptions)
                    ))

                    for row in clients:
                        participant_rows.append((
                            *key,
                            row["company_code"],
                            row["company_code"],
                            "client",
                            row["actual_revenue"],
                        ))

                    for code, revenue in sorted(allocations.items()):
                        participant_rows.append((
                            *key, code, code, "competitor", revenue
                        ))

                    participant_rows.append((
                        *key, "__other__", None, "other", other
                    ))

                    if (
                        client_total + other + sum(allocations.values())
                        != total
                    ):
                        raise ValueError(f"Нарушен баланс рынка: {key}")

                execute_values(cur, """
                    INSERT INTO market.daily_market_totals (
                        market_date, region_code, channel, product_code,
                        total_revenue, model_version, assumptions
                    ) VALUES %s
                """, totals_rows, page_size=1000)

                execute_values(cur, """
                    INSERT INTO market.daily_market_sales (
                        market_date, region_code, channel, product_code,
                        participant_key, company_code,
                        participant_role, revenue
                    ) VALUES %s
                """, participant_rows, page_size=1000)

                cur.execute("""
                    SELECT count(*) AS errors
                    FROM market.v_market_balance
                    WHERE difference_rub <> 0
                """)
                if cur.fetchone()["errors"]:
                    raise ValueError(
                        "Проверка баланса не пройдена. Запись отменена."
                    )

                print(
                    f"Записей объема рынка: {len(totals_rows)}; "
                    f"строк участников: {len(participant_rows)}. "
                    "Баланс проверен."
                )
    finally:
        conn.close()
