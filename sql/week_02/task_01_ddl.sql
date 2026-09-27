--Создание схем
create schema if not exists de2_raw
;
create schema if not exists de2_core
;
create schema if not exists de2_mart
;
--Удаление старых таблиц
drop table if exists de2_core.payments cascade
;
drop table if exists de2_core.order_items cascade
;
drop table if exists de2_core.orders cascade
;
--Создание новых таблиц
create table if not exists de2_core.orders 
	(
		  order_id					bigint			
			generated always as identity
			primary key
		, order_number				text			unique not null
		, order_date				date			not null
		, order_amount				numeric(12,2)	not null
		, order_currency			text			not null
		, t_updated_dt				timestamptz		not null
		, t_load_id					int				not null
	    , constraint c_order_valid_amount check (order_amount > 0)
	)
;
comment on table de2_core.orders is 'Заказы';
comment on column de2_core.orders.order_id is 'Идентификатор заказа';
comment on column de2_core.orders.order_number is 'Номер заказа';
comment on column de2_core.orders.order_date is 'Дата заказа';
comment on column de2_core.orders.order_amount is 'Итоговая сумма заказа';
comment on column de2_core.orders.order_currency is 'Валюта';
comment on column de2_core.orders.t_updated_dt is 'Дата обновления записи';
comment on column de2_core.orders.t_load_id is 'Идентификатор загрузки'
;
create table if not exists de2_core.order_items 
	(
		  order_id					bigint			not null
		, line_number				integer			not null
		, product_code				text			not null
		, quantity					integer			not null
		, unit_price				numeric(12,2)	not null
		, t_updated_dt				timestamptz		not null
		, t_load_id					int				not null
	    , constraint pk_order_items
			primary key (order_id, line_number)
		, constraint fk_order_items_x_order
			foreign key (order_id)
			references de2_core.orders (order_id)
			on delete no action
	    , constraint c_order_item_valid_line_number check (line_number > 0)
	    , constraint c_order_item_valid_quantity check (quantity > 0)
	    , constraint c_order_item_valid_unit_price check (unit_price >= 0)
	)
;
comment on table de2_core.order_items is 'Позиции в заказе';
comment on column de2_core.order_items.order_id is 'Идентификатор заказа';
comment on column de2_core.order_items.line_number is 'Номер позиции внутри заказа';
comment on column de2_core.order_items.product_code is 'Код продукта';
comment on column de2_core.order_items.quantity is 'Количество';
comment on column de2_core.order_items.unit_price is 'Цена единицы';
comment on column de2_core.order_items.t_updated_dt is 'Дата обновления записи';
comment on column de2_core.order_items.t_load_id is 'Идентификатор загрузки'
;
create table if not exists de2_core.payments 
	(
		  payment_id				bigint			
			generated always as identity
			primary key
		, external_payment_code		text			unique not null
		, order_id					bigint			not null
		, amount					numeric(12,2)	not null
		, payment_status			text			not null
		, created_at				timestamptz		not null
		, paid_at					timestamptz
		, t_updated_dt				timestamptz		not null
		, t_load_id					int				not null
		, unique (payment_id, order_id)
		, constraint fk_payments_x_order
			foreign key (order_id)
			references de2_core.orders (order_id)
			on delete no action
	    , constraint c_payments_valid_amount check (amount > 0)
	    , constraint c_payments_valid_status check (payment_status in ('pending', 'paid', 'failed'))
	    , constraint c_payments_valid_paid_at check (
				   (payment_status = 'paid' and paid_at is not null)
				or (payment_status in ('pending', 'failed') and paid_at is null)
	    	)
	    , constraint c_payments_created_vs_paid check (paid_at > created_at)
	)
;
comment on table de2_core.payments is 'Платежи';
comment on column de2_core.payments.payment_id is 'Идентификатор платежа';
comment on column de2_core.payments.external_payment_code is 'Внешний код платежа';
comment on column de2_core.payments.order_id is 'Номер заказа';
comment on column de2_core.payments.amount is 'Сумма операции';
comment on column de2_core.payments.payment_status is 'Статус операции';
comment on column de2_core.payments.created_at is 'Дата создания платежа';
comment on column de2_core.payments.paid_at is 'Дата оплаты';
comment on column de2_core.payments.t_updated_dt is 'Дата обновления записи';
comment on column de2_core.payments.t_load_id is 'Идентификатор загрузки';