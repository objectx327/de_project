create schema if not exists de7_etl;
create schema if not exists de7_stg;
create schema if not exists de7_core;
-----------------------------------------
--технические таблицы - логи запусков и тд
create table if not exists de7_etl.load_runs (
    load_id uuid primary key,
    run_id text not null unique,
    report_date date not null,
    source_path text not null,
    --манифест
    source_version text,        -- manifest.snapshot_id
    source_system text,
    manifest_period_start timestamptz,
    manifest_period_end timestamptz,
    complete boolean,
    covered_until timestamptz,
    expected_row_count bigint,
    expected_sums jsonb,
    manifest_raw text,
    manifest_errors text[] not null default '{}',
    csv_errors text[] not null default '{}',
    --ожидаемые границы, вычисленные для отчетной даты в europe/berlin
    period_start timestamptz not null,
    period_end timestamptz not null,
    --статусы чтения
    read_complete boolean not null default false,
    read_rows bigint,
    accepted_rows bigint,
    rejected_rows bigint,
    prepared_rows bigint,
    staging_hash text,
    prepared_hash text,
    candidate_hash text,
    state text not null default 'created',
    last_error text,
    created_at timestamptz not null default clock_timestamp(),
    finished_at timestamptz,
    published_at timestamptz,
    constraint load_runs_period_chk check (period_start < period_end),
    constraint load_runs_state_chk check (
        state in ('created', 'staged', 'prepared', 'checked',
                  'blocked', 'published', 'failed')
    )
)
;

--история выполнения отдельных задач и попыток с причинами ошибок и счетчиками
create table if not exists de7_etl.load_events (
    event_id bigint generated always as identity primary key,
    load_id uuid not null references de7_etl.load_runs(load_id),
    run_id text not null,
    report_date date not null,
    period_start timestamptz not null,
    period_end timestamptz not null,
    task_id text not null,
    attempt integer not null check (attempt > 0),
    stage text not null,
    event_status text not null check (
        event_status in ('STARTED', 'SUCCESS', 'WARN', 'FAILED')
    ),
    counters jsonb,
    reason text,
    event_at timestamptz not null default clock_timestamp()
)
;

---------------------------------
--справочники - разовая загрузка
create table if not exists de7_stg.ref_channels (
    channel_id      text primary key,
    channel_name    text not null,
    updated_at      timestamptz not null default clock_timestamp()
)
;

insert into de7_stg.ref_channels (channel_id, channel_name)
values
    ('web', 'Сайт'),
    ('app', 'Приложение'),
    ('partner', 'Партнёр')
on conflict (channel_id) do nothing
;

----------------------
--stage
-- исходные записи
create table if not exists de7_stg.payments_raw (
    load_id uuid not null references de7_etl.load_runs(load_id),
    row_no bigint not null,
    file_line_no bigint not null,
    raw_csv text not null,
    raw_record jsonb not null,
    structure_error text,
    primary key (load_id, row_no),
    check (row_no > 0),
    check (file_line_no > 0)
)
;

--результаты попыток преобразования
create table if not exists de7_stg.payments_typed (
    load_id uuid not null,
    row_no bigint not null,
    source_system text,
    payment_id text,
    order_id text,
    paid_at timestamptz,
    paid_at_error text,
    amount numeric,             
    amount_scale integer,
    amount_error text,
    currency text,
    status text,
    channel_id text,
    primary key (load_id, row_no),
    foreign key (load_id, row_no)
        references de7_stg.payments_raw(load_id, row_no)
)
;

--отклонения
create table if not exists de7_stg.rejections (
    load_id uuid not null,
    row_no bigint not null,
    file_line_no bigint not null,
    raw_csv text not null,
    raw_record jsonb not null,
    reasons text[] not null,
    primary key (load_id, row_no),
    foreign key (load_id, row_no)
        references de7_stg.payments_raw(load_id, row_no),
    check (cardinality(reasons) > 0)
)
;

