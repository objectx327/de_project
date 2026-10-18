import argparse
import psycopg 
import os
import csv
import json
import re
import logging
from getpass import getpass 
from decimal import Decimal 
from itertools import islice 
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

#########
#Параметры
#########
base_dir = Path(__file__).resolve().parent.parent 
data_dir = base_dir / "data"

######
#Настройка логирования
####
log_dir = base_dir / "data" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=log_dir / "load.log",
    encoding="utf-8",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

#Режим загрузки в буфер:
# Для copy - False
# Для batch - True
is_batch = True
batch_size = 25
rejection_limit = Decimal("0.02")   #Допустимый порог отклонений

#Ожидаемые атрибуты 
EXPECTED_FIELDS = [ 
    "Payment ID", 
    "Amount", 
    "Currency", 
    "Created At", 
    "Paid At", 
    "Status", 
    "Comment", 
] 
#Маппинг исходных и целевых атрибутов
FIELD_MAP = { 
    "Payment ID": "payment_id", 
    "Amount": "amount", 
    "Currency": "currency", 
    "Created At": "created_at", 
    "Paid At": "paid_at", 
    "Status": "status", 
    "Comment": "comment", 
}
#Cтатусы
STATUS_MAP = { 
    "paid": "paid", 
    "оплачен": "paid", 
} 
#Временный буфер текущей загрузки
CREATE_BUFFER_SQL = """ 
    CREATE TEMP TABLE payment_events_batch ( 
        LIKE de4_hw.payment_events 
        INCLUDING CONSTRAINTS 
        INCLUDING INDEXES 
    ) 
    ON COMMIT DROP 
"""
#Вставка в буфер
INSERT_BUFFER_SQL = """
    INSERT INTO payment_events_batch (
        payment_id,
        amount,
        currency,
        created_at,
        paid_at,
        status,
        comment
    )
    VALUES (
        %(payment_id)s,
        %(amount)s,
        %(currency)s,
        %(created_at)s,
        %(paid_at)s,
        %(status)s,
        %(comment)s
    )
"""
#Проверочный запрос к буферу
BUFFER_TOTALS_SQL = """ 
    SELECT 
        COUNT(*), 
        COALESCE(SUM(amount), 0) 
    FROM payment_events_batch 
""" 
#Перенос из буфера в постоянную таблицу
INSERT_FROM_BUFFER_SQL = """ 
    INSERT INTO de4_hw.payment_events ( 
        payment_id, amount, currency, created_at, paid_at, status, comment
    ) 
    SELECT 
        payment_id, amount, currency, created_at, paid_at, status, comment
    FROM payment_events_batch 
    ON CONFLICT (payment_id) DO NOTHING 
""" 
#Сверка постоянной таблицы по ключам загрузки
TARGET_TOTALS_SQL = """ 
    SELECT 
        COUNT(*), 
        COALESCE(SUM(p.amount), 0) 
    FROM payment_events_batch AS b 
    JOIN de4_hw.payment_events AS p 
        ON p.payment_id = b.payment_id 
"""
#сравнение всех содержательных полей
MISMATCH_SQL = """ 
    SELECT b.payment_id 
    FROM payment_events_batch AS b 
    LEFT JOIN de4_hw.payment_events AS p 
        ON p.payment_id = b.payment_id 
    WHERE p.payment_id IS NULL 
        OR (p.amount, p.currency, p.created_at, p.paid_at, p.status, p.comment) 
            IS DISTINCT FROM 
            (b.amount, b.currency, b.created_at, b.paid_at, b.status, b.comment) 
    LIMIT 1 
"""

#####
#Загрузка, очистка и проверка источника из csv
####
#Проверка заголовков
def validate_header(fieldnames): 
    if fieldnames is None:
        raise ValueError("Файл пуст: заголовок отсутствует") 
    if sorted(fieldnames) != sorted(EXPECTED_FIELDS): 
        raise ValueError(f"Неверный заголовок: {fieldnames}")

