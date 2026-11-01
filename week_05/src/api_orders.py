import requests 
import os 
import argparse 
import logging
import time
import re
import json
import psycopg
from pathlib import Path
from uuid import uuid4
from decimal import Decimal 
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

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
    "order_id", 
    "customer", 
    "status", 
    "currency", 
    "total_amount", 
    "created_at",
    "updated_at",  
    "items", 
]

STATUSES = [
    "pending",
    "paid",
    "cancelled",
    "refunded",
]

#Архивирование результатов опроса апи
def archive_response(response, run_id, url, params, page_number, attempt, duration_s):
    raw_response_id = uuid4()
    received_at = datetime.now(timezone.utc)

    archive_dir = data_dir / "raw" / "api_responses" / str(run_id)
    archive_dir.mkdir(parents=True, exist_ok=True)

    #Сохраняйте тело каждого полученного HTTP-ответа до разбора JSON, в том числе ответа с ошибкой
    body_file = archive_dir / f"{raw_response_id}.bin"
    #Рядом сохраняйте метаданные
    meta_file = archive_dir / f"{raw_response_id}_meta.json"

    #Для точного сохранения тела используйте байты ответа, а не повторную сериализацию разобранного JSON
    body_file.write_bytes(response.content)

    #на случай если ключ отсутствует, например курсор при первом запуске, чтобы не было ошрибки - пропускаем параметр
    safe_params = {}
    allowed_params = (
        "account_id",
        "updated_from",
        "updated_to",
        "limit",
        "snapshot",
        "scenario",
        "cursor",
        "status",
    )
    for param in allowed_params:
        if param in params:
            safe_params[param] = params[param]

    metadata = {
        "run_id": str(run_id),
        "raw_response_id": str(raw_response_id),
        "received_at": received_at.isoformat(),
        "endpoint": url,
        "params": safe_params,
        "page_number": page_number,
        "attempt": attempt,
        "http_status": response.status_code,
        "duration_s": round(duration_s, 6),
        "body_path": body_file.relative_to(base_dir).as_posix(),
    }

    with meta_file.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)

    return raw_response_id, received_at

#проверка страницы
#Проверить структуру ответа
def validate_page(orders, expected_snapshot):
    if not isinstance(orders, dict): 
        raise ValueError("Ожидался JSON-объект")
    
    if "data" not in orders: 
        raise ValueError("В ответе отсутствует поле data") 
    records = orders["data"]
    if not isinstance(records, list): 
        raise ValueError("Поле data должно быть списком") 
	
    if "next_cursor" not in orders: 
        raise ValueError("Отсутствует поле next_cursor") 
    cursor = orders["next_cursor"] 
    if cursor is not None: 
        if not isinstance(cursor, str) or not cursor: 
            raise ValueError("Некорректный next_cursor")
	
    if "meta" not in orders: 
        raise ValueError("В ответе отсутствует поле meta") 
    meta = orders["meta"]
    if not isinstance(meta, dict): 
        raise ValueError("Некорректный meta") 
	
    if "total_count" not in meta: 
        raise ValueError("В ответе отсутствует поле total_count") 
    total_count = meta["total_count"]
    if type(total_count) is not int or total_count < 0: 
        raise ValueError("Некорректный total_count") 
	
    if "snapshot" not in meta: 
        raise ValueError("В ответе отсутствует поле snapshot") 
    snapshot = meta["snapshot"]
    if snapshot != expected_snapshot:
        raise ValueError("Снимок ответа не совпадает с запрошенным") 
    return records, cursor, total_count

