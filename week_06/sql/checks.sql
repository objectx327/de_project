select * from de6_etl.load_runs
;
select * from de6_etl.pipeline_state
;
select * from de6_stg.orders_raw
;
select * from de6_stg.i_orders
;
select * from  de6_core.orders_current

;
--Полная замена заданной области
INSERT INTO de6_core.orders_current (
    source_system, order_id, source_version,
    status, amount, is_deleted, t_updated_at
)
VALUES
    ('shop', '000001', 1, 'new', 90.00, false, clock_timestamp()),
    ('shop', 'X', 1, 'new', 5.00, false, clock_timestamp()),
    ('partner', '000001', 1, 'paid', 999.00, false, clock_timestamp())
;

;
SELECT source_system, order_id, status, amount, source_version, is_deleted
FROM de6_core.orders_current
ORDER BY source_system, order_id
;
--контроль
SELECT
    count(*) AS active_orders,
    coalesce(sum(amount), 0) AS total_amount
FROM de6_core.orders_current
WHERE source_system IN ('shop', 'pos')
  AND NOT is_deleted
;
--Проверка журнала
select run_id, batch_id, mode, status, staged_rows, prepared_rows
FROM de6_etl.load_runs
;
--Проверка stage
SELECT count(*) AS raw_rows
FROM de6_stg.orders_raw
;

--Проверка интеграционной таблицы
SELECT
    count(*) AS prepared_rows,
    sum(amount) AS prepared_amount
FROM de6_stg.i_orders
;

SELECT pipeline_name, watermark, run_id
FROM de6_etl.pipeline_state
WHERE pipeline_name = 'orders';
;

--Повторный full load
SELECT source_system, order_id, status, amount, source_version, is_deleted
FROM de6_core.orders_current
WHERE source_system IN ('shop', 'pos')
ORDER BY source_system, order_id;

;
select run_id, batch_id, mode, status, staged_rows, prepared_rows
FROM de6_etl.load_runs
;
SELECT current_database() AS db
;
SELECT pipeline_name, watermark 
FROM de6_etl.pipeline_state 
;
SELECT source_system,order_id,source_version,status,amount,is_deleted
FROM de6_core.orders_current
ORDER BY source_system,order_id
;
select
    row_no,
    source_system,
    order_id,
    source_version,
    decision,
    applied_action
from de6_stg.orders_raw
where run_id = (
    select run_id
    from de6_etl.load_runs
    where batch_id = 'delta_1'
      and mode = 'delta'
      and status = 'succeeded'
    order by started_at desc
    limit 1
)
order by row_no
;
select
    source_system, order_id, source_version,
    status, amount, is_deleted
from de6_core.orders_current
where source_system in ('shop', 'pos')
order by source_system, order_id
;
select
    count(case when not is_deleted then 1 end) as active_orders,
    sum(case when not is_deleted then amount else 0 end) as total_amount,
    count(case when is_deleted then 1 end) as deleted_orders
from de6_core.orders_current
where source_system in ('shop', 'pos')
;
select watermark
from de6_etl.pipeline_state
;
select source_version, status, amount
from de6_core.orders_current
where source_system = 'shop'
  and order_id = '000001'
;
select run_id, batch_id, mode, status, applied_rows
from de6_etl.load_runs
where batch_id = 'delta_1'
order by started_at
;
select source_system,order_id,source_version,status,amount,is_deleted,t_updated_at
from de6_core.orders_current
where source_system='shop' and order_id='000006'
;
select run_id,batch_id,status,stage,error_code,error_message
from de6_etl.load_runs
where batch_id='conflict_1015'
order by started_at desc limit 1
;
select source_system,order_id,source_version,status,amount,is_deleted
from de6_core.orders_current
where source_system='shop' and order_id in ('000003','000007')
order by order_id
;
select run_id,batch_id,status,error_code,error_message
from de6_etl.load_runs
where batch_id='conflict_target_1015'
order by started_at desc limit 1;

select source_system,order_id,source_version,status,amount,is_deleted
from de6_core.orders_current
--where source_system='shop' and order_id='000004'
;
create table if not exists de6_stg.reference_full_1000 (
    source_system text not null,
    order_id text not null,
    status text not null,
    amount numeric(12,2) not null,
    source_version bigint not null,
    primary key (source_system, order_id)
);
truncate de6_stg.reference_full_1000
;
insert into de6_stg.reference_full_1000
    (source_system,order_id,status,amount,source_version)
values
    ('shop','000001','paid',120.00,2),
    ('shop','000003','cancelled',50.00,2),
    ('shop','000004','paid',45.00,2),
    ('shop','000005','paid',30.00,1),
    ('pos','000001','paid',70.00,1);
;
with actual as (
    select source_system,order_id,status,amount,source_version
    from de6_core.orders_current
    where source_system in ('shop','pos') and not is_deleted
), expected as (
    select source_system,order_id,status,amount,source_version
    from de6_stg.reference_full_1000
)
select 'extra_in_target' as problem,a.*
from (select * from actual except all select * from expected) a
union all
select 'missing_in_target' as problem,e.*
from (select * from expected except all select * from actual) e
;
select * from de6_core.orders_current
where source_system is null or btrim(source_system)=''
   or order_id is null or btrim(order_id)=''
   or source_version is null or source_version<=0
   or is_deleted is null or t_updated_at is null
   or (is_deleted and (status is not null or amount is not null))
   or (not is_deleted and (status is null
       or status not in ('new','paid','cancelled')
       or amount is null or amount<=0))
;
select source_system,order_id,count(*) as n
from de6_core.orders_current group by source_system,order_id
having count(*)>1;
;
select source_system, order_id, source_version,
       status, amount, is_deleted
from de6_core.orders_current
where source_system in ('shop', 'pos')
  and is_deleted
order by source_system, order_id
;
select run_id,batch_id,status,stage,staging_ready,staged_rows,error_code
from de6_etl.load_runs
where batch_id='delta_1' --and status='failed'
order by started_at desc limit 1
;
select count(*) as saved_rows
from de6_stg.orders_raw
where run_id in (select run_id from de6_etl.load_runs
	where batch_id='delta_1' and status='failed'
	order by started_at desc limit 1)
;
select
    count(case when not is_deleted then 1 end) as active_orders,
    sum(case when not is_deleted then amount else 0 end) as total_amount,
    count(case when is_deleted then 1 end) as deleted_orders
from de6_core.orders_current
where source_system in ('shop', 'pos')
;
select watermark
from de6_etl.pipeline_state
