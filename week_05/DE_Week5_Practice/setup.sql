-- Run once in your existing learning database, using your SQL client.
-- This script creates a separate schema and does not delete existing data.
create schema if not exists de5_hw;

create table if not exists de5_hw.load_runs (
    run_id uuid primary key,
    source_system text not null,
    source_account_id text not null,
    period_from timestamptz not null,
    period_to timestamptz not null,
    started_at timestamptz not null default current_timestamp,
    finished_at timestamptz,
    status text not null check (status in ('running', 'succeeded', 'failed')),
    received_count integer not null default 0 check (received_count >= 0),
    accepted_count integer not null default 0 check (accepted_count >= 0),
    rejected_count integer not null default 0 check (rejected_count >= 0),
    inserted_count integer not null default 0 check (inserted_count >= 0),
    updated_count integer not null default 0 check (updated_count >= 0),
    skipped_count integer not null default 0 check (skipped_count >= 0),
    error_summary text,
    check (period_from < period_to)
);

create table if not exists de5_hw.orders (
    source_system text not null,
    source_account_id text not null,
    order_id text not null,
    customer_id text,
    status text not null check (status in ('pending', 'paid', 'cancelled', 'refunded')),
    currency text not null check (currency = 'EUR'),
    total_amount numeric(14,2) not null check (total_amount >= 0),
    created_at timestamptz not null,
    source_updated_at timestamptz not null,
    received_at timestamptz not null,
    run_id uuid not null references de5_hw.load_runs(run_id),
    raw_response_id uuid not null,
    record_no integer not null check (record_no > 0),
    payload jsonb not null,
    primary key (source_system, source_account_id, order_id),
    check (source_updated_at >= created_at)
);

create table if not exists de5_hw.order_items (
    source_system text not null,
    source_account_id text not null,
    order_id text not null,
    line_id text not null,
    sku text not null,
    quantity integer not null check (quantity > 0),
    unit_price numeric(14,2) not null check (unit_price >= 0),
    primary key (source_system, source_account_id, order_id, line_id),
    foreign key (source_system, source_account_id, order_id)
        references de5_hw.orders(source_system, source_account_id, order_id)
);

create table if not exists de5_hw.load_state (
    source_system text not null,
    source_account_id text not null,
    completed_to timestamptz not null,
    run_id uuid not null references de5_hw.load_runs(run_id),
    primary key (source_system, source_account_id)
);