def validate_row_shape(row): 
    if None in row: 
        raise ValueError(f"Лишние поля: {row[None]}") 
    for name in EXPECTED_FIELDS: 
        if row[name] is None: 
            raise ValueError( 
                f"В записи отсутствует поле: {name}" 
            )

#Маппинг исходных и целевых атрибутов
def rename_fields(raw): 
    result = {} 
    for source_name, target_name in FIELD_MAP.items(): 
        if source_name not in raw: 
            raise ValueError( 
                f"Отсутствует поле: {source_name}" 
            ) 
        if target_name in result: 
            raise ValueError( 
                f"Повтор целевого поля: {target_name}" 
            ) 
        result[target_name] = raw[source_name] 
    return result

#Приведение данных
#Строка
def required_text(value, field): 
    if not isinstance(value, str): 
        raise ValueError( 
            f"{field}: ожидалась строка" 
        ) 
    result = value.strip() 
    if result == "": 
        raise ValueError( 
            f"{field}: пустое значение" 
        ) 
    return result

#Преобразование суммы в Decimal
def parse_amount(value): 
    text = required_text(value, "amount") 
    if re.fullmatch(r"[0-9]+(,[0-9]{1,2})?", text) is None: 
        raise ValueError("amount: неверный формат") 
    amount = Decimal(text.replace(",", ".")) 
    if amount > Decimal("9999999999.99"): 
        raise ValueError("amount: превышен диапазон") 
    if amount <= Decimal("0"): 
        raise ValueError("amount: сумма меньше либо равна нулю") 
    return amount.quantize(Decimal("0.01"))

#Cтатусы
def normalize_status(value): 
    text = required_text(value, "status") 
    key = text.casefold()
    if key not in STATUS_MAP: 
        raise ValueError(f"Неизвестный статус: {text}") 
    return STATUS_MAP[key]

#Время
def parse_date_at(value, attr): 
    text = required_text(value, attr) 
    result = datetime.fromisoformat(text) 
    if result.utcoffset() is None: 
        raise ValueError(f"{attr}: отсутствует часовой пояс") 
    return result.astimezone(timezone.utc)

#Очистка записей
def transform_payment(raw): 
    record = rename_fields(raw)

    payment_id = required_text( 
        record["payment_id"], "payment_id" 
    )

    amount = parse_amount(record["amount"])

    currency = required_text( 
        record["currency"], "currency" 
    ).upper() 
    if currency != "EUR": 
        raise ValueError("Ожидалась валюта EUR")
    
    status = normalize_status(record["status"]) 
    if status != "paid": 
        raise ValueError("Запись не является оплаченным платежом")
    
    created_at = parse_date_at(record["created_at"], "created_at")
    paid_at = parse_date_at(record["paid_at"], "paid_at")
    if paid_at.astimezone(timezone.utc) < created_at.astimezone(timezone.utc):
        raise ValueError("Оплата произошла раньше создания")
    
    return { 
        "payment_id": payment_id,
        "amount": amount, 
        "currency": currency, 
        "created_at": created_at,
        "paid_at": paid_at,
        "status": status, 
        "comment": record["comment"],
    }

#Обработка ошибок
def issue(field, code, message): 
    return { 
        "field": field, 
        "code": code, 
        "message": message, 
    }

def validate_source(raw): 
    if not isinstance(raw, dict): 
        return [issue(None, "RECORD_TYPE", "Ожидался объект")] 
    errors = [] 
    for field in FIELD_MAP: 
        if field not in raw: 
            errors.append(issue(field, "MISSING_FIELD", "Поле отсутствует"))
        elif raw[field] is None: 
            errors.append(issue(field, "NULL_VALUE", "Передан null"))
        elif not isinstance(raw[field], str): 
            errors.append(issue(field, "INVALID_TYPE", "Ожидалась строка"))
        elif field != "Comment" and raw[field].strip() == "": 
            errors.append(issue(field, "EMPTY_VALUE", "Пустая строка"))
        else: 
            continue 
    return errors

