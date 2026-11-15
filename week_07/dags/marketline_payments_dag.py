import json
import csv
import sys
from datetime import date, datetime, timedelta, time
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pendulum
import psycopg

from airflow.sdk import dag, task, get_current_context
from airflow.timetables.interval import CronDataIntervalTimetable
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk.exceptions import AirflowFailException
from psycopg.types.json import Jsonb

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from load_payments import (
    RULE_VERSION,
    validate_manifest,
    payments_raw,
    stage_payments,
    prepare_payments,
    check_quality,
    quality_gate,
    publish_payments,
    save_event,
)

CONN_ID = "marketline_pg"
DATA_DIR = PROJECT_DIR / "DE_Week_7_Data"

def connect_db():
    settings = PostgresHook(postgres_conn_id=CONN_ID).get_connection(CONN_ID)
    conn = psycopg.connect(
        host=settings.host,
        port=settings.port or 5432,
        dbname=settings.schema,
        user=settings.login,
        password=settings.password,
        autocommit=True,
    )
    conn.execute("SET TIME ZONE 'UTC'")
    return conn

@task(task_id="receive_files", retries=2, retry_delay=timedelta(seconds=30), execution_timeout=timedelta(minutes=5))
def receive_files_task():
    context = get_current_context()
    dag_run = context["dag_run"]
    conf = dag_run.conf or {}
    run_type = getattr(dag_run.run_type, "value", str(dag_run.run_type))

    if run_type == "manual":
        if not conf.get("report_date") or not conf.get("source_dir"):
            raise AirflowFailException("Для ручного запуска нужны report_date и source_dir")
        try:
            report_date = date.fromisoformat(conf["report_date"])
        except ValueError as error:
            raise AirflowFailException("Неверный формат report_date") from error
        source_dir = Path(conf["source_dir"])
    else:
        report_date = context["data_interval_start"].in_timezone("Europe/Berlin").date()
        source_dir = DATA_DIR / "daily" / report_date.isoformat()

    manifest_path = source_dir / "manifest.json"
    period_start = datetime.combine(report_date, time.min, ZoneInfo("Europe/Berlin"))
    period_end = datetime.combine(report_date + timedelta(days=1), time.min, ZoneInfo("Europe/Berlin"))
    attempt = context["ti"].try_number

    with connect_db() as conn:
        saved = conn.execute("SELECT load_id,report_date,source_path FROM de7_etl.load_runs WHERE run_id=%s", (dag_run.run_id,),).fetchone()
        if saved:
            load_id = saved[0]
            if saved[1] != report_date or Path(saved[2]) != source_dir:
                raise AirflowFailException("У данного DAG Run уже зарегистрированы другие дата или каталог")
        else:
            load_id = uuid4()
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO de7_etl.load_runs
                       (load_id,run_id,report_date,source_path,period_start,period_end)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (load_id, dag_run.run_id, report_date, str(source_dir), period_start, period_end),
                )

        if not manifest_path.is_file():
            save_event(conn, load_id, "receive_files", attempt, "FAILED", reason="Manifest временно недоступен")
            raise FileNotFoundError(f"Manifest не найден: {manifest_path.name}")
        
        try:
            config = validate_manifest(manifest_path)
            manifest_raw = manifest_path.read_text(encoding="utf-8")
        except UnicodeError:
            config = {
                "source_version": None, 
                "source_system": None,
                "manifest_period_start": None, 
                "manifest_period_end": None,
                "complete": None, 
                "covered_until": None,
                "expected_row_count": None, 
                "expected_sums": None,
                "manifest_errors": ["manifest: некорректная кодировка UTF-8"],
            }
            manifest_raw = None

        with conn.transaction():
            conn.execute(
                """
                UPDATE de7_etl.load_runs SET
                    source_version=%s,source_system=%s,
                    manifest_period_start=%s,manifest_period_end=%s,
                    complete=%s,covered_until=%s,expected_row_count=%s,
                    expected_sums=%s,manifest_raw=%s,manifest_errors=%s
                WHERE load_id=%s
                """,
                (config["source_version"], config["source_system"],
                 config["manifest_period_start"], config["manifest_period_end"],
                 config["complete"], config["covered_until"],
                 config["expected_row_count"],
                 Jsonb(config["expected_sums"]) if config["expected_sums"] is not None else None,
                 manifest_raw, config["manifest_errors"], load_id),
            )

        if config["manifest_errors"]:
            errors = config["manifest_errors"]
            technical = any("некорректный JSON" in error or "UTF-8" in error for error in errors)
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO de7_etl.dq_results
                        (rule_name,rule_version,load_id,source_version,run_id,task_id,
                        attempt,period_start,period_end,expected,actual,unit,severity,status,detail)
                    SELECT 'manifest_structure',%s,load_id,source_version,run_id,
                        'check_totals',%s,period_start,period_end,'[]'::jsonb,%s::jsonb,
                        'fields','CRITICAL',%s,'Manifest не прошёл проверку'
                    FROM de7_etl.load_runs WHERE load_id=%s
                    ON CONFLICT DO NOTHING
                    """,
                    (RULE_VERSION, attempt, json.dumps(errors, ensure_ascii=False), "ERROR" if technical else "FAIL", load_id),
                )
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE de7_etl.load_runs
                    SET state='failed',last_error=%s,finished_at=clock_timestamp()
                    WHERE load_id=%s
                    """,
                    ("Некорректный manifest", load_id),
                )
            save_event(conn, load_id, "receive_files", attempt, "FAILED", reason="Некорректный manifest")
            raise AirflowFailException(f"Manifest отклонён; load_id={load_id}")

        save_event(conn, load_id, "receive_files", attempt, "SUCCESS")

    return {"load_id": str(load_id), "source_dir": str(source_dir)}

