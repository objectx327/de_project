-- Выполните один раз в своей учебной базе PostgreSQL.
-- Скрипт создаёт отдельную схему и пустую таблицу. Исходные строки не загружаются.
-- Если схема de4_hw уже существует, используйте другую пустую учебную базу.
begin;
create schema de4_hw;
create table de4_hw.payment_events (
    payment_id text primary key,
    amount numeric(12, 2) not null,
    currency text not null,
    created_at timestamptz not null,
    paid_at timestamptz not null,
    status text not null,
    comment text not null,
    check (payment_id <> ''),
    check (amount > 0 and amount <= 9999999999.99),
    check (currency = 'EUR'),
    check (status = 'paid'),
    check (paid_at >= created_at)
);
commit;
