-- === Базовые загрузки / Проверки ===
-- [01] Проверка из отчёта
SELECT
    report_date,
    currency,
    channel_id,
    payment_count,
    amount_sum
FROM de7_core.payments_daily
ORDER BY report_date, currency, channel_id;

-- [02] Проверка из отчёта
SELECT
    load_id,
    run_id,
    report_date,
    source_version,
    state,
    read_rows,
    accepted_rows,
    rejected_rows,
    prepared_rows,
    last_error
FROM de7_etl.load_runs
ORDER BY created_at DESC
LIMIT 20;


-- === Базовые загрузки / Проверки / Проверка баланса записей ===
-- [03] Проверка из отчёта
SELECT
    l.load_id,
    l.read_rows,
    l.accepted_rows,
    l.rejected_rows,
    l.prepared_rows,
    l.read_rows = l.accepted_rows + l.rejected_rows AS balance_ok
FROM de7_etl.load_runs l;

-- === Базовые загрузки / Проверки / Проверка баланса записей на примере последнего запуска: ===
-- [04] Проверка из отчёта
WITH p AS (SELECT '090b0a59-8f3d-4c70-ae18-33c32c374509'::uuid AS load_id),
counts AS (
    SELECT
        (SELECT count(*) FROM de7_stg.payments_raw r WHERE r.load_id=p.load_id) AS raw_count,
        (SELECT count(*) FROM de7_stg.payments_typed t WHERE t.load_id=p.load_id) AS typed_count,
        (SELECT count(*) FROM de7_stg.rejections r WHERE r.load_id=p.load_id) AS rejected_count,
        (SELECT count(*) FROM de7_stg.accepted a WHERE a.load_id=p.load_id) AS accepted_count,
        (SELECT count(*) FROM de7_stg.payments_prepared x WHERE x.load_id=p.load_id) AS prepared_count
    FROM p
)
SELECT *, raw_count = accepted_count + rejected_count AS balance_ok,
          accepted_count = prepared_count AS prepared_count_ok
FROM counts;


-- === Базовые загрузки / Проверки / DQ ===
-- [05] Проверка из отчёта
SELECT
    load_id,
	task_id,
    attempt,
    status,
    COUNT(*) AS rules
FROM de7_etl.dq_results
--WHERE load_id = '090b0a59-8f3d-4c70-ae18-33c32c374509'::uuid
GROUP BY load_id, task_id, attempt, status
ORDER BY load_id, task_id, attempt, status;

-- === Базовые загрузки / Повтор 25 октября ===
-- [06] Проверка из отчёта
SELECT
    report_date,
    currency,
    channel_id,
    payment_count,
    amount_sum,
    load_id,
    source_version
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-25'
ORDER BY currency, channel_id;

-- [07] Проверка из отчёта
SELECT DISTINCT load_id, source_version
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-25';

