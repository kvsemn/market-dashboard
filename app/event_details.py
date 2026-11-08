from decimal import Decimal, InvalidOperation

from nicegui import ui


def number(value, suffix="", signed=False):
    if value is None:
        return "Нет данных"
    try:
        value = Decimal(str(value))
        return (
            format(value, "+.2f" if signed else ".2f") + suffix
        )
    except (InvalidOperation, ValueError):
        return "Нет данных"


def render_event_details(event):
    evidence = event.get("evidence") or {}
    event_type = event["event_type"]

    if event_type == "margin_below_plan":
        ui.label(
            "День продаж: " + str(evidence.get("sales_date", "—"))
        ).classes("text-sm text-slate-500")

        metrics = [
            (
                "Маржа: факт",
                number(evidence.get("actual_margin_pct"), "%"),
            ),
            (
                "Маржа: план",
                number(evidence.get("plan_margin_pct"), "%"),
            ),
            (
                "Отклонение от плана",
                number(evidence.get("margin_gap_pp"), " п.п.", True),
            ),
            (
                "Себестоимость единицы к плану",
                number(
                    evidence.get("unit_cost_change_pct"), "%", True
                ),
            ),
        ]

        with ui.row().classes("w-full gap-6"):
            for title, value in metrics:
                with ui.column().classes("gap-1"):
                    ui.label(title).classes("text-xs text-slate-500")
                    ui.label(value).classes("font-semibold")

        competitor = evidence.get("competitor_name")
        if competitor:
            ui.label(
                "Минимальная средняя цена предложения за день: "
                + competitor
            ).classes("text-sm font-medium")

            ui.label(
                "Клиент: "
                + number(evidence.get("client_offer_rub"), " ₽")
                + " · Конкурент: "
                + number(evidence.get("competitor_offer_rub"), " ₽")
                + " · Отклонение: "
                + number(
                    evidence.get("competitor_gap_pct"), "%", True
                )
            ).classes("text-sm")

            ui.label(
                "Сравнение одного товара, региона и канала. "
                "Совпадение с падением маржи не доказывает причину."
            ).classes("text-xs text-slate-500")

    elif event_type in ("price_decrease", "price_increase"):
        ui.label(
            number(evidence.get("previous_price_rub"), " ₽")
            + " → "
            + number(evidence.get("current_price_rub"), " ₽")
            + " · "
            + number(evidence.get("change_pct"), "%", True)
        ).classes("font-semibold")

        ui.label(
            "Предыдущее наблюдение: "
            + str(evidence.get("previous_observed_at", "—"))
        ).classes("text-xs text-slate-500")

    elif event_type == "new_competitor":
        ui.label(
            "Первое присутствие в синтетических данных: "
            + str(evidence.get("first_seen_at", "—"))
        ).classes("text-sm text-slate-500")


    elif event_type == "market_share_change":
        ui.label(
            "Расчетная доля: " + number(evidence.get("previous_share_pct"), "%")
            + " → " + number(evidence.get("current_share_pct"), "%")
            + " · " + number(evidence.get("share_change_pp"), " п.п.", True)
        ).classes("font-semibold")
        ui.label(
            f"Дни: {evidence.get('previous_date', '—')} → {evidence.get('market_date', '—')}"
        ).classes("text-sm text-slate-500")
        ui.label(
            "Доля по выручке в синтетической модели. Изменение не доказывает "
            "переход покупателей и не является оценкой фактического рынка."
        ).classes("text-xs text-slate-500")

    elif event_type == "price_position_change":
        ui.label(
            "Наценка клиента к самому дешевому конкуренту: "
            + number(evidence.get("previous_premium_pct"), "%")
            + " → " + number(evidence.get("current_premium_pct"), "%")
            + " · " + number(evidence.get("position_change_pp"), " п.п.", True)
        ).classes("font-semibold")
        ui.label(
            "Цена клиента: " + number(evidence.get("previous_client_price_rub"), " ₽")
            + " → " + number(evidence.get("client_price_rub"), " ₽")
        )
        ui.label(
            "Минимальная цена конкурента: "
            + number(evidence.get("previous_competitor_price_rub"), " ₽")
            + " → " + number(evidence.get("competitor_price_rub"), " ₽")
        )
        ui.label(
            f"Конкурент: {evidence.get('previous_competitor_name', '—')} → "
            f"{evidence.get('competitor_name', '—')}"
        )
        ui.label(
            "Предыдущий час: " + str(evidence.get("previous_observed_at", "—"))
        ).classes("text-sm text-slate-500")
        ui.label(
            "Рост наценки означает ухудшение ценовой позиции. Сравниваются "
            "один товар, регион и канал за соседние часы. Самый дешевый конкурент "
            "может меняться; это не свидетельство изменения спроса или маржи."
        ).classes("text-xs text-slate-500")


    elif event_type in ("assortment_added", "assortment_removed", "substitute_added"):
        ui.label("Предложение: " + str(evidence.get("offered_product_name", "—"))).classes("font-semibold")
        ui.label("Доступность: " + ("В наличии" if evidence.get("previous_available") else "Недоступен")
                 + " → " + ("В наличии" if evidence.get("is_available") else "Недоступен"))
        ui.label("Цена предложения: " + number(evidence.get("price_rub"), " ₽/уп."))
        ui.label("Предыдущий снимок: " + str(evidence.get("previous_observed_at", "—")))
        ui.label(str(evidence.get("source_label", "Синтетические данные"))).classes("text-xs text-slate-500")
        ui.label("Демо-сценарий в отдельном реестре ассортимента; ценовые графики не изменяются. "
                 "Недоступность не означает прекращение производства. "
                 "Для заменителя требуется проверка характеристик и условий применения.").classes("text-xs text-slate-500")
