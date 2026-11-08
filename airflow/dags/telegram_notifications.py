"""Single-recipient Telegram outbox. Never retry ambiguous delivery automatically."""
import json
from html import escape
from datetime import timezone
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DeliveryUnknown(RuntimeError):
    pass


class DeliveryRejected(RuntimeError):
    def __init__(self, code, retry_after=60):
        self.code = str(code) if isinstance(code, int) else 'rejected'
        self.retry_after = max(60, int(retry_after))
        super().__init__('Telegram rejected request: ' + self.code)


def send_message(token, chat_id, text):
    request = Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=urlencode({
            'chat_id': chat_id, 'text': text,
            # Old queued plain-text messages retain their original mode.
            **({'parse_mode': 'HTML'} if text.startswith(HTML_HEADER) else {}),
        }).encode(),
        method='POST',
    )
    try:
        try:
            with urlopen(request, timeout=25) as response:
                data = json.load(response)
        except HTTPError as error:
            data = json.loads(error.read())
        if data.get('ok') is False:
            raise DeliveryRejected(
                data.get('error_code'),
                data.get('parameters', {}).get('retry_after', 60),
            )
        if data.get('ok') is not True:
            raise ValueError('Unrecognized response')
        return int(data['result']['message_id'])
    except DeliveryRejected:
        raise
    except Exception:
        # Never include urllib exceptions: their URLs contain the token.
        raise DeliveryUnknown('Delivery result unknown; manual review required') from None


# TELEGRAM_HTML_V1
HTML_HEADER = '🚨 <b>Критические события рынка</b>'


def build_message(rows):
    if not rows:
        raise ValueError('Нельзя сформировать пустую сводку')
    types = {
        'price_decrease': '📉 Снижение цены',
        'price_increase': '📈 Повышение цены',
        'margin_below_plan': '💸 Маржа ниже плана',
        'market_share_change': '📊 Изменение расчетной доли',
        'price_position_change': '⚖️ Изменение ценовой позиции',
        'new_competitor': '🆕 Новый конкурент',
        'notification_test': '🧪 Тест уведомления',
        'assortment_added': '🆕 Появление товара',
        'assortment_removed': '📦 Исчезновение товара',
        'substitute_added': '🔄 Потенциальный заменитель',
    }
    counts = Counter((r[2], r[3]) for r in rows)
    times = [r[1].astimezone(timezone.utc) for r in rows]
    first, last = min(times), max(times)
    period = first.strftime('%d.%m.%Y %H:%M')
    if last != first:
        period += ' — ' + last.strftime('%d.%m.%Y %H:%M')
    lines = [
        HTML_HEADER,
        '🧪 <i>Демонстрационные данные</i>',
        '',
        f'📊 Новых событий: <b>{len(rows)}</b>',
        f'🕒 <b>Период событий:</b> {period} UTC',
    ]
    previous_region = None
    for (region, kind), count in sorted(counts.items()):
        extra = []
        if region != previous_region:
            extra += ['', f'📍 <b>{escape(region[:80])}</b>']
        label = escape(types.get(kind, '⚠️ ' + kind[:50]))
        extra.append(f'{label} — <b>{count}</b>')
        # Bound the complete HTML source in UTF-16 units, without cutting tags.
        candidate = '\n'.join(lines + extra)
        if len(candidate.encode('utf-16-le')) // 2 > 3500:
            lines += ['', '➕ Остальные группы — в дашборде.']
            break
        lines += extra
        previous_region = region
    lines += [
        '',
        '🔎 Подробности и статусы — в разделе <b>«События рынка»</b>.',
        '<i>Синтетические данные. Не подтвержденные сигналы реального рынка.</i>',
    ]
    return '\n'.join(lines)


