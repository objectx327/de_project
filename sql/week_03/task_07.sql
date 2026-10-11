--Проверка фактов на дубли
select oi.order_id, oi.line_no, count(*)
from de3_dwh.fact_order_item oi 
group by oi.order_id, oi.line_no
having count(*) > 1
;
select p.payment_id, count(*)
from de3_dwh.fact_payment p
group by p.payment_id 
having count(*) > 1
;
select r.refund_id, count(*) 
from de3_dwh.fact_refund r
group by r.refund_id 
having count(*) > 1
;
select s.snapshot_date , s.warehouse_key , s.product_key , count(*)
from de3_dwh.fact_stock_snapshot s
group by s.snapshot_date , s.warehouse_key , s.product_key
having count(*) > 1
;
--Отсутствующие ссылки на измерения
select oi.order_id, oi.line_no, oi.channel_key, oi.customer_key, oi.product_key
from de3_dwh.fact_order_item oi 
where oi.channel_key is null 
	or oi.customer_key is null 
	or oi.product_key is null
;
select p.payment_id, p.customer_key, p.channel_key
from de3_dwh.fact_payment p
where p.customer_key is null 
	or p.channel_key is null
;
select r.refund_id, r.customer_key, r.channel_key
from de3_dwh.fact_refund r
where r.customer_key is null 
	or r.channel_key is null
;
select s.snapshot_date , s.warehouse_key , s.product_key
from de3_dwh.fact_stock_snapshot s
where s.warehouse_key is null 
	or s.product_key is null
;
--customer_key = 0
select *
from de3_dwh.fact_order_item oi 
where oi.customer_key = 0
;
select *
from de3_dwh.fact_payment p
where p.customer_key = 0
;
select *
from de3_dwh.fact_refund r
where r.customer_key = 0
;
--Пересечения периодов клиентов
select
    c.history_id,
    c2.history_id
from de3_dwh.dim_customer c
inner join de3_dwh.dim_customer c2
    on c.source_system = c2.source_system
   and c.source_customer_id = c2.source_customer_id
   and c.customer_key < c2.customer_key
   and (c2.valid_to is null or c.valid_from < c2.valid_to)
   and (c.valid_to is null or c2.valid_from < c.valid_to)
where c.customer_key <> 0
  	and c2.customer_key <> 0;
;
--несколько открытых версий одного клиента
select *
from de3_dwh.dim_customer c
where (source_system, source_customer_id) in (
		select source_system, source_customer_id 
		from de3_dwh.dim_customer 
		where customer_key != 0 
			and is_current is true
		group by source_system, source_customer_id
		having count(*) > 1
	)	
;
--valid_to ≤ valid_from
select *
from de3_dwh.dim_customer c
where customer_key != 0 
	and valid_to <= valid_from
;
--несоответствие is_current и открытого конца
select *
from de3_dwh.dim_customer c
where customer_key != 0 
	and is_current is distinct from (valid_to is null)
;
--Историческая связь факта: выбранная версия относится к правильным source_system + customer_id и покрывает время события
select oi.order_id, oi.line_no, oi.customer_key
from de3_dwh.fact_order_item oi 
left join de3_dwh.dim_customer c
    on oi.customer_key = c.customer_key
where oi.customer_key <> 0
  and (
      c.customer_key is null
      or oi.source_system is distinct from c.source_system
      or oi.source_customer_id is distinct from c.source_customer_id
      or c.valid_from is null
      or oi.ordered_at < c.valid_from
      or (
          c.valid_to is not null
          and oi.ordered_at >= c.valid_to
      	)
	)
;
select p.payment_id, p.customer_key
from de3_dwh.fact_payment p
left join de3_dwh.dim_customer c
    on p.customer_key = c.customer_key
where p.customer_key <> 0
  and (
      c.customer_key is null
      or p.source_system is distinct from c.source_system
      or p.source_customer_id is distinct from c.source_customer_id
      or c.valid_from is null
      or p.paid_at < c.valid_from
      or (
          c.valid_to is not null
          and p.paid_at >= c.valid_to
      	)
	)
;
select r.refund_id, r.customer_key
from de3_dwh.fact_refund r
left join de3_dwh.dim_customer c
    on r.customer_key = c.customer_key
where r.customer_key <> 0
  and (
      c.customer_key is null
      or r.source_system is distinct from c.source_system
      or r.source_customer_id is distinct from c.source_customer_id
      or c.valid_from is null
      or r.refunded_at < c.valid_from
      or (
          c.valid_to is not null
          and r.refunded_at >= c.valid_to
      	)
	)
;
--Потерянные и лишние идентификаторы фактов, расхождения количеств и сумм после обогащения
select oi.order_id, oi.line_no, oi2.order_id, oi2.line_no
from de3_dwh.fact_order_item oi 
full join (select t.* from de3_src.order_items t inner join de3_src.orders o on o.order_id = t.order_id and o.is_test is false and o.status = 'completed') oi2 
	on oi.order_id = oi2.order_id 
	and oi.line_no = oi2.line_no