#Чтение страницы
def fetch_page(url, headers, params, stats, run_id, read_timeout=30.0, connect_timeout=5.0): 
    max_attempts = 3
    retry_statuses = (429, 500, 502, 503, 504)
    page_number = stats["pages"] + 1
    stats["current_page"] = page_number

    #Для первых трех попыток
    for attempt in range(1, max_attempts + 1):
        stats["http_attempts"] += 1
        stats["attempt"] = attempt
        stats["http_status"] = None
        stats["stage"] = "http_request"
        #время выполнения
        request_started = time.perf_counter()
        logger.info(
            "event=http_started stage=http run_id=%s page=%s attempt=%s http_attempts=%s",
            run_id, page_number, attempt, stats["http_attempts"],
        )
        #Выполнить GET с заданным timeout 
        try:
            response = requests.get( 
                url, 
                headers=headers, 
                params=params, 
                timeout=(connect_timeout, read_timeout)
            )
        #если ошибки подключения/ожидания
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as error:
            duration_s = time.perf_counter() - request_started
            logger.warning(
                "event=http_no_response stage=http run_id=%s page=%s attempt=%s http_status=none duration_s=%.3f error_type=%s",
                run_id, page_number, attempt, duration_s, type(error).__name__,
            )

            if attempt == max_attempts:
                raise RuntimeError("Не удалось получить страницу API") from error

            #Бозовая пауза для сетевых ошибок                    
            pause = attempt

        #если успешыйй requests.get()
        else:
            duration_s = time.perf_counter() - request_started 
            stats["http_status"] = response.status_code
            try:
                stats["stage"] = "archive"
                raw_response_id, received_at = archive_response(response, run_id, url, params, page_number, attempt, duration_s)
                logger.info(
                    "event=http_response stage=http run_id=%s page=%s attempt=%s http_status=%s duratin=%s raw_response_is=%s",
                    run_id, page_number, attempt, response.status_code, duration_s, raw_response_id,
                )
                #Если получен ожидаемый успешный HTTP-статус:
                if response.status_code == 200: 
                    #Разобрать json
                    stats["stage"] = "parse_json"
                    try:
                        orders = response.json() 
                    except requests.exceptions.JSONDecodeError as error:
                        raise ValueError("Некорректный JSON") from error
                    stats["stage"] = "validate_page"

                    records, cursor, total_count = validate_page(orders, params.get("snapshot", "initial"))
                    return records, cursor, total_count, raw_response_id, received_at

                stats["stage"] = "http_status"
                if response.status_code not in retry_statuses:
                    response.raise_for_status() 
                    raise RuntimeError(f"Неожиданный HTTP-статус: {response.status_code}") 
                
                if attempt == max_attempts:
                    response.raise_for_status()

        #Рассчитать паузу с учётом Retry-After 
        #Проверить, что ожидание укладывается в общий срок 
        #Подождать перед следующей попыткой    
                pause = attempt
                #Для 429 пауза не меньше одновременно соответствующей базовой паузы и полученного `Retry-After`
                if response.status_code == 429:
                    response_retry_after = response.headers.get("Retry-After")

                    if response_retry_after is not None:
                        try:
                            retry_after = int(response_retry_after)
                        except ValueError as error:
                            raise ValueError("Некорректный Retry-After")

                        if retry_after < 0:
                            raise ValueError("Retry-After не может быть отрицательным")

                        pause = max(pause, retry_after)

            finally:
                response.close()
        #Повтор запроса
        stats["stage"] = "retry_wait"
        logger.warning(
            "event=http_retry stage=http run_id=%s page=%s attempt=%s next_attempt=%s http_status=%s sleep_s=%s",
            run_id, page_number, attempt, attempt + 1, stats["http_status"], pause, 
        )
        time.sleep(pause)
    raise RuntimeError("Попытки получения страницы исчерпаны")