#Дублирующиеся ключи
def extract_key(raw): 
    if not isinstance(raw, dict): 
        return None 
    value = raw.get("Payment ID") 
    if not isinstance(value, str): 
        return None 
    return value.strip() or None

def check_record(raw, key_counts): 
    key = extract_key(raw) 
    if key is not None and key_counts[key] > 1: 
        return None, [issue("Payment ID", "DUPLICATE_KEY", "Ключ повторяется внутри загрузки")]

    errors = validate_source(raw) 
    if errors: 
        return None, errors 
    try: 
        prepared = transform_payment(raw) 
    except ValueError as error: 
        return None, [issue(None, "TRANSFORM_ERROR", str(error))] 
    return prepared, []


#Загрузка файла из csv + очистка данных и приведение их к нужному виду по правилам договора с источником
#Чтение исходного csv и провверка структуры
def read_records(path):
    path = Path(path)
    logger.info("event=csv_read_started path=%s", str(Path(path).relative_to(base_dir)))

    if not path.is_file():
       raise FileNotFoundError(f"Исходный CSV-файл не найден: {path}")
    
    raw_records = []
    with path.open("r", encoding="utf-8", newline="") as file: 
        reader = csv.DictReader( 
            file, 
            delimiter=";", 
            quotechar='"', 
            strict=True,
        )

        validate_header(reader.fieldnames)

        for row in reader: 
            validate_row_shape(row)
            raw_records.append(row)
            
    logger.info(
        "event=csv_read_completed path=%s rows=%d",
        str(Path(path).relative_to(base_dir)),
        len(raw_records),
    )
    return raw_records

#Преобразование
def prepare_records(raw_records):
    #Дубли
    key_counts = Counter() 
    for raw in raw_records: 
        key = extract_key(raw) 
        if key is not None: 
            key_counts[key] += 1

    accepted = [] 
    rejected = [] 

    for record_number, raw in enumerate(raw_records, start=1): 
        prepared, errors = check_record(raw, key_counts) 
        if errors: 
            rejected.append({ 
                "record_number": record_number, 
                "raw": raw, 
                "errors": errors, 
            }) 
        else: 
            accepted.append(prepared)

    logger.info(
        "event=preparation_completed read=%d accepted=%d rejected=%d",
        len(raw_records),
        len(accepted),
        len(rejected),
    )

    return accepted, rejected

#Сохранение отклонений
def save_rejected(rejected, path, run_id):
    target_rejected = data_dir / "rejected" / run_id / "payments.jsonl"
    #Отклоненные записи
    target_rejected.parent.mkdir(parents=True, exist_ok=True) 

    with target_rejected.open("x", encoding="utf-8", newline="\n") as file: 
        for item in rejected: 
            entry = { 
                "run_id": run_id, 
                "source_file": str(Path(path).relative_to(base_dir)), 
                **item, 
            } 
            text = json.dumps(entry, ensure_ascii=False, allow_nan=False) 
            file.write(text + "\n")

    logger.info(
        "event=save_rejected count=%s path=%s",
        len(rejected),
        str(Path(target_rejected).relative_to(base_dir)),
    )

    return target_rejected

#Сохранение принятых записей (без загрузки в бд)
def save_accepted(accepted, run_id):
    target_accepted = data_dir / "processed" / run_id / "payments.jsonl"
    #Допущенные записи
    target_accepted.parent.mkdir(parents=True, exist_ok=True) 

    with target_accepted.open("x", encoding="utf-8", newline="") as file: 
        writer = csv.DictWriter(
            file, 
            fieldnames=list(FIELD_MAP.values()),
            delimiter=";",
            quotechar='"',
        )
        writer.writeheader()
        for row in accepted: 
            writer.writerow(row) 

    logger.info(
        "event=save_accepted count=%s path=%s",
        len(accepted),
        str(Path(target_accepted).relative_to(base_dir)),
    )

    return target_accepted