where oi.order_id is null or oi2.order_id is null
;
select 'fact' as lvl, count(*), sum(oi.quantity) as sum_quantity, sum(oi.line_amount_eur) as sum_line_amount_eur
from de3_dwh.fact_order_item oi 
union all
select 'src' as lvl, count(*), sum(t.quantity) as sum_quantity, sum(t.quantity * t.unit_price_eur - t.line_discount_eur) as sum_line_amount_eur
from de3_src.order_items t 
inner join de3_src.orders o 
	on o.order_id = t.order_id 
	and o.is_test is false 
	and o.status = 'completed'
;
----
select p1.payment_id, p2.payment_id 
from de3_dwh.fact_payment p1
full join (select p.* from de3_src.payments p inner join de3_src.orders o on p.order_id = o.order_id and o.is_test is false and p.status = 'succeeded') p2
	on p1.payment_id = p2.payment_id
where p1.payment_id is null or p2.payment_id is null
;
select 'fact' as lvl, count(*), sum(p1.amount_eur)
from de3_dwh.fact_payment p1
union all
select 'src' as lvl, count(*), sum(p.amount_eur)
from de3_src.payments p 
inner join de3_src.orders o 
	on p.order_id = o.order_id 
	and o.is_test is false 
	and p.status = 'succeeded'
;
----
select r1.refund_id, r2.refund_id 
from de3_dwh.fact_refund r1
full join (
				select r.* 
				from de3_src.refunds r
				inner join de3_src.payments p
					on r.payment_id = p.payment_id
					and p.status = 'succeeded'
					and r.status = 'succeeded'
				inner join de3_src.orders o
					on p.order_id = o.order_id
					and o.is_test is false
			) r2
	on r1.refund_id = r2.refund_id
where r1.refund_id is null or r2.refund_id is null
;
select 'fact' as lvl, count(*), sum(r.amount_eur)
from de3_dwh.fact_refund r
union all
select 'src' as lvl, count(*), sum(r.amount_eur )
from de3_src.refunds r
inner join de3_src.payments p
	on r.payment_id = p.payment_id
	and p.status = 'succeeded'
	and r.status = 'succeeded'
inner join de3_src.orders o
	on p.order_id = o.order_id
	and o.is_test is false
;
--расхождения витрин с деталями на каждой комбинации их ключей
with payments_agg as (
	select 
		  p.channel_key
		, date_trunc('day', p.paid_at, 'Europe/Berlin') as paid_dt
		, count(payment_id) as payment_count
		, sum(amount_eur) as paid_amount_eur
	from de3_dwh.fact_payment p
	group by p.channel_key, paid_dt
), 
refunds_agg as (
	select 
		  r.channel_key
		, date_trunc('day', r.refunded_at, 'Europe/Berlin') as refunded_dt
		, count(refund_id) as refund_count
		, sum(amount_eur) as refunded_amount_eur
	from de3_dwh.fact_refund r
	group by r.channel_key, refunded_dt
)
, detail_agg as (
	select 
		  d.full_date as report_date
		, dc.channel_key
		, coalesce(p.payment_count, 0) as payment_count
		, coalesce(p.paid_amount_eur, 0) as paid_amount_eur
		, coalesce(r.refund_count, 0) as refund_count
		, coalesce(r.refunded_amount_eur, 0) as refunded_amount_eur
		, coalesce(p.paid_amount_eur, 0) - coalesce(r.refunded_amount_eur, 0) as net_cash_eur
		, case when p.payment_count is null then null else paid_amount_eur / payment_count end as avg_payment_eur
	from de3_dwh.dim_date d
	inner join de3_dwh.dim_channel dc 
		on d.full_date between '2025-09-01' and '2025-09-07'
	left join payments_agg p
		on d.full_date = p.paid_dt
		and dc.channel_key = p.channel_key 
	left join refunds_agg r
		on d.full_date = r.refunded_dt
		and dc.channel_key = r.channel_key
) 
select *
from de3_dwh.mart_cash_daily cd
inner join detail_agg d
	on cd.report_date = d.report_date 
	and cd.channel_key = d.channel_key
where  cd.payment_count <> d.payment_count 
	or cd.paid_amount_eur <> d.paid_amount_eur 
	or cd.refund_count <> d.refund_count 
	or cd.refunded_amount_eur <> d.refunded_amount_eur 
	or cd.net_cash_eur <> d.net_cash_eur 
	or cd.avg_payment_eur <> d.avg_payment_eur 
	or (cd.avg_payment_eur is null and d.avg_payment_eur is not null) 
	or (cd.avg_payment_eur is not null and d.avg_payment_eur is null) 
;
with agg_product_order as 
(
	select 
		  dp.category_name
		, date_trunc('day', oi.ordered_at, 'Europe/Berlin') as ordered_dt
		, sum(oi.quantity) as ordered_units
		, sum(oi.line_amount_eur) as ordered_amount_eur
	from de3_dwh.fact_order_item oi
	inner join de3_dwh.dim_product dp
		on oi.product_key = dp.product_key
	group by dp.category_name, ordered_dt
)
, detail_agg as (
	select 
		  d.full_date as report_date
		, dp.category_name
		, coalesce(a.ordered_units, 0) as ordered_units
		, coalesce(a.ordered_amount_eur, 0) as ordered_amount_eur
	from de3_dwh.dim_date d
	inner join (select distinct category_name from de3_dwh.dim_product) dp
		on d.full_date between '2025-09-01' and '2025-09-07'
	left join agg_product_order a 
		on a.ordered_dt = d.full_date
		and a.category_name = dp.category_name
)
select *
from de3_dwh.mart_order_category_daily cd
inner join detail_agg d
	on cd.report_date = d.report_date 
	and cd.category_name = d.category_name
