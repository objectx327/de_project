--1. Посчитайте COUNT(*) и SUM(amount) для payments. 
--   Затем рассчитайте те же показатели после INNER JOIN с orders и после последовательного INNER JOIN с orders и customers.
--Таблица контроля по этапам:
select 'payments' as stage, count(*) as row_count, sum(amount) as total_amount from de2_hw.payments
union
select 'payments+orders' as stage, count(*), sum(p.amount)
from de2_hw.payments p
inner join de2_hw.orders o
	on p.order_id = o.order_id 
union 
select 'payments+orders+customers' as stage, count(*), sum(p.amount)
from de2_hw.payments p
inner join de2_hw.orders o
	on p.order_id = o.order_id 
inner join de2_hw.customers c
	on c.customer_id = o.customer_id
;
--2. LEFT JOIN, сохраняющий каждую строку payments
select 
	  p.payment_id
	, p.order_id
	, p.amount
	, p.payment_status
	, o.customer_id
	, c.customer_name
from de2_hw.payments p
left join de2_hw.orders o
	on p.order_id = o.order_id 
left join de2_hw.customers c
	on c.customer_id = o.customer_id
order by p.payment_id
;
--3. Платежи без заказа и платежи, у которых заказ найден, а клиент отсутствует
select
	  p.payment_id
	  , case when o.order_id is null then 'order_id'
	  		when c.customer_id is null then 'customer_id'
	  		end as missing_id
from de2_hw.payments p
left join de2_hw.orders o
	on p.order_id = o.order_id 
left join de2_hw.customers c
	on c.customer_id = o.customer_id
where o.order_id is null
	or o.order_id is not null and c.customer_id is null
order by p.payment_id

