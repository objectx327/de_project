with source_rows as 
	(
		select * 
		from de2_hw.payment_deliveries
	)
, keyed as 
	(
		select * 
			, row_number() over(partition by source_system, payment_id order by source_updated_at desc, loaded_at desc, delivery_id desc) as rn
		from source_rows 
		where payment_id is not null
	)
, latest as 
	(
		select 
			  delivery_id 
			, source_system 
			, payment_id 
			, order_id 
			, amount 
			, payment_status 
			, created_on 
			, paid_on 
			, source_updated_at 
			, loaded_at 
		from keyed 
		where rn = 1
	)
, valid as 
	(
		select * 
		from latest
		where order_id is not null								--required_fields
			and created_on is not null
			and amount > 0 and amount <= 10000					--invalid_amount 
			and payment_status in ('paid', 'pending', 'failed')	--invalid_status
			and greatest(created_on, paid_on) <= '2026-09-16'	--future_date
			and created_on <= paid_on							--paid_before_created
			and (payment_status = 'paid' and paid_on is not null or paid_on is null)	--status_date_mismatch
	)
, paid as 
	(
		select * 
		from valid
		where paid_on >= '2026-09-14' and paid_on < '2026-09-17'
	)
, matched as 
	(
		select t.*
		from paid t
		inner join de2_hw.orders o
			on t.order_id = o.order_id
	)
, daily as 
	(
		select 
			  paid_on
			, count(*) as payment_count
			, sum(amount) as payment_amount
		from matched
		group by paid_on
	)
--1. дневной результат paid_on, payment_count, paid_amount, отсортированный по дате
select * 
from daily
order by paid_on 
;
with source_rows as 
	(
		select * 
		from de2_hw.payment_deliveries
	)
, keyed as 
	(
		select * 
			, row_number() over(partition by source_system, payment_id order by source_updated_at desc, loaded_at desc, delivery_id desc) as rn
		from source_rows 
		where payment_id is not null
	)
, latest as 
	(
		select 
			  delivery_id 
			, source_system 
			, payment_id 
			, order_id 
			, amount 
			, payment_status 
			, created_on 
			, paid_on 
			, source_updated_at 
			, loaded_at 
		from keyed 
		where rn = 1
	)
, valid as 
	(
		select * 
		from latest
		where order_id is not null								--required_fields
			and created_on is not null
			and amount > 0 and amount <= 10000					--invalid_amount 
			and payment_status in ('paid', 'pending', 'failed')	--invalid_status
			and greatest(created_on, paid_on) <= '2026-09-16'	--future_date
			and created_on <= paid_on							--paid_before_created
			and (payment_status = 'paid' and paid_on is not null or paid_on is null)	--status_date_mismatch
	)
, paid as 
	(
		select * 
		from valid
		where paid_on >= '2026-09-14' and paid_on < '2026-09-17'
	)
, matched as 
	(
		select t.*
		from paid t
		inner join de2_hw.orders o
			on t.order_id = o.order_id
	)
, daily as 
	(
		select 
			  paid_on
			, count(*) as payment_count
			, sum(amount) as payment_amount
		from matched
		group by paid_on
	)
--2. контроль для source_rows, keyed, latest, valid, paid, matched
select 
	'1 source_rows' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from source_rows 
union 
select 
	'2 keyed' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from keyed 
union 
select 
	'3 latest' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from latest 
union 
select 
	'4 valid' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from valid 
union 
select 
	'5 paid' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from paid 
union 
select 
	'6 matched' as stage, count(*) as row_count, count(amount) as amount_count, sum(amount) as total_amount
from matched 
;
with source_rows as 
	(
		select * 
		from de2_hw.payment_deliveries
	)
, keyed as 
	(
		select * 
			, row_number() over(partition by source_system, payment_id order by source_updated_at desc, loaded_at desc, delivery_id desc) as rn
		from source_rows 
		where payment_id is not null
	)