#Сохранение статистики обработки
def check_batch(raw_records, accepted, rejected, path, run_id):
    #Подсчет результатов и допуск к загрузке
    read_count = len(raw_records) 
    accepted_count = len(accepted) 
    rejected_count = len(rejected) 

    rejection_rate = (
        Decimal(rejected_count) / Decimal(read_count)
        if read_count > 0
        else None
    )

    load_allowed = (
        read_count == accepted_count + rejected_count
        and read_count > 0
        and Decimal(rejected_count) <= Decimal(read_count) * rejection_limit
    )

    summary = {
        "run_id": run_id,
        "source_file": str(Path(path).relative_to(base_dir)),
        "read": read_count,
        "accepted": accepted_count,
        "rejected": rejected_count,
        "rejection_rate": (
            str(rejection_rate)
            if rejection_rate is not None
            else None
        ),
        "load_allowed": load_allowed,
    }

    target_summary = data_dir / "processed" / run_id / "summary_payments.jsonl"
    target_summary.parent.mkdir(parents=True, exist_ok=True)
    with target_summary.open("x", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, allow_nan=False)

    if read_count != accepted_count + rejected_count: 
        logger.warning(
            "run_id=%s event=batch_rejected reason=wrong_balance "
            "read=%d accepted_count=%d rejected_count=%d",
            run_id,
            read_count,
            accepted_count,
            rejected_count,
        )
        raise RuntimeError("Нарушен баланс записей")

    if read_count == 0:
        logger.warning(
            "run_id=%s event=batch_rejected reason=empty_csv "
            "read=%d",
            run_id,
            read_count,
        )
        raise RuntimeError("Загрузка запрещена: в CSV нет данных")

    if Decimal(rejected_count) > Decimal(read_count) * rejection_limit:
        logger.warning(
            "run_id=%s event=batch_rejected reason=rejection_limit "
            "read=%d rejected=%d rejection_rate=%s limit=%s",
            run_id,
            read_count,
            rejected_count,
            rejection_rate,
            rejection_limit,
        )
        raise RuntimeError("Загрузка запрещена: превышена допустимая доля отклонений")
    
    return target_summary

#####
#Подготовка к загрузке в базу
#####
#Вариант 1 - Функция загрузки записей в буфер через COPY (хочется сюда тоже добавить какой-то счетчик загруженных записей)
def copy_to_buffer(cur, records): 
    rows_count = 0
    with cur.copy(""" 
        COPY payment_events_batch ( 
            payment_id, amount, currency, created_at, paid_at, status, comment
        ) 
        FROM STDIN 
    """) as copy: 
        for record in records: 
            copy.write_row((
                record["payment_id"], 
                record["amount"], 
                record["currency"], 
                record["created_at"], 
                record["paid_at"], 
                record["status"], 
                record["comment"], 
            ))
            rows_count += 1

    logger.info("event=copy_completed rows=%d", rows_count)

    return rows_count

#Вариант 2 - Функция загрузки записей в буфер через batch
def batches(records, size): 
    if size <= 0: 
        raise ValueError("Размер порции должен быть положительным") 
    iterator = iter(records) 
    while True: 
        batch = list(islice(iterator, size)) 
        if not batch: 
            return 
        yield batch

#Батч-загрузка порциями
def batch_to_buffer(cur, records, size):
    buffered = 0 
    for batch in batches(records, size): 
        cur.executemany(INSERT_BUFFER_SQL, batch) 
        buffered += cur.rowcount    

    logger.info("event=batch_completed rows=%d", buffered)
    
    return buffered


#Проверки
#Общая функция сравнения 
def check_totals(cur, query, expected): 
    cur.execute(query) 
    actual = cur.fetchone() 
    
    if actual != expected: 
        raise ValueError( 
            f"Ожидалось {expected}, получено {actual}" 
        )
    
