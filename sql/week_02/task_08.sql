--1. Подтверждение уникальности ключей перед соединением
select 'ledger_before' as source, count(*), count(distinct payment_id), sum(amount)
from de2_hw.ledger_before
union all
select 'ledger_after' as source, count(*), count(distinct payment_id), sum(amount)
from de2_hw.ledger_after
;
--2. Сверка
select 
	lb.payment_id as lb_payment_id, la.payment_id as la_payment_id
	, lb.amount as before_amount, la.amount as after_amount
	, lb.currency_code as before_currency, la.currency_code as after_currency
	, lb.paid_on as before_paid_on, la.paid_on as after_paid_on
	, la.payment_id is null as missing_after
	, lb.payment_id is null as new_after
	, case when lb.payment_id = la.payment_id and lb.amount is distinct from la.amount then true else false end as amount_changed
	, case when lb.payment_id = la.payment_id and lb.currency_code is distinct from la.currency_code then true else false end as currency_changed
	, case when lb.payment_id = la.payment_id and lb.paid_on is distinct from la.paid_on then true else false end as date_changed
from de2_hw.ledger_before lb 
full join de2_hw.ledger_after la
	on lb.payment_id = la.payment_id 
;
--3. Расхождения
with dq_check as 
	(
		select 
			lb.payment_id as lb_payment_id, la.payment_id as la_payment_id
			, lb.amount as before_amount, la.amount as after_amount
			, lb.currency_code as before_currency, la.currency_code as after_currency
			, lb.paid_on as before_paid_on, la.paid_on as after_paid_on
			, la.payment_id is null as missing_after
			, lb.payment_id is null as new_after
			, case when lb.payment_id = la.payment_id and lb.amount is distinct from la.amount then true else false end as amount_changed
			, case when lb.payment_id = la.payment_id and lb.currency_code is distinct from la.currency_code then true else false end as currency_changed
			, case when lb.payment_id = la.payment_id and lb.paid_on is distinct from la.paid_on then true else false end as date_changed
		from de2_hw.ledger_before lb 
		full join de2_hw.ledger_after la
			on lb.payment_id = la.payment_id 
	)
select 
	  coalesce(lb_payment_id, la_payment_id) as payment_id
	, before_amount, after_amount
	, before_currency, after_currency
	, before_paid_on, after_paid_on
	, missing_after, new_after
	, amount_changed, currency_changed, date_changed
from dq_check 
where missing_after or new_after or amount_changed or currency_changed or date_changed
order by payment_id