#Цикл выгрузки и защита от бесконечного обхода
def read_all_pages(url, headers, base_params, run_id, stats, read_timeout=30.0, connect_timeout=5.0):
    records_list = []

    params = dict(base_params)
    seen_cursors = set()
    seen_order_ids = set()
    max_pages = 100

    while True:
        if stats["pages"] >= max_pages:
            raise RuntimeError("Достигнут лимит 100 страниц без конца выгрузки")
        page_started = time.perf_counter()
        records, next_cursor, total_count, raw_response_id, received_at = fetch_page(url, headers, params, stats, run_id, read_timeout, connect_timeout) 

        stats["stage"] = "pagination"
        if next_cursor is not None and next_cursor in seen_cursors: 
            raise RuntimeError("API повторил курсор")

        if stats["pages"] == 0:
            stats["total_count"] = total_count
        elif total_count != stats["total_count"]:
            raise RuntimeError("Изменился total_count во время обхода страниц")

        page_number = stats["pages"] + 1

        for row_number, raw in enumerate(records, start = 1):
            #проверка на дубли по order_id
            if isinstance(raw, dict):
                order_id = raw.get("order_id")
                if isinstance(order_id, str) and order_id.strip():
                    if order_id in seen_order_ids:
                        raise RuntimeError(f"Апи повторил order_id = {order_id}")
                    seen_order_ids.add(order_id)

            records_list.append({
                "raw": raw,
                "page_number": page_number,
                "row_number": row_number,
                "raw_response_id": raw_response_id,
                "received_at": received_at,
            })

        stats["pages"] += 1
        stats["received"] += len(records)
        logger.info(
            "event=page_received stage=pagination run_id=%s page=%s records=%s received=%s http_attempts=%s duration_s=%.3f end=%s",
            run_id, page_number, len(records), stats["received"], stats["http_attempts"], 
            time.perf_counter() - page_started, next_cursor is None,
        )
        if next_cursor is None:
            break

        seen_cursors.add(next_cursor) 
        params["cursor"] = next_cursor

    if stats["received"] != stats["total_count"]:
        raise RuntimeError(
            f"Получено {stats["received"]} заказов вместо заявленных {stats["total_count"]}"
        )

    return records_list, stats

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
    if amount > Decimal("999999999999.99"): 
        raise ValueError(f"{field}: превышен диапазон") 
    return amount

#Целые числа
def positive_int(value, field):
    if type(value) is not int: 
        raise ValueError(f"{field}: ожидался тип int")
    if value > 2147483647: 
        raise ValueError(f"{field}: превышен диапазон") 
    if value <= 0: 
        raise ValueError(f"{field}: сумма меньше либо равна нулю") 
    return value

#Время
def parse_date_at(value, attr): 
    text = required_text(value, attr) 
    result = datetime.fromisoformat(text) 
    if result.utcoffset() is None: 
        raise ValueError(f"{attr}: отсутствует часовой пояс") 
    return result.astimezone(timezone.utc)


