create schema if not exists de3_dwh;
-----------------
--Измерение Клиент
-----------------
set time zone 'Europe/Berlin'
;
drop table if exists de3_dwh.dim_customer cascade
;
create table if not exists de3_dwh.dim_customer (	
	  customer_key			bigint 
			generated always as identity
			primary key
    , source_system 		text
    , source_customer_id 	text
    , history_id 			integer
    , customer_name 		text not null
    , segment 				text not null
    , valid_from 			timestamptz
    , valid_to 				timestamptz
    , is_current 			boolean
    , t_changed_dttm		timestamptz default current_timestamp
	, unique (source_system, source_customer_id, valid_from)
)
;
comment on table de3_dwh.dim_customer is 'Измерение Клиент';

comment on column de3_dwh.dim_customer.customer_key is 'Суррогатный ключ - идентификатор клиента';
comment on column de3_dwh.dim_customer.source_system is 'Система-источник';
comment on column de3_dwh.dim_customer.source_customer_id is 'Идентификатор клиента в источнике';
comment on column de3_dwh.dim_customer.history_id is 'Идентификатор клиента';
comment on column de3_dwh.dim_customer.customer_name is 'Наименование клиента';
comment on column de3_dwh.dim_customer.segment is 'Сегмент';
comment on column de3_dwh.dim_customer.valid_from is 'Дата начала действия записи';
comment on column de3_dwh.dim_customer.valid_to is 'Дата окончания действия записи';
comment on column de3_dwh.dim_customer.is_current is 'Признак актуальности записи';
comment on column de3_dwh.dim_customer.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.dim_customer(customer_key, customer_name, segment)
overriding system value
select 0, 'Неизвестный клиент', 'unknown'
;
insert into de3_dwh.dim_customer(source_system, source_customer_id, history_id, customer_name, segment, valid_from, valid_to, is_current)
select 
	  ch.source_system
	, ch.customer_id as source_customer_id
	, history_id
	, coalesce(cnc.corrected_name, ch.customer_name) as customer_name
	, ch.segment
	, ch.valid_from 
	, ch.valid_to
	, ch.is_current
from de3_src.customer_history ch 
left join  de3_src.customer_name_corrections cnc 
	on ch.customer_id = cnc.customer_id 
	and ch.source_system = cnc.source_system 
;

-----------------
--Измерение Товар
-----------------
drop table if exists de3_dwh.dim_product cascade
;
create table if not exists de3_dwh.dim_product (
	  product_key		bigint  
			generated always as identity
			primary key
    , product_id 		text unique not null
    , product_name 		text not null
    , category_name 	text not null
    , unit 				text not null
    , t_changed_dttm	timestamptz default current_timestamp
)
;
comment on table de3_dwh.dim_product is 'Измерение Товар';

comment on column de3_dwh.dim_product.product_key is 'Суррогатный ключ - идентификатор товара';
comment on column de3_dwh.dim_product.product_id is 'Идентификатор товара в источнике';
comment on column de3_dwh.dim_product.product_name is 'Наименование товара';
comment on column de3_dwh.dim_product.category_name is 'Категория товара';
comment on column de3_dwh.dim_product.unit is 'Единица измерения товара';
comment on column de3_dwh.dim_product.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.dim_product (product_id, product_name, category_name, unit) 
select 
	  product_id
	, product_name 
	, category_name
	, unit
from de3_src.products p
;
-----------------
--Измерение Канал
-----------------
drop table if exists de3_dwh.dim_channel cascade
;
create table if not exists de3_dwh.dim_channel (
	  channel_key		bigint  
			generated always as identity
			primary key
    , channel_id 		text unique not null
    , channel_name 		text not null
    , t_changed_dttm	timestamptz default current_timestamp
)
;
comment on table de3_dwh.dim_channel is 'Измерение Канал';

comment on column de3_dwh.dim_channel.channel_key is 'Суррогатный ключ - идентификатор канала';
comment on column de3_dwh.dim_channel.channel_id is 'Идентификатор канала в источнике';
comment on column de3_dwh.dim_channel.channel_name is 'Наименование канала';
comment on column de3_dwh.dim_channel.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.dim_channel (channel_id, channel_name) 
select 
	  channel_id
	, channel_name
