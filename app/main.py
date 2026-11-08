import logging
from functools import partial
import os
from datetime import datetime
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor
from nicegui import app, run, ui
from event_details import render_event_details
from map_view import build_map
from history_view import build_history
from sales_view import build_sales
from market_view import build_market
from price_model import build_price_model
from shared_filters import build_filters
from event_navigation import build_event_navigation
from assortment_view import build_assortment, build_assortment_navigation


def connect(write=False):
    conn = psycopg2.connect(
        host="postgres",
        dbname="dashboard",
        user="dashboard_app",
        password=os.environ["APP_DB_PASSWORD"],
        connect_timeout=10,
        options="-c statement_timeout=15000",
    )
    conn.set_session(
        readonly=not write,
        isolation_level="REPEATABLE READ",
    )
    return conn


def normalize(rows):
    return [
        {
            key: (
                float(value) if isinstance(value, Decimal)
                else value.isoformat() if isinstance(value, datetime)
                else value
            )
            for key, value in row.items()
        }
        for row in rows
    ]


def load(region, event_type="", event_days=7,
         event_status="", event_severity=""):
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT code, name FROM market.regions ORDER BY name
                """)
                regions = cur.fetchall()

                cur.execute("""
                    SELECT max(period_at) AS period,
                           max(completed_at) AS loaded
                    FROM market.pipeline_runs
                """)
                freshness = cur.fetchone()

                cur.execute("""
                    SELECT client_name, product_name, competitor_name,
                           client_price_rub, competitor_price_rub,
                           competitor_gap_pct
                    FROM market.v_price_comparison
                    WHERE (%s = '' OR region_code = %s)
                    ORDER BY competitor_gap_pct, client_code,
                             product_code, competitor_code
                """, (region, region))
                prices = normalize(cur.fetchall())
                for index, row in enumerate(prices):
                    row["id"] = index

                cur.execute("""
                    SELECT count(*) AS total,
                           count(*) FILTER (
                               WHERE severity = 'critical'
                           ) AS critical
                    FROM market.events
                    WHERE (%s = '' OR region_code = %s)
                      AND (%s = '' OR event_type = %s)
                      AND (%s = '' OR workflow_status = %s)
                      AND (%s = '' OR severity = %s)
                      AND (
                          %s = 0 OR observed_at >= (
                              SELECT max(period_at)
                              FROM market.pipeline_runs
                          ) - %s * interval '1 day'
                      )
                """, (
                    region, region,
                    event_type, event_type,
                    event_status, event_status,
                    event_severity, event_severity,
                    event_days, event_days,
                ))
                counts = cur.fetchone()

                cur.execute("""
                    SELECT * FROM (
                        SELECT f.*, e.workflow_status,
                               e.status_updated_at, e.status_version
                        FROM market.v_event_feed f
                        JOIN market.events e ON e.id = f.id
                    ) AS feed
                    WHERE (%s = '' OR region_code = %s)
                      AND (%s = '' OR event_type = %s)
                      AND (%s = '' OR workflow_status = %s)
                      AND (%s = '' OR severity = %s)
                      AND (
                          %s = 0 OR observed_at >= (
                              SELECT max(period_at)
                              FROM market.pipeline_runs
                          ) - %s * interval '1 day'
                      )
                    ORDER BY observed_at DESC, id DESC
                    LIMIT 100
                """, (
                    region, region,
                    event_type, event_type,
                    event_status, event_status,
                    event_severity, event_severity,
                    event_days, event_days,
                ))
                events = cur.fetchall()

                return regions, freshness, prices, counts, events
    finally:
        conn.close()


# EVENT_WORKFLOW_V1
EVENT_STATUSES = {
    "new": "Новое",
    "in_progress": "В работе",
    "closed": "Закрыто",
}


def save_event_status(event_id, expected_version, new_status):
    if new_status not in EVENT_STATUSES:
        raise ValueError("Недопустимый статус")
    conn = connect(write=True)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE market.events
                    SET workflow_status = %s,
                        status_updated_at = clock_timestamp(),
                        status_version = status_version + 1
                    WHERE id = %s AND status_version = %s
                    RETURNING id
                """, (new_status, event_id, expected_version))
                return cur.fetchone() is not None
    except psycopg2.errors.SerializationFailure:
        return False
    finally:
        conn.close()


@app.get("/health")
def health():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok"}
    finally:
        conn.close()