@task(task_id="stage_payments", retries=2, retry_delay=timedelta(seconds=30), execution_timeout=timedelta(minutes=5))
def stage_payments_task(received):
    context = get_current_context()
    conf = context["dag_run"].conf or {}
    attempt = context["ti"].try_number
    fault = conf.get("test_failure")
    if fault == "file_always" or (fault == "file_once" and attempt == 1):
        with connect_db() as conn:
            save_event(conn, received["load_id"], "stage_payments", attempt, "FAILED", reason="Тест недоступного CSV")
        raise FileNotFoundError("Тест временной/постоянной недоступности CSV")

    manifest_path = Path(received["source_dir"]) / "manifest.json"
    try:
        records, file_hash = payments_raw(manifest_path)
    except FileNotFoundError:
        raise
    except (ValueError, UnicodeError, csv.Error) as error:
        raise AirflowFailException("Ошибка формата или кодировки CSV") from error
    with connect_db() as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    count = stage_payments(cur, UUID(received["load_id"]), records, file_hash)
        except ValueError as error:
            raise AirflowFailException("Нарушение состояния staging") from error
        except psycopg.ProgrammingError as error:
            raise AirflowFailException("Ошибка SQL в staging") from error

        if fault == "after_staging_once" and attempt == 1:
            save_event(conn, received["load_id"], "stage_payments", attempt, "FAILED", counters={"staged_rows": count}, reason="Тестовый сбой после COMMIT staging")
            raise RuntimeError("Тестовый сбой после COMMIT staging")
        save_event(conn, received["load_id"], "stage_payments", attempt, "SUCCESS", counters={"staged_rows": count})
    return {"load_id": received["load_id"]}


@task(task_id="prepare_payments", execution_timeout=timedelta(minutes=5))
def prepare_payments_task(staged):
    attempt = get_current_context()["ti"].try_number
    with connect_db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                count = prepare_payments(cur, staged["load_id"])
        save_event(conn, staged["load_id"], "prepare_payments", attempt, "SUCCESS", counters={"prepared_rows": count})
    return staged


