--Полная замена заданной области - подготовка тестового состояния задания 2 на очищенной тестовой БД
INSERT INTO de6_core.orders_current (
    source_system, order_id, source_version,
    status, amount, is_deleted, t_updated_at
)
VALUES
    ('shop', '000001', 1, 'new', 90.00, false, clock_timestamp()),
    ('shop', 'X', 1, 'new', 5.00, false, clock_timestamp()),
    ('partner', '000001', 1, 'paid', 999.00, false, clock_timestamp())
;

--подготовка независимого эталона задания 6
create table if not exists de6_stg.reference_full_1000 (
    source_system text not null,
    order_id text not null,
    status text not null,
    amount numeric(12,2) not null,
    source_version bigint not null,
    primary key (source_system, order_id)
);
truncate de6_stg.reference_full_1000
;
insert into de6_stg.reference_full_1000
    (source_system,order_id,status,amount,source_version)
values
    ('shop','000001','paid',120.00,2),
    ('shop','000003','cancelled',50.00,2),
    ('shop','000004','paid',45.00,2),
    ('shop','000005','paid',30.00,1),
    ('pos','000001','paid',70.00,1);