--Создание таблицы-реестра проверок
drop table if exists de2_hw.dq_payment_deliveries cascade
;
create table if not exists de2_hw.dq_payment_deliveries
(
	  delivery_id 		bigint
	, source_system		text
	, payment_id		bigint
	, rule_code			text
	, constraint pk_order_items
			primary key (delivery_id, rule_code)
)
;
insert into de2_hw.dq_payment_deliveries (delivery_id, source_system, payment_id, rule_code)
--Хотя бы одно из полей payment_id, order_id, amount, payment_status, created_on равно NULL
select 
	  delivery_id
	, source_system
	, payment_id
	, 'required_fields' as rule_code
from de2_hw.payment_deliveries pd
where payment_id is null 
	or order_id is null 
	or amount is null 
	or payment_status is null 
	or created_on is null
union all 
--Пара (source_system, payment_id) встречается больше одного раза. Строки с payment_id IS NULL в эту проверку не включать
select 
	  delivery_id
	, source_system
	, payment_id
	, 'duplicate_key' as rule_code
from 
	(
		select 
			  delivery_id
			, source_system
			, payment_id
			, count(*) over (partition by source_system, payment_id) as cnt
		from de2_hw.payment_deliveries pd
		where payment_id is not null
	) t
where t.cnt > 1
union all
--order_id заполнен, но такого заказа нет в orders
select 
	  delivery_id
	, source_system
	, payment_id
	, 'unknown_order' as rule_code
from de2_hw.payment_deliveries pd
where order_id not in (select order_id from de2_hw.orders)
union all 
--Заполненная сумма ≤ 0 или > 10000. NULL уже проверяется правилом required_fields
select 
	  delivery_id
	, source_system
	, payment_id
	, 'invalid_amount' as rule_code
from de2_hw.payment_deliveries pd
where amount <= 0 or amount > 10000
union all 
--Заполненный статус не входит в pending, paid, failed
select 
	  delivery_id
	, source_system
	, payment_id
	, 'invalid_status' as rule_code
from de2_hw.payment_deliveries pd
where payment_status not in ('pending', 'paid', 'failed')
union all 
--created_on или paid_on позже контрольной даты
select 
	  delivery_id
	, source_system
	, payment_id
	, 'future_date' as rule_code
from de2_hw.payment_deliveries pd
where created_on > '2026-09-16' or paid_on > '2026-09-16'
union all
--Обе даты заполнены и paid_on раньше created_on
select 
	  delivery_id
	, source_system
	, payment_id
	, 'paid_before_created' as rule_code
from de2_hw.payment_deliveries pd
where paid_on < created_on
union all
--Для paid отсутствует paid_on; либо для pending или failed paid_on заполнена
select 
	  delivery_id
	, source_system
	, payment_id
	, 'status_date_mismatch' as rule_code
from de2_hw.payment_deliveries pd
where payment_status = 'paid' and paid_on is null 
	or payment_status in ('pending', 'failed') and paid_on is not null
;
--Количество результатов по каждому rule_code, общее количество результатов и количество различных delivery_id
select distinct
	  rule_code
	, max(cnt_rule_pre) over(partition by rule_code) as cnt_rule
	, max(cnt_total_pre) over() as cnt_total
	, max(cnt_uq_delivery_id_pre) over() as cnt_uq_delivery_id
from 
	(
		select 
			  rule_code
			, row_number() over(partition by rule_code) as cnt_rule_pre
			, row_number() over() as cnt_total_pre
			, dense_rank() over(order by delivery_id) as cnt_uq_delivery_id_pre
		from de2_hw.dq_payment_deliveries pd
	) t
order by rule_code
;
--Отсортируйте подробный реестр по delivery_id, rule_code
select *
from de2_hw.dq_payment_deliveries pd
order by delivery_id, rule_code