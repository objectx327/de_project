import argparse
import csv
import os
import hashlib
import json
import logging
import psycopg
import sys
from datetime import date, datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4
from psycopg.types.json import Jsonb
from zoneinfo import ZoneInfo

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
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(
                log_dir / "load.log",
                encoding="utf-8"
            ),
            logging.StreamHandler(sys.stdout)
        ]
    )

RULE_VERSION = "1"

CSV_FIELDS = [
    'source_system', 
    'payment_id',
    'order_id', 
    'paid_at',
    'amount', 
    'currency',
    'status', 
    'channel_id',
]

REQUIRED_FIELDS = [
    "source_system",
    "payment_id",
    "order_id",
    "paid_at",
    "amount",
    "currency",
    "status",
]

ROW_RULES = (
    "csv_record_shape", "typed_row_present", "required_fields",
    "source_system_match", "payment_key_unique", "paid_at_parse",
    "paid_at_in_period", "amount_parse", "amount_valid",
    "currency_allowed", "status_allowed", "channel_known",
)

TOTAL_RULES = (
    "input_complete", "manifest_structure", "manifest_period",
    "source_coverage", "reference_integrity", "manifest_row_count",
    "manifest_success_sums", "empty_input_confirmed", "missing_channel_share",
    "classification_balance", "prepared_count", "join_cardinality",
    "prepared_sums", "aggregate_payment_count", "aggregate_sums",
    "aggregate_groups",
)

