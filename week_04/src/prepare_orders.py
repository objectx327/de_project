import argparse
import json 
import csv
import re 
import logging
from pathlib import Path 
from decimal import Decimal
from uuid import uuid4

#########
#Параметры
#########
base_dir = Path(__file__).resolve().parent.parent 
data_dir = base_dir / "data"

######
#Настройка логирования
####
logger = logging.getLogger(__name__)

def configure_logging():
    log_dir = base_dir / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=log_dir / "load.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

HEADERS = [
    "order_id", 
    "line_no", 
    "customer_id", 
    "sku", 
    "quantity", 
    "unit_price", 
    "line_amount"
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

#Проверка строк - непустые, крайние пробелы удаляются
def required_text(value, field): 
    if not isinstance(value, str): 
        raise ValueError(f"{field}: ожидалась строка") 
    
    result = value.strip() 
    if result == "": 
        raise ValueError(f"{field}: пустое значение") 
    return result

#Преобразование суммы в Decimal
def parse_unit_price(value): 
    text = required_text(value, "unit_price") 
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", text) is None: 
        raise ValueError("unit_price: неверный формат") 
    
    amount = Decimal(text.replace(",", ".")) 
    if amount > Decimal("9999999999.99"): 
        raise ValueError("unit_price: превышен диапазон") 
    if amount <= Decimal("0"): 
        raise ValueError("unit_price: сумма меньше либо равна нулю") 
    
    return amount.quantize(Decimal("0.01"))

#Целые числа
def positive_int(value, field):
    if type(value) is not int: 
        raise ValueError(f"{field}: ожидался тип int")
    if value <= 0: 
        raise ValueError(f"{field}: сумма меньше либо равна нулю") 
    return value

#Обработка одного заказа
def order_process(raw):
    if not isinstance(raw, dict):
        raise ValueError("Заказ должен быть массивом")
    
    rows = []
    order_id = required_text(
        validate_fields(raw, "order_id" , str),
        "order_id"
    )

    customer = validate_fields(raw, "customer", dict)
    customer_id = required_text(
        validate_fields(customer, "customer_id", str),
        "customer_id"
    )

    items = validate_fields(raw, "items", list)
    
    item_line_no = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Позиция должна быть массивом")
        
        line_no = positive_int(
            validate_fields(item, "line_no", int),
            "line_no"
        )
        if line_no in item_line_no:
            raise ValueError(f"Повторяется значение line_no = {line_no} в заказе")
        item_line_no.append(line_no)

        sku = required_text(
            validate_fields(item, "sku", str),
            "sku"
        )
        quantity = positive_int(
            validate_fields(item, "quantity", int),
            "quantity"
        )
        unit_price = parse_unit_price(
            validate_fields(item, "unit_price", str)
        )
        line_amount = quantity * unit_price
        row = {
            "order_id": order_id,
            "line_no": line_no,
            "customer_id": customer_id,
            "sku": sku,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
        }
        rows.append(row)
    return rows


#Чтение исходного файла и провверка структуры
def json_format_check(path):
    path = Path(path)
    logger.info("event=src_read_started path=%s", str(Path(path).relative_to(base_dir)))

    if not path.is_file():
        raise FileNotFoundError("Входной файл не найден")

    extension = path.suffix.lower()

    if extension not in {".json", ".jsonl"}:
        raise ValueError("Поддерживаются только .json и .jsonl")
    
    return extension


#Один запуск
def run_load(path):
    run_id = uuid4().hex
    path = Path(path).resolve()

    logger.info(
        "run_id=%s event=load_started input=%s",
        run_id,
        str(Path(path).relative_to(base_dir)),
    )

    

    target_accepted = data_dir / "processed" / run_id / "orders.csv"
    target_rejected = data_dir / "rejected" / run_id / "orders.jsonl"
    target_summary = data_dir / "processed" / run_id / "summary_orders.json" 

    #Счётчики
    orders_read = 0
    orders_accepted = 0
    orders_rejected = 0
    blank_lines = 0
    items_written = 0
    units = 0
    total_amount = Decimal("0.00")       
    try:
        extension = json_format_check(path)     
        
        if extension == ".json":
            logger.info(
                "event=load_json",
            )
            with path.open("r", encoding="utf-8") as file: 
                document = json.load(file) 

            export_date = required_text(
                validate_fields(document, "export_date", str),
                "export_date",
            )
            orders = validate_fields(document, "orders", list)

            all_rows = []
            for order in orders:
                order_rows = order_process(order)
                all_rows.extend(order_rows)

            target_accepted.parent.mkdir(parents=True, exist_ok=True) 

            with target_accepted.open("w", encoding="utf-8", newline="") as file: 
                writer = csv.DictWriter(
                    file,
                    fieldnames=HEADERS,
                    delimiter=";"
                )

                writer.writeheader()
                writer.writerows(all_rows)
            #счетчики со статискикой
            orders_read = len(orders)
            orders_accepted = len(orders)
            items_written = len(all_rows)
            for row in all_rows:
                units += row["quantity"]
                total_amount += row["line_amount"]

        elif extension == ".jsonl":
            logger.info(
                "event=load_jsonl",
            )
            target_accepted.parent.mkdir(parents=True, exist_ok=True)
            target_rejected.parent.mkdir(parents=True, exist_ok=True)


            with (
                path.open("r", encoding="utf-8") as input_file,
                target_accepted.open("w", encoding="utf-8", newline="") as accepted_file,
                target_rejected.open("w", encoding="utf-8") as rejected_file,
            ):

                writer = csv.DictWriter(
                    accepted_file,
                    fieldnames=HEADERS,
                    delimiter=";"
                )
                writer.writeheader()

                for line_number, line in enumerate(input_file, start=1):
                    #Пустые строки пропускаем, учитываем отдельным счечтиком
                    if not line.strip():
                        blank_lines += 1
                        continue

                    orders_read += 1
                    try:
                        record = json.loads(line)
                        order_rows = order_process(record)

                    except (json.JSONDecodeError, ValueError) as error:
                        orders_rejected += 1
                        raw_text = line.rstrip("\r\n")
                        rejection = {
                            "run_id": run_id,
                            "source_file": str(Path(path).relative_to(base_dir)),
                            "line_number": line_number,
                            "raw_text": raw_text,
                            "reason": str(error)
                        }
                        text = json.dumps(
                            rejection, ensure_ascii=False, allow_nan=False,
                        )
                        rejected_file.write(text + "\n")
                        continue

                    writer.writerows(order_rows)

                    orders_accepted += 1
                    items_written += len(order_rows)

                    for row in order_rows:
                        units += row["quantity"]
                        total_amount += Decimal(row["line_amount"])

            if orders_read != orders_accepted + orders_rejected: 
                raise RuntimeError("Нарушен баланс записей")

        status = (
            "SUCCESS_WITH_REJECTS"
            if orders_rejected > 0
            else "SUCCESS"
        )
        #Статистика
        summary = {
            "run_id": run_id,
            "source_file": str(Path(path).relative_to(base_dir)),
            "status": status, 
            "orders_read": orders_read,
            "orders_accepted": orders_accepted,
            "orders_rejected": orders_rejected,
            "blank_lines": blank_lines,
            "items_written": items_written,
            "units": units,
            "total_amount": str(total_amount)
        }

        with target_summary.open("w", encoding="utf-8") as file:
            json.dump(
                summary,
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )
    #Счетчики в саммари тольлко если обработка завершилась успешно
    except Exception as error:
        summary = {
            "run_id": run_id,
            "source_file": str(Path(path).relative_to(base_dir)),
            "status": "FAILED", 
            "error": str(error),
        }
        target_summary.parent.mkdir(parents=True, exist_ok=True)
        with target_summary.open("w", encoding="utf-8") as file:
            json.dump(
                summary,
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )
        raise



########
#Основная часть кода
#####
def main(): 
    parser = argparse.ArgumentParser() 
    parser.add_argument("--input", type=Path, required=True, help = "Путь к файлу") 
    args = parser.parse_args() 

    configure_logging()

    try: 
        run_load(args.input) 
    except Exception as error: 
        logger.exception("event=load_failed")
        return 1 
    
    logger.info("event=load_completed")
    return 0 



if __name__ == "__main__": 
    raise SystemExit(main())