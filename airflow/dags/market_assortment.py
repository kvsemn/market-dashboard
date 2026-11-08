from pathlib import Path


def generate_assortment():
    import psycopg2
    from airflow.sdk import Connection
    cfg = Connection.get('dashboard_db')
    conn = psycopg2.connect(host=cfg.host,port=cfg.port or 5432,dbname=cfg.schema,
                           user=cfg.login,password=cfg.password,connect_timeout=10)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(Path(__file__).with_name('assortment.sql').read_text())
                cur.execute('SELECT max(period_at),count(*) FROM market.assortment_runs')
                print('Assortment snapshot / completed periods:',cur.fetchone())
    finally:
        conn.close()
