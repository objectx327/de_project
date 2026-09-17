drop table if exists training.homework_payments
;
create table if not exists training.homework_payments
(
	  id 		integer 		primary key 
	, amount 	numeric(10,2) 	not null
)
;
insert into training.homework_payments (id, amount)
values 
	(1, 120.00),
	(2, 80.00),
	(3, 50.00)
;
select * 
from training.homework_payments
order by id
;
select 
	  count(*) as rows_cnt
	, sum(amount) as sum_amount
from training.homework_payments