--------------------------
--Витрина - Движение денег
--------------------------
drop view if exists de3_dwh.mart_cash_daily cascade
;
create or replace view de3_dwh.mart_cash_daily
(
	  report_date
	, channel_key
	, payment_count
	, paid_amount_eur
	, refund_count
	, refunded_amount_eur
	, net_cash_eur
	, avg_payment_eur
) as 
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
;
comment on view de3_dwh.mart_cash_daily is 'Витрина - Движение денег';
comment on column de3_dwh.mart_cash_daily.report_date is 'Отчетная дата';
comment on column de3_dwh.mart_cash_daily.channel_key is 'Канал заказа';
comment on column de3_dwh.mart_cash_daily.payment_count is 'Количество успешных нетестовых платежей';
comment on column de3_dwh.mart_cash_daily.paid_amount_eur is 'Сумма успешных нетестовых платежей';
comment on column de3_dwh.mart_cash_daily.refund_count is 'Количество подходящих успешных возвратов';
comment on column de3_dwh.mart_cash_daily.refunded_amount_eur is 'Сумма подходящих успешных возвратов';
comment on column de3_dwh.mart_cash_daily.net_cash_eur is 'Разница суммы платежей и суммы возвратов';
comment on column de3_dwh.mart_cash_daily.avg_payment_eur is 'Средняя сумма платежей';
;
--Куда попали платежи p01, p06
select 
	  p.channel_key
	, date_trunc('day', p.paid_at, 'Europe/Berlin') as paid_dt
	, p.payment_id
	, p.paid_at
from de3_dwh.fact_payment p
where p.payment_id in ('p01', 'p06')
;
--Куда попал возврат r02
select 
	  r.channel_key
	, date_trunc('day', r.refunded_at, 'Europe/Berlin') as refunded_dt
	, r.refund_id
	, r.refunded_at
from de3_dwh.fact_refund r
where r.refund_id = 'r02'
;
--Статистика
select cast(channel_key as varchar) as channel_key
	, count(*) as rows_cnt
	, sum(payment_count) as total_payment_count
	, sum(paid_amount_eur) as total_paid_amount_eur
	, sum(refund_count) as total_refund_count
	, sum(refunded_amount_eur) as total_refunded_amount_eur
	, sum(net_cash_eur) as total_net_cash_eur
	, case when sum(payment_count) != 0 then sum(paid_amount_eur) / sum(payment_count) end as total_avg_payment_eur
from de3_dwh.mart_cash_daily
group by channel_key
union 
select 'total' as channel_key
	, count(*) as rows_cnt
	, sum(payment_count) as total_payment_count
	, sum(paid_amount_eur) as total_paid_amount_eur
	, sum(refund_count) as total_refund_count
	, sum(refunded_amount_eur) as total_refunded_amount_eur
	, sum(net_cash_eur) as total_net_cash_eur
	, case when sum(payment_count) != 0 then sum(paid_amount_eur) / sum(payment_count) end as total_avg_payment_eur
from de3_dwh.mart_cash_daily
;
--Дубли по ключам
select report_date, channel_key, count(*)
from de3_dwh.mart_cash_daily
group by report_date, channel_key 
having count(*) > 1