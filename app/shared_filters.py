import logging

from nicegui import run, ui
from psycopg2.extras import RealDictCursor


def load_options(connect, region):
    if not region:
        return []
    conn = connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT DISTINCT company_code, company_name,
                                    product_code, product_name
                    FROM market.v_latest_prices
                    WHERE region_code = %s AND role = 'client'
                      AND channel = 'wholesale'
                    ORDER BY company_code, product_code
                """, (region,))
                return cur.fetchall()
    finally:
        conn.close()


class SharedFilters:
    def __init__(self, region, connect):
        self.region = region
        self.connect = connect
        self.callbacks = []
        self.updating = False
        self.loading = False
        self.revision = 0
        self.rows = []
        with ui.card().classes("w-full"):
            ui.label("Общие фильтры графиков").classes("text-lg font-semibold")
            with ui.row().classes("w-full gap-4"):
                self.client = ui.select({}, label="Общий выбор клиента").classes("w-72")
                self.product = ui.select({}, label="Общий выбор товара").classes("w-80")
            ui.label(
                "Все аналитические вкладки используют этот выбор. "
                "Период задается отдельно в каждом графике. "
                "В долях показан весь рынок, выбранный клиент отмечен в легенде."
            ).classes("text-sm text-slate-500")
            self.status = ui.label().classes("text-sm text-slate-500")
        region.on_value_change(self.region_changed)
        self.client.on_value_change(self.client_changed)
        self.product.on_value_change(self.product_changed)

    def subscribe(self, callback):
        self.callbacks.append(callback)

    def snapshot(self):
        return (self.revision, self.region.value, self.client.value, self.product.value)

    async def notify(self):
        for callback in self.callbacks:
            await callback()

    def region_changed(self, _):
        if not self.updating:
            return self.reload()

    def client_changed(self, _):
        if self.updating:
            return
        self.revision += 1
        self.update_products()
        return self.notify()

    def product_changed(self, _):
        if self.updating:
            return
        self.revision += 1
        return self.notify()

    def update_products(self):
        products = {r['product_code']: r['product_name'] for r in self.rows
                    if r['company_code'] == self.client.value}
        self.updating = True
        try:
            value = self.product.value if self.product.value in products else next(iter(products), None)
            self.product.set_options(products, value=value)
            self.product.set_enabled(bool(products))
        finally:
            self.updating = False

    async def select_context(self, region_code, client_code, product_code):
        """Validate the exact pair before changing any controls."""
        before = self.snapshot()
        rows = await run.io_bound(load_options, self.connect, region_code)
        if before != self.snapshot() or self.loading:
            raise ValueError("Фильтры изменились. Повторите переход из события.")
        if not any(r['company_code'] == client_code and r['product_code'] == product_code
                   for r in rows):
            raise ValueError("Для выбранной связки больше нет текущих данных.")
        self.revision += 1
        self.updating = True
        try:
            self.region.set_value(region_code)
            self.rows = rows
            clients = {r['company_code']: r['company_name'] for r in rows}
            products = {r['product_code']: r['product_name'] for r in rows
                        if r['company_code'] == client_code}
            self.client.set_options(clients, value=client_code)
            self.product.set_options(products, value=product_code)
            self.client.enable()
            self.product.enable()
            self.status.set_text("")
        finally:
            self.updating = False
        selected = self.snapshot()
        await self.notify()
        if selected != self.snapshot():
            raise ValueError("Выбор изменился во время перехода. Повторите его.")

    async def reload(self):
        self.revision += 1
        request = self.revision
        self.loading = True
        self.client.disable()
        self.product.disable()
        self.status.set_text("Загрузка фильтров…")
        await self.notify()
        try:
            rows = await run.io_bound(load_options, self.connect, self.region.value)
        except Exception:
            logging.exception("Shared filters load failed")
            if request == self.revision:
                self.loading = False
                self.status.set_text("Не удалось загрузить фильтры. Выберите регион повторно.")
            return
        if request != self.revision:
            return
        self.rows = rows
        clients = {r['company_code']: r['company_name'] for r in rows}
        self.updating = True
        try:
            value = self.client.value if self.client.value in clients else next(iter(clients), None)
            self.client.set_options(clients, value=value)
            self.client.set_enabled(bool(clients))
        finally:
            self.updating = False
        self.update_products()
        self.loading = False
        self.status.set_text("" if clients else "Выберите регион с данными на карте или в списке.")
        await self.notify()


async def build_filters(region, connect):
    filters = SharedFilters(region, connect)
    await filters.reload()
    return filters
