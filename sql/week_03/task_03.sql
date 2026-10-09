set time zone 'Europe/Berlin'
;
--------------------------
--Факт - Позиции в заказе
--------------------------
drop table if exists de3_dwh.fact_order_item cascade
;
create table if not exists de3_dwh.fact_order_item (
	  order_id				text not null			--pk
	, line_no				int not null			--pk
	, ordered_at			timestamptz not null
	, customer_key			bigint not null			--fk
    , source_system 		text not null
    , source_customer_id 	text not null
	, product_key			bigint not null			--fk
	, channel_key 			bigint not null			--fk
	, quantity				int not null
	, unit_price_eur		numeric(12,2) not null
	, line_discount_eur		numeric(12,2) not null
	, line_amount_eur		numeric(12,2) not null
    , t_changed_dttm		timestamptz default current_timestamp
    , primary key (order_id, line_no)
    , constraint fk_f_order_item_dim_cust
			foreign key (customer_key)
			references de3_dwh.dim_customer (customer_key)
			on delete no action
    , constraint fk_f_order_item_dim_prod
			foreign key (product_key)
			references de3_dwh.dim_product (product_key)
			on delete no action
    , constraint fk_f_order_item_dim_chnl
			foreign key (channel_key)
			references de3_dwh.dim_channel (channel_key)
			on delete no action
    , constraint c_f_order_item_valid_qnt check (quantity > 0)
    , constraint c_f_order_item_valid_price check (unit_price_eur >= 0)
    , constraint c_f_order_item_valid_disc check (line_discount_eur >= 0 and line_discount_eur <= quantity * unit_price_eur)
	, constraint c_f_order_item_valid_amt check (line_amount_eur >= 0)
)
;
comment on table de3_dwh.fact_order_item is 'Факт - Позиции в заказе';
comment on column de3_dwh.fact_order_item.order_id is 'Идентификатор заказа';
comment on column de3_dwh.fact_order_item.line_no is 'Номер позиции в заказе';
comment on column de3_dwh.fact_order_item.ordered_at is 'Дата формирования заказа';
comment on column de3_dwh.fact_order_item.customer_key is 'Идентификатор клиента';
comment on column de3_dwh.fact_order_item.source_system is 'Система-источник клиента';
comment on column de3_dwh.fact_order_item.source_customer_id is 'Идентификатор клиента в источнике';
comment on column de3_dwh.fact_order_item.product_key is 'Идентификатор продукта';
comment on column de3_dwh.fact_order_item.channel_key is 'Идентификатор канала';
comment on column de3_dwh.fact_order_item.quantity is 'Количество товара';
comment on column de3_dwh.fact_order_item.unit_price_eur is 'Цена за единицу';
comment on column de3_dwh.fact_order_item.line_discount_eur is 'Сумма скидки на позицию';
comment on column de3_dwh.fact_order_item.line_amount_eur is 'Стоимость позиции с учетом скидки';
comment on column de3_dwh.fact_order_item.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.fact_order_item (
	  order_id
	, line_no
	, ordered_at
	, customer_key
    , source_system
    , source_customer_id
	, product_key
	, channel_key
	, quantity
	, unit_price_eur
	, line_discount_eur
	, line_amount_eur
) 
select 
	  o.order_id
	, oi.line_no
	, o.ordered_at
	, coalesce(dc.customer_key, 0) as customer_key
    , o.source_system
    , o.customer_id as source_customer_id
	, dp.product_key
	, dch.channel_key
	, oi.quantity
	, oi.unit_price_eur
	, oi.line_discount_eur
	, oi.quantity * oi.unit_price_eur - oi.line_discount_eur as line_amount_eur
from de3_src.orders o
inner join de3_src.order_items oi
	on o.order_id = oi.order_id 
	and o.is_test is false
	and o.status = 'completed'
left join de3_dwh.dim_customer dc 
	on o.customer_id = dc.source_customer_id
	and o.source_system = dc.source_system
	and o.ordered_at >= dc.valid_from and (dc.valid_to is null or o.ordered_at < dc.valid_to)
left join de3_dwh.dim_product dp 
	on oi.product_id = dp.product_id 
left join de3_dwh.dim_channel dch
	on dch.channel_id = o.channel_id
