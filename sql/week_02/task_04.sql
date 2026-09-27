--прямое соединение orders LEFT JOIN order_items LEFT JOIN payments
select 
	  o.order_id
	, count(*) as rows_cnt
	, sum(oi.quantity * oi.unit_price) as total_cst
	, sum(o.order_amount) as total_amount
from de2_hw.orders o
left join de2_hw.order_items oi
	on o.order_id = oi.order_id 
left join de2_hw.payments p
	on o.order_id = p.order_id 
	and p.payment_status = 'paid'
group by o.order_id
order by o.order_id
;
--Агрегат с корректными суммами, одна строка = один заказ
with order_items_grp as 
--агрегат позиций на один order_id
	(
		select  
			  order_id
			, sum(quantity * unit_price) as items_amount  
			, count(*) as item_count
		from de2_hw.order_items oi
		group by order_id	
	)
, payments_grp as 
--агрегат успешных платежей на один order_id
	(
		select 
			  order_id
			, sum(amount) as paid_amount
			, count(*) as paid_count
		from de2_hw.payments p
		where payment_status = 'paid'
		group by order_id
	)
select 	
	  o.order_id, o.order_amount 
	, coalesce(oi.item_count, 0) as item_count
	, coalesce(oi.items_amount, 0) as items_amount
	, coalesce(p.paid_count, 0) as paid_count
	, coalesce(p.paid_amount, 0) as paid_amount
	, o.order_amount - coalesce(p.paid_amount, 0) as balance_amount
from de2_hw.orders o
left join order_items_grp oi
	on o.order_id = oi.order_id 
left join payments_grp p
	on o.order_id = p.order_id
order by o.order_id
;
--пример размноженного заказа
select 
	  o.order_id, o.order_amount, oi.product_code, oi.quantity , oi.unit_price, p.payment_id , p.amount as paid_amount
from de2_hw.orders o
left join de2_hw.order_items oi
	on o.order_id = oi.order_id 
left join de2_hw.payments p
	on o.order_id = p.order_id 
	and p.payment_status = 'paid'
where o.order_id = 2006
;
select  
			  order_id
			, sum(quantity * unit_price) as items_amount  
			, count(*) as item_count
		from de2_hw.order_items oi
		where order_id = 2006
		group by order_id
;
select 
			  order_id
			, sum(amount) as paid_amount
			, count(*) as paid_count
		from de2_hw.payments p
		where payment_status = 'paid' and order_id = 2006
		group by order_id