, latest as 
	(
		select 
			  delivery_id 
			, source_system 
			, payment_id 
			, order_id 
			, amount 
			, payment_status 
			, created_on 
			, paid_on 
			, source_updated_at 
			, loaded_at 
		from keyed 
		where rn = 1
	)
, valid as 
	(
		select * 
		from latest
		where order_id is not null								--required_fields
			and created_on is not null
			and amount > 0 and amount <= 10000					--invalid_amount 
			and payment_status in ('paid', 'pending', 'failed')	--invalid_status
			and greatest(created_on, paid_on) <= '2026-09-16'	--future_date
			and created_on <= paid_on							--paid_before_created
			and (payment_status = 'paid' and paid_on is not null or paid_on is null)	--status_date_mismatch
	)
, paid as 
	(
		select * 
		from valid
		where paid_on >= '2026-09-14' and paid_on < '2026-09-17'
	)
, matched as 
	(
		select t.*
		from paid t
		inner join de2_hw.orders o
			on t.order_id = o.order_id
	)
, daily as 
	(
		select 
			  paid_on
			, count(*) as payment_count
			, sum(amount) as payment_amount
		from matched
		group by paid_on
	)
--3. журнал исключений delivery_id
select 
	  s.delivery_id
	, case 
		when t2.delivery_id is null then '2 keyed'
		when t3.delivery_id is null then '3 latest'
		when t4.delivery_id is null then '4 valid'
		when t5.delivery_id is null then '5 paid'
		when t6.delivery_id is null then '6 matched'
	end as excluded_at
from source_rows s
left join keyed t2 
	on s.delivery_id = t2.delivery_id 
left join latest t3
	on s.delivery_id = t3.delivery_id 
left join valid t4
	on s.delivery_id = t4.delivery_id 
left join paid t5
	on s.delivery_id = t5.delivery_id 
left join matched t6 
	on s.delivery_id = t6.delivery_id
;
with source_rows as 
	(
		select * 
		from de2_hw.payment_deliveries
	)
, keyed as 
	(
		select * 
			, row_number() over(partition by source_system, payment_id order by source_updated_at desc, loaded_at desc, delivery_id desc) as rn
		from source_rows 
		where payment_id is not null
	)
, latest as 
	(
		select 
			  delivery_id 
			, source_system 
			, payment_id 
			, order_id 
			, amount 
			, payment_status 
			, created_on 
			, paid_on 
			, source_updated_at 
			, loaded_at 
		from keyed 
		where rn = 1
	)
, valid as 
	(
		select * 
		from latest
		where order_id is not null								--required_fields
			and created_on is not null
			and amount > 0 and amount <= 10000					--invalid_amount 
			and payment_status in ('paid', 'pending', 'failed')	--invalid_status
			and greatest(created_on, paid_on) <= '2026-09-16'	--future_date
			and created_on <= paid_on							--paid_before_created
			and (payment_status = 'paid' and paid_on is not null or paid_on is null)	--status_date_mismatch
	)
, paid as 
	(
		select * 
		from valid
		where paid_on >= '2026-09-14' and paid_on < '2026-09-17'
	)
, matched as 
	(
		select t.*
		from paid t
		inner join de2_hw.orders o
			on t.order_id = o.order_id
	)
, daily as 
	(
		select 
			  paid_on
			, count(*) as payment_count
			, sum(amount) as payment_amount
		from matched
		group by paid_on
	)
--Сверьте SUM(payment_count) дневного результата с числом строк matched, а SUM(paid_amount) - с суммой matched.amount
select d.rows_cnt as daily_rows_cnt, m.rows_cnt as matched_rows_cnt, m.rows_cnt - d.rows_cnt as diff_rows_cnt
	, d.payment_amt as daily_payment_amt, m.payment_amt as matched_payment_amt, m.payment_amt - d.payment_amt as diff_payment_amt
from (select sum(payment_count) as rows_cnt, sum(payment_amount) as payment_amt from daily) d
inner join 
	(select count(*) as rows_cnt, sum(amount) as payment_amt from matched) m 
	on 1=1
	