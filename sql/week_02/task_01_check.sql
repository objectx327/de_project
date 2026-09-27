--Предварительная очистка таблиц
truncate table de2_core.orders restart identity cascade;
truncate table de2_core.order_items cascade;
truncate table de2_core.payments restart identity cascade;

--Корректная вставка:
insert into de2_core.orders (order_number, order_date, order_amount, order_currency, t_updated_dt, t_load_id) values
	('111', '2026-09-16', 100, 'EUR', current_timestamp, 0),
	('112', '2026-09-16', 150, 'EUR', current_timestamp, 0),
	('113A', '2026-09-16', 300, 'EUR', current_timestamp, 0)
;
select * from de2_core.orders
;
insert into de2_core.order_items (order_id, line_number, product_code, quantity, unit_price, t_updated_dt, t_load_id) values
	(1, 1, '1000aa', 1, 5, current_timestamp, 0),
	(1, 2, '2000', 10, 2, current_timestamp, 0),
	(2, 1, '1000aa', 1, 5, current_timestamp, 0)
;
select * from de2_core.order_items
;
insert into de2_core.payments (external_payment_code, order_id, amount, payment_status, created_at, paid_at, t_updated_dt, t_load_id) values
	('1111', 1, 100, 'paid', '2026-09-16 14:00:00+00', '2026-09-26 14:00:00+00', current_timestamp, 0),
	('1112', 2, 150, 'pending', '2026-09-16 14:00:00+00', null, current_timestamp, 0),
	('1113', 3, 300, 'failed', '2026-09-16 14:00:00+00', null, current_timestamp, 0)
;
select * from de2_core.payments
;
--Проверка отказов:
--1. повтор order_number
insert into de2_core.orders (order_number, order_date, order_amount, order_currency, t_updated_dt, t_load_id) values
	('111', '2026-09-16', 100, 'EUR', current_timestamp, 0)

--2. платеж без существующего заказа
insert into de2_core.payments (external_payment_code, order_id, amount, payment_status, created_at, paid_at, t_updated_dt, t_load_id) values 
	('123', 999, 1, 'pending', '2026-09-16 14:00:00+00', null, current_timestamp, 0)

--3. quantity = 0
insert into de2_core.order_items (order_id, line_number, product_code, quantity, unit_price, t_updated_dt, t_load_id) values
	(1, 1, '3000', 0, 5, current_timestamp, 0)

--4. статус paid без paid_at
insert into de2_core.payments (external_payment_code, order_id, amount, payment_status, created_at, paid_at, t_updated_dt, t_load_id) values 
	('123', 1, 1, 'paid', '2026-09-16 14:00:00+00', null, current_timestamp, 0)