@task(task_id="check_rows", execution_timeout=timedelta(minutes=5))
def check_rows_task(prepared):
    attempt = get_current_context()["ti"].try_number
    with connect_db() as conn:
        return check_quality(conn, prepared["load_id"], "rows", attempt)


@task(task_id="check_totals", execution_timeout=timedelta(minutes=5))
def check_totals_task(prepared):
    attempt = get_current_context()["ti"].try_number
    with connect_db() as conn:
        return check_quality(conn, prepared["load_id"], "totals", attempt)


@task(task_id="quality_gate", retries=0, execution_timeout=timedelta(minutes=5))
def quality_gate_task(prepared, rows_result, totals_result):
    context = get_current_context()
    attempt = context["ti"].try_number
    conf = context["dag_run"].conf or {}
    test_failure = conf.get("test_failure")
    
    with connect_db() as conn:
        try:
            if test_failure == "missing_result":
                rows_result = dict(rows_result)
                rows_result["result_ids"] = rows_result["result_ids"][:-1]
            elif test_failure == "foreign_load_result":
                own_result_id = rows_result["result_ids"][0]
                foreign = conn.execute(
                    """
                    SELECT result_id
                    FROM de7_etl.dq_results
                    WHERE load_id <> %s
                      AND task_id = 'check_rows'
                      AND status = 'PASS'
                      AND rule_name = (
                          SELECT rule_name
                          FROM de7_etl.dq_results
                          WHERE result_id = %s
                      )
                    ORDER BY result_id DESC
                    LIMIT 1
                    """,
                    (UUID(prepared["load_id"]), own_result_id)
                ).fetchone()
                if foreign is None:
                    raise ValueError(
                        "Не найден результат другой загрузки для теста"
                    )

                rows_result = dict(rows_result)
                rows_result["result_ids"] = list(rows_result["result_ids"])
                rows_result["result_ids"][0] = foreign[0]
                
            approved = quality_gate(conn, prepared["load_id"], rows_result, totals_result)
        except Exception as error:
            save_event(conn, prepared["load_id"], "quality_gate", attempt, "FAILED", reason=str(error)[:2000])
            raise AirflowFailException(f"Публикация заблокирована; load_id={prepared['load_id']}") from error
        save_event(conn, prepared["load_id"], "quality_gate", attempt, "WARN" if approved["warnings"] else "SUCCESS",
                   counters={"warnings": approved["warnings"]})
        return approved


@task(task_id="publish_payments", retries=0,execution_timeout=timedelta(minutes=5))
def publish_payments_task(approved):
    context = get_current_context()
    attempt = context["ti"].try_number
    conf = context["dag_run"].conf or {}
    with connect_db() as conn:
        try:
            published = publish_payments(
                conn, approved["load_id"],
                fail_inside_publication=(conf.get("test_failure") == "inside_publication"),
            )
        except Exception as error:
            save_event(conn, approved["load_id"], "publish_payments", attempt, "FAILED", 
                       reason=f"{type(error).__name__}: откат публикации")
            raise
        save_event(conn, approved["load_id"], "publish_payments", attempt, "SUCCESS", counters={"published_groups": published})
    return {"load_id": approved["load_id"], "published_groups": published}

@dag(
    dag_id="marketline_payments",
    start_date=pendulum.datetime(2026, 10, 24, tz="Europe/Berlin"),
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="Europe/Berlin"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=4,
    tags=["week_07", "data_quality", "payments"],
)
def marketline_payments_dag():
    received = receive_files_task()
    staged = stage_payments_task(received)
    prepared = prepare_payments_task(staged)
    rows_result = check_rows_task(prepared)
    totals_result = check_totals_task(prepared)
    approved = quality_gate_task(prepared, rows_result, totals_result)
    publish_payments_task(approved)


marketline_payments_dag()