--Вариант 1 - сортировка по  source_updated_at DESC, loaded_at DESC, delivery_id DESC
drop table if exists de2_hw.payment_deliveries_sort_var1
;
create table if not exists de2_hw.payment_deliveries_sort_var1 (
    delivery_id bigint,
    source_system text,
    payment_id bigint,
    order_id bigint,
    amount numeric(12,2),
    payment_status text,
    created_on date,
    paid_on date,
    source_updated_at timestamptz,
    loaded_at timestamptz,
    rn integer
)
;
with pre_sorted as 
	(
		select 
			row_number() over(partition by pd.source_system, pd.payment_id order by pd.source_updated_at desc, pd.loaded_at desc, pd.delivery_id desc) as rn
			,* 
		from de2_hw.payment_deliveries pd
		where pd.payment_id is not null	
	)
insert into de2_hw.payment_deliveries_sort_var1(delivery_id, source_system, payment_id, order_id, amount, payment_status, created_on, paid_on, source_updated_at, loaded_at, rn)
select
	  t.delivery_id 
	, t.source_system 
	, t.payment_id 
	, t.order_id 
	, t.amount 
	, t.payment_status 
	, t.created_on 
	, t.paid_on 
	, t.source_updated_at 
	, t.loaded_at 
	, t.rn
from pre_sorted t
--where rn = 1
order by t.source_system, t.payment_id
;
--Вариант 2 - сортировка по loaded_at desc, delivery_id desc)
drop table if exists de2_hw.payment_deliveries_sort_var2
;
create table if not exists de2_hw.payment_deliveries_sort_var2 (
    delivery_id bigint,
    source_system text,
    payment_id bigint,
    order_id bigint,
    amount numeric(12,2),
    payment_status text,
    created_on date,
    paid_on date,
    source_updated_at timestamptz,
    loaded_at timestamptz,
    rn integer
)
;
with pre_sorted as 
	(
		select 
			row_number() over(partition by pd.source_system, pd.payment_id order by pd.loaded_at desc, pd.delivery_id desc) as rn
			,* 
		from de2_hw.payment_deliveries pd
		where pd.payment_id is not null	
	)
insert into de2_hw.payment_deliveries_sort_var2(delivery_id, source_system, payment_id, order_id, amount, payment_status, created_on, paid_on, source_updated_at, loaded_at, rn)
select
	  t.delivery_id 
	, t.source_system 
	, t.payment_id 
	, t.order_id 
	, t.amount 
	, t.payment_status 
	, t.created_on 
	, t.paid_on 
	, t.source_updated_at 
	, t.loaded_at 
	, t.rn
from pre_sorted t
--where rn = 1
order by t.source_system, t.payment_id
;
--Актуальные версии
select 
	  t.delivery_id 
	, t.source_system 
	, t.payment_id 
	, t.order_id 
	, t.amount 
	, t.payment_status 
	, t.created_on 
	, t.paid_on 
	, t.source_updated_at 
	, t.loaded_at 
from de2_hw.payment_deliveries_sort_var1 t
where  rn = 1
order by source_system, payment_id 
;
select 
	  t.delivery_id 
	, t.source_system 
	, t.payment_id 
	, t.order_id 
	, t.amount 
	, t.payment_status 
	, t.created_on 
	, t.paid_on 
	, t.source_updated_at 
	, t.loaded_at 
from de2_hw.payment_deliveries_sort_var2 t
where  rn = 1
order by source_system, payment_id 
;
--Список неполных ключей
select *
from de2_hw.payment_deliveries pd
where pd.payment_id is null
;
--Расхождения:
select 
	t1.delivery_id, t1.source_system, t1.payment_id, t1.source_updated_at, t1.loaded_at,
	t2.delivery_id, t2.source_system, t2.payment_id, t2.source_updated_at, t2.loaded_at
from (select * from de2_hw.payment_deliveries_sort_var1 where rn = 1) t1
full join (select * from de2_hw.payment_deliveries_sort_var2 where rn = 1) t2
	on t1.source_system = t2.source_system 
	and t1.payment_id = t2.payment_id
where t1.delivery_id <> t2.delivery_id
;
select * 
from de2_hw.payment_deliveries_sort_var1 
where rn > 1
;
select * 
from de2_hw.payment_deliveries_sort_var2
where rn > 1
;
--статистика до и после сортировки
with stats as 
	(
		select distinct
			  'original' as stage
			, source_system
			, payment_id
			, count(*) over(partition by source_system, payment_id) as pk_cnt
			, count(*) over() as total_cnt
		from de2_hw.payment_deliveries
		union all
		select distinct
			  'var1' as stage
			, source_system
			, payment_id
			, count(*) over(partition by source_system, payment_id) as pk_cnt
			, count(*) over() as total_cnt
		from de2_hw.payment_deliveries_sort_var1
		where rn = 1
		union all
		select distinct
			  'var2' as stage
			, source_system
			, payment_id
			, count(*) over(partition by source_system, payment_id) as pk_cnt
			, count(*) over() as total_cnt
		from de2_hw.payment_deliveries_sort_var2
		where rn = 1
	)
select 
	  stage
	, max(total_cnt) as total_rows_count
	, count(case when pk_cnt > 1 then 1 end) as pk_doubles_count
from stats
group by stage
order by stage