-- [08] Проверка из отчёта
WITH old_result AS (
    SELECT report_date, currency, channel_id,
           payment_count, amount_sum
    FROM de7_stg.i_payments_daily
    WHERE load_id = '5ff6697d-4f6c-48e9-874d-d15a77eb08d7'::uuid
),
new_result AS (
    SELECT report_date, currency, channel_id,
           payment_count, amount_sum
    FROM de7_core.payments_daily
    WHERE report_date = DATE '2026-10-25'
),
differences AS (
    (SELECT * FROM old_result
     EXCEPT ALL
     SELECT * FROM new_result)

    UNION ALL

    (SELECT * FROM new_result
     EXCEPT ALL
     SELECT * FROM old_result)
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === Отрицательные сценарии Data Quality ===
-- [09] Проверка из отчёта
SELECT *
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-25'
ORDER BY currency, channel_id;


-- === Отрицательные сценарии Data Quality / channel_limit ===
-- [10] Проверка из отчёта
SELECT rule_name, status, expected, actual
FROM de7_etl.dq_results
WHERE load_id = 'ae4e9c6f-b9b0-4f36-a787-b086445fc98d'::uuid
  AND rule_name = 'missing_channel_share';


-- === Отрицательные сценарии Data Quality / invalid_records ===
-- [11] Проверка из отчёта
SELECT
    row_no,
    file_line_no,
    CARDINALITY(reasons) AS reason_count,
    reasons
FROM de7_stg.rejections
WHERE load_id = '92b08892-a48c-452a-abb7-a0f42275cbd1'::uuid
ORDER BY row_no;

-- [12] Проверка из отчёта
SELECT
    read_rows,
    accepted_rows,
    rejected_rows,
    read_rows = accepted_rows + rejected_rows AS balance_ok
FROM de7_etl.load_runs
WHERE load_id = '92b08892-a48c-452a-abb7-a0f42275cbd1'::uuid;


-- === Отрицательные сценарии Data Quality / incomplete ===
-- [13] Проверка из отчёта
SELECT complete, covered_until, period_end, state
FROM de7_etl.load_runs
WHERE load_id = '4609ecbb-14f5-4a10-b355-ec60abe99f34'::uuid;

-- [14] Проверка из отчёта
SELECT rule_name, status, expected, actual
FROM de7_etl.dq_results
WHERE load_id = '4609ecbb-14f5-4a10-b355-ec60abe99f34'::uuid
  AND rule_name = 'source_coverage';


-- === Отрицательные сценарии Data Quality / unconfirmed_empty ===
-- [15] Проверка из отчёта
SELECT
    l.read_rows,
    l.expected_row_count,
    l.complete,
    l.state,
    COUNT(r.row_no) AS actual_raw_rows
FROM de7_etl.load_runs l
LEFT JOIN de7_stg.payments_raw r ON r.load_id = l.load_id
WHERE l.load_id = '65ecbadd-43ac-47ad-87f9-a6ed7dcd4419'::uuid
GROUP BY l.load_id;

-- [16] Проверка из отчёта
SELECT rule_name, status, expected, actual
FROM de7_etl.dq_results
WHERE load_id = '65ecbadd-43ac-47ad-87f9-a6ed7dcd4419'::uuid
  AND rule_name IN (
      'empty_input_confirmed',
      'source_coverage'
  );


-- === Отрицательные сценарии Data Quality / stale - источник не покрыл конец периода ===
-- [17] Проверка из отчёта
SELECT
    report_date,
    complete,
    covered_until,
    period_end,
    covered_until >= period_end AS coverage_ok
FROM de7_etl.load_runs
WHERE load_id = '630f7f65-3e2e-4316-8382-b80d67795be6'::uuid;

-- [18] Проверка из отчёта
SELECT rule_name, status, expected, actual
FROM de7_etl.dq_results
WHERE load_id = '630f7f65-3e2e-4316-8382-b80d67795be6'::uuid
  AND rule_name = 'source_coverage';


-- === Отрицательные сценарии Data Quality / control_mismatch — не совпадает контрольная сумма ===
-- [19] Проверка из отчёта
SELECT
    l.read_rows,
    COUNT(r.row_no) AS staged_rows
FROM de7_etl.load_runs l
LEFT JOIN de7_stg.payments_raw r ON r.load_id = l.load_id
WHERE l.load_id = '54247d67-b7ca-4d6b-a5e0-4880a6898ec2'::uuid
GROUP BY l.load_id;

-- [20] Проверка из отчёта
SELECT
    rule_name,
    status,
    expected,
    actual
FROM de7_etl.dq_results
WHERE load_id = '54247d67-b7ca-4d6b-a5e0-4880a6898ec2'::uuid
  AND rule_name = 'manifest_success_sums';


-- [21] Проверка из отчёта
SELECT load_id, state, last_error, published_at
FROM de7_etl.load_runs
WHERE load_id = '54247d67-b7ca-4d6b-a5e0-4880a6898ec2'::uuid;


-- === Отрицательные сценарии Data Quality / broken_manifest — повреждённый JSON ===
-- [22] Проверка из отчёта
SELECT
    load_id,
    state,
    manifest_errors,
    last_error
FROM de7_etl.load_runs
WHERE load_id = '92973d71-05ac-4d21-a7a5-032baee11966'::uuid;

-- [23] Проверка из отчёта
SELECT rule_name, status, actual, detail
FROM de7_etl.dq_results
WHERE load_id = '92973d71-05ac-4d21-a7a5-032baee11966'::uuid;

-- [24] Проверка из отчёта
SELECT COUNT(*) AS raw_rows
FROM de7_stg.payments_raw
WHERE load_id = '92973d71-05ac-4d21-a7a5-032baee11966'::uuid;

--Проверка обработки ошибки csv
SELECT
    l.load_id,
    l.state,
    l.last_error,
    l.finished_at,
    e.task_id,
    e.attempt,
    e.event_status,
    e.reason
FROM de7_etl.load_runs l
JOIN de7_etl.load_events e ON e.load_id = l.load_id
WHERE l.source_path LIKE '%test_wrong_header%'
ORDER BY e.event_at DESC;

-- === Предупреждение, исправленная версия и пустая поставка / warning — допустимое отсутствие канала ===
-- [25] Проверка из отчёта
SELECT rule_name, status, expected, actual
FROM de7_etl.dq_results
WHERE load_id = '415830db-e9b9-4d30-8275-4acbe892bd62'::uuid
  AND rule_name = 'missing_channel_share';

-- [26] Проверка из отчёта
SELECT
    row_no,
    payment_id,
    currency,
    status,
    channel_id
FROM de7_stg.payments_prepared
WHERE load_id = '415830db-e9b9-4d30-8275-4acbe892bd62'::uuid
  AND channel_id = 'unknown';

-- [27] Проверка из отчёта
SELECT
    report_date,
    currency,
    channel_id,
    payment_count,
    amount_sum
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-25'
  AND channel_id = 'unknown';

-- [28] Проверка из отчёта
SELECT
    load_id,
    state,
    published_at
FROM de7_etl.load_runs
WHERE load_id = '415830db-e9b9-4d30-8275-4acbe892bd62'::uuid;


-- === Предупреждение, исправленная версия и пустая поставка / corrected ===
-- [29] Проверка из отчёта
SELECT report_date, currency, channel_id,
       payment_count, amount_sum,
       source_version, load_id
FROM de7_core.payments_daily
ORDER BY report_date, currency, channel_id;

-- [30] Проверка из отчёта
WITH differences AS (
    (
        SELECT report_date, currency, channel_id,
               payment_count, amount_sum
        FROM de7_stg.i_payments_daily
        WHERE load_id = '58ed8e37-01dc-44bb-a6cd-d56a9de2d3c1'::uuid
        EXCEPT ALL
        SELECT report_date, currency, channel_id,
               payment_count, amount_sum
        FROM de7_core.payments_daily
        WHERE report_date = DATE '2026-10-25'
    )
    UNION ALL
    (
        SELECT report_date, currency, channel_id,
               payment_count, amount_sum
        FROM de7_core.payments_daily
        WHERE report_date = DATE '2026-10-25'
        EXCEPT ALL
        SELECT report_date, currency, channel_id,
               payment_count, amount_sum
        FROM de7_stg.i_payments_daily
        WHERE load_id = '58ed8e37-01dc-44bb-a6cd-d56a9de2d3c1'::uuid
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;

-- [31] Проверка из отчёта
SELECT load_id, run_id, source_version, state
FROM de7_etl.load_runs
WHERE load_id IN (
    '58ed8e37-01dc-44bb-a6cd-d56a9de2d3c1'::uuid,
    'ab1f2698-04d5-4983-9a51-728640239091'::uuid
)
ORDER BY created_at;


-- === Предупреждение, исправленная версия и пустая поставка / Подтвержденная пустота за 27 октября ===
-- [32] Проверка из отчёта
SELECT COUNT(*)
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-27';

-- [33] Проверка из отчёта
SELECT report_date, currency, channel_id,
       payment_count, amount_sum
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-27';

-- [34] Проверка из отчёта
SELECT
    load_id,
    state,
    read_complete,
    read_rows,
    expected_row_count,
    expected_sums,
    complete,
    covered_until >= period_end AS coverage_ok,
    published_at
FROM de7_etl.load_runs
WHERE load_id = '540da43d-3c5f-4d6b-a530-16c727d16812'::uuid;

-- [35] Проверка из отчёта
SELECT COUNT(*) AS rows_27
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-10-27';

-- [36] Проверка из отчёта
WITH differences AS (
    (
        SELECT *
        FROM de7_etl.qa_before_c4
        EXCEPT ALL
        SELECT *
        FROM de7_core.payments_daily
        WHERE report_date <> DATE '2026-10-27'
    )
    UNION ALL
    (
        SELECT *
        FROM de7_core.payments_daily
        WHERE report_date <> DATE '2026-10-27'
        EXCEPT ALL
        SELECT *
        FROM de7_etl.qa_before_c4
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === Повторы после временной недоступности / Сбой после фиксации staging ===
-- [37] Проверка из отчёта
SELECT
    attempt,
    event_status,
    counters,
    reason,
    event_at
FROM de7_etl.load_events
WHERE load_id = '775d26bb-747a-43f2-8f10-93155e09281d'::uuid
  AND task_id = 'stage_payments'
ORDER BY event_at;

-- [38] Проверка из отчёта
SELECT
    l.load_id,
    l.read_rows,
    l.expected_row_count,
    COUNT(r.row_no) AS actual_raw_rows,
    COUNT(DISTINCT r.row_no) AS unique_row_numbers,
    l.read_complete,
    l.staging_hash IS NOT NULL AS hash_saved
FROM de7_etl.load_runs l
LEFT JOIN de7_stg.payments_raw r
    ON r.load_id = l.load_id
WHERE l.load_id = '775d26bb-747a-43f2-8f10-93155e09281d'::uuid
GROUP BY l.load_id;

-- [39] Проверка из отчёта
SELECT load_id, state, read_rows,
       accepted_rows, rejected_rows, prepared_rows
FROM de7_etl.load_runs
WHERE load_id = '775d26bb-747a-43f2-8f10-93155e09281d'::uuid;


-- === Повторы после временной недоступности / Откат внутри публикации ===
-- [40] Проверка из отчёта
WITH differences AS (
    (
        SELECT *
        FROM de7_etl.qa_before_d4
        EXCEPT ALL
        SELECT *
        FROM de7_core.payments_daily
    )
    UNION ALL
    (
        SELECT *
        FROM de7_core.payments_daily
        EXCEPT ALL
        SELECT *
        FROM de7_etl.qa_before_d4
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;

-- [41] Проверка из отчёта
SELECT
    attempt,
    event_status,
    reason,
    event_at
FROM de7_etl.load_events
WHERE load_id = 'fb3092a8-3374-450e-90be-4e0a8ab20009'::uuid
  AND task_id = 'publish_payments'
ORDER BY event_at;

-- [42] Проверка из отчёта
SELECT
    l.load_id,
    l.state,
    l.published_at,
    a.approved_at
FROM de7_etl.load_runs l
LEFT JOIN de7_etl.publication_approvals a
    ON a.load_id = l.load_id
WHERE l.load_id = 'fb3092a8-3374-450e-90be-4e0a8ab20009'::uuid;


-- === Повторы после временной недоступности / Отсутствие и подмена обязательного результата ===
-- [43] Проверка из отчёта
SELECT
    load_id,
    state,
    last_error,
    published_at
FROM de7_etl.load_runs
WHERE load_id = 'ae3b1b92-5672-4afb-a9fe-a3bacac5d6f0'::uuid;

-- [44] Проверка из отчёта
SELECT COUNT(*) AS approval_count
FROM de7_etl.publication_approvals
WHERE load_id = 'ae3b1b92-5672-4afb-a9fe-a3bacac5d6f0'::uuid;

-- [45] Проверка из отчёта
SELECT
    load_id,
    state,
    last_error,
    published_at
FROM de7_etl.load_runs
WHERE load_id = '927b14fd-1eae-427f-8b65-a8223bd0028f'::uuid;

-- [46] Проверка из отчёта
SELECT COUNT(*) AS approval_count
FROM de7_etl.publication_approvals
WHERE load_id = '927b14fd-1eae-427f-8b65-a8223bd0028f'::uuid;

-- [47] Проверка из отчёта
WITH differences AS (
    (
        SELECT *
        FROM de7_etl.qa_before_d5
        EXCEPT ALL
        SELECT *
        FROM de7_core.payments_daily
        WHERE report_date = DATE '2026-10-25'
    )
    UNION ALL
    (
        SELECT *
        FROM de7_core.payments_daily
        WHERE report_date = DATE '2026-10-25'
        EXCEPT ALL
        SELECT *
        FROM de7_etl.qa_before_d5
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === Повторы после временной недоступности / Проверка календарных интервалов и перехода на зимнее время ===
-- [48] Проверка из отчёта
SELECT DISTINCT
    report_date,
    period_start,
    period_end,
    EXTRACT(EPOCH FROM period_end - period_start) / 3600 AS hours
FROM de7_etl.load_runs
WHERE report_date = DATE '2026-10-25';

-- [49] Проверка из отчёта
WITH events(payment_id,paid_at) AS (VALUES
    ('B1','2026-10-24T23:59:59+02:00'::timestamptz),
    ('B2','2026-10-25T00:00:00+02:00'::timestamptz),
    ('B3','2026-10-25T02:30:00+02:00'::timestamptz),
    ('B4','2026-10-25T02:30:00+01:00'::timestamptz),
    ('B5','2026-10-25T23:59:59+01:00'::timestamptz),
    ('B6','2026-10-26T00:00:00+01:00'::timestamptz)
), bounds AS (
    SELECT '2026-10-25T00:00:00+02:00'::timestamptz AS start_at,
           '2026-10-26T00:00:00+01:00'::timestamptz AS end_at
)
SELECT payment_id, paid_at,
       paid_at>=start_at AND paid_at<end_at AS included
FROM events CROSS JOIN bounds
ORDER BY payment_id;


-- === Повторы после временной недоступности / scheduled run ===
-- [50] Проверка из отчёта
SELECT
    load_id,
    run_id,
    report_date,
    source_path,
    state,
    read_rows,
    prepared_rows
FROM de7_etl.load_runs
WHERE run_id LIKE 'scheduled__%'
ORDER BY created_at DESC;

-- [51] Проверка из отчёта
SELECT
    report_date,
    period_start,
    period_end,
    period_start =
        (report_date::timestamp AT TIME ZONE 'Europe/Berlin')
        AS start_ok,
    period_end =
        ((report_date + 1)::timestamp AT TIME ZONE 'Europe/Berlin')
        AS end_ok,
    EXTRACT(EPOCH FROM period_end - period_start) / 3600
        AS hours,
    state
FROM de7_etl.load_runs
WHERE load_id = 'ee6ab4c7-d7ba-4ddf-ba75-8bfeffba47e6'::uuid;

-- [52] Проверка из отчёта
SELECT
    report_date,
    currency,
    channel_id,
    payment_count,
    amount_sum
FROM de7_core.payments_daily
WHERE report_date = DATE '2026-11-14';


-- === Итоговая сверка с независимой агрегацией исходных корректных файлов по тем же правилам / 2026-10-24 ===
-- [53] Проверка из отчёта
SELECT
    COUNT(*) AS source_rows,
    COUNT(*) FILTER (WHERE status = 'succeeded')
        AS succeeded_rows,
    COUNT(*) FILTER (WHERE status = 'failed')
        AS failed_rows
FROM de7_etl.qa_reference_payments;

-- [54] Проверка из отчёта
SELECT
    currency,
    COUNT(*) AS succeeded_payments,
    SUM(amount::numeric) AS succeeded_amount
FROM de7_etl.qa_reference_payments
WHERE status = 'succeeded'
GROUP BY currency
ORDER BY currency;

-- [55] Проверка из отчёта
WITH expected AS (
    SELECT
        (
            paid_at::timestamptz
            AT TIME ZONE 'Europe/Berlin'
        )::date AS report_date,
        currency,
        CASE
            WHEN TRIM(COALESCE(channel_id, '')) = ''
            THEN 'unknown'
            ELSE channel_id
        END AS channel_id,
        COUNT(*) AS payment_count,
        SUM(amount::numeric) AS amount_sum
    FROM de7_etl.qa_reference_payments
    WHERE status = 'succeeded'
    GROUP BY 1, 2, 3
),actual AS (
    SELECT
        report_date,
        currency,
        channel_id,
        payment_count,
        amount_sum
    FROM de7_core.payments_daily
    WHERE report_date = DATE '2026-10-24'
),
differences AS (
    (
        SELECT * FROM expected
        EXCEPT ALL
        SELECT * FROM actual
    )
    UNION ALL
    (
        SELECT * FROM actual
        EXCEPT ALL
        SELECT * FROM expected
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === 2026-10-25 ===
-- [56] Проверка из отчёта
WITH expected AS (
    SELECT
        (
            paid_at::timestamptz
            AT TIME ZONE 'Europe/Berlin'
        )::date AS report_date,
        currency,
        CASE
            WHEN TRIM(COALESCE(channel_id, '')) = ''
            THEN 'unknown'
            ELSE channel_id
        END AS channel_id,
        COUNT(*) AS payment_count,
        SUM(amount::numeric) AS amount_sum
    FROM de7_etl.qa_reference_payments
    WHERE status = 'succeeded'
    GROUP BY 1, 2, 3
),
actual AS (
    SELECT
        report_date,
        currency,
        channel_id,
        payment_count,
        amount_sum
    FROM de7_core.payments_daily
    WHERE report_date = DATE '2026-10-25'
),
differences AS (
    (
        SELECT * FROM expected
        EXCEPT ALL
        SELECT * FROM actual
    )
    UNION ALL
    (
        SELECT * FROM actual
        EXCEPT ALL
        SELECT * FROM expected
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === 2026-10-26 ===
-- [57] Проверка из отчёта
WITH expected AS (
    SELECT
        (
            paid_at::timestamptz
            AT TIME ZONE 'Europe/Berlin'
        )::date AS report_date,
        currency,
        CASE
            WHEN TRIM(COALESCE(channel_id, '')) = ''
            THEN 'unknown'
            ELSE channel_id
        END AS channel_id,
        COUNT(*) AS payment_count,
        SUM(amount::numeric) AS amount_sum
    FROM de7_etl.qa_reference_payments
    WHERE status = 'succeeded'
    GROUP BY 1, 2, 3
),
actual AS (
    SELECT
        report_date,
        currency,
        channel_id,
        payment_count,
        amount_sum
    FROM de7_core.payments_daily
    WHERE report_date = DATE '2026-10-26'
),
differences AS (
    (
        SELECT * FROM expected
        EXCEPT ALL
        SELECT * FROM actual
    )
    UNION ALL
    (
        SELECT * FROM actual
        EXCEPT ALL
        SELECT * FROM expected
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;


-- === 2026-10-27 ===
-- [58] Проверка из отчёта
--0 строк

WITH expected AS (
    SELECT
        (
            paid_at::timestamptz
            AT TIME ZONE 'Europe/Berlin'
        )::date AS report_date,
        currency,
        CASE
            WHEN TRIM(COALESCE(channel_id, '')) = ''
            THEN 'unknown'
            ELSE channel_id
        END AS channel_id,
        COUNT(*) AS payment_count,
        SUM(amount::numeric) AS amount_sum
    FROM de7_etl.qa_reference_payments
    WHERE status = 'succeeded'
    GROUP BY 1, 2, 3
),
actual AS (
    SELECT
        report_date,
        currency,
        channel_id,
        payment_count,
        amount_sum
    FROM de7_core.payments_daily
    WHERE report_date = DATE '2026-10-27'
),
differences AS (
    (
        SELECT * FROM expected
        EXCEPT ALL
        SELECT * FROM actual
    )
    UNION ALL
    (
        SELECT * FROM actual
        EXCEPT ALL
        SELECT * FROM expected
    )
)
SELECT COUNT(*) AS different_rows
FROM differences;

-- === 2026-10-27 / Проверка перехода на зимнее время ===
-- [59] Проверка из отчёта
SELECT
    payment_id,
    paid_at,
    paid_at::timestamptz AT TIME ZONE 'UTC' AS paid_at_utc
FROM de7_etl.qa_reference_payments
WHERE payment_id IN ('B3', 'B4')
ORDER BY paid_at::timestamptz;
