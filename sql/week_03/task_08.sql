--1. Сколько денег поступило, сколько возвращено и каков net_cash_eur за 1-7 сентября по каждому каналу и в целом
select cast(channel_key as varchar) as channel_key, sum(paid_amount_eur) as paid_amount_eur, sum(refunded_amount_eur) as refunded_amount_eur, sum(net_cash_eur) as net_cash_eur
from de3_dwh.mart_cash_daily
group by channel_key
union  
select 'total' as channel_key, sum(paid_amount_eur) as paid_amount_eur, sum(refunded_amount_eur) as refunded_amount_eur, sum(net_cash_eur) as net_cash_eur
from de3_dwh.mart_cash_daily
;
--В какие даты общий net_cash_eur отрицателен
select c.report_date, sum(net_cash_eur) as net_cash_eur
from de3_dwh.mart_cash_daily c
group by c.report_date
having sum(net_cash_eur) < 0
;
--2. Какова средняя сумма успешного платежа за период? Сравните её с простым средним дневных средних по всем каналам вместе
with daily as (
    select
        report_date,
		sum(paid_amount_eur) as sum_paid_amount_eur,
		sum(payment_count) as sum_payment_count,
        sum(paid_amount_eur)
            / nullif(sum(payment_count), 0) as daily_avg
    from de3_dwh.mart_cash_daily
    group by report_date
)
select round(sum(sum_paid_amount_eur)/sum(sum_payment_count), 2) as counted_avg_payment
	, round(avg(daily_avg), 2) as avg_daily_payment
from daily
;
--3. Какова стоимость завершённых заказов по каждой категории? Почему её общий итог отличается от суммы платежей и от net_cash_eur?
select category_name, sum(ordered_amount_eur) as ordered_amount_eur
from de3_dwh.mart_order_category_daily
group by category_name 
;
select 'mart_cash_daily' as mart, sum(cd.paid_amount_eur), sum(cd.refunded_amount_eur), sum(cd.net_cash_eur)
from de3_dwh.mart_cash_daily cd
;
select  sum(ordered_amount_eur) as ordered_amount_eur
from de3_dwh.mart_order_category_daily
;
--4. Сколько сентябрьских успешных платежей и на какую сумму относятся к сегменту premium на момент платежа? Сколько денег приходится на неизвестного клиента?
select count(*), sum(p.amount_eur)
from de3_dwh.fact_payment p 
inner join de3_dwh.dim_customer dc 
	on p.customer_key = dc.customer_key 
where dc.segment = 'premium'
	and date_trunc('month', p.paid_at) = '2025-09-01'
;
select count(*), sum(p.amount_eur)
from de3_dwh.fact_payment p 
where p.customer_key = 0
	and date_trunc('month', p.paid_at) = '2025-09-01'
;
--5. Можно ли опубликовать полный остаток каждого склада на конец 3 сентября? Укажите отсутствующие данные и отдельно покажите известную часть остатка
select * 
from de3_dwh.mart_stock_daily 
where snapshot_date = '2025-09-03'
and snapshot_received  is false
;
select * 
from de3_dwh.mart_stock_daily 
where snapshot_date = '2025-09-03'
and snapshot_received  is true
;