#sql-проверки поставки
TOTAL_SQL_RULES = {
    'input_complete': (
        'records',
        'CSV полностью прочитан, заголовок корректен',
        """
        SELECT CASE WHEN EXISTS (SELECT 1 FROM unnest(csv_errors) e WHERE e LIKE 'CSV_ERROR:%%') THEN 'ERROR'
                    WHEN read_complete AND cardinality(csv_errors)=0 THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('read_complete', true, 'csv_errors', '[]'::jsonb) AS expected,
               jsonb_build_object('read_complete', read_complete, 'csv_errors', csv_errors) AS actual,
               (SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=l.load_id) AS checked_rows,
               NULL::bigint AS bad_rows,
               'При неполном чтении число прочитанных строк — только диагностическое' AS detail
        FROM de7_etl.load_runs l WHERE load_id=%(load_id)s
        """,
    ),
    'manifest_structure': (
        'fields',
        'Manifest читается и содержит все необходимые корректные поля',
        """
        SELECT
            CASE
                WHEN manifest_raw IS NULL THEN 'ERROR'
                WHEN cardinality(manifest_errors) > 0 THEN 'FAIL'
                ELSE 'PASS'
            END AS status,
            '[]'::jsonb AS expected,
            to_jsonb(manifest_errors) AS actual,
            NULL::bigint AS checked_rows,
            NULL::bigint AS bad_rows,
            'Проверка структуры manifest' AS detail
        FROM de7_etl.load_runs
        WHERE load_id = %(load_id)s
        """,
    ),
    'manifest_period': (
        'timestamp',
        'Границы manifest равны выбранным суткам Europe/Berlin',
        """
        SELECT CASE WHEN manifest_period_start IS NULL OR manifest_period_end IS NULL THEN 'NOT_EVALUATED'
                    WHEN manifest_period_start=period_start AND manifest_period_end=period_end THEN 'PASS'
                    ELSE 'FAIL' END AS status,
               jsonb_build_object('start', period_start, 'end', period_end) AS expected,
               jsonb_build_object('start', manifest_period_start, 'end', manifest_period_end) AS actual,
               NULL::bigint AS checked_rows, NULL::bigint AS bad_rows,
               'NULL в фактических границах означает, что поле manifest не преобразовано' AS detail
        FROM de7_etl.load_runs WHERE load_id=%(load_id)s
        """,
    ),
    'source_coverage': (
        'timestamp',
        'complete=true и covered_until>=period_end',
        """
        SELECT CASE WHEN complete IS NULL OR covered_until IS NULL THEN 'NOT_EVALUATED'
                    WHEN complete AND covered_until>=period_end THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('complete', true, 'covered_until_min', period_end) AS expected,
               jsonb_build_object('complete', complete, 'covered_until', covered_until) AS actual,
               NULL::bigint AS checked_rows, NULL::bigint AS bad_rows,
               'Проверяется покрытие источника, а не max(paid_at) или сегодняшняя дата' AS detail
        FROM de7_etl.load_runs WHERE load_id=%(load_id)s
        """,
    ),
    'reference_integrity': (
        'records',
        'Статический справочник содержит непустые активные коды и названия',
        """
        WITH a AS (
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE channel_id ~ '^[[:space:]]*$'
                                          OR channel_name ~ '^[[:space:]]*$') AS bad
            FROM de7_stg.ref_channels
        )
        SELECT CASE WHEN total>0 AND bad=0 THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('bad_records',0,'nonempty_reference',true) AS expected,
               jsonb_build_object('active_records',total,'bad_records',bad) AS actual,
               total AS checked_rows, bad AS bad_rows,
               'Справочник создан setup.sql; загрузчик его не обновляет. Уникальность channel_id обеспечивает PK' AS detail
        FROM a
        """,
    ),
    'manifest_row_count': (
        'records',
        'Число всех прочитанных платежей равно manifest.row_count',
        """
        WITH a AS (SELECT count(*) AS n FROM de7_stg.payments_raw WHERE load_id=%(load_id)s)
        SELECT CASE WHEN NOT l.read_complete OR l.expected_row_count IS NULL THEN 'NOT_EVALUATED'
                    WHEN a.n=l.expected_row_count THEN 'PASS' ELSE 'FAIL' END AS status,
               to_jsonb(l.expected_row_count) AS expected, to_jsonb(a.n) AS actual,
               a.n AS checked_rows, NULL::bigint AS bad_rows,
               'Счётчик включает failed, дубликаты и некорректные строки; при неполном чтении сверка невозможна' AS detail
        FROM de7_etl.load_runs l CROSS JOIN a WHERE l.load_id=%(load_id)s
        """,
    ),
    'manifest_success_sums': (
        'amount_by_currency',
        'Полные суммы succeeded по валютам равны manifest',
        """
        WITH t AS (
            SELECT * FROM de7_stg.payments_typed WHERE load_id=%(load_id)s
        ), a AS (
            SELECT count(*) AS typed_rows,
                   count(*) FILTER (WHERE status IS NULL OR status NOT IN ('succeeded','failed')
                          OR (status='succeeded' AND (amount IS NULL OR amount::text IN ('NaN','Infinity','-Infinity') OR currency IS NULL
                                                     OR currency NOT IN ('EUR','USD')))) AS unusable,
                   count(*) FILTER (WHERE status='succeeded' AND amount IS NOT NULL AND amount::text NOT IN ('NaN','Infinity','-Infinity')
                                          AND currency IN ('EUR','USD')) AS usable
            FROM t
        ), sums AS (
            SELECT currency, sum(amount) AS amount FROM t
            WHERE status='succeeded' AND amount IS NOT NULL AND amount::text NOT IN ('NaN','Infinity','-Infinity') AND currency IN ('EUR','USD')
            GROUP BY currency
        ), x AS (
            SELECT l.*, a.*,
                   (SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=l.load_id) AS raw_rows,
                   (SELECT coalesce(jsonb_object_agg(currency, amount),'{}'::jsonb) FROM sums) AS observed,
                   CASE WHEN l.expected_sums IS NOT NULL THEN
                     (SELECT coalesce(jsonb_object_agg(key, value::numeric),'{}'::jsonb)
                      FROM jsonb_each_text(l.expected_sums)) END AS control
            FROM de7_etl.load_runs l CROSS JOIN a WHERE l.load_id=%(load_id)s
        ), y AS (
            SELECT *, read_complete AND control IS NOT NULL AND unusable=0
                      AND typed_rows=raw_rows AS evaluable FROM x
        )
        SELECT CASE WHEN NOT evaluable THEN 'NOT_EVALUATED'
                    WHEN observed=control THEN 'PASS' ELSE 'FAIL' END AS status,
               control AS expected,
               CASE WHEN evaluable THEN observed ELSE
                    jsonb_build_object('partial_sums',observed, 'unusable_records',unusable,
                                       'raw_rows',raw_rows,'typed_rows',typed_rows) END AS actual,
               usable AS checked_rows, NULL::bigint AS bad_rows,
               CASE WHEN evaluable THEN 'Все исходные succeeded, в том числе отклонённые по другим причинам'
                    ELSE 'Нельзя получить полный контроль: вход/manifest недоступен, потеряны typed-строки или не определены status/currency/amount. Частичная сумма не является результатом сверки'
               END AS detail
        FROM y
        """,
    ),
    'empty_input_confirmed': (
        'records',
        'Пустота подтверждена manifest, полнотой и покрытием',
        """
        WITH a AS (SELECT count(*) AS n FROM de7_stg.payments_raw WHERE load_id=%(load_id)s)
        SELECT CASE WHEN NOT l.read_complete THEN 'NOT_EVALUATED'
                    WHEN a.n>0 THEN 'PASS'
                    WHEN cardinality(l.manifest_errors)>0 THEN 'NOT_EVALUATED'
                    WHEN l.complete IS TRUE AND l.covered_until>=l.period_end
                      AND l.expected_row_count=0 AND l.expected_sums='{}'::jsonb
                      AND l.manifest_period_start=l.period_start AND l.manifest_period_end=l.period_end
                    THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('empty_requires','complete=true, coverage, matching period, row_count=0, sums={}') AS expected,
               jsonb_build_object('read_rows',a.n,'complete',l.complete,'row_count',l.expected_row_count,
                                  'sums',l.expected_sums,'covered_until',l.covered_until) AS actual,
               a.n AS checked_rows, NULL::bigint AS bad_rows,
               'Непустой набор проходит этот условный контроль; другие контроли остаются обязательными' AS detail
        FROM de7_etl.load_runs l CROSS JOIN a WHERE l.load_id=%(load_id)s
        """,
    ),
    'missing_channel_share': (
        'fraction',
        'Доля отсутствующих каналов не больше 0.02; при ненулевой доле WARN',
        """
        WITH a AS (
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE raw_record->>'channel_id' IS NULL
                                      OR raw_record->>'channel_id' ~ '^[[:space:]]*$') AS missing
            FROM de7_stg.payments_raw WHERE load_id=%(load_id)s
        ), x AS (
            SELECT l.*, a.*,
                   CASE WHEN a.n>0 THEN a.missing::numeric/a.n
                        WHEN l.read_complete AND cardinality(l.manifest_errors)=0
                          AND cardinality(l.csv_errors)=0 AND l.complete IS TRUE
                          AND l.covered_until>=l.period_end AND l.expected_row_count=0
                          AND l.expected_sums='{}'::jsonb AND l.manifest_period_start=l.period_start
                          AND l.manifest_period_end=l.period_end THEN 0::numeric END AS share
            FROM de7_etl.load_runs l CROSS JOIN a WHERE l.load_id=%(load_id)s
        )
        SELECT CASE WHEN NOT read_complete OR share IS NULL THEN 'NOT_EVALUATED'
                    WHEN missing=0 THEN 'PASS'
                    WHEN missing::numeric*100 <= n::numeric*2 THEN 'WARN' ELSE 'FAIL' END AS status,
               jsonb_build_object('max_share',0.02) AS expected,
               jsonb_build_object('missing',missing,'read_rows',n,'share',share) AS actual,
               n AS checked_rows, missing AS bad_rows,
               'Знаменатель — все прочитанные записи. Порог — проверка набора, не причина удаления строк' AS detail
        FROM x
        """,
    ),
    'classification_balance': (
        'records',
        'Прочитано=принято+отклонено; все raw имеют typed-строку',
        """
        WITH a AS (
            SELECT (SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=%(load_id)s) AS raw_n,
                   (SELECT count(*) FROM de7_stg.payments_typed WHERE load_id=%(load_id)s) AS typed_n,
                   (SELECT count(*) FROM de7_stg.accepted WHERE load_id=%(load_id)s) AS accepted_n,
                   (SELECT count(*) FROM de7_stg.rejections WHERE load_id=%(load_id)s) AS rejected_n
        )
        SELECT CASE WHEN raw_n=typed_n AND raw_n=accepted_n+rejected_n THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('read_rows',raw_n) AS expected,
               jsonb_build_object('typed',typed_n,'accepted',accepted_n,'rejected',rejected_n,
                                  'classified',accepted_n+rejected_n) AS actual,
               raw_n AS checked_rows, NULL::bigint AS bad_rows,
               'rejections содержит одну строку на исходную запись, не на причину' AS detail FROM a
        """,
    ),
    'prepared_count': (
        'records',
        'После подготовки сохранилось число принятых платежей, включая failed',
        """
        WITH a AS (
            SELECT (SELECT count(*) FROM de7_stg.accepted WHERE load_id=%(load_id)s) AS before_n,
                   (SELECT count(*) FROM de7_stg.payments_prepared WHERE load_id=%(load_id)s) AS after_n
        )
        SELECT CASE WHEN before_n=after_n THEN 'PASS' ELSE 'FAIL' END AS status,
               to_jsonb(before_n) AS expected, to_jsonb(after_n) AS actual,
               before_n AS checked_rows, NULL::bigint AS bad_rows,
               'Базовая линия — принятые typed-записи, а не вся поставка' AS detail FROM a
        """,
    ),
    'join_cardinality': (
        'records',
        'Каждая принятая исходная запись дала ровно одну строку после JOIN',
        """
        WITH multiplicity AS (
            SELECT a.row_no, count(p.prepared_id) AS n
            FROM de7_stg.accepted a
            LEFT JOIN de7_stg.payments_prepared p
                   ON p.load_id=a.load_id AND p.row_no=a.row_no
            WHERE a.load_id=%(load_id)s GROUP BY a.row_no
        ), x AS (
            SELECT count(*) AS total, count(*) FILTER (WHERE n<>1) AS bad,
                   (SELECT count(*) FROM de7_stg.payments_prepared p
                    WHERE p.load_id=%(load_id)s AND NOT EXISTS (
                        SELECT 1 FROM de7_stg.accepted a
                        WHERE a.load_id=p.load_id AND a.row_no=p.row_no)) AS extra
            FROM multiplicity
        )
        SELECT CASE WHEN bad=0 AND extra=0 THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('rows_per_accepted_payment',1,'unexpected_rows',0) AS expected,
               jsonb_build_object('bad_multiplicity_payments',bad,'unexpected_rows',extra) AS actual,
               total AS checked_rows, bad+extra AS bad_rows,
               'Проверка по (load_id,row_no): потеря одной строки не компенсирует размножение другой' AS detail
        FROM x
        """,
    ),
    'prepared_sums': (
        'amount_by_currency',
        'После подготовки сохранены суммы всех принятых платежей по валютам',
        """
        WITH a AS (
            SELECT currency,sum(amount) AS total FROM de7_stg.accepted
            WHERE load_id=%(load_id)s GROUP BY currency
        ), p AS (
            SELECT currency,sum(amount) AS total FROM de7_stg.payments_prepared
            WHERE load_id=%(load_id)s GROUP BY currency
        ), x AS (
            SELECT (SELECT coalesce(jsonb_object_agg(currency,total),'{}'::jsonb) FROM a) AS before_sums,
                   (SELECT coalesce(jsonb_object_agg(currency,total),'{}'::jsonb) FROM p) AS after_sums
        )
        SELECT CASE WHEN before_sums=after_sums THEN 'PASS' ELSE 'FAIL' END AS status,
               before_sums AS expected, after_sums AS actual,
               (SELECT count(*) FROM de7_stg.accepted WHERE load_id=%(load_id)s) AS checked_rows,
               NULL::bigint AS bad_rows,
               'Включает succeeded и failed; EUR и USD не складываются' AS detail FROM x
        """,
    ),
    'aggregate_payment_count': (
        'records',
        'Число succeeded равно сумме payment_count в кандидате витрины',
        """
        WITH x AS (
            SELECT (SELECT count(*) FROM de7_stg.payments_prepared
                    WHERE load_id=%(load_id)s AND status='succeeded') AS before_n,
                   (SELECT coalesce(sum(payment_count),0) FROM de7_stg.i_payments_daily
                    WHERE load_id=%(load_id)s) AS after_n,
                   (SELECT count(*) FROM de7_stg.i_payments_daily WHERE load_id=%(load_id)s) AS groups_n
        )
        SELECT CASE WHEN before_n=after_n THEN 'PASS' ELSE 'FAIL' END AS status,
               to_jsonb(before_n) AS expected,
               jsonb_build_object('sum_payment_count',after_n,'mart_rows',groups_n) AS actual,
               before_n AS checked_rows, NULL::bigint AS bad_rows,
               'Количество строк агрегата — это число групп, а не число платежей' AS detail FROM x
        """,
    ),
    'aggregate_sums': (
        'amount_by_currency',
        'После агрегации сохранены суммы succeeded по валютам',
        """
        WITH p AS (
            SELECT currency,sum(amount) AS total FROM de7_stg.payments_prepared
            WHERE load_id=%(load_id)s AND status='succeeded' GROUP BY currency
        ), m AS (
            SELECT currency,sum(amount_sum) AS total FROM de7_stg.i_payments_daily
            WHERE load_id=%(load_id)s GROUP BY currency
        ), x AS (
            SELECT (SELECT coalesce(jsonb_object_agg(currency,total),'{}'::jsonb) FROM p) AS before_sums,
                   (SELECT coalesce(jsonb_object_agg(currency,total),'{}'::jsonb) FROM m) AS after_sums
        )
        SELECT CASE WHEN before_sums=after_sums THEN 'PASS' ELSE 'FAIL' END AS status,
               before_sums AS expected, after_sums AS actual,
               (SELECT count(*) FROM de7_stg.payments_prepared
                WHERE load_id=%(load_id)s AND status='succeeded') AS checked_rows,
               NULL::bigint AS bad_rows,
               'Контроль каждой валюты отдельно, без добавления failed' AS detail FROM x
        """,
    ),
    'aggregate_groups': (
        'groups',
        'Ключи групп и показатели кандидата совпадают с агрегацией succeeded',
        """
        WITH expected_rows AS (
            SELECT report_date,currency,channel_id,count(*) AS payment_count,sum(amount) AS amount_sum
            FROM de7_stg.payments_prepared WHERE load_id=%(load_id)s AND status='succeeded'
            GROUP BY report_date,currency,channel_id
        ), actual_rows AS (
            SELECT report_date,currency,channel_id,payment_count,amount_sum
            FROM de7_stg.i_payments_daily WHERE load_id=%(load_id)s
        ), missing AS (SELECT * FROM expected_rows EXCEPT ALL SELECT * FROM actual_rows),
           extra AS (SELECT * FROM actual_rows EXCEPT ALL SELECT * FROM expected_rows)
        SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM missing) AND NOT EXISTS (SELECT 1 FROM extra)
                    AND NOT EXISTS (SELECT 1 FROM actual_rows a JOIN de7_etl.load_runs l
                                    ON l.load_id=%(load_id)s WHERE a.report_date<>l.report_date)
                    THEN 'PASS' ELSE 'FAIL' END AS status,
               jsonb_build_object('missing_groups',0,'extra_groups',0,'wrong_dates',0) AS expected,
               jsonb_build_object('missing_groups',(SELECT count(*) FROM missing),
                                  'extra_groups',(SELECT count(*) FROM extra),
                                  'wrong_dates',(SELECT count(*) FROM actual_rows a JOIN de7_etl.load_runs l
                                                 ON l.load_id=%(load_id)s WHERE a.report_date<>l.report_date)) AS actual,
               (SELECT count(*) FROM expected_rows) AS checked_rows,
               NULL::bigint AS bad_rows,
               'Проверяются дата, валюта, канал и оба показателя; failed не входят в агрегат' AS detail
        """,
    ),
}