#Проверка загрузки
def check_loaded(cur, expected): 
    check_totals(cur, TARGET_TOTALS_SQL, expected) 

    cur.execute(MISMATCH_SQL) 
    row = cur.fetchone() 
    
    if row is not None: 
        raise ValueError(f"Несовпадение платежа: {row[0]}")

#######
#Загрузка
##########
def load_records(accepted):
    if not accepted: 
        raise ValueError("Ожидался непустой набор платежей") 

    expected = ( 
        len(accepted), 
        sum( 
            (record["amount"] for record in accepted), 
            Decimal("0.00"), 
        ), 
    )

    #Настройка подключения к базе
    conn_params = { 
        "host": os.environ["PGHOST"], 
        "port": int(os.environ["PGPORT"]), 
        "dbname": os.environ["PGDATABASE"], 
        "user": os.environ["PGUSER"], 
        "connect_timeout": 5, 
    }
    password = getpass("Пароль PostgreSQL: ")

    #Проверка подключения
    with psycopg.connect( 
        **conn_params, 
        password=password, 
    ) as conn: 
        with conn.cursor() as cur: 
            cur.execute( 
                "SELECT current_database(), current_user" 
            ) 
            database, user = cur.fetchone() 
            logging.info("event=db_connected database=%s user=%s", database, user)
            #Создаем временную таблицу
            cur.execute(CREATE_BUFFER_SQL) 

            #Заполняем буфер
            if not is_batch:
                #Вариант 1 - через copy
                buffered = copy_to_buffer(cur, accepted) 
            else:
                #Вариант 2 - через батч
                buffered = batch_to_buffer(cur, accepted, size=25) 

            logger.info(
                "event=buffer_loaded rows=%d method=%s",
                buffered,
                "batch" if is_batch else "copy",
            )

            if buffered != expected[0]:
                raise RuntimeError(f"В буфер передано {buffered} записей, ожидалось {expected[0]}")
                
            #Проверяем буфер
            check_totals(cur, BUFFER_TOTALS_SQL, expected) 

            #Переносим данные в целевую таблицу
            cur.execute(INSERT_FROM_BUFFER_SQL) 
            inserted = cur.rowcount 

            #Проверка сбоя после insert и до commit
            print("Вставлено строк до коммита:", inserted)
            raise RuntimeError("Контрольный сбой после INSERT, до COMMIT")
        
            #Проверяем результат
            check_loaded(cur, expected) 

    logger.info(
        "event=db_transaction_commited inserted=%d",
        inserted,
    )
    return inserted

#Один запуск
def run_load(path):
    run_id = uuid4().hex
    path = Path(path).resolve()
    logger.info(
        "run_id=%s event=load_started input=%s",
        run_id,
        str(Path(path).relative_to(base_dir)),
    )

    raw_records = read_records(path)
    accepted, rejected = prepare_records(raw_records)

    logger.info(
        "event=data_prepared "
        "read=%d accepted=%d rejected=%d",
        len(raw_records),
        len(accepted),
        len(rejected),
    )

    save_rejected(rejected, path, run_id)
    save_accepted(accepted, run_id)


    check_batch(raw_records, accepted, rejected, path, run_id)

    inserted = load_records(accepted)
    status = "SUCCESS"
    if rejected: 
        status = "SUCCESS_WITH_REJECTS"

    result = { 
        "run_id": run_id, 
        "status": status, 
        "read": len(raw_records), 
        "accepted": len(accepted), 
        "rejected": len(rejected), 
        "inserted": inserted, 
    }

    return result

########
#Основная часть кода
#####
def main(): 
    parser = argparse.ArgumentParser() 
    parser.add_argument("--input", type=Path, required=True, help = "Путь к CSV") 
    args = parser.parse_args() 

    try: 
        result = run_load(args.input) 
    except Exception as error: 
        logger.exception("event=load_failed")
        return 1 
    
    logger.info("event=load_completed result=%s", result)
    return 0 



if __name__ == "__main__": 
    raise SystemExit(main())