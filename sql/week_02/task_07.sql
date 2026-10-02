--Подробная история с оконными показателями
select 
	  order_id, payment_id, paid_at, amount
	, lag(amount) over(partition by order_id order by paid_at, payment_id) as previous_amount
	, lead(amount) over(partition by order_id order by paid_at, payment_id) as next_amount
	, amount - lag(amount) over(partition by order_id order by paid_at, payment_id) as amount_change
	, sum(amount) over(partition by order_id order by paid_at, payment_id rows between unbounded preceding and current row) as running_amount
from de2_hw.payments p 
where p.order_id in (select order_id from de2_hw.orders o )
	and p.payment_status = 'paid'
	and paid_at >= '2026-09-14 00:00:00+00' and paid_at < '2026-09-17 00:00:00+00'
order by order_id, paid_at, payment_id
;
--полный рейтинг order_id, paid_amount, amount_rank, amount_dense_rank
with pre_amount as 
(
	select 
		  order_id 
		, sum(amount) as paid_amount 
	from de2_hw.payments p 
	where p.order_id in (select order_id from de2_hw.orders o )
		and p.payment_status = 'paid'
		and paid_at >= '2026-09-14 00:00:00+00' and paid_at < '2026-09-17 00:00:00+00'
	group by order_id 
)
select 
	  order_id
	, paid_amount
	, rank() over(order by paid_amount desc) as amount_rank
	, dense_rank() over(order by paid_amount desc) as amount_dense_rank
from pre_amount
;
--заказы с двумя наибольшими различными суммами
with pre_amount as 
(
	select 
		  order_id 
		, sum(amount) as paid_amount 
	from de2_hw.payments p 
	where p.order_id in (select order_id from de2_hw.orders o )
		and p.payment_status = 'paid'
		and paid_at >= '2026-09-14 00:00:00+00' and paid_at < '2026-09-17 00:00:00+00'
	group by order_id 
)
, ranked as 
(
	select 
		  order_id
		, paid_amount
		, rank() over(order by paid_amount desc) as amount_rank
		, dense_rank() over(order by paid_amount desc) as amount_dense_rank
	from pre_amount
)
select * 
from ranked
where amount_dense_rank <= 2