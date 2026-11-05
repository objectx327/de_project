create schema if not exists de6_stg;
create schema if not exists de6_core;
create schema if not exists de6_etl;

drop table if exists de6_etl.load_runs cascade
;
create table if not exists de6_etl.load_runs (
    run_id          uuid primary key,
    batch_id        text not null,
    mode            text not null,
    manifest        jsonb not null,
    status          text not null check (status in ('running', 'succeeded', 'failed')),
    started_at      timestamptz not null default clock_timestamp(),
    finished_at     timestamptz,
    read_start      timestamptz,
    as_of           timestamptz,
    watermark_before timestamptz,
    stage           text,
    staged_rows     integer,
    prepared_rows   integer,
    applied_rows    integer,
    decision_counts jsonb,
    action_counts   jsonb,
    error_code      text,
    error_message   text,
    staging_ready   boolean NOT NULL DEFAULT false,
    source_run_id   uuid references de6_etl.load_runs(run_id),
    scope           jsonb,
    lower_bound     timestamptz,
    upper_bound     timestamptz,
    check (
        mode in ('full', 'delta', 'replay')
    )
);


drop table if exists de6_etl.pipeline_state
;
create table if not exists de6_etl.pipeline_state (
    pipeline_name   text primary key,
    watermark       timestamptz,
    run_id     uuid references de6_etl.load_runs(run_id),
    updated_at      timestamptz
);


drop table if exists de6_stg.orders_raw
;
create table if not exists de6_stg.orders_raw (
    run_id          uuid not null references de6_etl.load_runs(run_id),
    batch_id        text not null,
    id              bigint 
			generated always as identity
			primary key,
    row_no          integer,
    decision        text,
    applied_action  text,
    source_system   text,
    order_id        text,
    source_version  text,
    operation       text,
    status          text,
    amount          text,
    updated_at      text,
    available_at    text
);

--интеграционная таблица
drop table if exists de6_stg.i_orders
;
create table if not exists de6_stg.i_orders (
    run_id          uuid not null references de6_etl.load_runs(run_id),
    batch_id        text not null,
    source_system   text not null,
    order_id        text not null,
    source_version  bigint not null check (source_version > 0),
    operation       text not null check (operation in ('upsert', 'delete')),
    status          text,
    amount          numeric(12, 2),
    updated_at      timestamptz not null,
    available_at    timestamptz not null,
    primary key (run_id, source_system, order_id),
    check (
        (operation = 'delete' and status is null and amount is null)
        or
        (operation = 'upsert'
         and status is not null and status in ('new', 'paid', 'cancelled')
         and amount is not null and amount > 0 and amount <= 9999999999.99)
    )
);
drop table if exists de6_core.orders_current
;
create table if not exists de6_core.orders_current (
    source_system   text not null,
    order_id        text not null,
    source_version  bigint not null check (source_version > 0),
    status          text,
    amount          numeric(12, 2),
    is_deleted      boolean not null,
    t_updated_at    timestamptz not null,
    primary key (source_system, order_id),
    check (
        (is_deleted and status is null and amount is null)
        or
        (not is_deleted
         and status is not null and status in ('new', 'paid', 'cancelled')
         and amount is not null and amount > 0 and amount <= 9999999999.99)
    )
);