def deliver(conn, token, chat_id):
    # Session lock covers DB commits and the external request.
    with conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(710020)')
            if not cur.fetchone()[0]:
                raise RuntimeError('Another Telegram sender is running')
            cur.execute('SELECT count(*) FROM market.telegram_settings')
            if cur.fetchone()[0] != 1:
                raise RuntimeError('Telegram migration has not been initialized')
            cur.execute("""
                UPDATE market.telegram_batches
                SET status='unknown', updated_at=now(), error_code='interrupted'
                WHERE status='sending'
            """)
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM market.telegram_batches WHERE status='unknown' LIMIT 1")
            uncertain = cur.fetchone()
    if uncertain:
        raise RuntimeError(f'Telegram batch {uncertain[0]} requires manual delivery review; sending paused')

    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, message_text, chat_id, retry_at IS NULL OR retry_at <= now()
                FROM market.telegram_batches WHERE status='failed'
                ORDER BY id LIMIT 1 FOR UPDATE
            """)
            batch = cur.fetchone()
            if batch:
                batch_id, message, destination, ready = batch
                if destination != chat_id:
                    raise RuntimeError('Recipient changed; review pending batch before sending')
                if not ready:
                    raise RuntimeError('Telegram retry interval has not elapsed')
                cur.execute("""
                    UPDATE market.telegram_batches
                    SET status='sending', attempts=attempts+1, updated_at=now(),
                        error_code=NULL, retry_at=NULL WHERE id=%s
                """, (batch_id,))
            else:
                cur.execute("""
                    SELECT e.id, e.observed_at, r.name, e.event_type
                    FROM market.events e JOIN market.regions r ON r.code=e.region_code
                    WHERE e.severity='critical'
                      AND NOT EXISTS (
                        SELECT 1 FROM market.telegram_event_log l WHERE l.event_id=e.id
                      )
                    ORDER BY e.id LIMIT 1000
                """)
                rows = cur.fetchall()
                if not rows:
                    print('Telegram: no new critical events; nothing sent')
                    return
                message = build_message(rows)
                cur.execute("""
                    INSERT INTO market.telegram_batches(status, message_text, chat_id)
                    VALUES ('sending', %s, %s) RETURNING id
                """, (message, chat_id))
                batch_id = cur.fetchone()[0]
                cur.executemany("""
                    INSERT INTO market.telegram_event_log(event_id, batch_id)
                    VALUES (%s, %s)
                """, [(r[0], batch_id) for r in rows])
    # Persisted BEFORE sending. A crash from here requires manual review, not resend.
    try:
        message_id = send_message(token, chat_id, message)
    except DeliveryRejected as error:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE market.telegram_batches
                    SET status='failed', error_code=%s, updated_at=now(),
                        retry_at=now() + %s * interval '1 second' WHERE id=%s
                """, (error.code, error.retry_after, batch_id))
        raise RuntimeError(f'Telegram rejected batch {batch_id}; code {error.code}') from None
    except DeliveryUnknown:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE market.telegram_batches
                    SET status='unknown', error_code='network_or_response', updated_at=now()
                    WHERE id=%s
                """, (batch_id,))
        raise RuntimeError(f'Telegram batch {batch_id}: delivery unknown; manual review required') from None
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE market.telegram_batches
                SET status='sent', telegram_message_id=%s, sent_at=now(), updated_at=now()
                WHERE id=%s
            """, (message_id, batch_id))
    print(f'Telegram: batch {batch_id} sent successfully')


def notify_telegram():
    import psycopg2
    from airflow.sdk import Connection

    token = Path('/run/secrets/telegram_token').read_text().strip()
    chat_id = Path('/run/secrets/telegram_chat_id').read_text().strip()
    if not token or ':' not in token or not chat_id.lstrip('-').isdigit():
        raise RuntimeError('Invalid Telegram secret file')
    config = Connection.get('dashboard_db')
    conn = psycopg2.connect(
        host=config.host, port=config.port or 5432, dbname=config.schema,
        user=config.login, password=config.password, connect_timeout=10,
        options='-c statement_timeout=15000',
    )
    try:
        deliver(conn, token, chat_id)
    finally:
        conn.close()  # Releases session advisory lock, including on failure.
