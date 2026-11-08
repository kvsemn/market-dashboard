from datetime import timedelta

import pendulum
from airflow.sdk import Connection, dag, task, get_current_context


@dag(
    dag_id="check_database",
    schedule="@hourly",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=30),
        "execution_timeout": timedelta(minutes=2),
    },
    tags=["dashboard", "check"],
)
def check_database():
    @task
    def write_heartbeat():
        import psycopg2

        config = Connection.get("dashboard_db")
        context = get_current_context()

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
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS pipeline_heartbeat (
                            run_id TEXT PRIMARY KEY,
                            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    cur.execute("""
                        INSERT INTO pipeline_heartbeat (run_id)
                        VALUES (%s)
                        ON CONFLICT (run_id) DO NOTHING
                    """, (context["run_id"],))
            print("Запись в PostgreSQL выполнена успешно.")
        finally:
            conn.close()

    write_heartbeat()


check_database()
