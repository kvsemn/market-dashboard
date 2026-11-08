import logging
from nicegui import run, ui
from psycopg2.extras import RealDictCursor


def load_assortment(connect, region, client, product):
    conn=connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT max(period_at) AS latest FROM market.assortment_runs')
                latest=cur.fetchone()['latest']
                cur.execute('''
                    SELECT c.name AS company, p.name AS product, a.is_available,
                           a.observed_at, a.price_rub,p.package_kg,
                           CASE WHEN a.product_code=%s THEN 'Идентичный товар'
                                ELSE 'Потенциальный заменитель' END AS kind,
                           coalesce(s.rationale,'Тот же код номенклатуры') AS rationale,
                           a.source_label
                    FROM market.assortment_observations a
                    JOIN market.companies c ON c.code=a.company_code
                    JOIN market.products p ON p.code=a.product_code
                    LEFT JOIN market.product_substitutes s
                      ON s.substitute_code=a.product_code AND s.product_code=%s
                    WHERE a.observed_at=%s AND a.region_code=%s AND a.channel='wholesale'
                      AND (a.product_code=%s OR s.product_code IS NOT NULL)
                      AND (c.role='competitor' OR a.company_code=%s)
                    ORDER BY c.role,a.company_code,a.product_code
                ''',(product,product,latest,region,product,client))
                rows=cur.fetchall()
                for i,r in enumerate(rows):
                    r['id']=i;r['price_rub']=float(r['price_rub']);r['package_kg']=float(r['package_kg'])
                    r['observed_at']=r['observed_at'].isoformat()
                    r['availability']='В наличии' if r['is_available'] else 'Недоступен'
                return latest,rows
    finally:
        conn.close()


async def build_assortment(region,connect,filters):
    with ui.card().classes('w-full'):
        ui.label('Ассортимент и заменители').classes('text-xl font-semibold')
        ui.badge('Синтетические данные · демонстрационный сценарий',color='amber')
        ui.label('Отдельные снимки ассортимента. Демо-сценарий доступности не меняет предложения в остальных ценовых графиках. '
                 'Для аналога совпадение назначения и массы упаковки не доказывает взаимозаменяемость.').classes('text-sm text-slate-500')
        status=ui.label().classes('text-sm text-slate-500')
        body=ui.column().classes('w-full')
    revision=0
    async def refresh():
        nonlocal revision
        revision+=1;request=revision;selection=filters.snapshot()
        body.clear()
        if filters.loading or not region.value or not filters.client.value or not filters.product.value:
            status.set_text('Выберите регион, клиента и товар в общих фильтрах.');return
        try:
            latest,rows=await run.io_bound(load_assortment,connect,region.value,filters.client.value,filters.product.value)
        except Exception:
            logging.exception('Assortment load failed')
            if request==revision:status.set_text('Не удалось загрузить ассортимент.')
            return
        if request!=revision or selection!=filters.snapshot():return
        status.set_text(f'Снимок: {latest} · Предложений: {len(rows)}' if latest else 'Первый снимок появится после запуска DAG.')
        with body:
            if not rows:ui.label('Для выбранного товара предложений нет.');return
            ui.table(columns=[dict(name=k,field=k,label=v,align='left',sortable=True) for k,v in (
                ('company','Участник'),('product','Предложение'),('kind','Сопоставимость'),
                ('availability','Доступность'),('price_rub','Последняя цена, ₽/уп.'),
                ('package_kg','Масса, кг'),('rationale','Основание сопоставления'),('source_label','Источник'),
            )],rows=rows,row_key='id',pagination=10).classes('w-full')
    filters.subscribe(refresh)
    await refresh()
    ui.timer(60,refresh)


def build_assortment_navigation(connect,filters,activate):
    from shared_filters import load_options
    with ui.dialog() as dialog, ui.card().classes('w-full').style('max-width:700px'):
        content=ui.column().classes('w-full')
        ui.button('Закрыть',on_click=dialog.close).props('flat')
    revision=0
    async def open_event(event):
        nonlocal revision
        revision+=1;request=revision
        content.clear();dialog.open()
        try:
            rows=await run.io_bound(load_options,connect,event['region_code'])
        except Exception:
            logging.exception('Assortment navigation failed')
            ui.notify('Не удалось загрузить клиентов.',type='negative');return
        if request!=revision:return
        choices={r['company_code']:r['company_name'] for r in rows if r['product_code']==event['product_code']}
        with content:
            ui.label(event['title']).classes('font-semibold')
            ui.label('Выберите клиента с базовым товаром события. Откроется текущий ассортимент; дата события может отличаться.')
            if not choices:ui.label('Сейчас нет клиентов с этим базовым товаром.');return
            client=ui.select(choices,value=None,label='Клиент').classes('w-full')
            button=ui.button('Открыть ассортимент')
            async def apply():
                if not client.value:ui.notify('Выберите клиента.');return
                button.disable()
                try:
                    await filters.select_context(event['region_code'],client.value,event['product_code'])
                    activate();dialog.close()
                except Exception:
                    logging.exception('Assortment selection failed')
                    ui.notify('Не удалось применить выбор. Повторите переход.',type='warning')
                finally:button.enable()
            button.on_click(apply)
    return open_event