##############################
#Проверка обязательных полей
def validate_fields(obj, fieldname, fieldtype):
    if not isinstance(obj, dict):
        return None, f"{obj}: Ожидался объект"
    
    if fieldname not in obj:
        return None, f"Отсутствует поле: {fieldname}"
    
    value = obj[fieldname]
    if value is None:
        return None, f"{fieldname}: значение отсутствует"
    
    if not isinstance(value, fieldtype): 
        return None, f"{fieldname}: ожидался тип {fieldtype.__name__}"
    
    return value, None

#Приведение данных
#Строка
def required_text(value, field): 
    if not isinstance(value, str): 
        return None, f"{field}: ожидалась строка" 
    
    if value.strip() == "": 
        return None, f"{field}: пустое значение" 
    return value, None


#Преобразование суммы в Decimal
def parse_amount(value, field, allow_zero=False): 
    if not isinstance(value, str): 
        return None, f"{field}: ожидалась строка" 
    if value.strip() == "":
        return None, f"{field}: пустое значение"

    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None, f"{field}: некорректное десятичное число"

    if not amount.is_finite():
        return None, f"{field}: требуется конечное число"

    if allow_zero:
        if amount < 0:
            return amount, f"{field}: сумма не может быть отрицательной"
    elif amount <= 0:
        return amount, f"{field}: сумма должна быть больше нуля"

    if amount.as_tuple().exponent < -2:
        return amount, f"{field}: больше двух знаков после точки"
    
    return amount, None