-- принятые платежи после соединения со справочником каналов
create table if not exists de7_stg.payments_prepared (
    prepared_id bigint generated always as identity primary key,
    load_id uuid not null,
    row_no bigint not null,
    report_date date not null,
    source_system text not null,
    payment_id text not null,
    order_id text not null,
    paid_at timestamptz not null,
    amount numeric not null,
    currency text not null,
    status text not null,
    channel_id text not null,      -- 'unknown' for an allowed missing channel
    channel_name text,
    foreign key (load_id, row_no)
        references de7_stg.payments_typed(load_id, row_no)
)
;

-- предварительная агрегация до публилкации (интеграционная таблица)
create table if not exists de7_stg.i_payments_daily (
    load_id uuid not null references de7_etl.load_runs(load_id),
    report_date date not null,
    currency text not null,
    channel_id text not null,
    payment_count bigint not null check (payment_count > 0),
    amount_sum numeric not null check (amount_sum >= 0),
    primary key (load_id, report_date, currency, channel_id)
)
;

--результаты проверок dq
create table if not exists de7_etl.dq_results (
    result_id bigint generated always as identity primary key,
    rule_name text not null,
    rule_version text not null,
    load_id uuid not null references de7_etl.load_runs(load_id),
    source_version text,
    run_id text not null,
    task_id text not null,
    attempt integer not null check (attempt > 0),
    period_start timestamptz not null,
    period_end timestamptz not null,
    checked_at timestamptz not null default clock_timestamp(),
    expected jsonb,
    actual jsonb,
    unit text not null,
    severity text not null check (severity in ('CRITICAL', 'WARNING')),
    status text not null check (
        status in ('PASS', 'WARN', 'FAIL', 'NOT_EVALUATED', 'ERROR')
    ),
    checked_rows bigint,
    bad_rows bigint,
    detail text,
    unique (load_id, run_id, task_id, attempt, rule_name, rule_version)
)
;

--подготовленный к публилкации набор, прошедший проверки
create table if not exists de7_etl.publication_approvals (
    load_id uuid primary key references de7_etl.load_runs(load_id),
    run_id text not null,
    prepared_hash text not null,
    candidate_hash text not null,
    dq_result_ids bigint[] not null,
    approved_at timestamptz not null default clock_timestamp(),
    check (cardinality(dq_result_ids) > 0)
);

-- витрина агрегирована по date + currency + channel
create table if not exists de7_core.payments_daily (
    report_date date not null,
    currency text not null,
    channel_id text not null,       -- can be 'unknown'
    payment_count bigint not null check (payment_count > 0),
    amount_sum numeric not null check (amount_sum >= 0),
    load_id uuid not null references de7_etl.load_runs(load_id),
    source_version text not null,
    run_id text not null,
    period_start timestamptz not null,
    period_end timestamptz not null,
    published_at timestamptz not null default clock_timestamp(),
    primary key (report_date, currency, channel_id),
    check (period_start < period_end)
);

--проверки
create or replace view de7_etl.row_checks as
with src as (
    select
        r.load_id, r.row_no, r.raw_record, r.structure_error,
        t.row_no is not null as typed_exists,
        t.paid_at, t.paid_at_error,
        t.amount, t.amount_scale, t.amount_error,
        l.source_system as manifest_source_system,
        l.period_start, l.period_end,
        r.raw_record->>'source_system' as source_system,
        r.raw_record->>'payment_id' as payment_id,
        r.raw_record->>'currency' as currency,
        r.raw_record->>'status' as payment_status,
        r.raw_record->>'channel_id' as channel_id,
        count(*) over (
            partition by r.load_id,
                         r.raw_record->>'source_system',
                         r.raw_record->>'payment_id'
        ) as key_count,
        array(
            select f.field_name
            from unnest(array[
                'source_system', 'payment_id', 'order_id', 'paid_at',
                'amount', 'currency', 'status'
            ]) as f(field_name)
            where trim(coalesce(r.raw_record->>f.field_name, '')) = ''
        ) as missing_fields
    from de7_stg.payments_raw r
    join de7_etl.load_runs l on l.load_id = r.load_id
    left join de7_stg.payments_typed t
      on t.load_id = r.load_id and t.row_no = r.row_no
)
select src.load_id, src.row_no, rule.rule_name,
       rule.rule_status as status, rule.detail
