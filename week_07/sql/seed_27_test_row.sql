INSERT INTO de7_core.payments_daily (
    report_date,
    currency,
    channel_id,
    payment_count,
    amount_sum,
    load_id,
    source_version,
    run_id,
    period_start,
    period_end
)
SELECT
    DATE '2026-10-27',
    currency,
    channel_id,
    payment_count,
    amount_sum,
    load_id,
    source_version,
    run_id,
    period_start,
    period_end
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-24'
ORDER BY currency, channel_id
LIMIT 1;