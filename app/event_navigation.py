import logging

from nicegui import run, ui
from psycopg2.extras import RealDictCursor
from event_context import candidate_rows
from event_details import render_event_details


def load_context(connect, event_id):
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''
                    SELECT e.*, c.role AS company_role, r.name AS region_name
                    FROM market.events e
                    LEFT JOIN market.companies c ON c.code=e.company_code
                    JOIN market.regions r ON r.code=e.region_code
                    WHERE e.id=%s
                ''', (event_id,))
                event = cur.fetchone()
                if event is None:
                    raise ValueError('Событие не найдено.')
                cur.execute('''
                    SELECT * FROM market.v_price_comparison
                    WHERE region_code=%s AND channel='wholesale'
                    ORDER BY client_code, product_code, competitor_code
                ''', (event['region_code'],))
                rows = candidate_rows(event, cur.fetchall())
                cur.execute('''
                    SELECT explanation, verification_needed
                    FROM market.hypotheses WHERE event_id=%s
                    ORDER BY hypothesis_code
                ''', (event_id,))
                return event, rows, cur.fetchall()
    finally:
        conn.close()


def build_event_navigation(connect, filters, open_calculator):
    with ui.dialog() as dialog, ui.card().classes('w-full').style('max-width:850px'):
        content = ui.column().classes('w-full gap-3')
        ui.button('Закрыть окно', on_click=dialog.close).props('flat no-caps')
    request_number = 0

    async def open_event(event_id):
        nonlocal request_number
        request_number += 1
        request = request_number
        content.clear()
        with content:
            ui.label('Загрузка контекста события…')
        dialog.open()
        try:
            event, rows, hypotheses = await run.io_bound(load_context, connect, event_id)
        except Exception:
            logging.exception('Event analysis load failed')
            if request == request_number:
                content.clear()
                with content:
                    ui.label('Не удалось загрузить контекст события. Повторите переход.')
            return
        if request != request_number:
            return
        content.clear()
        with content:
            ui.label('Разбор события').classes('text-xl font-semibold')
            ui.label(event['title']).classes('font-semibold')
            ui.label(f"{event['region_name']} · Дата события: {event['observed_at']}")
            ui.badge('Факты на дату события · синтетические данные', color='blue')
            render_event_details(event)
            for hypothesis in hypotheses:
                with ui.expansion('Гипотеза — требует проверки').classes('w-full'):
                    ui.label(hypothesis['explanation'])
                    ui.label(hypothesis['verification_needed']).classes('text-slate-500')
            ui.separator()
            ui.label(
                'Выберите связку для анализа. Графики сохранят свои периоды; '
                'для давнего события увеличьте период графика. '
                'Калькулятор откроется по текущим ценам. '
                'Совпадение событий не доказывает причинную связь.'
            ).classes('text-sm text-slate-500')
            if not rows:
                ui.label(
                    'Сейчас нет сопоставимых предложений для участников и товара этого события. '
                    'Другой клиент, товар или конкурент автоматически не подставляется.'
                ).classes('text-orange-700')
                return
            ui.label(
                'Событие о конкуренте не определяет единственного пострадавшего клиента. '
                'Выбор ниже задает контекст анализа, а не подтверждает влияние.'
                if event.get('company_role') == 'competitor'
                else 'Связки ограничены клиентом и товаром события.'
            ).classes('text-sm text-slate-500')
            options = {
                i: f"{r['client_name']} · {r['product_name']} · {r['competitor_name']}"
                for i, r in enumerate(rows)
            }
            choice = ui.select(options, value=None, label='Клиент → товар → конкурент').classes('w-full')
            button = ui.button('Открыть графики и калькулятор', icon='open_in_new').props('no-caps')

            async def apply_selection():
                if choice.value is None:
                    ui.notify('Выберите связку для анализа.', type='warning')
                    return
                row = rows[choice.value]
                button.disable()
                try:
                    await filters.select_context(
                        event['region_code'], row['client_code'], row['product_code'],
                    )
                    opened = await open_calculator(row['competitor_code'], event['title'])
                    if not opened:
                        ui.notify('Текущие данные изменились. Проверьте фильтры и повторите переход.', type='warning')
                        return
                    dialog.close()
                    ui.notify('Графики и калькулятор открыты для выбранной связки.', type='positive')
                except ValueError as error:
                    ui.notify(str(error), type='warning')
                except Exception:
                    logging.exception('Event analysis navigation failed')
                    ui.notify('Не удалось завершить переход. Повторите попытку.', type='negative')
                finally:
                    button.enable()

            button.on_click(apply_selection)
    return open_event
