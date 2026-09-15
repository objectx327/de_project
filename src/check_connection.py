from getpass import getpass

import psycopg

password = getpass("Пароль пользователя de_student: ")

with psycopg.connect(
    host="localhost",
    port=5432,
    dbname="de_course",
    user="de_student",
    password=password,
    connect_timeout=5,
) as connection:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*), SUM(amount)
            FROM training.setup_check
        """)
        row_count, total_amount = cursor.fetchone()

if row_count != 2 or total_amount != 150:
    raise RuntimeError(
        f"Неожиданный результат: "
        f"строк={row_count}, сумма={total_amount}"
    )

print("Подключение к PostgreSQL работает")
print(f"Количество строк: {row_count}")
print(f"Общая сумма: {total_amount}")