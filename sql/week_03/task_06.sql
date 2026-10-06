--------------
--Факт - Отстатки на складах
--------------
drop table if exists de3_dwh.fact_stock_snapshot cascade
;
create table if not exists de3_dwh.fact_stock_snapshot (
	  snapshot_date		timestamptz not null
	, warehouse_key		bigint not null
	, product_key		bigint not null
	, quantity_on_hand	integer not null
	, t_changed_dttm	timestamptz default current_timestamp
	, primary key (snapshot_date, warehouse_key, product_key)
	, constraint fk_f_stock_snap_dim_cust
			foreign key (warehouse_key)
			references de3_dwh.dim_warehouse (warehouse_key)
			on delete no action
	, constraint fk_f_stock_snap_dim_chnl
			foreign key (product_key)
			references de3_dwh.dim_product (product_key)
			on delete no action
)
;
comment on table de3_dwh.fact_stock_snapshot is 'Факт - Остатки на складах';
comment on column de3_dwh.fact_stock_snapshot.snapshot_date is 'Дата снимка';
comment on column de3_dwh.fact_stock_snapshot.warehouse_key is 'Идентификатор склада';
comment on column de3_dwh.fact_stock_snapshot.product_key is 'Идентификатор продукта';
comment on column de3_dwh.fact_stock_snapshot.quantity_on_hand is 'Остаток на конец дня';
comment on column de3_dwh.fact_stock_snapshot.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.fact_stock_snapshot (
	  snapshot_date
	, warehouse_key
	, product_key
	, quantity_on_hand
)
select
	  ss.snapshot_date
	, w.warehouse_key 
	, p.product_key
	, ss.quantity_on_hand
from de3_src.stock_snapshots ss 
left join de3_dwh.dim_warehouse w
	on ss.warehouse_id = w.warehouse_id 
left join de3_dwh.dim_product p
	on ss.product_id = p.product_id 
;
--------------------------
--Витрина - Витрина остатков
--------------------------
drop view if exists de3_dwh.mart_stock_daily cascade
;
create or replace view de3_dwh.mart_stock_daily
(
	  snapshot_date
	, warehouse_key
	, product_key
	, quantity_on_hand
	, snapshot_received
) as 
select
	  d.full_date as snapshot_date
	, w.warehouse_key
	, p.product_key
	, ss.quantity_on_hand
	, ss.snapshot_date is not null as snapshot_received
--snapshot_date, warehouse_key, product_key, quantity_on_hand, snapshot_received
from de3_dwh.dim_date d
inner join de3_src.assortment a 
	on d.full_date between '2025-09-01' and '2025-09-03'
inner join de3_dwh.dim_warehouse w
	on a.warehouse_id = w.warehouse_id 
inner join de3_dwh.dim_product p
	on a.product_id = p.product_id 
left join de3_dwh.fact_stock_snapshot ss
	on ss.snapshot_date = d.full_date
	and ss.warehouse_key = w.warehouse_key
	and ss.product_key = p.product_key
;
--Контроль покрытия по каждой дате и складу
select 
	  snapshot_date
	, warehouse_key
	, count(*) as expected_snapshot_count
	, count(case when snapshot_received is true then 1 end) as received_snapshot_count
	, count(case when snapshot_received is false then 1 end) as missing_snapshot_count
	, case when count(case when snapshot_received is true then 1 end) = 0 then null 
		else sum(quantity_on_hand)
		end as known_quantity
	, case when count(case when snapshot_received is false then 1 end) > 0 then null 
		else sum(quantity_on_hand)
		end as total_quantity
from de3_dwh.mart_stock_daily d
group by snapshot_date, warehouse_key
;
--Нулевой остаток и отсутствующий снимок
select * 
from de3_dwh.mart_stock_daily d
where quantity_on_hand = 0 or snapshot_received is false
;
--остаток по каждому складу на конец 2 сентября
select * 
from de3_dwh.mart_stock_daily d
where snapshot_date = '2025-09-02'