;


--------------------------
--Факт - Успешные платежи
--------------------------
drop table if exists de3_dwh.fact_payment cascade
;
create table if not exists de3_dwh.fact_payment (
	  payment_id			text primary key
	, order_id				text not null
	, paid_at				timestamptz not null
	, customer_key			bigint not null			--fk
	, source_system 		text not null
	, source_customer_id 	text not null
	, channel_key			bigint not null			--fk
	, amount_eur			numeric(12,2) not null
	, t_changed_dttm		timestamptz default current_timestamp 
	, constraint fk_f_payment_dim_cust
			foreign key (customer_key)
			references de3_dwh.dim_customer (customer_key)
			on delete no action
	, constraint fk_f_payment_dim_chnl
			foreign key (channel_key)
			references de3_dwh.dim_channel (channel_key)
			on delete no action
	, constraint c_f_payment_valid_amt check (amount_eur >= 0)
)
;
comment on table de3_dwh.fact_payment is 'Факт - Успешные платежи';
comment on column de3_dwh.fact_payment.payment_id is 'Идентификатор платежа';
comment on column de3_dwh.fact_payment.order_id is 'Идентификатор заказа';
comment on column de3_dwh.fact_payment.paid_at is 'Дата и время платежа';
comment on column de3_dwh.fact_payment.customer_key is 'Идентификатор клиента';
comment on column de3_dwh.fact_payment.source_system is 'Система-источник клиента';
comment on column de3_dwh.fact_payment.source_customer_id is 'Идентификатор клиента в источнике';
comment on column de3_dwh.fact_payment.channel_key is 'Идентификатор канала';
comment on column de3_dwh.fact_payment.amount_eur is 'Сумма операции в евро';
comment on column de3_dwh.fact_payment.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.fact_payment (
	  payment_id
	, order_id
	, paid_at
	, customer_key
    , source_system
    , source_customer_id
	, channel_key
	, amount_eur
)
select
	  p.payment_id
	, p.order_id
	, p.occurred_at as paid_at
	, coalesce(dc.customer_key, 0) as customer_key
    , o.source_system
    , o.customer_id as source_customer_id
	, dch.channel_key
	, p.amount_eur
from de3_src.payments p
inner join de3_src.orders o 
	on p.order_id = o.order_id
	and o.is_test is false
left join de3_dwh.dim_customer dc 
	on o.customer_id = dc.source_customer_id
	and o.source_system = dc.source_system
	and p.occurred_at >= dc.valid_from and (dc.valid_to is null or p.occurred_at < dc.valid_to) 
left join de3_dwh.dim_channel dch
	on dch.channel_id = o.channel_id
where p.status = 'succeeded'
;
--------------------------
--Факт - успешные возвраты
--------------------------
drop table if exists de3_dwh.fact_refund cascade
;
create table if not exists de3_dwh.fact_refund (
	  refund_id				text primary key
	, payment_id			text not null
	, order_id				text not null
	, refunded_at			timestamptz not null
	, customer_key			bigint not null			--fk
    , source_system 		text not null
    , source_customer_id 	text not null
	, channel_key			bigint not null			--fk
	, amount_eur			numeric(12,2) not null
	, t_changed_dttm		timestamptz default current_timestamp
	, constraint fk_f_refund_dim_cust
			foreign key (customer_key)
			references de3_dwh.dim_customer (customer_key)
			on delete no action
	, constraint fk_f_refund_dim_chnl
			foreign key (channel_key)
			references de3_dwh.dim_channel (channel_key)
			on delete no action
	, constraint c_f_refund_valid_amt check (amount_eur >= 0)
)
;
comment on table de3_dwh.fact_refund is 'Факт - Успешные возвраты';
comment on column de3_dwh.fact_refund.refund_id is 'Идентификатор операции возврата';
comment on column de3_dwh.fact_refund.payment_id is 'Идентификатор платежа';
comment on column de3_dwh.fact_refund.order_id is 'Идентификатор заказа';
comment on column de3_dwh.fact_refund.refunded_at is 'Дата совершения операции возврата';
comment on column de3_dwh.fact_refund.customer_key is 'Идентификатор клиента';
comment on column de3_dwh.fact_refund.source_system is 'Система-источник клиента';
comment on column de3_dwh.fact_refund.source_customer_id is 'Идентификатор клиента в источнике';
comment on column de3_dwh.fact_refund.channel_key is 'Идентификатор канала';
comment on column de3_dwh.fact_refund.amount_eur is 'Сумма операции в евро';
comment on column de3_dwh.fact_refund.t_changed_dttm is 'Дата и время изменения записи';
;
insert into de3_dwh.fact_refund (
	  refund_id
	, payment_id
	, order_id
	, refunded_at
	, customer_key
	, source_system
	, source_customer_id
	, channel_key
	, amount_eur
)
select 
	  r.refund_id
	, r.payment_id
	, p.order_id
	, r.occurred_at as refunded_at
	, coalesce(dc.customer_key, 0) as customer_key
    , o.source_system
    , o.customer_id as source_customer_id
	, dch.channel_key
	, r.amount_eur