from src
cross join lateral (values
    ('csv_record_shape',
     case when src.structure_error is null then 'PASS' else 'FAIL' end,
     coalesce(src.structure_error, 'Структура CSV-записи корректна')),
    ('typed_row_present',
     case when src.typed_exists then 'PASS' else 'FAIL' end,
     case when src.typed_exists then 'Строка преобразована'
          else 'Нет результата преобразования' end),
    ('required_fields',
     case when cardinality(src.missing_fields) = 0 then 'PASS' else 'FAIL' end,
     case when cardinality(src.missing_fields) = 0
          then 'Все обязательные поля заполнены'
          else 'Не заполнены: ' || array_to_string(src.missing_fields, ', ') end),
    ('source_system_match',
     case when src.manifest_source_system is null or
                    trim(coalesce(src.source_system, '')) = ''
          then 'NOT_EVALUATED'
          when src.source_system = src.manifest_source_system then 'PASS'
          else 'FAIL' end,
     'Источник должен совпадать с manifest.source_system'),
    ('payment_key_unique',
     case when trim(coalesce(src.source_system, '')) = '' or
                    trim(coalesce(src.payment_id, '')) = ''
          then 'NOT_EVALUATED'
          when src.key_count = 1 then 'PASS' else 'FAIL' end,
     'Число записей с ключом: ' || src.key_count::text),
    ('paid_at_parse',
     case when trim(coalesce(src.raw_record->>'paid_at', '')) = ''
                    or not src.typed_exists then 'NOT_EVALUATED'
          when src.paid_at IS null then 'FAIL' else 'PASS' end,
     coalesce(src.paid_at_error, 'Преобразование paid_at')), 
    ('paid_at_in_period',
     case when src.paid_at IS null then 'NOT_EVALUATED'
          when src.paid_at >= src.period_start and src.paid_at < src.period_end
          then 'PASS' else 'FAIL' end,
     'Период: [period_start, period_end); paid_at с часовым поясом'),
    ('amount_parse',
     case when trim(coalesce(src.raw_record->>'amount', '')) = ''
                    or not src.typed_exists then 'NOT_EVALUATED'
          when src.amount IS null then 'FAIL' else 'PASS' end,
     coalesce(src.amount_error, 'Преобразование amount')), 
    ('amount_valid',
     case when src.amount IS null or src.amount_scale IS null
          then 'NOT_EVALUATED'
          when src.amount::text not in ('NaN', 'Infinity', '-Infinity')
               and src.amount > 0 and src.amount_scale <= 2
          then 'PASS' else 'FAIL' end,
     'Конечное число > 0; максимум 2 знака после точки'),
    ('currency_allowed',
     case when trim(coalesce(src.currency, '')) = ''
          then 'NOT_EVALUATED'
          when src.currency in ('EUR', 'USD') then 'PASS' else 'FAIL' end,
     'Разрешены EUR и USD'),
    ('status_allowed',
     case when trim(coalesce(src.payment_status, '')) = ''
          then 'NOT_EVALUATED'
          when src.payment_status in ('succeeded', 'failed') then 'PASS'
          else 'FAIL' end,
     'Разрешены succeeded и failed'),
    ('channel_known',
     case when trim(coalesce(src.channel_id, '')) = '' then 'PASS'
          when exists (
              select 1 from de7_stg.ref_channels c
              where c.channel_id = src.channel_id
          ) then 'PASS' else 'FAIL' end,
     'Пустой канал допустим на уровне записи; долю проверяем отдельно')
) as rule(rule_name, rule_status, detail)
;

-- accepted = typed - rejections
create or replace view de7_stg.accepted as
select t.*
from de7_stg.payments_typed t
where not exists (
    select 1 from de7_stg.rejections r
    where r.load_id = t.load_id and r.row_no = t.row_no
);