def parse_datetime(value, field):
    value, error = required_text(value, field)
    if error:
        return None, error
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None, f"{field}: некорректная дата"
    
    if result.utcoffset() is None:
        return None, f"{field}: не указано смещение UTC"
    
    return result, None


#манифест
def validate_manifest(path):
    config = {
        "source_version": None,         #snapshot_id из манифеста
        "source_system": None,
        "manifest_period_start": None, 
        "manifest_period_end": None,
        "complete": None, 
        "covered_until": None,
        "expected_row_count": None, 
        "expected_sums": None, 
        "manifest_errors": [],
    }

    errors = config["manifest_errors"]

    try:
        with path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
    except json.JSONDecodeError as error:
        errors.append(f"manifest: некорректный JSON ({error.msg})")
        return config
    
    if not isinstance(manifest, dict):
        errors.append("manifest должен быть JSON-объектом")
        return config

    value, error = validate_fields(manifest, "snapshot_id", str)
    if not error:
        value, error = required_text(value, "snapshot_id")
    if error:
        errors.append(error)
    else:
        config["source_version"] = value
    
    value, error = validate_fields(manifest, "source_system", str)
    if not error:
        value, error = required_text(value, "source_system")
    if error:
        errors.append(error)
    else:
        config["source_system"] = value
    
    for field, target in (("period_start", "manifest_period_start"),
                          ("period_end", "manifest_period_end"),
                          ("covered_until", "covered_until")):
        if field not in manifest:
            errors.append(f"{field}: поле отсутствует")
            continue
        value, error = parse_datetime(manifest[field], field)
        if error:
            errors.append(error)
        else:
            config[target] = value

    start = config["manifest_period_start"]
    end = config["manifest_period_end"]
    if start is not None and end is not None and start >= end:
        errors.append("period_start должен быть раньше period_end")

    complete = manifest.get("complete")
    if type(complete) is bool:
        config["complete"] = complete
    else:
        errors.append("complete: требуется логическое true/false")

    row_count = manifest.get("row_count")
    if type(row_count) is int and 0 <= row_count <= 9223372036854775807:
        config["expected_row_count"] = row_count
    else:
        errors.append("row_count: требуется неотрицательное целое bigint")

    sums = manifest.get("succeeded_amount_by_currency")
    if not isinstance(sums, dict):
        errors.append("succeeded_amount_by_currency: требуется объект")
    else:
        valid_sums = {}
        sums_ok = True
        for currency, raw_amount in sums.items():
            if currency not in ("EUR", "USD"):
                errors.append(f"succeeded_amount_by_currency: неизвестная валюта {currency}")
                sums_ok = False
                continue

            amount, error = parse_amount(raw_amount, f"{currency}_expected_amount", allow_zero=True)
            if error:
                errors.append(error)
                sums_ok = False
            else:
                valid_sums[currency] = str(amount)

        if sums_ok:
            config["expected_sums"] = valid_sums

    return config

#проверка атрибутов платежа
def validate_payment(raw):
    result = {
        "source_system": raw.get("source_system"),
        "payment_id": raw.get("payment_id"),
        "order_id": raw.get("order_id"),
        "currency": raw.get("currency"),
        "status": raw.get("status"),
        "paid_at": None,
        "paid_at_error": None,
        "amount": None,
        "amount_scale": None,
        "amount_error": None,
        "channel_id": None,
    }
    #даты
    result["paid_at"], result["paid_at_error"] = parse_datetime(
        raw.get("paid_at"), "paid_at"
    )

    #сумма
    raw_amount = raw.get("amount")
    if not isinstance(raw_amount, str) or raw_amount.strip() == "":
        result["amount_error"] = "amount: значение отсутствует"
    else:
        try:
            amount = Decimal(raw_amount)
        except InvalidOperation:
            result["amount_error"] = "amount: некорректное число"
        else:
            if not amount.is_finite():
                result["amount_error"] = "amount: число не является конечным"
            else:
                result["amount"] = amount
                result["amount_scale"] = max(0, -amount.as_tuple().exponent)

    channel = raw.get("channel_id")

    if isinstance(channel, str) and channel.strip():
        result["channel_id"] = channel

    return result


##################
#Проверка заголовков
def validate_header(fieldnames): 
    if fieldnames is None:
        raise ValueError("Файл пуст: заголовок отсутствует") 
    if sorted(fieldnames) != sorted(CSV_FIELDS): 
        raise ValueError(f"Неверный заголовок: {fieldnames}")

def validate_row_shape(row): 
    errors = []
    if None in row: 
        errors.append("В строке больше полей, чем в заголовке") 
    for field in CSV_FIELDS: 
        if row.get(field) is None: 
            errors.append(f"В строке отсутствует значение столбца {field}")
    return "; ".join(errors) if errors else None

#чтение csv
def payments_raw(manifest_path):
    source_path = Path(manifest_path).parent / "payments.csv"
    if not source_path.is_file():
        raise FileNotFoundError(f"Исходный CSV-файл не найден: {source_path.name}")

    raw_bytes = source_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8")
    lines = text.splitlines(keepends=True)
    records = []

    with source_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=",", strict=True)
        validate_header(reader.fieldnames)

        previous_line = reader.line_num
        for row_no, row in enumerate(reader, 1):
            end_line = reader.line_num
            records.append({
                "row_no": row_no,
                "file_line_no": previous_line + 1,
                "raw_csv": "".join(lines[previous_line:end_line]).rstrip("\r\n"),
                "raw_record": {field: row.get(field) for field in CSV_FIELDS},
                "structure_error": validate_row_shape(row),
            })
            previous_line = end_line

    return records, file_hash