where  cd.ordered_units <> d.ordered_units 
	or cd.ordered_amount_eur <> d.ordered_amount_eur 
;
with detail as (
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
)
select *
from de3_dwh.mart_stock_daily sd
inner join detail d
	on sd.snapshot_date = d.snapshot_date 
	and sd.warehouse_key = d.warehouse_key
	and sd.product_key = d.product_key
where sd.quantity_on_hand is distinct from d.quantity_on_hand
	or sd.snapshot_received <> d.snapshot_received 
;
--отсутствующие ожидаемые даты, категории и каналы
select distinct d.full_date, dc.channel_key
from de3_dwh.dim_date d
inner join de3_dwh.dim_channel dc 
	on d.full_date between '2025-09-01' and '2025-09-07'
left join de3_dwh.mart_cash_daily cd 
	on cd.report_date = d.full_date 
	and cd.channel_key = dc.channel_key 
where cd.report_date is null
;
select distinct d.full_date, p.category_name 
from de3_dwh.dim_date d
inner join (select distinct category_name from de3_dwh.dim_product) p
	on d.full_date between '2025-09-01' and '2025-09-07'
left join de3_dwh.mart_order_category_daily cd 
	on cd.report_date = d.full_date 
	and cd.category_name = p.category_name 
where cd.report_date is null
;
select
	  d.full_date as snapshot_date
	, w.warehouse_key
	, p.product_key
from de3_dwh.dim_date d
inner join de3_src.assortment a 
	on d.full_date between '2025-09-01' and '2025-09-03'
inner join de3_dwh.dim_warehouse w
	on a.warehouse_id = w.warehouse_id 
inner join de3_dwh.dim_product p
	on a.product_id = p.product_id 
left join de3_dwh.mart_stock_daily  cd 
	on cd.snapshot_date = d.full_date
	and cd.warehouse_key = w.warehouse_key
	and cd.product_key = p.product_key
where cd.snapshot_date is null or cd.warehouse_key is null or cd.product_key is null
;
---------------------------
--Плохие клиентские данные:
---------------------------
--Пересечения периодов клиентов
select
    c.history_id,
    c2.history_id
from de3_src.customer_history_bad c
inner join de3_src.customer_history_bad c2
    on c.source_system = c2.source_system
   and c.customer_id = c2.customer_id
   and c.history_id < c2.history_id
   and (c2.valid_to is null or c.valid_from < c2.valid_to)
   and (c.valid_to is null or c2.valid_from < c.valid_to)
;
--несколько открытых версий одного клиента
select *
from de3_src.customer_history_bad  c
where (source_system, customer_id) in (
		select source_system, customer_id 
		from de3_src.customer_history_bad
		where is_current is true
		group by source_system, customer_id
		having count(*) > 1
	)	
;
--valid_to ≤ valid_from
select *
from de3_src.customer_history_bad c
where valid_to <= valid_from
;
--несоответствие is_current и открытого конца
select *
from de3_src.customer_history_bad c
where is_current is distinct from (valid_to is null)
;
-------------------------------
--Исправьте запрос аналитика
--размножение строк на примере o1001
select order_id, count(*) 
from de3_src.orders o
where order_id = 'o1001'
	and o.status = 'completed'
	and not o.is_test
group by o.order_id 
;
select order_id, count(*) 
from de3_src.order_items
where order_id = 'o1001'
group by order_id 
;
select order_id, count(*) 
from de3_src.payments p
where order_id = 'o1001' and p.status = 'succeeded'
group by order_id 
;
select o.order_id, count(*)
from de3_src.orders o
join de3_src.order_items i on i.order_id = o.order_id
join de3_src.payments p on p.order_id = o.order_id
where o.order_id = 'o1001'
	and o.status = 'completed'
	and not o.is_test
	and p.status = 'succeeded'
group by o.order_id
;
--Исправленный запрос:
with oi_agg as (
	select 
		  order_id
		, sum(i.quantity * i.unit_price_eur - i.line_discount_eur) as ordered_amount_eur
	from de3_src.order_items i
	group by order_id
)
, p_agg as (
	select order_id
		, sum(p.amount_eur) as paid_amount_eur
	from de3_src.payments p
	where p.status = 'succeeded'
	group by order_id
)
select
	  o.order_id
	, i.ordered_amount_eur
	, coalesce(p.paid_amount_eur, 0) as paid_amount_eur
from de3_src.orders o
left join oi_agg i 
	on i.order_id = o.order_id
left join p_agg p 
	on p.order_id = o.order_id
where o.status = 'completed'
	and not o.is_test