from de3_src.refunds r
inner join de3_src.payments p
	on r.payment_id = p.payment_id
	and p.status = 'succeeded'
inner join de3_src.orders o
	on p.order_id = o.order_id
	and o.is_test is false
left join de3_dwh.dim_customer dc
	on o.customer_id = dc.source_customer_id
	and o.source_system = dc.source_system
	and r.occurred_at >= dc.valid_from and (dc.valid_to is null or r.occurred_at < dc.valid_to)
left join de3_dwh.dim_channel dch
	on dch.channel_id = o.channel_id
where r.status = 'succeeded'
;

---
--Сверка кол-ва записей и сумм в фактах и в исходных данных
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
select 'fact' as lvl, count(*) as rows_cnt, count(distinct payment_id) as cnt_pk,  sum(amount_eur) as sum_amt
from de3_dwh.fact_payment
union 
select 'src' as lvl, count(*) as rows_cnt, count(distinct payment_id) as cnt_pk,  sum(amount_eur) as sum_amt
from de3_src.payments p
where p.order_id in (select order_id from de3_src.orders where is_test is false)
	and p.status = 'succeeded'
;
select 'fact' as lvl, count(*) as rows_cnt, count(distinct refund_id) as cnt_pk,  sum(amount_eur) as sum_amt 
from de3_dwh.fact_refund
union all
select 'src' as lvl, count(*) as rows_cnt, count(distinct refund_id) as cnt_pk,  sum(r.amount_eur) as sum_amt
from de3_src.refunds r
inner join de3_src.payments p
	on r.payment_id = p.payment_id
	and p.status = 'succeeded'
where p.order_id in (select order_id from de3_src.orders where is_test is false)
	and r.status = 'succeeded'
;
--выбранные версии для p05 и p06, а также обработка p11
select payment_id, p.paid_at, c.*
from de3_dwh.fact_payment p
left join de3_dwh.dim_customer c 
	on p.customer_key = c.customer_key
where p.payment_id in ('p05', 'p06', 'p11')
;
--сопоставление сегментов клиента заказа o1003 и сегментов двух его успешных платежей
select oi.order_id, oi.line_no, dc.segment, oi.customer_key, oi.source_system, oi.source_customer_id, dc.history_id, oi.ordered_at, dc.valid_from, dc.valid_to
from de3_dwh.fact_order_item oi
inner join de3_dwh.dim_customer dc
	on oi.customer_key = dc.customer_key
where order_id = 'o1003'
;
select p.payment_id, dc.segment, p.customer_key, p.source_system, p.source_customer_id, dc.history_id, p.paid_at, dc.valid_from, dc.valid_to
from de3_dwh.fact_payment p
inner join de3_dwh.dim_customer dc
	on p.customer_key = dc.customer_key
where order_id = 'o1003'
;
select r.refund_id, dc.segment, r.customer_key, r.source_system, r.source_customer_id, dc.history_id, r.refunded_at, dc.valid_from, dc.valid_to
from de3_dwh.fact_refund r
inner join de3_dwh.dim_customer dc
	on r.customer_key = dc.customer_key
where order_id = 'o1003'
;
select dc.customer_key, dc.source_system, dc.source_customer_id, dc.segment, dc.valid_from, dc.valid_to
from de3_dwh.dim_customer dc
where dc.customer_key in (1, 2)
order by dc.valid_from 