from de3_src.channels c 
;
-----------------
--Измерение Склад
-----------------
drop table if exists de3_dwh.dim_warehouse cascade
;
create table if not exists de3_dwh.dim_warehouse (
	  warehouse_key		bigint  
			generated always as identity
			primary key
    , warehouse_id 		text unique not null
    , warehouse_name 	text not null
    , t_changed_dttm	timestamptz default current_timestamp
)
;
comment on table de3_dwh.dim_warehouse is 'Измерение Склад';

comment on column de3_dwh.dim_warehouse.warehouse_key is 'Суррогатный ключ - идентификатор склада';
comment on column de3_dwh.dim_warehouse.warehouse_id is 'Идентификатор склада в источнике';
comment on column de3_dwh.dim_warehouse.warehouse_name is 'Наименование склада';
comment on column de3_dwh.dim_warehouse.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.dim_warehouse (warehouse_id, warehouse_name) 
select 
	  warehouse_id
	, warehouse_name
from de3_src.warehouses w 
;
-----------------
--Календарь
-----------------
drop table if exists de3_dwh.dim_date cascade
;
create table if not exists de3_dwh.dim_date (
	  date_key			int primary key
	, full_date			timestamptz unique not null
	, calendar_year		int not null
	, month_number		int not null
	, month_name 		text not null
	, day_number		int not null
    , t_changed_dttm	timestamptz default current_timestamp
)

;
comment on table de3_dwh.dim_date is 'Календарь';
comment on column de3_dwh.dim_date.date_key is 'Ключ строки календаря';
comment on column de3_dwh.dim_date.full_date is 'Календарная дата';
comment on column de3_dwh.dim_date.calendar_year is 'Год';
comment on column de3_dwh.dim_date.month_number is 'Номер месяца';
comment on column de3_dwh.dim_date.month_name is 'Название месяца';
comment on column de3_dwh.dim_date.day_number is 'День месяца';
comment on column de3_dwh.dim_date.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.dim_date(date_key, full_date, calendar_year, month_number, month_name, day_number) 
values 
	(20250831, '2025-08-31 00:00:00+02', 2025, 8, 'Август', 31), 
	(20250901, '2025-09-01 00:00:00+02', 2025, 9, 'Сентябрь', 1),
	(20250902, '2025-09-02 00:00:00+02', 2025, 9, 'Сентябрь', 2),
	(20250903, '2025-09-03 00:00:00+02', 2025, 9, 'Сентябрь', 3),
	(20250904, '2025-09-04 00:00:00+02', 2025, 9, 'Сентябрь', 4),
	(20250905, '2025-09-05 00:00:00+02', 2025, 9, 'Сентябрь', 5),
	(20250906, '2025-09-06 00:00:00+02', 2025, 9, 'Сентябрь', 6),
	(20250907, '2025-09-07 00:00:00+02', 2025, 9, 'Сентябрь', 7)
;
-----------------------------
--Проверка загруженных данных
-----------------------------

select * from de3_dwh.dim_customer
;
--Проверка дублей
select source_system, source_customer_id, valid_from, count(*)
from de3_dwh.dim_customer
group by source_system, source_customer_id, valid_from
having count(*) > 1
;
--Нет пересечения периодов
select c.*, c2.customer_key, c2.history_id, c2.valid_from, c2.valid_to
from de3_dwh.dim_customer c
inner join de3_dwh.dim_customer c2
	on c.source_system = c2.source_system
	and c.source_customer_id = c2.source_customer_id
	and c.customer_key != c2.customer_key
	and c.valid_from >= c2.valid_from and c.valid_from < coalesce(c2.valid_to, '2100-01-01')	--заглушка для открытых записей
; 
--Только одна открытая запись на клиента
select source_system, source_customer_id, count(*)
from de3_dwh.dim_customer c
where c.is_current
group by source_system, source_customer_id
having count(*) > 1
;
select * from de3_dwh.dim_product
;
select * from de3_dwh.dim_channel
;
select * from de3_dwh.dim_warehouse
;
select * from de3_dwh.dim_date
;