import argparse
import hashlib
import json
import logging
import os
import psycopg
import re
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from psycopg.types.json import Jsonb
from uuid import uuid4
from collections import Counter


#########
#Параметры
#########
base_dir = Path(__file__).resolve().parent.parent 
data_dir = base_dir / "data"

logger = logging.getLogger(__name__)
######
#Настройка логирования
####

def configure_logging():
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=log_dir / "load.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


REQUIRED_FIELDS = [
    'source_system', 
    'order_id', 
    'source_version', 
    'operation',
    'status', 
    'amount', 
    'updated_at', 
    'available_at',
]

DECISIONS = [
    "identical_repeat",         #одинаковый повтор внутри поставки
    "old_version",              #устаревшая версия
    "same_as_target",            #совпадение с сохранённым состоянием
    "updated_in_batch",         #строка более ранней версии, заменённая новой внутри пакета
    "applied_change",           #применённое изменение
]

ACTIONS = [
    "insert_active",            #вставка автивных заказов
    "update_active",            #обновления активных заказов
    "delete_known",             #удаления известных заказов
    "delete_unknown",           #удаление неизвестных ключей
]

#Проверка обязательных полей
def validate_fields(obj, fieldname, fieldtype):
    if not isinstance(obj, dict):
        raise ValueError("Ожидался объект")
    
    if fieldname not in obj:
        raise ValueError(f"Отсутствует поле: {fieldname}")
    
    value = obj[fieldname]
    if value is None:
        raise ValueError(f"Поле не может быть NULL: {fieldname}")
    
    if not isinstance(value, fieldtype): 
        raise ValueError(f"{fieldname}: ожидался тип {fieldtype.__name__}")
    
    return value

#Приведение данных
#Строка
def required_text(value, field): 
    if not isinstance(value, str): 
        raise ValueError(f"{field}: ожидалась строка") 
    
    result = value.strip() 
    if result == "": 
        raise ValueError(f"{field}: пустое значение") 
    return value


#Преобразование суммы в Decimal
def parse_amount(value, field): 
    if not isinstance(value, str): 
        raise ValueError( 
            f"{field}: ожидалась строка" 
        ) 
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value) is None: 
        raise ValueError(f"{field}: неверный формат") 
    amount = Decimal(value) 
    if not Decimal("0") < amount <= Decimal("9999999999.99"): 
        raise ValueError(f"{field}: вне допустимого диапазона") 
    return amount

#Целые числа (bigint)
def positive_int(value, field):
    if type(value) is not int: 
        raise ValueError(f"{field}: ожидался тип int")
    if value > 9223372036854775807: 
        raise ValueError(f"{field}: превышен диапазон") 
    if value <= 0: 
        raise ValueError(f"{field}: сумма меньше либо равна нулю") 
    return value


def parse_datetime(value, field):
    #дата источника - 29 сентября 2026 года, часовой пояс UTC
    required_text(value, field)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field}: некорректная дата") from error
    offset = result.utcoffset()
    if offset is None or offset.total_seconds() != 0: 
        raise ValueError(f"{field}: отсутствует явно указанный часовой пояс UTC")
    result = result.astimezone(timezone.utc)
    if result.date() != date(2026, 9, 29):
        raise ValueError(f"{field}: некорректная дата")
    return result

#манифест
def read_manifest(path):
    with path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    required_text(validate_fields(manifest, "batch_id", str), "batch_id")
    if validate_fields(manifest, "mode", str) not in ("full", "delta", "replay"):
        raise ValueError("В манифесте указан некорректный режим загрузки")
    
    return manifest


