select 
	current_database() as database_name,
	current_user as user_name, 
	1 + 1 as check_result
;
create schema if not exists training
;
create table if not exists training.setup_check (
	id integer primary key,
	amount numeric(10,2) not null
)
;
insert into training.setup_check(id, amount)
values 
	(1, 100.00),
	(2, 50.00)
on conflict (id) 
do update set amount = excluded.amount
;
select count(*) as row_count, sum(amount) as total_amount 
from training.setup_check