#Обработка одного заказа
def order_process(raw):
    if not isinstance(raw, dict):
        raise ValueError("Заказ должен быть объектом")
    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise ValueError(f"Отсутствует поле: {field}")

    order_id = required_text(
        validate_fields(raw, "order_id" , str),
        "order_id"
    )

    customer = raw["customer"]
    if customer is None:
        customer_id = None
    elif isinstance(customer, dict):
        customer_id = required_text(
            validate_fields(customer, "id", str),
            "customer.id"
        )
    else:
        raise ValueError("customer: ожидался объект или null")
    
    status = required_text(
        validate_fields(raw, "status" , str),
        "status"
    )
    if status not in STATUSES:
        raise ValueError(f"Неверное значение статуса {status}")
    
    currency = required_text(
        validate_fields(raw, "currency" , str),
        "currency"
    )
    if currency != "EUR":
        raise ValueError(f"Неверное значение валюты {currency}")
    
    total_amount = parse_amount(
        validate_fields(raw, "total_amount", str),
        "total_amount"
        )
    
    created_at = parse_date_at(
        validate_fields(raw, "created_at", str),
        "created_at"
    )
    updated_at = parse_date_at(
        validate_fields(raw, "updated_at", str),
        "updated_at"
    )
    if updated_at.astimezone(timezone.utc) < created_at.astimezone(timezone.utc):
        raise ValueError("Обновление произошло раньше создания")   

    items = validate_fields(raw, "items", list)
    item_rows = []
    item_line_id = set()
    calc_total_amount = Decimal("0.00")

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Позиция должна быть объектом")
        
        line_id = required_text(
            validate_fields(item, "line_id", str),
            "line_id"
        )
        if line_id in item_line_id:
            raise ValueError(f"Повторяется значение line_id = {line_id} в заказе")
        item_line_id.add(line_id)

        sku = required_text(
            validate_fields(item, "sku", str),
            "sku"
        )

        quantity = positive_int(
            validate_fields(item, "quantity", int),
            "quantity"
        )

        unit_price = parse_amount(
            validate_fields(item, "unit_price", str),
            "unit_price"
        )

        calc_total_amount += quantity * unit_price

        item_row = {
            "order_id": order_id,
            "line_id": line_id, 
            "sku": sku, 
            "quantity": quantity, 
            "unit_price": unit_price, 
        }

        item_rows.append(item_row)
    if total_amount != calc_total_amount:
        raise ValueError(f"Сумма заказа {total_amount} не совпадает с суммой позиций {calc_total_amount}")
    
    order_row = {
        "order_id": order_id, 
        "customer_id": customer_id, 
        "status": status, 
        "currency": currency, 
        "total_amount": total_amount, 
        "created_at": created_at,
        "updated_at": updated_at, 
    }

    return order_row, item_rows

def write_rejected(record, run_id, error):
    target_rejected = data_dir / "rejected" / str(run_id) / "api_orders.jsonl" 
    target_rejected.parent.mkdir(parents=True, exist_ok=True) 
    rejected_row = {
        "run_id": str(run_id),
        "raw_response_id": str(record["raw_response_id"]),
        "page_number": record["page_number"],
        "row_number": record["row_number"],
        "reason": str(error),
        "raw_order": record["raw"],
    }
    line = json.dumps(rejected_row, ensure_ascii=False) + "\n"
    with target_rejected.open("a", encoding="utf-8") as file:
        file.write(line)
    return target_rejected.relative_to(base_dir).as_posix()
   