def validate_manifest(manifest, replay=False):
    if manifest.get("dataset") != "orders":
        raise ValueError('Нужен dataset=orders')

    manifest_mode = manifest.get("mode")
    mode = "replay" if replay else manifest_mode
    scope = manifest.get("scope")
    if not isinstance(scope, list) or sorted(scope) != ["pos", "shop"]:
        raise ValueError("Некорректный scope")
    
    if manifest.get("complete") is not True:
        raise ValueError("Снимок неполный; не допущен к загрузке")

    record_count = manifest.get("record_count")
    if type(record_count) is not int or record_count < 0:
        raise ValueError("record_count должен быть неотрицательным целым числом")

    digest = manifest.get("sha256")
    if not isinstance(digest, str):
        raise ValueError('Некорректный SHA-256 в manifest')
    required_text(manifest.get("data_file"), "data_file")

    config = {
        "mode": mode, 
        "manifest_mode": manifest_mode,
        "scope": scope,
        "expected_count": record_count,
        "expected_hash": digest.lower(),
        "snapshot": None,
        "lower": None,
        "upper": None,
        "as_of": None,
        "read_start": None, #граница чтения
        "overlap_minutes": 0,
    }

    if mode == "replay":
        if "as_of" in manifest:
            config["as_of"] = parse_datetime(manifest["as_of"], "as_of")
        return config

    upper = parse_datetime(manifest.get("upper_bound"), "upper_bound")
    as_of = parse_datetime(manifest.get("as_of"), "as_of")
    if as_of < upper:
        raise ValueError("as_of не может быть раньше upper_bound")
    config["upper"] = upper
    config["as_of"] = as_of

    if mode == "full":
        snapshot = parse_datetime(manifest.get("snapshot_at"), "snapshot_at")
        if snapshot != upper:
            raise ValueError("snapshot не совпадает с upper_bound")
        config["snapshot"] = snapshot
    else:
        lower = parse_datetime(manifest.get("lower_bound"), "lower_bound")
        if lower >= upper:
            raise ValueError("lower_bound >= upper_bound")
        config["lower"] = lower
    
    return config


