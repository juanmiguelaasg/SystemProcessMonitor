-- ============================================================================
--  System Process Monitor - analytics reports
--
--  Each report starts with a "-- name:" header followed by "-- title:" and
--  "-- description:" lines. The named parameter :since (unix epoch seconds)
--  is bound by the Analytics view according to the selected time range.
-- ============================================================================

-- name: top_cpu
-- title: Top CPU consumers
-- description: Average and peak CPU usage per executable in the selected window.
SELECT
    name                                  AS process,
    COUNT(DISTINCT pid)                   AS instances,
    COUNT(*)                              AS samples,
    ROUND(AVG(cpu_percent), 2)            AS avg_cpu,
    ROUND(MAX(cpu_percent), 2)            AS peak_cpu,
    ROUND(AVG(mem_rss) / 1048576.0, 1)    AS avg_mem_mb
FROM process_samples
WHERE ts >= :since
GROUP BY name
ORDER BY avg_cpu DESC
LIMIT 25;

-- name: top_memory
-- title: Memory heavyweights
-- description: Executables ranked by peak combined resident memory across all their instances.
WITH per_tick AS (
    SELECT name, ts, SUM(mem_rss) AS rss, COUNT(*) AS instances
    FROM process_samples
    WHERE ts >= :since
    GROUP BY name, ts
)
SELECT
    name                                  AS process,
    MAX(instances)                        AS max_instances,
    ROUND(AVG(rss) / 1048576.0, 1)        AS avg_mem_mb,
    ROUND(MAX(rss) / 1048576.0, 1)        AS peak_mem_mb
FROM per_tick
GROUP BY name
ORDER BY peak_mem_mb DESC
LIMIT 25;

-- name: load_per_minute
-- title: Load per minute
-- description: Machine-wide CPU, memory and network throughput rolled up to one row per minute.
SELECT
    strftime('%Y-%m-%d %H:%M', CAST(ts / 60 AS INTEGER) * 60, 'unixepoch', 'localtime') AS minute,
    ROUND(AVG(cpu_percent), 1)            AS avg_cpu,
    ROUND(MAX(cpu_percent), 1)            AS peak_cpu,
    ROUND(AVG(mem_percent), 1)            AS avg_mem,
    ROUND(AVG(net_recv_rate) / 1024.0, 1) AS recv_kbps,
    ROUND(AVG(net_sent_rate) / 1024.0, 1) AS sent_kbps,
    MAX(process_count)                    AS processes
FROM system_snapshots
WHERE ts >= :since
GROUP BY CAST(ts / 60 AS INTEGER)
ORDER BY minute DESC
LIMIT 240;

-- name: cpu_spikes
-- title: CPU spikes and culprits
-- description: Moments when total CPU went above 40 percent, with the busiest process at that instant (window function).
WITH ranked AS (
    SELECT
        s.ts,
        s.cpu_percent                     AS system_cpu,
        p.name,
        p.pid,
        p.cpu_percent                     AS process_cpu,
        ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY p.cpu_percent DESC) AS rn
    FROM system_snapshots AS s
    JOIN process_samples  AS p ON p.snapshot_id = s.id
    WHERE s.ts >= :since
      AND s.cpu_percent >= 40
)
SELECT
    datetime(ts, 'unixepoch', 'localtime') AS time,
    ROUND(system_cpu, 1)                  AS system_cpu,
    name                                  AS top_process,
    pid,
    ROUND(process_cpu, 1)                 AS process_cpu
FROM ranked
WHERE rn = 1
ORDER BY ts DESC
LIMIT 200;

-- name: memory_growth
-- title: Memory growth
-- description: Change in resident memory between the first and last sample of each executable. Useful for spotting leaks.
WITH per_tick AS (
    SELECT name, ts, SUM(mem_rss) AS rss
    FROM process_samples
    WHERE ts >= :since
    GROUP BY name, ts
),
bounds AS (
    SELECT name, MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(*) AS ticks
    FROM per_tick
    GROUP BY name
    HAVING COUNT(*) > 1
)
SELECT
    b.name                                AS process,
    b.ticks                               AS samples,
    ROUND(f.rss / 1048576.0, 1)           AS start_mb,
    ROUND(l.rss / 1048576.0, 1)           AS end_mb,
    ROUND((l.rss - f.rss) / 1048576.0, 1) AS growth_mb,
    ROUND((b.last_ts - b.first_ts) / 60.0, 1) AS minutes
FROM bounds   AS b
JOIN per_tick AS f ON f.name = b.name AND f.ts = b.first_ts
JOIN per_tick AS l ON l.name = b.name AND l.ts = b.last_ts
ORDER BY growth_mb DESC
LIMIT 25;

-- name: sessions
-- title: Recording sessions
-- description: Every run of the monitor with its duration and average load (ignores the time range).
SELECT
    s.id                                  AS session,
    datetime(s.started_at, 'unixepoch', 'localtime') AS started,
    s.hostname,
    COUNT(ss.id)                          AS snapshots,
    ROUND((MAX(ss.ts) - MIN(ss.ts)) / 60.0, 1) AS minutes,
    ROUND(AVG(ss.cpu_percent), 1)         AS avg_cpu,
    ROUND(MAX(ss.cpu_percent), 1)         AS peak_cpu,
    ROUND(AVG(ss.mem_percent), 1)         AS avg_mem
FROM sessions AS s
LEFT JOIN system_snapshots AS ss ON ss.session_id = s.id
GROUP BY s.id
ORDER BY s.id DESC;

-- name: events
-- title: Action audit log
-- description: Processes terminated from the Processes view, with the outcome of each attempt.
SELECT
    datetime(ts, 'unixepoch', 'localtime') AS time,
    pid,
    name                                  AS process,
    action,
    result
FROM process_events
WHERE ts >= :since
ORDER BY ts DESC;

-- name: alert_history
-- title: Alert history
-- description: How often each watchdog rule fired, how long its alerts stayed open and how many still need attention.
SELECT
    rule_name                             AS rule,
    severity,
    COUNT(*)                              AS fired,
    SUM(resolved_at IS NULL)              AS still_open,
    SUM(acknowledged = 0)                 AS unacknowledged,
    ROUND(AVG(resolved_at - ts), 1)       AS avg_open_s,
    COUNT(DISTINCT process)               AS processes,
    datetime(MAX(ts), 'unixepoch', 'localtime') AS last_fired
FROM alerts
WHERE ts >= :since
GROUP BY rule_name, severity
ORDER BY fired DESC;
