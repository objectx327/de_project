--Проверочный запрос к буферу BUFFER_TOTALS_SQL =
SELECT 
    COUNT(*), 
    COALESCE(SUM(amount), 0) 
FROM payment_events_batch 
;
--Сверка постоянной таблицы по ключам загрузки TARGET_TOTALS_SQL = 
SELECT 
    COUNT(*), 
    COALESCE(SUM(p.amount), 0) 
FROM payment_events_batch AS b 
JOIN de4_hw.payment_events AS p 
    ON p.payment_id = b.payment_id 
;

-- сравнение всех содержательных полей MISMATCH_SQL = 
SELECT b.payment_id 
FROM payment_events_batch AS b 
LEFT JOIN de4_hw.payment_events AS p 
    ON p.payment_id = b.payment_id 
WHERE p.payment_id IS NULL 
    OR (p.amount, p.currency, p.created_at, p.paid_at, p.status, p.comment) 
        IS DISTINCT FROM 
        (b.amount, b.currency, b.created_at, b.paid_at, b.status, b.comment) 
LIMIT 1 
;
-----------------------------------
--Задание 2 Статистика запуска
select count(*) from de4_hw.payment_events
;

--Задание 4
--Сохранность комментария с апострофом и многострочного комментария:
select payment_id, comment from de4_hw.payment_events where payment_id in ('000001', '000007')
;

--Задание 5 
--payments_delta.csv
select count(*) as rows_cnt, sum(amount) as sum_amount 
from de4_hw.payment_events
;
--payments_conflict.csv - Состояние до/после попытки загрузки для платежей '000002', '000901'
select payment_id, paid_at, amount from de4_hw.payment_events where payment_id in ('000002', '000901')
;
--Задание 6
--Ошибки входного файла
--Состояние базы до/после попыток вставки записей:
select count(*) as rows_cnt, count(distinct payment_id) as count_payment_id, sum(amount) as sum_amount 
from de4_hw.payment_events
;
--Контрольный сбой внутри транзакции
select payment_id from de4_hw.payment_events where payment_id like '0003%' 
;
select count(*) as rows_cnt, count(distinct payment_id) as count_payment_id, sum(amount) as sum_amount 
from de4_hw.payment_events
;
