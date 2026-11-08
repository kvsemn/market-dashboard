import hashlib
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pendulum
from airflow.sdk import Connection, dag, task, get_current_context


REGIONS = [
    ("RU-NVS", "Новосибирская область"),
    ("RU-MOW", "Москва"),
    ("RU-SPE", "Санкт-Петербург"),
    ("RU-SVE", "Свердловская область"),
    ("RU-KDA", "Краснодарский край"),
    ("RU-TAT", "Республика Татарстан"),
    ("RU-KYA", "Красноярский край"),
    ("RU-SAM", "Самарская область"),
    ("RU-NIZ", "Нижегородская область"),
    ("RU-CHE", "Челябинская область"),
]

CATEGORIES = [
    "Цемент", "Плиточный клей", "Штукатурка",
    "Шпатлевка", "Наливной пол",
]


def fraction(key):
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)


@dag(
    dag_id="generate_market",
    schedule="@hourly",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
        "execution_timeout": timedelta(minutes=15),
    },
    tags=["dashboard", "synthetic"],
)
def generate_market():
    @task
    def generate():
        import psycopg2
        from psycopg2.extras import execute_values

        context = get_current_context()
        target = (
            context.get("data_interval_end")
            or context.get("logical_date")
            or datetime.now(timezone.utc)
        )
        target = target.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )

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
            # Каталоги создаем одной транзакцией.
            with conn:
                with conn.cursor() as cur:
                    # Запрещаем параллельную работу другого генератора.
                    cur.execute(
                        "SELECT pg_try_advisory_lock(710010)"
                    )
                    if not cur.fetchone()[0]:
                        raise RuntimeError("Генератор уже работает")

                    execute_values(cur, """
                        INSERT INTO market.regions (code, name)
                        VALUES %s ON CONFLICT DO NOTHING
                    """, REGIONS)

                    products = [
                        (
                            f"P{n:03d}",
                            f"{CATEGORIES[n // 6]} — состав {n % 6 + 1}",
                            CATEGORIES[n // 6],
                            f"ДемоБренд-{n % 3 + 1}",
                            25,
                        )
                        for n in range(30)
                    ]
                    execute_values(cur, """
                        INSERT INTO market.products
                            (code, name, category, brand, package_kg)
                        VALUES %s ON CONFLICT DO NOTHING
                    """, products)

                    companies = []
                    presence = []
                    history_start = target - timedelta(days=90)

                    for region_index, (region, _) in enumerate(REGIONS):
                        for slot in range(6):
                            code = f"R{region_index:02d}C{slot}"
                            role = "client" if slot < 2 else "competitor"
                            label = "Клиент" if slot < 2 else "Конкурент"
                            companies.append((
                                code,
                                f"Демо {label} {region_index + 1}-{slot + 1}",
                                role,
                            ))

                            # Один новый игрок для демонстрации появления.
                            first_seen = (
                                target - timedelta(hours=3)
                                if region_index == 0 and slot == 5
                                else history_start
                            )
                            presence.append((
                                code, region, "wholesale", first_seen
                            ))

                    execute_values(cur, """
                        INSERT INTO market.companies (code, name, role)
                        VALUES %s ON CONFLICT DO NOTHING
                    """, companies)

                    execute_values(cur, """
                        INSERT INTO market.presence
                            (company_code, region_code, channel,
                             first_seen_at)
                        VALUES %s ON CONFLICT DO NOTHING
                    """, presence)

                    cur.execute("""
                        SELECT company_code, first_seen_at
                        FROM market.presence
                        WHERE channel = 'wholesale'
                    """)
                    first_seen = dict(cur.fetchall())

                    # Начало истории сохраняется в справочнике присутствия.
                    origin = min(first_seen.values())
                    cur.execute(
                        "SELECT period_at FROM market.pipeline_runs"
                    )
                    completed = {row[0] for row in cur.fetchall()}

            recent_start = origin + timedelta(days=83)
            periods = []
            point = origin

            while point < recent_start and point <= target:
                periods.append(point)
                point += timedelta(days=1)

            point = recent_start
            while point <= target:
                periods.append(point)
                point += timedelta(hours=1)

            written = 0
            processed = 0

            for point in periods:
                if point in completed:
                    continue

                rows = []
                hour_number = int(point.timestamp() // 3600)

                for region_index, (region, _) in enumerate(REGIONS):
                    # В регионе шесть сопоставимых товаров.
                    product_numbers = [
                        (region_index * 3 + j) % 30
                        for j in range(6)
                    ]

                    for slot in range(6):
                        company = f"R{region_index:02d}C{slot}"
                        if point < first_seen[company]:
                            continue

                        for product_number in product_numbers:
                            product = f"P{product_number:03d}"
                            base = 300 + product_number * 27
                            regional = 1 + region_index * 0.015
                            position = 1 + (slot - 2) * 0.015
                            seasonal = 1 + 0.02 * math.sin(
                                hour_number / (24 * 7)
                            )
                            noise = 1 + 0.008 * (
                                fraction(
                                    f"{company}:{product}:{hour_number}"
                                ) - 0.5
                            )

                            # Периодическая акция у части конкурентов.
                            promotion = (
                                0.88
                                if slot == 3 and hour_number % 72 < 12
                                else 1
                            )

                            # Новый игрок в Новосибирской области.
                            if region_index == 0 and slot == 5:
                                position = 0.73

                            price = Decimal(str(
                                base * regional * position
                                * seasonal * noise * promotion
                            )).quantize(Decimal("0.01"))

                            rows.append((
                                point, company, region,
                                "wholesale", product, price,
                            ))

                # Цены и отметка завершения периода записываются вместе.
                with conn:
                    with conn.cursor() as cur:
                        if rows:
                            execute_values(cur, """
                                INSERT INTO market.prices
                                    (observed_at, company_code,
                                     region_code, channel,
                                     product_code, price_rub)
                                VALUES %s
                                ON CONFLICT DO NOTHING
                            """, rows, page_size=1000)

                        cur.execute("""
                            INSERT INTO market.pipeline_runs
                                (period_at, generator_version,
                                 prices_written)
                            VALUES (%s, %s, %s)
                        """, (point, "prices-v1", len(rows)))

                written += len(rows)
                processed += 1

            print(
                f"Готово: периодов={processed}, "
                f"наблюдений={written}, целевой час={target}"
            )
        finally:
            # Закрытие соединения также освобождает advisory lock.
            conn.close()

    @task
    def detect_events():
        from pathlib import Path
        import psycopg2

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
                with conn.cursor() as cur:
                    cur.execute(
                        Path(__file__).with_name(
                            "detect_events.sql"
                        ).read_text()
                    )
                    cur.execute("SELECT count(*) FROM market.events")
                    print(f"Всего событий: {cur.fetchone()[0]}")
        finally:
            conn.close()

    @task
    def compute_sales():
        from market_sales import generate_sales
        generate_sales()

    @task
    def compute_market():
        from market_shares import generate_market_shares
        generate_market_shares()

    # TELEGRAM_NOTIFICATIONS_V1
    @task(retries=2, retry_delay=timedelta(minutes=1),
          execution_timeout=timedelta(minutes=3))
    def send_telegram():
        from telegram_notifications import notify_telegram
        notify_telegram()

    @task
    def compute_assortment():
        from market_assortment import generate_assortment
        generate_assortment()

    generate() >> compute_assortment() >> compute_sales() >> compute_market() >> detect_events() >> send_telegram()


generate_market()