def orders_raw(manifest_path, manifest):
    data_file = manifest["data_file"]
    source_path = manifest_path.parent / data_file

    raw_bytes = source_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    records = []
    for line_number, line in enumerate(raw_bytes.decode("utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Строка {line_number}: некорректный JSOON") from error
        if not isinstance(record, dict):
            raise ValueError(f"Строка {line_number}: ожидался JSON-объект")
        records.append(record)
    return records, file_hash


def check_boundaries(cur, config, overlap_minutes):
    cur.execute("SELECT watermark FROM de6_etl.pipeline_state WHERE pipeline_name='orders'")
    state = cur.fetchone()
    watermark = None if state is None else state[0]

    mode = config["mode"]
    if mode == "full":
        if watermark is not None and config["upper"] < watermark:
            raise ValueError("Старый full после более свежих изменений")
    elif mode == "delta":
        if watermark is None:
            raise ValueError("Нужна full-загрузка до дельты")
        if config["lower"] != watermark:
            raise ValueError("lower_bound манифеста не совпадает с pipeline_state.watermark")
        if config["upper"] <= watermark:
            raise ValueError("upper_bound до watermark")
        config["overlap_minutes"] = overlap_minutes
        config["read_start"] = watermark - timedelta(minutes=overlap_minutes)
    else:
        if watermark is None:
            raise ValueError("Не было full загрузки до replay")
        
    return watermark
        

def validate_order(raw, config):
    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise ValueError(f"Отсутствует поле {field}")

    source_system = required_text(raw["source_system"], "source_system")
    if source_system not in config["scope"]:
        raise ValueError(f"Система {source_system} не входит в scope")

    required_text(raw["order_id"], "order_id")
    positive_int(raw["source_version"], "source_version")
    operation = raw["operation"]
    if operation == "upsert":
        if raw["status"] not in ("new", "paid", "cancelled"):
            raise ValueError("Для upsert допустимы new, paid, cancelled")
        parse_amount(raw["amount"], "amount")
    elif operation == "delete":
        if raw["status"] is not None or raw["amount"] is not None:
            raise ValueError("Для delete status и amount должны быть null")
    else:
        raise ValueError("Некорректная операция")

    if config["mode"] == "full" and operation != "upsert":
        raise ValueError("full снимок содержит удаленные заказы")

    updated = parse_datetime(raw["updated_at"], "updated_at")
    available = parse_datetime(raw["available_at"], "available_at")
    if config["as_of"] is not None and available > config["as_of"]:
        raise ValueError("available_at позже as_of")
    if config["upper"] is not None and updated > config["upper"]:
        raise ValueError("updated_at позже upper_bound")
    if config["mode"] == "delta":
        if config["overlap_minutes"] == 0:
            if updated <= config["lower"]:
                raise ValueError("updated_at не попадает в период (watermark, upper_bound]")
        elif updated < config["read_start"]:
            raise ValueError("updated_at раньше времени начала чтения с учетом перекрытия")


def stage_orders(cur, run_id, batch_id, records):
    rows = []
    for row_no, record in enumerate(records, 1):
        values = []
        for field in REQUIRED_FIELDS:
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                value = str(value)
            values.append(value)
        rows.append((run_id, batch_id, row_no, *values))

    if rows:
        cur.executemany(
            """
            INSERT INTO de6_stg.orders_raw (
                run_id, 
                batch_id, 
                row_no,
                source_system, 
                order_id, 
                source_version,
                operation, 
                status, 
                amount, 
                updated_at, 
                available_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, rows, 
        )
    cur.execute("SELECT count(*) FROM de6_stg.orders_raw WHERE run_id=%s", (run_id,))
    staged = cur.fetchone()[0]
    if staged != len(records):
        raise RuntimeError("Не все записи сохранены в orders_raw")
    cur.execute("UPDATE de6_etl.load_runs SET staged_rows=%s WHERE run_id=%s", 
                (staged, run_id))
    
    return staged

def prepare_orders(cur, run_id, mode):
    cur.execute(
        """
        SELECT source_system, order_id, source_version::bigint
        FROM de6_stg.orders_raw
        WHERE run_id=%s
        GROUP BY source_system, order_id, source_version::bigint
        HAVING count(DISTINCT (operation, status, amount::numeric)) > 1
        LIMIT 1
        """, (run_id,),
    )
    conflict = cur.fetchone()
    if conflict is not None:
        raise ValueError(f"Конфликт версий внутри поставки: {conflict}")

    if mode != "full":
        cur.execute(
            """
            SELECT r.source_system, r.order_id, r.source_version
            FROM de6_stg.orders_raw AS r
            JOIN de6_core.orders_current AS c
              ON c.source_system=r.source_system 
             AND c.order_id=r.order_id
             AND c.source_version=r.source_version::bigint
            WHERE r.run_id=%s
              AND ROW(r.operation='delete', r.status, r.amount::numeric)
                     IS DISTINCT FROM ROW(c.is_deleted, c.status, c.amount)
            LIMIT 1
            """,
            (run_id,),
        )
        conflict = cur.fetchone()
        if conflict is not None:
            raise ValueError(f"Конфликт равной версии с целевой таблицей: {conflict}")

    cur.execute(
        """
        INSERT INTO de6_stg.i_orders (
               run_id, 
               batch_id, 
               source_system, 
               order_id, 
               source_version, 
               operation, 
               status, 
               amount, 
               updated_at, 
               available_at
           )
           SELECT DISTINCT ON (source_system, order_id)
               run_id, batch_id, source_system, order_id, source_version::bigint,
               operation, status, amount::numeric(12,2),
               updated_at::timestamptz, available_at::timestamptz
           FROM de6_stg.orders_raw
           WHERE run_id=%s
           ORDER BY source_system, order_id, source_version::bigint DESC, row_no
        """,
        (run_id,),
    )
    prepared = cur.rowcount
    cur.execute("UPDATE de6_etl.load_runs SET prepared_rows=%s WHERE run_id=%s", (prepared, run_id))

    return prepared


def classify_orders(cur, run_id, config):
    cur.execute(
        """
        SELECT 
            id, 
            row_no, 
            source_system, 
            order_id, 
            source_version::bigint, 
            operation, 
            status, 
            amount::numeric
        FROM de6_stg.orders_raw
        WHERE run_id=%s 
        ORDER BY row_no
        """, (run_id,),
    )
    rows = cur.fetchall()
    cur.execute(
        """
        SELECT source_system, order_id, source_version, is_deleted
        FROM de6_core.orders_current
        WHERE source_system=ANY(%s)
        """, (config["scope"],),
    )
    target = {}
    #пишем версии в слловарь
    for system, order_id, version, is_deleted in cur.fetchall():
        target[(system, order_id)] = (version, is_deleted)

    first_version = set()
    duplicates = set()
    highest = {}

    for row in rows:
        row_id, row_no, system, order_id, version = row[:5]
        key = (system, order_id)
        version_key = (system, order_id, version)
        if version_key in first_version:
            duplicates.add(row_id)
        else:
            first_version.add(version_key)
        if key not in highest or version > highest[key]:
            highest[key] = version

    decisions = []
    #чтобы вести счетчики
    decision_counts = Counter()
    action_counts = Counter()
    for row in rows:
        row_id, row_no, system, order_id, version, operation, status, amount = row 
        key = (system, order_id)
        saved = target.get(key)
        action = None
        if row_id in duplicates:
            decision = "identical_repeat"
        elif version < highest[key]:
            decision = "updated_in_batch"
        elif config["mode"] != "full" and saved is not None and version < saved[0]:
            decision = "old_version"
        elif config["mode"] != "full" and saved is not None and version == saved[0]:
            decision = "same_as_target"
        else:
            decision = "applied_change"
            if config["mode"] != "full":
                if operation == "delete":
                    action = "delete_unknown" if saved is None else "delete_known"
                else:
                    action = "insert_active" if saved is None else "update_active"

        decisions.append((decision, action, row_id))
        decision_counts[decision] += 1
        if action is not None:
            action_counts[action] += 1

    if config["mode"] == "replay" and decision_counts["applied_change"]:
        raise ValueError("replay уже опубликованного пакета не должен менять данные")

    if decisions:
        cur.executemany("UPDATE de6_stg.orders_raw SET decision=%s, applied_action=%s WHERE id=%s",
                decisions,
        )
    return decision_counts, action_counts

def publish_orders(cur, run_id, config, expected_applied, fail_inside_publication=False): #, decision_counts=None, action_counts=None):
    mode = config["mode"]
    scope = config["scope"]

    if mode == "full":
        cur.execute("DELETE FROM de6_core.orders_current WHERE source_system=ANY(%s)", (scope,))
        cur.execute(
            """
            INSERT INTO de6_core.orders_current (
                source_system, 
                order_id, 
                source_version, 
                status, 
                amount,
                is_deleted, 
                t_updated_at
            ) 
            SELECT 
                source_system, 
                order_id, 
                source_version, 
                status, 
                amount,
                false,
                clock_timestamp()
            FROM de6_stg.i_orders WHERE run_id=%s
            """,
            (run_id,),
        )
    else:
        extra_where = ""
        params = (run_id,)

        if fail_inside_publication:
            cur.execute(
                """
                SELECT source_system, order_id
                FROM de6_stg.orders_raw
                WHERE run_id=%s AND decision='applied_change'
                ORDER BY row_no
                LIMIT 1
                """, (run_id,),
            )
            first_key = cur.fetchone()
            if first_key is None:
                raise RuntimeError("Нет применяемого изменения для тестового сбоя")
            extra_where = " AND source_system=%s AND order_id=%s"
            params = (run_id, first_key[0], first_key[1])

        cur.execute(
            """
            INSERT INTO de6_core.orders_current as c (
                source_system, 
                order_id, 
                source_version, 
                status, 
                amount,
                is_deleted, 
                t_updated_at
            ) 
            SELECT 
                source_system, 
                order_id, 
                source_version, 
                status, 
                amount,
                operation='delete',
                clock_timestamp()
            FROM de6_stg.i_orders WHERE run_id=%s
            """ + extra_where + """
            ON CONFLICT (source_system, order_id) DO UPDATE SET
                source_version = EXCLUDED.source_version,
                status = EXCLUDED.status,
                amount = EXCLUDED.amount,
                is_deleted = EXCLUDED.is_deleted,
                t_updated_at = EXCLUDED.t_updated_at
            WHERE EXCLUDED.source_version > c.source_version
            """,
            params,
        )
    applied = cur.rowcount
    if fail_inside_publication:
        if applied != 1:
            raise RuntimeError("Тестовый сбой: первое изменение не применилось")
        raise RuntimeError("Тестовый сбой после первого изменения orders_current")
    
    if applied != expected_applied:
        raise RuntimeError(f"Применено {applied} строк вместо ожидаемых {expected_applied}")

    return applied

#вынесла проверки бизнес-резуьтата
def check_business_state(cur, run_id, config, outside_before, inside_before):
    #системы, которые в скоупе
    cur.execute(
        """
        SELECT source_system, order_id, source_version, status, amount, is_deleted, t_updated_at
        FROM de6_core.orders_current
        WHERE source_system=ANY(%s)
        ORDER BY source_system, order_id
        """,
        (config["scope"],),
    )
    current_full = cur.fetchall()
    current = {row[:2]: row for row in current_full}
    previous = {row[:2]: row for row in inside_before}

    cur.execute(
        """
        SELECT source_system, order_id, source_version, status, amount, operation='delete'
        FROM de6_stg.i_orders 
        WHERE run_id=%s
        ORDER BY source_system, order_id
        """, 
        (run_id,),
    )
    prepared = cur.fetchall()

    if config["mode"] == "full":
        if [row[:6] for row in current_full] != prepared:
            raise RuntimeError("Цель не совпадает с подготовленным полным снимком")
    else:
        incoming = {row[:2]: row for row in prepared}
        #текущий набор не равен предыдущий + дельта
        if set(current) != set(previous) | set(incoming):
            raise RuntimeError("Пропущены/появились новые ключи")
        for key in set(previous) | set(incoming):
            old = previous.get(key)
            new = incoming.get(key)
            saved = current[key]
            if new is not None and (old is None or new[2] > old[2]):
                if saved[:6] != new:
                    raise RuntimeError(f"Неверные бизнес-поля {key}")
            elif saved != old:
                raise RuntimeError(f"Старый/пропущеный заказ изменился {key}")
            
    #системы, которые не в скоупе
    cur.execute(
        """
        SELECT * FROM de6_core.orders_current
        WHERE NOT (source_system=ANY(%s))
        ORDER BY source_system, order_id
        """,
        (config["scope"],),
    )
    if cur.fetchall() != outside_before:
        raise RuntimeError("Изменились данные вне scope")
    
    if config["mode"] == "replay" and current_full != inside_before:
        raise RuntimeError("replay изменил целевую таблицу")
    #неудаленные записи
    active = [row for row in current_full if not row[5]]
    total_amount = sum((row[4] for row in active), Decimal("0.00"))
    
    load_stats = {
        "active_orders": len(active),
        "deleted_orders": len(current_full) - len(active),
        "total_amount": total_amount,
    }
    return load_stats
    


def verify_load(conn_params, run_id, config, outside_before, inside_before, watermark):
    with psycopg.connect(**conn_params, autocommit=True) as conn:
        with conn.cursor() as cur:
            stats = check_business_state(cur, run_id, config, outside_before, inside_before)
            cur.execute("SELECT watermark FROM de6_etl.pipeline_state WHERE pipeline_name='orders'")
            state = cur.fetchone()
            if state is None or state[0] != watermark:
                raise RuntimeError("Некорректный watermark после публикации")
            cur.execute("SELECT status FROM de6_etl.load_runs WHERE run_id=%s", (run_id,))
            status = cur.fetchone()[0]
            if status is None or status != "succeeded":
                raise RuntimeError("Не подтверждена успешная публикация")

    stats["watermark"] = watermark
    return stats

#отпечаток длял контроля
def staging_fingerprint(cur, run_id):
    cur.execute(
        """
        SELECT 
            row_no, 
            batch_id, 
            source_system, 
            order_id, 
            source_version, 
            operation, 
            status, 
            amount, 
            updated_at, 
            available_at
        FROM de6_stg.orders_raw
        WHERE run_id=%s
        ORDER BY row_no
        """,
        (run_id,),
    )
    rows = cur.fetchall()

    orders_raw = json.dumps(rows, ensure_ascii=False)
    digest = hashlib.sha256(orders_raw.encode("utf-8")).hexdigest()
    return digest, rows


def run_load(manifest_path, conn_params, replay=False, overlap_minutes=0, 
             fail_after_staging=False, fail_inside_publication=False, resume_run=None):
    run_id = uuid4()
    if overlap_minutes < 0:
        raise ValueError("Отрицательный overlap")

    with psycopg.connect(**conn_params, autocommit=True) as conn:
        if resume_run is not None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT batch_id, manifest, status, staging_ready, lower_bound, read_start, staging_hash
                    FROM de6_etl.load_runs WHERE run_id=%s
                    """,
                    (resume_run,),
                )
                source = cur.fetchone()
            if source is None or source[2] != "failed" or not source[3] or not source[6]:
                raise ValueError("Нужна проваленная попытка запуска с staging_ready = true и хэшем")
            manifest = source[1]

            if source[4] is not None and source[5] is not None:
                saved_overlap = int((source[4] - source[5]).total_seconds() / 60)
                if overlap_minutes not in (0, saved_overlap):
                    raise ValueError("Перекрытие не совпадает с сохраненной попыткой")
                overlap_minutes = saved_overlap
        else:
            manifest_path = Path(manifest_path).resolve()
            manifest = read_manifest(manifest_path)

        effective_mode = "replay" if replay else manifest["mode"]
        stats = {"mode": effective_mode}
        stage = "started"

        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO de6_etl.load_runs (run_id, batch_id, mode, manifest, status, stage, source_run_id)
                VALUES (%s, %s, %s, %s, 'running', 'started', %s)
                """,
                (run_id, manifest["batch_id"], effective_mode, Jsonb(manifest), resume_run),
            )
        logger.info("event=load_started run_id=%s batch_id=%s mode=%s", 
                    run_id, manifest["batch_id"], effective_mode)

        try:
            config = validate_manifest(manifest, replay)

            if overlap_minutes and config["mode"] != "delta":
                raise ValueError("Перекрытие допустимо только для режима delta")

            stage = "boundaries"
            with conn.transaction(), conn.cursor() as cur:
                watermark_before = check_boundaries(cur, config, overlap_minutes)
                cur.execute(
                    """
                    UPDATE de6_etl.load_runs
                    SET stage='boundaries_checked', scope=%s, lower_bound=%s,
                        upper_bound=%s, as_of=%s, read_start=%s, watermark_before=%s
                    WHERE run_id=%s
                    """,
                    (Jsonb(config["scope"]), config["lower"], config["upper"], 
                     config["as_of"], config["read_start"], watermark_before, run_id),
                )
            logger.info("event=source_window run_id=%s batch_id=%s lower=%s read_start=%s upper=%s",
                        run_id, manifest["batch_id"], config["lower"], config["read_start"], config["upper"])

            stage = "staging"
            if resume_run is None:
                records, file_hash = orders_raw(manifest_path, manifest)
                stats["received"] = len(records)

                with conn.transaction(), conn.cursor() as cur:
                    stats["staged_rows"] = stage_orders(cur, run_id, manifest["batch_id"], records)
                    cur.execute(
                        "UPDATE de6_etl.load_runs SET stage='staged' WHERE run_id=%s", (run_id,),
                    )

                stage = "validation"
                if config["expected_hash"] != file_hash:
                    raise ValueError("Хэщ файла не совпадает с manifest")
                if config["expected_count"] != len(records):
                    raise ValueError("Не совпадает ожидаемое количество записей")

                for line_number, record in enumerate(records, 1):
                    try:
                        validate_order(record, config)
                    except ValueError as error:
                        raise ValueError(f"Строка {line_number}: {error}") from error
            else:
                stage = "resume_validation"
                with conn.transaction(), conn.cursor() as cur:
                    original_hash, original_rows = staging_fingerprint(cur, resume_run)
                    if original_hash != source[6] or len(original_rows) != config["expected_count"]:
                        raise ValueError("Сохраненный staging изменился")
                    
                    cur.execute(
                        """
                        INSERT INTO de6_stg.orders_raw(
                            run_id, batch_id, row_no, source_system, order_id, source_version, 
                            operation, status, amount, updated_at, available_at
                        )
                        SELECT %s, batch_id, row_no, source_system, order_id, source_version, 
                            operation, status, amount, updated_at, available_at
                        FROM de6_stg.orders_raw
                        WHERE run_id=%s 
                        ORDER BY row_no
                        """, (run_id, resume_run),
                    )
                    staged = cur.rowcount
                    copied_hash, copied_rows = staging_fingerprint(cur, run_id)
                    if copied_hash != original_hash or staged != config["expected_count"] or staged != len(copied_rows):
                        raise ValueError("Восстановленный stage неполный")
                    stats["received"] = staged
                    stats["staged_rows"] = staged
                    cur.execute(
                        """
                        UPDATE de6_etl.load_runs
                        SET staged_rows=%s, stage='staged'
                        WHERE run_id=%s
                        """, (staged, run_id),
                    )


                for row in copied_rows:
                    row_no, batch_id, *fields = row
                    if batch_id != manifest["batch_id"]:
                        raise ValueError(f"{row_no}: изменился batch_id")
                    
                    record = {}
                    for i, field in enumerate(REQUIRED_FIELDS):
                        record[field] = fields[i]

                    version = record["source_version"]
                    record["source_version"] = int(version)

                    try:
                        validate_order(record, config)
                    except ValueError as error:
                        raise ValueError(f"Восстановленная строка {row_no}: {error}") from error
                    
            stage = "staging_ready"

            with conn.transaction(), conn.cursor() as cur:
                digest, staged_rows = staging_fingerprint(cur, run_id)
                if len(staged_rows) != stats["staged_rows"]:
                    raise ValueError("Количество строк staging изменилось")
                cur.execute(
                    """
                    UPDATE de6_etl.load_runs
                    SET staging_ready=true, staging_hash=%s, stage='staging_ready'
                    WHERE run_id=%s
                    """, (digest, run_id),
                )
            logger.info("event=staging_ready run_id=%s batch_id=%s rws=%s", run_id, manifest["batch_id"], stats["staged_rows"])

            if fail_after_staging:
                raise RuntimeError("Тестовый сбой после подтверждения staging")

            stage = "publishing"
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE de6_etl.load_runs
                    SET stage='publishing'
                    WHERE run_id=%s
                    """, (run_id,),
                )

            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM de6_core.orders_current
                    WHERE NOT (source_system=ANY(%s)) 
                    ORDER BY source_system, order_id
                    """,
                    (config["scope"],),
                )
                outside_before = cur.fetchall()

                cur.execute(
                    """
                    SELECT *
                    FROM de6_core.orders_current
                    WHERE source_system=ANY(%s) 
                    ORDER BY source_system, order_id
                    """,
                    (config["scope"],),
                )
                inside_before = cur.fetchall()

                stats["prepared_rows"] = prepare_orders(cur, run_id, config["mode"])
                decision_counts, action_counts = classify_orders(cur, run_id, config)
                if sum(decision_counts.values()) != stats["staged_rows"]:
                    raise RuntimeError("Не по всем строкам есть решение")
                
                expected_applied = decision_counts["applied_change"]
                if config["mode"] != "full" and sum(action_counts.values()) != expected_applied:
                    raise RuntimeError("Не всем изменениям назначено действие")

                stats["applied_rows"] = publish_orders(cur, run_id, config, expected_applied,
                                                                  fail_inside_publication=fail_inside_publication, 
                                                                  )
                checked = check_business_state(cur, run_id, config, outside_before, inside_before)
                logger.info("event=precommit_verified run_id=%s batch_id=%s active=%s total=%s",
                            run_id, manifest["batch_id"], checked["active_orders"], checked["total_amount"])

                cur.execute("SELECT watermark FROM de6_etl.pipeline_state WHERE pipeline_name='orders'")
                state = cur.fetchone()
                watermark = None if state is None else state[0]
                if config["mode"] in ("full", "delta"):
                    watermark = config["upper"]
                    cur.execute(
                        """
                        INSERT INTO de6_etl.pipeline_state (pipeline_name, watermark, run_id, updated_at)
                        VALUES ('orders', %s, %s, clock_timestamp())
                        ON CONFLICT (pipeline_name) DO UPDATE SET
                            watermark = EXCLUDED.watermark,
                            run_id = EXCLUDED.run_id,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (watermark, run_id),
                    )

                cur.execute(
                    """
                    UPDATE de6_etl.load_runs
                    SET status='succeeded', stage='succeeded', finished_at=clock_timestamp(), error_message=NULL, 
                        applied_rows=%s, decision_counts=%s, action_counts=%s
                    WHERE run_id=%s
                    """, 
                    (stats["applied_rows"], Jsonb(dict(decision_counts or {})), Jsonb(dict(action_counts or {})), run_id),
                )

            stats["decisions"] = decision_counts
            stats["actions"] = action_counts

        except Exception as error:
            if fail_after_staging and stage == "staging_ready":
                error_code = "test_after_staging"
            elif fail_inside_publication and stage == "publishing":
                error_code = "test_in_publication"
            else:
                error_code = "processing_error"

            error_message = f"{type(error).__name__}: {error}"

            try:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE de6_etl.load_runs
                           SET status='failed', stage=%s, error_code=%s, finished_at=clock_timestamp(), error_message=%s
                           WHERE run_id=%s
                        """,
                        (stage, error_code, error_message, run_id),
                    )
            except Exception:
                logger.exception("event=failed_to_save_status run_id=%s batch_id=%s", run_id, manifest["batch_id"])
            logger.exception("event=load_failed run_id=%s batch_id=%s stage=%s error_code=%s", 
                             run_id, manifest["batch_id"], stage, error_code)
            raise

    logger.info("event=publication_commited run_id=%s applied_rows=%s", run_id, stats["applied_rows"])

    try:
        load_stats = verify_load(conn_params, run_id, config, outside_before, inside_before, watermark)
        stats["active_orders"] = load_stats["active_orders"]
        stats["deleted_orders"] = load_stats["deleted_orders"]
        stats["total_amount"] = load_stats["total_amount"]
        stats["watermark"] = load_stats["watermark"]
    except Exception:
        logger.exception("event=verification_failed run_id=%s batch_id=%s publication_commmited=true", run_id, manifest["batch_id"])
        raise
    
    logger.info("event=verification_passed run_id=%s batch_id=%s", run_id, manifest["batch_id"])
    return run_id, stats


#Основной код
def main():
    parser = argparse.ArgumentParser() 

    parser.add_argument("--manifest", type=Path, help = "Путь к manifest.json")
    parser.add_argument("--resume-run", type=str, help = "run_id failed-попытки, из staging которой восстановить загрузку")
    parser.add_argument("--replay", action="store_true", help = "Повтор пакета без продвижения watermark")
    parser.add_argument("--overlap-minutes", type=int, default=0, help = "Минуты перекрытия (для delta)")
    parser.add_argument("--fail-after-staging", action="store_true", help = "Тест - ошибка после фиксации staging")
    parser.add_argument("--fail-inside-publication", action="store_true", help = "Тест - ошибка после первого изменения цели")

    args = parser.parse_args() 

    configure_logging()

        #Настройка подключения к базе
    conn_params = { 
        "host": os.environ["PGHOST"], 
        "port": int(os.environ["PGPORT"]), 
        "dbname": os.environ["PGDATABASE"], 
        "user": os.environ["PGUSER"], 
        "password": os.environ["PGPASSWORD"],
        "connect_timeout": 5, 
    }

    try: 
        run_id, stats = run_load(args.manifest, conn_params, replay=args.replay, 
                                 overlap_minutes=args.overlap_minutes,
                                 fail_after_staging=args.fail_after_staging,
                                 fail_inside_publication=args.fail_inside_publication,
                                 resume_run=args.resume_run,
                                 ) 

    except Exception:
        logger.exception("event=program_failed")
        return 1

    print("run_id:", run_id)
    print("Режим:", stats["mode"])
    print("Статус: succeeded; сверка пройдена")
    print("Получено из источника:", stats["received"])
    print("Сохранено в staging:", stats["staged_rows"])
    print("Подготовлено уникальных ключей:", stats["prepared_rows"])
    print("Изменено строк:", stats["applied_rows"])
    for decision in DECISIONS:
        print(f"    {decision}: {stats['decisions'][decision]}")
    for action in ACTIONS:
        print(f"    {action}: {stats['actions'][action]}")
    print("Активных заказов:", stats["active_orders"])
    print("Сумма:", stats["total_amount"], "EUR")
    print("Удалено заказов:", stats["deleted_orders"])
    print("Watermark:", stats["watermark"])
   
    logger.info("event=load_completed")
    return 0 

if __name__ == "__main__":
    raise SystemExit(main())