#Записать исходные строки и сведения manifest в PostgreSQL
def stage_payments(cur, load_id, records, file_hash):
    load_id = UUID(str(load_id))
    cur.execute(
        """
        SELECT read_complete, read_rows, staging_hash
        FROM de7_etl.load_runs
        WHERE load_id = %s
        FOR UPDATE
        """,
        (load_id,),
    )
    saved = cur.fetchone()
    if saved is None:
        raise ValueError(f"Не зарегистрирован load_id={load_id}")

    read_complete, read_rows, saved_hash = saved
    if read_complete:
        if read_rows == len(records) and saved_hash == file_hash:
            return read_rows
        raise ValueError("Повторная попытка получила другой CSV")

    cur.execute("SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=%s", (load_id,))
    if cur.fetchone()[0] != 0:
        raise RuntimeError("Обнаружена незавершенная запись staging")

    rows = []
    for record in records:
        rows.append((
            load_id,
            record["row_no"],
            record["file_line_no"],
            record["raw_csv"],
            Jsonb(record["raw_record"]),
            record["structure_error"],
        ))

    if rows:
        cur.executemany(
            """
            INSERT INTO de7_stg.payments_raw
                (load_id, row_no, file_line_no, raw_csv, raw_record, structure_error)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )

    cur.execute("SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=%s", (load_id,))
    staged = cur.fetchone()[0]
    if staged != len(records):
        raise RuntimeError("Количество строк staging не совпало с числом прочитанных")

    cur.execute(
        """
        UPDATE de7_etl.load_runs
        SET read_rows=%s, read_complete=true, staging_hash=%s, state='staged'
        WHERE load_id=%s
        """,
        (staged, file_hash, load_id),
    )
    return staged


def calculate_hashes(cur, load_id):
    load_id = UUID(str(load_id))
    cur.execute(
        """SELECT row_no, report_date, source_system, payment_id, order_id,
                  paid_at, amount, currency, status, channel_id, channel_name
           FROM de7_stg.payments_prepared
           WHERE load_id=%s ORDER BY row_no, prepared_id""",
        (load_id,),
    )
    prepared = cur.fetchall()
    prepared_text = json.dumps(prepared, ensure_ascii=False, default=str)
    prepared_hash = hashlib.sha256(prepared_text.encode("utf-8")).hexdigest()

    cur.execute(
        """SELECT report_date, currency, channel_id, payment_count, amount_sum
           FROM de7_stg.i_payments_daily
           WHERE load_id=%s ORDER BY report_date, currency, channel_id""",
        (load_id,),
    )
    candidate = cur.fetchall()
    candidate_text = json.dumps(candidate, ensure_ascii=False, default=str)
    candidate_hash = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    return prepared_hash, candidate_hash


#Преобразовать значения, сохранить отклонения, выполнить JOIN и построить агрегат
def prepare_payments(cur, load_id):
    load_id = UUID(str(load_id))
    cur.execute(
        """
        SELECT state, source_system, report_date
        FROM de7_etl.load_runs
        WHERE load_id=%s
        FOR UPDATE
        """,
        (load_id,),
    )
    saved = cur.fetchone()
    if saved is None:
        raise ValueError(f"Неизвестная загрузка {load_id}")

    state, source_system, report_date = saved
    cur.execute("SELECT prepared_hash, read_complete FROM de7_etl.load_runs WHERE load_id=%s", (load_id,))
    prepared_hash, read_complete = cur.fetchone()
    if prepared_hash is not None:
        cur.execute("SELECT prepared_rows FROM de7_etl.load_runs WHERE load_id=%s", (load_id,))
        return cur.fetchone()[0]
    if not read_complete or source_system is None:
        raise ValueError("Подготовка доступна после staging и проверки manifest")

    cur.execute(
        """
        SELECT row_no, raw_record
        FROM de7_stg.payments_raw
        WHERE load_id=%s
        ORDER BY row_no
        """,
        (load_id,),
    )
    typed_rows = []
    
    for row_no, raw_record in cur.fetchall():
        typed = validate_payment(raw_record)
        typed_rows.append((
            load_id, 
            row_no, 
            typed["source_system"], 
            typed["payment_id"],
            typed["order_id"], 
            typed["paid_at"], 
            typed["paid_at_error"],
            typed["amount"], 
            typed["amount_scale"], 
            typed["amount_error"],
            typed["currency"], 
            typed["status"], 
            typed["channel_id"],
        ))

    if typed_rows:
        cur.executemany(
            """
            INSERT INTO de7_stg.payments_typed (
                load_id, row_no, source_system, payment_id, order_id,
                paid_at, paid_at_error, amount, amount_scale, amount_error,
                currency, status, channel_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            typed_rows,
        )
    #отклонения
    cur.execute(
        """
        WITH bad AS (
            SELECT load_id, row_no,
                   array_agg(rule_name || ': ' || detail ORDER BY rule_name) AS reasons
            FROM de7_etl.row_checks
            WHERE load_id=%s AND status='FAIL'
            GROUP BY load_id, row_no
        )
        INSERT INTO de7_stg.rejections
            (load_id, row_no, file_line_no, raw_csv, raw_record, reasons)
        SELECT r.load_id, r.row_no, r.file_line_no, r.raw_csv, r.raw_record,
               bad.reasons
        FROM de7_stg.payments_raw r
        INNER JOIN bad ON bad.load_id=r.load_id AND bad.row_no=r.row_no
        """,
        (load_id,),
    )

    #принятые строки + справочник
    cur.execute(
        """
        INSERT INTO de7_stg.payments_prepared (
            load_id, row_no, report_date, source_system, payment_id, order_id,
            paid_at, amount, currency, status, channel_id, channel_name
        )
        SELECT t.load_id, t.row_no, %s, t.source_system, t.payment_id, t.order_id,
               t.paid_at, t.amount, t.currency, t.status,
               COALESCE(t.channel_id, 'unknown'),
               COALESCE(c.channel_name, 'Неизвестный канал')
        FROM de7_stg.accepted t
        LEFT JOIN de7_stg.ref_channels c
          ON c.channel_id=t.channel_id
        WHERE t.load_id=%s
        """,
        (report_date, load_id),
    )

    #преддварительная группировка принятых
    cur.execute(
        """
        INSERT INTO de7_stg.i_payments_daily
            (load_id, report_date, currency, channel_id, payment_count, amount_sum)
        SELECT load_id, report_date, currency, channel_id,
               count(*), sum(amount)
        FROM de7_stg.payments_prepared
        WHERE load_id=%s AND status='succeeded'
        GROUP BY load_id, report_date, currency, channel_id
        """,
        (load_id,),
    )

    cur.execute("SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=%s", (load_id,))
    read_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM de7_stg.rejections WHERE load_id=%s", (load_id,))
    rejected_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM de7_stg.payments_prepared WHERE load_id=%s", (load_id,))
    prepared_rows = cur.fetchone()[0]
    accepted_rows = read_rows - rejected_rows

    if len(typed_rows) != read_rows:
        raise RuntimeError("Количество преобразованных строк не совпадает с raw")

    prepared_hash, candidate_hash = calculate_hashes(cur, load_id)
    cur.execute(
        """
        UPDATE de7_etl.load_runs
        SET accepted_rows=%s, rejected_rows=%s, prepared_rows=%s,
            prepared_hash=%s, candidate_hash=%s, state='prepared'
        WHERE load_id=%s
        """,
        (accepted_rows, rejected_rows, prepared_rows, prepared_hash, candidate_hash, load_id),
    )

    return prepared_rows


def save_event(conn, load_id, task_id, attempt, status, counters=None, reason=None):
    load_id = UUID(str(load_id))
    with conn.transaction():
        conn.execute(
            """INSERT INTO de7_etl.load_events
               (load_id,run_id,report_date,period_start,period_end,
                task_id,attempt,stage,event_status,counters,reason)
               SELECT load_id,run_id,report_date,period_start,period_end,
                      %s,%s,%s,%s,%s,%s
               FROM de7_etl.load_runs WHERE load_id=%s""",
            (task_id, attempt, task_id, status,
             Jsonb(counters or {}), reason, load_id),
        )


#sql-проверки и запись результатов
def check_quality(conn, load_id, group, attempt):
    load_id = UUID(str(load_id))
    if group not in ("rows", "totals"):
        raise ValueError("group должен быть rows или totals")

    task_id = "check_rows" if group == "rows" else "check_totals"
    required_rules = ROW_RULES if group == "rows" else TOTAL_RULES

    load = conn.execute(
        """SELECT run_id, source_version, period_start, period_end,
                  read_complete, prepared_hash
           FROM de7_etl.load_runs WHERE load_id=%s""", (load_id,)
    ).fetchone()
    if load is None or load[5] is None:
        raise ValueError("Сначала нужно подготовить платежи")
    run_id, source_version, start, end, read_complete, prepared_hash = load

    sql_rules = TOTAL_SQL_RULES if group == "totals" else {}
    row_summaries = {}
    raw_count = None

    if group == "rows":
        raw_count = conn.execute(
            "SELECT count(*) FROM de7_stg.payments_raw WHERE load_id=%s", (load_id,)
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT rule_name,
                      count(*) AS total,
                      count(*) FILTER (WHERE status='FAIL') AS failed,
                      count(*) FILTER (WHERE status='NOT_EVALUATED') AS skipped,
                      string_agg(DISTINCT detail, '; ')
                          FILTER (WHERE status='NOT_EVALUATED') AS explanation
               FROM de7_etl.row_checks WHERE load_id=%s
               GROUP BY rule_name""", (load_id,)
        ).fetchall()
        row_summaries = {row[0]: row[1:] for row in rows}

    result_ids = []
    technical_errors = []

    for name in required_rules:
        expected = None
        actual = None
        checked_rows = None
        bad_rows = None
        detail = ""
        unit = "records"
        severity = "WARNING" if name == "missing_channel_share" else "CRITICAL"

        if group == "rows":
            if name not in row_summaries and raw_count > 0:
                status = "ERROR"
                detail = "Не удалось получить результат обязательной построчной проверки"
                technical_errors.append(name)
            else:
                count, bad, skipped, why = row_summaries.get(name, (0, 0, 0, None))
                if not read_complete:
                    status = "NOT_EVALUATED"
                    detail = "CSV прочитан не полностью"
                elif bad:
                    status = "FAIL"
                    detail = f"Нарушений: {bad}; непроверенных строк: {skipped}"
                elif skipped:
                    status = "NOT_EVALUATED"
                    detail = f"Невозможно проверить {skipped} записей: {why or 'нет исходных значений'}"
                else:
                    status = "PASS"
                    detail = "Нарушений нет"
                expected = json.dumps({"bad_rows": 0})
                actual = json.dumps({"bad_rows": bad, "not_evaluated": skipped})
                checked_rows = count - skipped
                bad_rows = bad

        else:
            if name not in sql_rules:
                status = "ERROR"
                detail = "Обязательная проверка отсутствует в TOTAL_SQL_RULES"
                technical_errors.append(name)
            else:
                unit, description, query = sql_rules[name]
                try:
                    with conn.transaction():
                        result = conn.execute(
                            "SELECT q.status,q.expected::text,q.actual::text,"
                            "q.checked_rows,q.bad_rows,q.detail FROM (\n" + query + "\n) q",
                            {"load_id": load_id},
                        ).fetchone()
                        if result is None:
                            raise ValueError("SQL-проверка не вернула результата")
                    status, expected, actual, checked_rows, bad_rows, detail = result
                    if status not in ("PASS", "WARN", "FAIL", "NOT_EVALUATED", "ERROR"):
                        raise ValueError("SQL-проверка вернула неизвестный статус")
                    if status == "ERROR":
                        technical_errors.append(name)
                except Exception as error:
                    status = "ERROR"
                    expected = None
                    actual = json.dumps({
                        "exception": type(error).__name__,
                        "sqlstate": getattr(error, "sqlstate", None),
                    })
                    detail = "Техническая ошибка SQL-проверки"
                    technical_errors.append(name)

        with conn.transaction():
            result_id = conn.execute(
                """
                INSERT INTO de7_etl.dq_results
                    (rule_name,rule_version,load_id,source_version,run_id,task_id,
                    attempt,period_start,period_end,expected,actual,unit,
                    severity,status,checked_rows,bad_rows,detail)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                           %s,%s,%s,%s,%s,%s)
                RETURNING result_id
                """,
                (name, RULE_VERSION, load_id, source_version, run_id, task_id,
                 attempt, start, end, expected, actual, unit,
                 severity, status, checked_rows, bad_rows, detail),
            ).fetchone()[0]
            result_ids.append(result_id)

    if technical_errors:
        raise RuntimeError(f"Технические ошибки проверок {task_id}: {', '.join(technical_errors)}")

    return {
            "load_id": str(load_id), 
            "task_id": task_id,
            "attempt": attempt, 
            "result_ids": result_ids
        }




#решение о публикациии
def quality_gate(conn, load_id, rows_result, totals_result):
    load_id = UUID(str(load_id))
    load = conn.execute(
        """
        SELECT run_id,source_version,period_start,period_end,prepared_hash,candidate_hash
        FROM de7_etl.load_runs WHERE load_id=%s
        """, (load_id,)
    ).fetchone()
    if load is None:
        raise ValueError("Загрузка не найдена")
    run_id, source_version, start, end, prepared_hash, candidate_hash = load

    references = (rows_result, totals_result)
    ids = rows_result.get("result_ids", []) + totals_result.get("result_ids", [])
    errors = []

    if rows_result.get("load_id") != str(load_id) or totals_result.get("load_id") != str(load_id):
        errors.append("Получена ссылка на проверки другого load_id")
    if rows_result.get("task_id") != "check_rows" or totals_result.get("task_id") != "check_totals":
        errors.append("Неправильная группа проверок")

    expected_names = ({("check_rows", rule) for rule in ROW_RULES}
                      | {("check_totals", rule) for rule in TOTAL_RULES})

    records = []
    if ids:
        records = conn.execute(
            """
            SELECT result_id,load_id,run_id,source_version,task_id,
                attempt,rule_name,rule_version,period_start,period_end,status
            FROM de7_etl.dq_results WHERE result_id=ANY(%s)
            """, (ids,)
        ).fetchall()

    if len(ids) != len(expected_names) or len(records) != len(ids):
        errors.append("Не хватает результатов обязательных проверок")
    found_names = {(row[4], row[6]) for row in records}
    if found_names != expected_names or len(found_names) != len(records):
        errors.append("Неполный или повторяющийся список правил")

    warn = 0
    for r in records:
        result_id, result_load, result_run, result_version, task, attempt, name, version, r_start, r_end, status = r
        if (result_load != load_id or result_run != run_id
                or result_version != source_version or version != RULE_VERSION
                or r_start != start or r_end != end):
            errors.append(f"Результат {result_id} не относится к этой загрузке")
        for ref in references:
            if task == ref.get("task_id") and attempt != ref.get("attempt"):
                errors.append(f"Результат {result_id} относится к другой попытке")
        if status == "WARN" and name =="missing_channel_share":
            warn += 1
        elif status != "PASS":
            errors.append(f"{name}: {status}")

    with conn.transaction():
        cur = conn.cursor()
        current_prepared, current_candidate = calculate_hashes(cur, load_id)
        if (prepared_hash is None or candidate_hash is None
                or prepared_hash != current_prepared or candidate_hash != current_candidate):
            errors.append("Подготоввленный набор изменился после подготовки")

        if errors:
            res = []
            for e in errors:
                if e not in res:
                    res.append(e)
            reason = "; ".join(res)
            cur.execute(
                """
                UPDATE de7_etl.load_runs
                SET state='blocked',last_error=%s WHERE load_id=%s
                """,
                (reason[:2000], load_id),
            )
            cur.execute("DELETE FROM de7_etl.publication_approvals WHERE load_id=%s", (load_id,))

        else:
            cur.execute(
                """
                INSERT INTO de7_etl.publication_approvals
                   (load_id,run_id,prepared_hash,candidate_hash,dq_result_ids)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (load_id) DO UPDATE SET
                     run_id=EXCLUDED.run_id,
                     prepared_hash=EXCLUDED.prepared_hash,
                     candidate_hash=EXCLUDED.candidate_hash,
                     dq_result_ids=EXCLUDED.dq_result_ids,
                     approved_at=clock_timestamp()
                """,
                (load_id, run_id, prepared_hash, candidate_hash, ids),
            )
            cur.execute("UPDATE de7_etl.load_runs SET state='checked',last_error=NULL WHERE load_id=%s", (load_id,),)
            reason = None

    if errors:
        raise ValueError(f"Публикация запрещена: {reason}")
    return {"load_id": str(load_id), "warnings": warn}


#заменить результат выбранной даты
def publish_payments(conn, load_id, fail_inside_publication=False):
    load_id = UUID(str(load_id))
    with conn.transaction():
        cur = conn.cursor()
        cur.execute(
            """
            SELECT report_date,run_id,source_version,period_start,period_end,
                      prepared_hash,candidate_hash,published_at
            FROM de7_etl.load_runs WHERE load_id=%s FOR UPDATE
            """, (load_id,)
        )
        load = cur.fetchone()
        if load is None:
            raise ValueError("Загрузка не найдена")
        report_date, run_id, source_version, start, end, prepared_hash, candidate_hash, published_at = load

        if published_at is not None:
            return 0

        cur.execute(
            """
            SELECT run_id,prepared_hash,candidate_hash,dq_result_ids
            FROM de7_etl.publication_approvals WHERE load_id=%s FOR SHARE
            """,
            (load_id,),
        )
        approval = cur.fetchone()
        if approval is None:
            raise ValueError("У загрузки нет разрешения на публикацию")
        a_run, a_prepared, a_candidate, dq_ids = approval
        current_prepared, current_candidate = calculate_hashes(cur, load_id)
        if (a_run != run_id or a_prepared != prepared_hash or a_candidate != candidate_hash
                or a_prepared != current_prepared or a_candidate != current_candidate):
            raise ValueError("Данные изменились после выдачи разрешения")

        cur.execute(
            """
            SELECT count(*),
                count(*) FILTER (WHERE load_id=%s AND run_id=%s AND
                                    rule_version=%s AND period_start=%s AND period_end=%s AND
                                    (
                                        status='PASS' OR (status='WARN' AND rule_name='missing_channel_share')
                                    )
                                )
            FROM de7_etl.dq_results WHERE result_id=ANY(%s)
            """,
            (load_id, run_id, RULE_VERSION, start, end, dq_ids),
        )
        count_all, count_ok = cur.fetchone()
        needed = len(ROW_RULES) + len(TOTAL_RULES)
        if len(dq_ids) != needed or count_all != needed or count_ok != needed:
            raise ValueError("Разрешение не подтверждено полным набором DQ")

        cur.execute(
            """
            SELECT count(*) FROM de7_stg.i_payments_daily
            WHERE load_id=%s AND report_date<>%s
            """, (load_id, report_date),
        )
        if cur.fetchone()[0] != 0:
            raise ValueError("В кандидате есть записи другой отчётной даты")
        
        cur.execute("DELETE FROM de7_core.payments_daily WHERE report_date=%s", (report_date,))

        if fail_inside_publication:
            raise RuntimeError("Тестовый сбой после DELETE, откатывается транзакция")

        cur.execute(
            """
            INSERT INTO de7_core.payments_daily
                (report_date,currency,channel_id,payment_count,amount_sum,load_id,source_version,run_id,period_start,period_end)
            SELECT report_date,currency,channel_id,payment_count,amount_sum,%s,%s,%s,%s,%s
            FROM de7_stg.i_payments_daily WHERE load_id=%s
            """,
            (load_id, source_version, run_id, start, end, load_id),
        )
        published_groups = cur.rowcount

        cur.execute(
            """
            WITH expected AS (
                SELECT report_date,currency,channel_id,payment_count,amount_sum
                FROM de7_stg.i_payments_daily WHERE load_id=%s
            ), actual AS (
                SELECT report_date,currency,channel_id,payment_count,amount_sum
                FROM de7_core.payments_daily WHERE report_date=%s
            ), differences AS (
                (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
                UNION ALL
                (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
            )
            SELECT count(*) FROM differences
            """, (load_id, report_date),
        )
        if cur.fetchone()[0] != 0:
            raise RuntimeError("Опубликованная витрина не совпала с кандидатом")

        cur.execute(
            """
            UPDATE de7_etl.load_runs
            SET state='published',published_at=clock_timestamp(),
                finished_at=clock_timestamp(),last_error=NULL
            WHERE load_id=%s
            """, (load_id,),
        )

    return published_groups



#последовательно вызвать этапы при запуске из терминала
def run_load(conn, manifest_path=None, report_date=None, resume_load=None, check_only=False, attempt=None,
             fail_after_staging=False, fail_inside_publication=False):
    if resume_load is None:
        if manifest_path is None or report_date is None:
            raise ValueError("Для нового запуска нужны manifest_path и report_date")
        manifest_path = Path(manifest_path)
        if isinstance(report_date, str):
            report_date = date.fromisoformat(report_date)
        
        period_start = datetime.combine(report_date, time.min, ZoneInfo("Europe/Berlin"))
        period_end = datetime.combine(report_date + timedelta(days=1), time.min, ZoneInfo("Europe/Berlin"))
        load_id = uuid4()
        #добавила manual чтобы отличать потом от плановых запусков
        run_id = f"manual__{uuid4().hex}"
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO de7_etl.load_runs
                    (load_id,run_id,report_date,source_path,period_start,period_end)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (load_id, run_id, report_date, str(manifest_path.parent), period_start, period_end),
            )
    else:
        load_id = resume_load if isinstance(resume_load, UUID) else UUID(str(resume_load))
        load = conn.execute("SELECT source_path,report_date FROM de7_etl.load_runs WHERE load_id=%s", (load_id,),).fetchone()
        if load is None:
            raise ValueError("Невозможно продолжить: load_id не найден")
        manifest_path = Path(load[0]) / "manifest.json"
        report_date = load[1]

    if attempt is None:
        attempt = conn.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM de7_etl.dq_results WHERE load_id=%s", (load_id,),).fetchone()[0]

    logger.info("event=load_started load_id=%s date=%s attempt=%s",
                load_id, report_date, attempt)
    stage = "manifest"

    try:
        load = conn.execute("SELECT read_complete,prepared_hash,published_at FROM de7_etl.load_runs WHERE load_id=%s",(load_id,),).fetchone()
        if load[2] is not None:
            return {"load_id": str(load_id), "state": "published", "reused": True}

        if not load[0]:
            config = validate_manifest(manifest_path)
            try:
                manifest_raw = Path(manifest_path).read_text(encoding="utf-8")
            except UnicodeError:
                manifest_raw = None

            with conn.transaction():
                conn.execute(
                    """
                    UPDATE de7_etl.load_runs 
                    SET source_version=%s,source_system=%s,
                        manifest_period_start=%s,manifest_period_end=%s,
                        complete=%s,covered_until=%s,expected_row_count=%s,
                        expected_sums=%s,manifest_raw=%s,manifest_errors=%s
                    WHERE load_id=%s
                    """,
                    (config["source_version"], config["source_system"],
                     config["manifest_period_start"], config["manifest_period_end"],
                     config["complete"], config["covered_until"], config["expected_row_count"],
                     Jsonb(config["expected_sums"]) if config["expected_sums"] is not None else None, 
                     manifest_raw, config["manifest_errors"], load_id),
                )

            if config["manifest_errors"]:
                is_json_error = any("некорректный JSON" in err or "UTF-8" in err for err in config["manifest_errors"])
                with conn.transaction():
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO de7_etl.dq_results
                            (rule_name,rule_version,load_id,source_version,run_id,task_id,attempt,period_start,period_end,expected,actual,unit,severity,status,detail)
                        SELECT 'manifest_structure',%s,load_id,source_version,run_id,
                            'check_totals',%s,period_start,period_end,
                            '[]'::jsonb,%s::jsonb,'fields','CRITICAL',%s,
                            'Нарушение структуры или чтения manifest'
                        FROM de7_etl.load_runs WHERE load_id=%s
                        """,
                        (RULE_VERSION, attempt, json.dumps(config["manifest_errors"], ensure_ascii=False),
                         "ERROR" if is_json_error else "FAIL", load_id),
                    )
                raise ValueError("Manifest не прошёл проверку; ошибки сохранены в load_runs")

            stage = "csv"
            try:
                records, file_hash = payments_raw(manifest_path)
            except (ValueError, csv.Error, UnicodeError) as error:
                with conn.transaction():
                    conn.execute("UPDATE de7_etl.load_runs SET csv_errors=%s WHERE load_id=%s", ([f"CSV_ERROR: {type(error).__name__}"], load_id),)
                raise

            stage = "staging"
            with conn.transaction():
                cur = conn.cursor()
                count = stage_payments(cur, load_id, records, file_hash)
            save_event(conn, load_id, "stage", attempt, "SUCCESS", {"staged_rows": count})

            if fail_after_staging:
                raise RuntimeError("Тестовый сбой после COMMIT staging")

        stage = "prepare"
        with conn.transaction():
            cur = conn.cursor()
            prepared_count = prepare_payments(cur, load_id)
        save_event(conn, load_id, "prepare", attempt, "SUCCESS", {"prepared_rows": prepared_count})

        stage = "check_rows"
        row_results = check_quality(conn, load_id, "rows", attempt)
        save_event(conn, load_id, "check_rows", attempt,"SUCCESS", {"check_count": len(row_results["result_ids"])})

        stage = "check_totals"
        total_results = check_quality(conn, load_id, "totals", attempt)
        save_event(conn, load_id, "check_totals", attempt,"SUCCESS", {"check_count": len(total_results["result_ids"])})

        stage = "quality_gate"
        approval = quality_gate(conn, load_id, row_results, total_results)
        event_status = "WARN" if approval["warnings"] else "SUCCESS"
        save_event(conn, load_id, "quality_gate", attempt, event_status, {"warnings": approval["warnings"]}, f"Результаты DQ для load_id={load_id}")

        if check_only:
            return {
                        "load_id": str(load_id), 
                        "state": "checked", 
                        "prepared_rows": prepared_count
                    }

        stage = "publish"
        published_groups = publish_payments(conn, load_id, fail_inside_publication)
        save_event(conn, load_id, "publish", attempt, "SUCCESS", {"published_groups": published_groups})
        return {
                    "load_id": str(load_id), 
                    "state": "published", 
                    "prepared_rows": prepared_count, 
                    "published_groups": published_groups
                }

    except Exception as error:
        msg = (str(error) if stage == "quality_gate" else f"{type(error).__name__} на этапе {stage}")
        with conn.transaction():
            conn.execute(
                """
                UPDATE de7_etl.load_runs
                SET state=CASE WHEN state='blocked' THEN state ELSE 'failed' END,
                    last_error=%s,
                    finished_at=clock_timestamp() 
                WHERE load_id=%s
                """,
                (msg[:2000], load_id),
            )
        save_event(conn, load_id, stage, attempt, "FAILED", reason=msg[:2000])
        logger.error("event=load_failed load_id=%s stage=%s type=%s", load_id, stage, type(error).__name__)
        raise


#Основной код
def main():
    parser = argparse.ArgumentParser() 

    parser.add_argument("--manifest", type=Path, help="Путь к manifest.json")
    parser.add_argument("--date", help="Отчётная дата YYYY-MM-DD")
    parser.add_argument("--resume-load", help="Продолжить сохраненую загрузку по load_id")
    parser.add_argument("--attempt", type=int, help="Номер попытки")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--fail-after-staging", action="store_true")
    parser.add_argument("--fail-inside-publication", action="store_true")
    
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
        with psycopg.connect(**conn_params, autocommit=True) as conn:
            conn.execute("SET TIME ZONE 'UTC'")  # одинаковый формат timestamps для отпечатков
            result = run_load(
                conn,
                manifest_path=args.manifest,
                report_date=args.date,
                resume_load=args.resume_load,
                check_only=args.check_only,
                attempt=args.attempt,
                fail_after_staging=args.fail_after_staging,
                fail_inside_publication=args.fail_inside_publication,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        logger.exception("event=program_failed")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())