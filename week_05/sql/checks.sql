-- Подготовка БД
--week_05/DE_Week5_Practice/00_setup.sql
-- Run once in your existing learning database, using your SQL client.
-- This script creates a separate schema and does not delete existing data.
/*
create schema if not exists de5_hw;

create table if not exists de5_hw.load_runs (
    run_id uuid primary key,
    source_system text not null,
    source_account_id text not null,
    period_from timestamptz not null,
    period_to timestamptz not null,
    started_at timestamptz not null default current_timestamp,
    finished_at timestamptz,
    status text not null check (status in ('running', 'succeeded', 'failed')),
    received_count integer not null default 0 check (received_count >= 0),
    accepted_count integer not null default 0 check (accepted_count >= 0),
    rejected_count integer not null default 0 check (rejected_count >= 0),
    inserted_count integer not null default 0 check (inserted_count >= 0),
    updated_count integer not null default 0 check (updated_count >= 0),
    skipped_count integer not null default 0 check (skipped_count >= 0),
    error_summary text,
    check (period_from < period_to)
);

create table if not exists de5_hw.orders (
    source_system text not null,
    source_account_id text not null,
    order_id text not null,
    customer_id text,
    status text not null check (status in ('pending', 'paid', 'cancelled', 'refunded')),
    currency text not null check (currency = 'EUR'),
    total_amount numeric(14,2) not null check (total_amount >= 0),
    created_at timestamptz not null,
    source_updated_at timestamptz not null,
    received_at timestamptz not null,
    run_id uuid not null references de5_hw.load_runs(run_id),
    raw_response_id uuid not null,
    record_no integer not null check (record_no > 0),
    payload jsonb not null,
    primary key (source_system, source_account_id, order_id),
    check (source_updated_at >= created_at)
);

create table if not exists de5_hw.order_items (
    source_system text not null,
    source_account_id text not null,
    order_id text not null,
    line_id text not null,
    sku text not null,
    quantity integer not null check (quantity > 0),
    unit_price numeric(14,2) not null check (unit_price >= 0),
    primary key (source_system, source_account_id, order_id, line_id),
    foreign key (source_system, source_account_id, order_id)
        references de5_hw.orders(source_system, source_account_id, order_id)
);

create table if not exists de5_hw.load_state (
    source_system text not null,
    source_account_id text not null,
    completed_to timestamptz not null,
    run_id uuid not null references de5_hw.load_runs(run_id),
    primary key (source_system, source_account_id)
);
*/

select * from de5_hw.load_runs
;
select count(*) as rows_count, sum(total_amount) as total_amount from de5_hw.orders
;
select count(*) as rows_count, sum(quantity*unit_price) as total_amount from de5_hw.order_items
;
select * from de5_hw.load_state
;
select order_id, status, run_id from de5_hw.orders where order_id in ('000003', '000008','000019',' 000020', '000006') order by 1
;
select order_id, line_id, sku from de5_hw.order_items where order_id in ('000008') order by 1
;
select source_system,  source_account_id, completed_to, run_id from de5_hw.load_state
;
select order_id, status, run_id from de5_hw.orders where order_id in ('000003', '000008') order by 1
;
select order_id, total_amount, status, run_id from de5_hw.orders where order_id in ('000001') order by 1
;
select * from de5_hw.order_items where order_id in ('000001') order by order_id
;
select * from de5_hw.load_runs order by started_at
;
drop schema de5_hw cascade
;
create database de_course_test owner de_student
;
delete from de5_hw.load_state
;
--уникальность ключей
select source_system, source_account_id, order_id, count(*) 
from de5_hw.orders
group by source_system, source_account_id, order_id 
having count(*) > 1
;
select source_system, source_account_id, order_id, line_id, count(*)
from de5_hw.order_items
group by source_system, source_account_id, order_id, line_id
having count(*) > 1
;
-- отсутствие позиций без заказа
select * from de5_hw.order_items
where order_id not in (select order_id from de5_hw.orders)
;
-- совпадение суммы позиций с total_amount каждого заказа
with oi as (
	select order_id, sum(quantity*unit_price) as total_amount
	from de5_hw.order_items
	group by order_id
)
select o.order_id, o.total_amount , oi.order_id , oi.total_amount 
from de5_hw.orders o
join oi 
	on o.order_id = oi.order_id
where o.total_amount != oi.total_amount
;
-- гостевой заказ с NULL customer_id и сохранение заказов без позиций
select o.order_id, o.customer_id 
from de5_hw.orders o
left join (select distinct order_id from de5_hw.order_items) oi 
	on o.order_id = oi.order_id 
where customer_id is null
	or oi.order_id is null