@ui.page("/")
async def index():
    ui.colors(primary="#2563eb")
    ui.query("body").classes("bg-slate-50")

    with ui.header().classes("bg-slate-900 text-white"):
        ui.label("Мониторинг рынка").classes("text-xl font-semibold")
        ui.space()
        ui.badge("Синтетические данные", color="amber")

    with ui.column().classes("w-full max-w-screen-xl mx-auto p-6 gap-5"):
        ui.label("Клиенты и конкурентное окружение").classes(
            "text-2xl font-semibold"
        )
        ui.label(
            "Опт · ₽ за упаковку · без НДС и доставки"
        ).classes("text-slate-500")

        try:
            initial = await run.io_bound(load, "")
        except Exception:
            logging.exception("Initial data load failed")
            ui.label("Не удалось загрузить данные из базы.").classes(
                "text-red-700"
            )
            return

        options = {"": "Все регионы"}
        options.update({r["code"]: r["name"] for r in initial[0]})

        with ui.row().classes("w-full items-center gap-4"):
            region = ui.select(
                options, value="", label="Регион"
            ).classes("w-80")
            ui.button("Обновить", on_click=lambda: content.refresh())

        # COMPACT_LAYOUT_V1
        build_map(region, options)
        filters = await build_filters(region, connect)

        with ui.tabs().classes("w-full bg-white rounded-lg").props(
            "align=left outside-arrows mobile-arrows active-color=primary indicator-color=primary"
        ) as analysis_tabs:
            ui.tab("prices", label="История цен", icon="show_chart").props("no-caps")
            ui.tab("sales", label="Продажи и маржа", icon="payments").props("no-caps")
            ui.tab("shares", label="Доли рынка", icon="pie_chart").props("no-caps")
            ui.tab("calculator", label="Структура цены конкурента", icon="calculate").props("no-caps")
            ui.tab("assortment", label="Ассортимент и заменители", icon="inventory_2").props("no-caps")

        with ui.tab_panels(
            analysis_tabs, value="prices", animated=False,
        ).classes("w-full bg-transparent").props("keep-alive"):
            with ui.tab_panel("prices").classes("p-0"):
                await build_history(region, connect, filters)
            with ui.tab_panel("sales").classes("p-0"):
                await build_sales(region, connect, filters)
            with ui.tab_panel("shares").classes("p-0"):
                await build_market(region, connect, filters)
            with ui.tab_panel("calculator").classes("p-0"):
                open_calculator = await build_price_model(region, connect, filters)
            with ui.tab_panel("assortment").classes("p-0"):
                await build_assortment(region, connect, filters)

        def resize_charts(_):
            # Let the visible panel settle before recalculating chart dimensions.
            ui.run_javascript(
                'requestAnimationFrame(() => requestAnimationFrame(() => '
                'window.dispatchEvent(new Event("resize"))))'
            )

        analysis_tabs.on_value_change(resize_charts)

        async def open_calculator_tab(code, title):
            analysis_tabs.set_value("calculator")
            # Wait until the previously hidden panel has mounted before scrolling.
            await ui.run_javascript(
                'return new Promise(resolve => requestAnimationFrame(() => '
                'requestAnimationFrame(() => resolve(true))))'
            )
            return await open_calculator(code, title)

        open_event = build_event_navigation(connect, filters, open_calculator_tab)
        def activate_assortment():
            analysis_tabs.set_value("assortment")
            ui.run_javascript(
                f'requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById("c{analysis_tabs.id}")?.scrollIntoView({{behavior:"smooth",block:"start"}})))'
            )

        open_assortment = build_assortment_navigation(connect, filters, activate_assortment)

        event_filters = {
            "type": "", "days": 7, "status": "", "severity": "",
        }
        saving_events = set()

        async def change_event_status(event_id, version, new_status):
            if event_id in saving_events:
                return
            saving_events.add(event_id)
            try:
                saved = await run.io_bound(
                    save_event_status, event_id, version, new_status,
                )
                if saved:
                    ui.notify("Статус сохранен", type="positive")
                else:
                    ui.notify(
                        "Событие уже изменено в другой вкладке или удалено. "
                        "Список обновлен — проверьте текущий статус.",
                        type="warning",
                    )
                content.refresh()
            except Exception:
                logging.exception("Event status update failed")
                ui.notify(
                    "Не удалось подтвердить сохранение. "
                    "Обновите список и проверьте статус перед повтором.",
                    type="negative",
                )
            finally:
                saving_events.discard(event_id)

        def change_event_filter(key, value):
            event_filters[key] = value
            content.refresh()

        @ui.refreshable
        async def content():
            try:
                _, fresh, prices, counts, events = await run.io_bound(
                    load,
                    region.value,
                    event_filters["type"],
                    event_filters["days"],
                    event_filters["status"],
                    event_filters["severity"],
                )
            except Exception:
                logging.exception("Dashboard refresh failed")
                ui.label(
                    "Ошибка загрузки. Нажмите «Обновить»."
                ).classes("text-red-700")
                return

            period = fresh["period"]
            loaded = fresh["loaded"]
            ui.label(
                f"Период данных: {period} · Загружено: {loaded}"
            ).classes("text-sm text-slate-500")

            ui.label("События рынка").classes(
                "text-xl font-semibold"
            )

            with ui.row().classes("w-full items-center gap-4"):
                ui.select(
                    {
                        "": "Все события",
                        "new_competitor": "Новый конкурент",
                        "price_decrease": "Снижение цены",
                        "price_increase": "Повышение цены",
                        "margin_below_plan": "Маржа ниже плана",
                        "market_share_change": "Изменение расчетной доли",
                        "assortment_added": "Появление товара",
                        "assortment_removed": "Исчезновение товара",
                        "substitute_added": "Появление заменителя",
                        "price_position_change": "Изменение ценовой позиции",
                    },
                    value=event_filters["type"],
                    label="Тип события",
                    on_change=lambda e: change_event_filter(
                        "type", e.value
                    ),
                ).classes("w-72")

                ui.select(
                    {
                        1: "Последние сутки",
                        7: "7 дней",
                        30: "30 дней",
                        90: "90 дней",
                        0: "Вся история",
                    },
                    value=event_filters["days"],
                    label="Период событий",
                    on_change=lambda e: change_event_filter(
                        "days", e.value
                    ),
                ).classes("w-48")

            with ui.row().classes("w-full items-center gap-4"):
                ui.select(
                    {"": "Все статусы", **EVENT_STATUSES},
                    value=event_filters["status"],
                    label="Статус события",
                    on_change=lambda e: change_event_filter("status", e.value),
                ).classes("w-56")
                ui.select(
                    {
                        "": "Любая важность",
                        "info": "Информация",
                        "warning": "Внимание",
                        "critical": "Критическое",
                    },
                    value=event_filters["severity"],
                    label="Важность",
                    on_change=lambda e: change_event_filter("severity", e.value),
                ).classes("w-56")

            ui.label(
                f"Показано {len(events)} из {counts['total']} событий. "
                "Период отсчитывается от последнего часа данных, UTC. "
                "При большом числе событий уточни фильтры."
            ).classes("text-sm text-slate-500")
            if not events:
                ui.label("По выбранным фильтрам событий нет.")

            for event in events:
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center"):
                        ui.badge(
                            {
                                "critical": "Критическое",
                                "warning": "Внимание",
                                "info": "Информация",
                            }[event["severity"]],
                            color="red"
                            if event["severity"] == "critical"
                            else "orange",
                        )
                        ui.label(event["title"]).classes(
                            "font-semibold"
                        )
                    ui.label(
                        f'{event["region_name"]} · '
                        f'{event["observed_at"]}'
                    ).classes("text-sm text-slate-500")
                    ui.label(
                        f'{event["company_name"] or ""} · '
                        f'{event["product_name"] or "Все товары"}'
                    )
                    with ui.row().classes("items-center gap-3"):
                        ui.badge(
                            EVENT_STATUSES[event["workflow_status"]],
                            color={
                                "new": "blue", "in_progress": "orange",
                                "closed": "green",
                            }[event["workflow_status"]],
                        )
                        for target, caption in (
                            ("in_progress", "В работу"),
                            ("closed", "Закрыть"),
                            ("new", "Вернуть в новые"),
                        ):
                            button = ui.button(
                                caption,
                                on_click=partial(
                                    change_event_status,
                                    event["id"], event["status_version"], target,
                                ),
                            ).props("outline dense no-caps")
                            if target == event["workflow_status"]:
                                button.disable()
                    if event["status_updated_at"]:
                        ui.label(
                            f'Статус изменен: {event["status_updated_at"]}'
                        ).classes("text-xs text-slate-500")
                    if event["event_type"] in (
                        "price_decrease", "price_increase", "new_competitor", "margin_below_plan",
                        "market_share_change", "price_position_change",
                    ):
                        ui.button(
                            "Разобрать событие", icon="manage_search",
                            on_click=partial(open_event, event["id"]),
                        ).props("outline no-caps")
                    if event["event_type"] in ("assortment_added", "assortment_removed", "substitute_added"):
                        ui.button("Открыть ассортимент", icon="inventory_2",
                                  on_click=partial(open_assortment, event)).props("outline no-caps")
                    render_event_details(event)
                    for hypothesis in event["hypotheses"]:
                        with ui.expansion(
                            "Гипотеза — требует проверки",
                            icon="help_outline",
                        ).classes("w-full"):
                            ui.label(hypothesis["explanation"])
                            ui.label(
                                hypothesis["verification_needed"]
                            ).classes("text-slate-500")

        region.on_value_change(lambda _: content.refresh())
        await content()
        ui.timer(60, content.refresh)


ui.run(
    host="0.0.0.0",
    port=8080,
    title="Мониторинг рынка",
    reload=False,
    show=False,
)
