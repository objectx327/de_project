--------------------------
--Витрина - Заказы по категориям
--------------------------
drop view if exists de3_dwh.mart_order_category_daily cascade
;
create or replace view de3_dwh.mart_order_category_daily
(
	  report_date
	, category_name
	, ordered_units
	, ordered_amount_eur
) as 
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
;

comment on view de3_dwh.mart_order_category_daily is 'Витрина - Заказы по категориям';
comment on column de3_dwh.mart_order_category_daily.report_date is 'Отчетная дата';
comment on column de3_dwh.mart_order_category_daily.category_name is 'Наименование категории';
comment on column de3_dwh.mart_order_category_daily.ordered_units is 'Количество заказанных товаров';
comment on column de3_dwh.mart_order_category_daily.ordered_amount_eur is 'Стоимость завершенных заказов';

select * from de3_dwh.mart_order_category_daily
;
--отменённый заказ o1005, тестовый заказ o1008 исключены; завершённый заказ o1010 без успешного платежа остался
select 
	  dp.category_name
	, date_trunc('day', oi.ordered_at, 'Europe/Berlin') as ordered_dt
	, oi.order_id
	, oi.quantity
	, oi.line_amount_eur
from de3_dwh.fact_order_item oi
inner join de3_dwh.dim_product dp
		on oi.product_key = dp.product_key
where oi.order_id in ('o1005', 'o1008', 'o1010')
;
--Сравнение с суммой подходящих детальных позиций за тот же период 
select 'mart' as lvl
	, sum(ordered_units) as sum_ordered_units
	, sum(ordered_amount_eur) as sum_ordered_amount_eur
from de3_dwh.mart_order_category_daily
union all 
select 'src' as lvl
	, sum(quantity) as sum_ordered_units
	, sum(oi.quantity * oi.unit_price_eur - oi.line_discount_eur) as sum_ordered_amount_eur
from de3_src.order_items oi
inner join de3_src.orders o
	on o.order_id = oi.order_id
	and o.is_test is false
	and o.status = 'completed'	
where date_trunc('day', o.ordered_at, 'Europe/Berlin') between '2025-09-01' and '2025-09-07'
;
--Сравнение с суммой подходящих детальных позиций за тот же период по каждой категории и дате
select 
	  dp.category_name
	, date_trunc('day', o.ordered_at, 'Europe/Berlin') as ordered_dt
	, sum(oi.quantity) as ordered_units
	, sum(oi.quantity * oi.unit_price_eur - oi.line_discount_eur) as ordered_amount_eur
from de3_src.order_items oi
inner join de3_src.orders o
	on o.order_id = oi.order_id
	and o.is_test is false
	and o.status = 'completed'
inner join de3_dwh.dim_product dp
	on oi.product_id = dp.product_id	
where date_trunc('day', o.ordered_at, 'Europe/Berlin') between '2025-09-01' and '2025-09-07'
group by dp.category_name, ordered_dt