#действия с проверенным заказом
def save_order(cur, order_row, items, record, run_id, account_id):
    #проверяем наличие записей, нужно ли обновлять, пропустить существующую или загрузить новую
    #набор параметров для запросов
    keys = ("northcart", account_id, order_row["order_id"])
    cur.execute(
        """
        SELECT 
            order_id, 
            customer_id, 
            status, 
            currency,
            total_amount, 
            created_at, 
            source_updated_at as updated_at
        FROM de5_hw.orders
        WHERE source_system = %s AND source_account_id = %s AND order_id = %s
        FOR UPDATE
        """,
        keys,
    )
    existing = cur.fetchone()

    values = (
        order_row["customer_id"],
        order_row["status"],
        order_row["currency"],
        order_row["total_amount"],
        order_row["created_at"],
        order_row["updated_at"],
        record["received_at"],
        run_id,
        record["raw_response_id"],
        record["row_number"],
        Jsonb(record["raw"]),
    )  

    if existing is not None:
        #более старое время: пропуск
        if order_row["updated_at"] < existing["updated_at"]:
            return "skipped"
        
        elif order_row["updated_at"] == existing["updated_at"]:
            cur.execute(
                """
                SELECT 
                    line_id, 
                    sku, 
                    quantity, 
                    unit_price
                FROM de5_hw.order_items
                WHERE source_system = %s AND source_account_id = %s AND order_id = %s
                """,
                keys,
            )
            #сравнивваем версии
            saved_items = {}
            for item in cur.fetchall():
                saved_items[item["line_id"]] = (item["sku"], item["quantity"], item["unit_price"])

            incoming_items = {}
            for item in items:
                incoming_items[item["line_id"]] = (item["sku"], item["quantity"], item["unit_price"])
            #одинаковое время и одинаковые подготовленные бизнес-поля и позиции: пропуск
            if existing == order_row and saved_items == incoming_items:
                return "skipped"
            logger.error(
                "event=version_conflict stage=write run_id=%s page=%s record_no=%s raw_response_id=%s",
                run_id, record["page_number"], record["row_number"], record["raw_response_id"],
            )
            #одинаковое время, но разные подготовленные бизнес-поля или позиции: конфликт, остановка запуска и откат его изменений.
            raise RuntimeError(f"Расходятся версии для заказа {order_row["order_id"]}")
        
    #новый ключ: вставка
    if existing is None:
        cur.execute(
            """
            INSERT INTO de5_hw.orders (
                source_system,
                source_account_id,
                order_id,
                customer_id,
                status,
                currency,
                total_amount,
                created_at,
                source_updated_at,
                received_at,
                run_id,
                raw_response_id,
                record_no,
                payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            keys + values,
        )
        action = "inserted"  
    #более новое время: обновление заказа и замена его полного набора позиций в одной транзакции
    else:
        cur.execute(
            """
            UPDATE de5_hw.orders
            SET 
                customer_id = %s,
                status = %s,
                currency = %s,
                total_amount = %s,
                created_at = %s,
                source_updated_at = %s,
                received_at = %s,
                run_id = %s,
                raw_response_id = %s,
                record_no = %s,
                payload = %s
            WHERE source_system = %s AND source_account_id = %s AND order_id = %s
            """,
            values + keys,
        )
        cur.execute(
            """
            DELETE FROM de5_hw.order_items
            WHERE source_system = %s AND source_account_id = %s AND order_id = %s
            """,
            keys,
        )
        action = "updated"
    #обновляем позициии
    for item in items:
        cur.execute(
            """
            INSERT INTO de5_hw.order_items(
                source_system,
                source_account_id,
                order_id,
                line_id,
                sku,
                quantity,
                unit_price
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            keys + (item["line_id"], item["sku"], item["quantity"], item["unit_price"]),
        )
    return action


def process_records(cur, records_list, run_id, account_id, stats, fail_after_page=None):
    #границы странцц
    pages = {}

    for page_number in range(1, stats["pages"] + 1):
        pages[page_number] = []
    for record in records_list:
        pages[record["page_number"]].append(record)

    for page_number, page_records in pages.items():
        page_started = time.perf_counter()
        stats["current_page"] = page_number

        for record in page_records:    
            stats["stage"] = "validate_orders"
            try:
                order_row, items = order_process(record["raw"])
            except ValueError as error:
                #сохранение отклоненых записей
                stats["stage"] = "write_rejected"
                stats["rejected_path"] = write_rejected(record, run_id, error)
                stats["rejected_orders"] += 1
                logger.info(
                    "event=order_rejected stage=validation run_id=%s page=%s record_no=%s raw_response_id=%s error_type=ValueError rejected_count=%s",
                    run_id, page_number, record["row_number"], record["raw_response_id"], stats["rejected_orders"],
                )
                continue

            stats["accepted_orders"] += 1
            stats["accepted_items"] += len(items)
            stats["accepted_total_amount"] += order_row["total_amount"]
            stats["stage"] = "write_orders"
            action = save_order(cur, order_row, items, record, run_id, account_id)
            if action == "inserted":
                stats["inserted_orders"] += 1
            elif action == "updated":
                stats["updated_orders"] += 1
            else:
                stats["skipped_orders"] += 1

            if action != "skipped":
                stats["written_items"] += len(items)
                stats["written_total_amount"] += order_row["total_amount"]

        logger.info(
            "event=page_processed stage=write run_id=%s page=%s duration_s=%.3f accepted_couont=%s rejected_count=%s pending_inserted=%s pending_updated=%s skipped_count=%s",
            run_id, page_number, time.perf_counter() - page_started, stats["accepted_orders"], stats["rejected_orders"], 
            stats["inserted_orders"], stats["updated_orders"], stats["skipped_orders"],
        )
        #Тестовый сбой
        if fail_after_page == page_number:
            stats["stage"] = "test_failure"
            logger.warning(
                "event=test_forced_failure stage=write run_id=%s page=%s",
            run_id, page_number,
            )
            raise RuntimeError("Тестовый сбой после записи страницы")

    if (stats["accepted_orders"] + stats["rejected_orders"] != stats["received"] 
            or stats["inserted_orders"] + stats["updated_orders"] + stats["skipped_orders"] != stats["accepted_orders"]) :
        raise RuntimeError("Нарушен баланс записей")



def run_load(url, headers, params, conn_params, read_timeout=30.0, connect_timeout=5.0, fail_after_page=None):
    run_id = uuid4()
    run_started = time.perf_counter()
    stats = {
        "pages": 0,
        "http_attempts": 0,
        "received": 0,
        "total_count": None,
        "accepted_orders": 0,
        "rejected_orders": 0,
        "accepted_items": 0,
        "accepted_total_amount": Decimal("0"),
        "inserted_orders": 0,
        "updated_orders": 0,
        "skipped_orders": 0,
        "written_items": 0,
        "written_total_amount": Decimal("0"),
        "rejected_path": None,
        "duration_s": 0.0,
        "stage": "start",
        "current_page": 0,
        "attempt": 0,
        "http_status": None,
    }

    period_from = parse_date_at(params["updated_from"], "updated_from")
    period_to = parse_date_at(params["updated_to"], "updated_to")
    if period_from >= period_to:
        raise ValueError("updated_to должен быть позже updated_from")
    account_id = params["account_id"]

    try:
        stats["stage"] = "db_connect"
        #Проверка подключения
        with psycopg.connect(
            **conn_params, 
            autocommit=True
        ) as conn: 
            stats["stage"] = "run"
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO de5_hw.load_runs (
                            run_id,
                            source_system,
                            source_account_id,
                            period_from,
                            period_to,
                            status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (run_id, "northcart", account_id, period_from, period_to, "running"),
                    )
            logger.info("event=load_started stage=run run_id=%s", run_id)
            
            try:
                with conn.transaction():
                    records_list, stats = read_all_pages(url, headers, params, run_id, stats, read_timeout, connect_timeout, )

                    with conn.cursor(row_factory=dict_row) as cur:
                        process_records(cur, records_list, run_id, account_id, stats, fail_after_page)
                        stats["stage"] = "verify"
                        verify_started = time.perf_counter()

                        #проверка
                        cur.execute(
                            """
                            SELECT count(*) AS order_count, 
                                coalesce(sum(total_amount), 0) AS total_amount
                            FROM de5_hw.orders
                            WHERE run_id = %s
                            """,
                            (run_id,),
                        )
                        actual = cur.fetchone()
                        if actual["order_count"] != stats["inserted_orders"] + stats["updated_orders"]:
                            raise RuntimeError("Не совпало число записанных заказов в БД")
                        if actual["total_amount"] != stats["written_total_amount"]:
                            raise RuntimeError("Не совпала сумма записанных заказов в БД")
                        
                        cur.execute(
                            """
                            SELECT
                                count(*) as item_count
                            FROM de5_hw.order_items AS i
                            JOIN de5_hw.orders AS o
                            ON o.source_system = i.source_system
                            AND o.source_account_id = i.source_account_id
                            AND o.order_id = i.order_id
                            WHERE o.run_id = %s
                            """,
                            (run_id,),
                        )
                        actual_item = cur.fetchone()
                        if actual_item["item_count"] != stats["written_items"]:
                            raise RuntimeError("Не совпало число записанных позиций в БД")
                        logger.info(
                            "event=verification_passed stage=verify run_id=%s duration_s=%.3f received_count=%s accepted_count=%s rejected_count=%s",
                            run_id, time.perf_counter() - verify_started, stats["received"], stats["accepted_orders"], stats["rejected_orders"],
                        )

                        stats["stage"] = "update"

                    #Загрузка с тестовым фильтром `status` не должна обновлять общий прогресс всех заказов
                        if "status" not in params:
                            cur.execute(
                                """
                                INSERT INTO de5_hw.load_state AS current_state (
                                    source_system, source_account_id, completed_to, run_id
                                )
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (source_system, source_account_id)
                                DO UPDATE SET completed_to = EXCLUDED.completed_to,
                                            run_id = EXCLUDED.run_id
                                WHERE EXCLUDED.completed_to > current_state.completed_to
                                """,
                                ("northcart", account_id, period_to, run_id)
                            )
                        stats["stage"] = "commit"
                        cur.execute(
                            """
                            UPDATE de5_hw.load_runs
                            SET
                                status = %s, 
                                finished_at = clock_timestamp(),
                                received_count = %s, 
                                accepted_count = %s, 
                                rejected_count = %s, 
                                inserted_count = %s, 
                                updated_count = %s, 
                                skipped_count = %s, 
                                error_summary = %s
                            WHERE run_id = %s
                            """,
                            (
                                "succeeded", stats["received"], stats["accepted_orders"], 
                                stats["rejected_orders"], stats["inserted_orders"],
                                stats["updated_orders"], stats["skipped_orders"],
                                None, run_id,
                            )
                        )
                    stats["stage"] = "commit"

            except Exception as error:
                failed_stage = stats["stage"]
                logger.warning(
                    "event=rollback stage=%s run_id=%s attempted_insert=%s attempted_update=%s",
                    failed_stage, run_id, stats["inserted_orders"], stats["updated_orders"],
                )

                stats["inserted_orders"] = 0
                stats["updated_orders"] = 0
                stats["written_items"] = 0
                stats["written_total_amount"] = Decimal("0")

                error_summary = f"stage={failed_stage} error_type={type(error).__name__}"
                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE de5_hw.load_runs
                                SET
                                    status = %s, 
                                    finished_at = clock_timestamp(),
                                    received_count = %s, 
                                    accepted_count = %s, 
                                    rejected_count = %s, 
                                    inserted_count = %s, 
                                    updated_count = %s, 
                                    skipped_count = %s, 
                                    error_summary = %s
                                WHERE run_id = %s
                                """,
                                (
                                    "failed", stats["received"], stats["accepted_orders"], 
                                    stats["rejected_orders"], stats["inserted_orders"],
                                    stats["updated_orders"], stats["skipped_orders"],
                                    error_summary, run_id,
                                )
                            )
                except Exception as error:
                    logger.error(
                        "event=failed_to_save_status stage=finish_run run_id=%s error=%s",
                        run_id, type(error).__name__,
                    )

                raise
    except Exception as error:
        stats["duration_s"] = time.perf_counter() - run_started
        logger.error(
            "event=load_failed stage=%s run_id=%s page=%s processed_pages=%s attempt=%s http_status=%s duration_s=%.3f "
            "pages=%s http_attempts=%s received=%s accepted=%s rejected=%s error_type=%s ",
            stats["stage"], run_id, stats["current_page"], stats["pages"], stats["attempt"], stats["http_status"], stats["duration_s"]
            , stats["pages"], stats["http_attempts"], stats["received"], stats["accepted_orders"], stats["rejected_orders"], type(error).__name__,
        )
        raise

    stats["stage"] = "commited"
    stats["duration_s"] = time.perf_counter() - run_started
    logger.info(
        "event=load_succeeded stage=commited run_id=%s status=succeeded duration_s=%.3f  pages=%s http_attempts=%s "
        "received=%s accepted=%s rejected=%s amount=%s "
        "inserted=%s updadted=%s skipped=%s items=%s written_total_amount=%s ",
        run_id, stats["duration_s"], stats["pages"], stats["http_attempts"], 
        stats["received"], stats["accepted_orders"], stats["rejected_orders"], stats["accepted_total_amount"], 
        stats["inserted_orders"], stats["updated_orders"], stats["skipped_orders"], stats["written_items"], stats["written_total_amount"], 
    )
    return run_id, stats


#Основной код
def main():
    parser = argparse.ArgumentParser() 
    
    parser.add_argument("--url", default="http://127.0.0.1:8765/v1/orders", help="url (без секретов и запросов)")
    parser.add_argument("--account-id", default="shop_de", help="Аккаунт источника")
    parser.add_argument("--updated-from", default="2026-09-24T00:00:00Z", help="Начало периода включительно")
    parser.add_argument("--updated-to", default="2026-09-27T00:00:00Z", help="Конец периода исключительно")
#    parser.add_argument("--limit", type=int, choices=range(1, 6), default=4, help="Количество заказов на странице от 1 до 5")
    parser.add_argument("--limit", type=int, default=4, help="Количество заказов на странице от 1 до 5")
    parser.add_argument("--snapshot", choices=("initial", "updated"), default="initial", help="Снимок данных")
    parser.add_argument("--scenario", default="normal", help="Сценарий учебного API")
    parser.add_argument("--status", choices=("pending", "paid", "cancelled", "refunded"), default=None, help="Необязательный фильтр по статусу заказа")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="Тайм-аут соединения с API в секундах")
    parser.add_argument("--read-timeout", type=float, default=30.0, help="Тайм-аут чтения ответа API в секундах")
    parser.add_argument("--fail-after-page", type=int, default=None, help="Тестовый сбой после записи второй страницы")

    args = parser.parse_args() 

    configure_logging()

    if args.read_timeout <= 0:
        parser.error("--read-timeout должен быть больше нуля")
    if args.connect_timeout <= 0:
        parser.error("--connect-timeout должен быть больше нуля")

 #token
    token = os.getenv("DE5_API_TOKEN") 
    if not token: 
        raise RuntimeError( "Не задана DE5_API_TOKEN" )

    headers = { 
        "Accept": "application/json", 
        "Authorization": f"Bearer {token}", 
    }    

        #Настройка подключения к базе
    conn_params = { 
        "host": os.environ["PGHOST"], 
        "port": int(os.environ["PGPORT"]), 
        "dbname": os.environ["PGDATABASE"], 
        "user": os.environ["PGUSER"], 
        "password": os.environ["PGPASSWORD"],
        "connect_timeout": 5, 
    }
    params = { 
        "account_id": args.account_id,
        "updated_from": args.updated_from,
        "updated_to": args.updated_to,
        "limit": args.limit,
        "snapshot": args.snapshot,
        "scenario": args.scenario,
    }

    if args.status is not None:
        params["status"] = args.status

    try:
        run_id, stats = run_load(
            args.url, 
            headers, 
            params,
            conn_params,
            read_timeout=args.read_timeout,
            connect_timeout=args.connect_timeout,
            fail_after_page=args.fail_after_page,
        )

    except Exception as error:
        error_summary = type(error).__name__
        logger.error(
            "event=program_failed stage=exit error_type=%s", 
            error_summary,
        )
        return 1

    print("run_id:", run_id)
    print("Статус: succeeded")
    print("Успешных страниц:", stats["pages"])
    print("HTTP-попыток:", stats["http_attempts"])
    print("Получено из API:", stats["received"])
    print("Принято заказов:", stats["accepted_orders"])
    print("Отклонено заказов:", stats["rejected_orders"])
    print("Позиций у принятых заказов:", stats["accepted_items"])
    print("Сумма принятых заказов:", stats["accepted_total_amount"], "EUR")
    print("Вставлено:", stats["inserted_orders"])
    print("Обновлено:", stats["updated_orders"])
    print("Пропущено:", stats["skipped_orders"])
    print("Файл с причинами отклонений:", stats["rejected_path"])

    return 0

if __name__ == "__main__":
    raise SystemExit(main())