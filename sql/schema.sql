-- ============================================================================
--  System Process Monitor - SQLite schema
--  Applied automatically on startup. Every statement is idempotent.
-- ============================================================================

-- One row per application run.
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL    NOT NULL,              -- unix epoch (seconds)
    hostname      TEXT    NOT NULL,
    platform      TEXT    NOT NULL,
    cpu_count     INTEGER NOT NULL,
    total_memory  INTEGER NOT NULL               -- bytes
);

-- One row per sampling tick: machine-wide metrics.
CREATE TABLE IF NOT EXISTS system_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts               REAL    NOT NULL,
    cpu_percent      REAL    NOT NULL,
    mem_percent      REAL    NOT NULL,
    mem_used         INTEGER NOT NULL,
    swap_percent     REAL    NOT NULL,
    disk_percent     REAL    NOT NULL,
    disk_read_rate   REAL    NOT NULL,           -- bytes / second
    disk_write_rate  REAL    NOT NULL,
    net_sent_rate    REAL    NOT NULL,
    net_recv_rate    REAL    NOT NULL,
    process_count    INTEGER NOT NULL,
    thread_count     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts      ON system_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON system_snapshots(session_id);

-- The heaviest processes (by CPU and by memory) captured at each snapshot.
CREATE TABLE IF NOT EXISTS process_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  INTEGER NOT NULL REFERENCES system_snapshots(id) ON DELETE CASCADE,
    ts           REAL    NOT NULL,
    pid          INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    username     TEXT,
    cpu_percent  REAL    NOT NULL,               -- normalised to 0..100 across all cores
    mem_rss      INTEGER NOT NULL,               -- bytes
    mem_percent  REAL    NOT NULL,
    threads      INTEGER NOT NULL,
    status       TEXT
);

CREATE INDEX IF NOT EXISTS idx_samples_ts       ON process_samples(ts);
CREATE INDEX IF NOT EXISTS idx_samples_name_ts  ON process_samples(name, ts);
CREATE INDEX IF NOT EXISTS idx_samples_snapshot ON process_samples(snapshot_id);

-- Audit log of actions taken from the UI (for example "End task").
CREATE TABLE IF NOT EXISTS process_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    pid     INTEGER NOT NULL,
    name    TEXT    NOT NULL,
    action  TEXT    NOT NULL,
    result  TEXT    NOT NULL
);

-- Aggregated, all-time statistics per executable name.
CREATE VIEW IF NOT EXISTS v_process_stats AS
SELECT
    name,
    COUNT(*)                              AS samples,
    ROUND(AVG(cpu_percent), 2)            AS avg_cpu,
    ROUND(MAX(cpu_percent), 2)            AS peak_cpu,
    ROUND(AVG(mem_rss) / 1048576.0, 1)    AS avg_mem_mb,
    ROUND(MAX(mem_rss) / 1048576.0, 1)    AS peak_mem_mb,
    datetime(MIN(ts), 'unixepoch', 'localtime') AS first_seen,
    datetime(MAX(ts), 'unixepoch', 'localtime') AS last_seen
FROM process_samples
GROUP BY name;

-- Machine load rolled up to one row per minute.
CREATE VIEW IF NOT EXISTS v_system_minutely AS
SELECT
    datetime(CAST(ts / 60 AS INTEGER) * 60, 'unixepoch', 'localtime') AS minute,
    ROUND(AVG(cpu_percent), 1)            AS avg_cpu,
    ROUND(MAX(cpu_percent), 1)            AS peak_cpu,
    ROUND(AVG(mem_percent), 1)            AS avg_mem,
    ROUND(AVG(net_recv_rate) / 1024.0, 1) AS avg_recv_kbps,
    ROUND(AVG(net_sent_rate) / 1024.0, 1) AS avg_sent_kbps,
    MAX(process_count)                    AS max_processes
FROM system_snapshots
GROUP BY CAST(ts / 60 AS INTEGER);

-- ============================================================================
--  Watchdog agent
-- ============================================================================

-- Small key/value store for one-off flags (for example "default rules seeded").
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Rules the local watchdog agent evaluates on every sample.
CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    metric      TEXT    NOT NULL,                -- see spm/agent.py METRICS
    threshold   REAL    NOT NULL DEFAULT 0,      -- fires when value >= threshold
    duration_s  REAL    NOT NULL DEFAULT 0,      -- condition must hold this long (window for growth rules)
    cooldown_s  REAL    NOT NULL DEFAULT 300,    -- minimum gap between two alerts of the same rule/target
    severity    TEXT    NOT NULL DEFAULT 'warning'
                CHECK (severity IN ('info', 'warning', 'critical')),
    target      TEXT,                            -- optional process name pattern, e.g. 'chrome*.exe'
    enabled     INTEGER NOT NULL DEFAULT 1
);

-- Every alert the agent raised. resolved_at stays NULL while the condition persists.
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    rule_id       INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    rule_name     TEXT    NOT NULL,
    metric        TEXT    NOT NULL,
    severity      TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    message       TEXT    NOT NULL,
    value         REAL,
    pid           INTEGER,
    process       TEXT,
    resolved_at   REAL,
    acknowledged  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_ts   ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(acknowledged, resolved_at);

-- Default rule set, inserted exactly once per database.
INSERT INTO alert_rules (name, metric, threshold, duration_s, cooldown_s, severity, target, enabled)
SELECT name, metric, threshold, duration_s, cooldown_s, severity, target, enabled
FROM (
              SELECT 'High CPU load'        AS name, 'system_cpu'        AS metric,  90 AS threshold,  30 AS duration_s,  600 AS cooldown_s, 'warning'  AS severity, NULL AS target, 1 AS enabled
    UNION ALL SELECT 'CPU saturated',               'system_cpu',                  98,               60,               900,               'critical',             NULL,           1
    UNION ALL SELECT 'Memory pressure',             'system_memory',               95,               60,               900,               'warning',              NULL,           1
    UNION ALL SELECT 'System disk almost full',     'disk_usage',                  90,                0,              3600,               'critical',             NULL,           1
    UNION ALL SELECT 'Runaway process',             'process_cpu',                 50,               60,               900,               'warning',              NULL,           1
    UNION ALL SELECT 'Memory hog',                  'process_memory_mb',         4096,               30,              1800,               'warning',              NULL,           1
    UNION ALL SELECT 'Possible memory leak',        'memory_growth_mb',           500,              600,              1800,               'warning',              NULL,           1
    UNION ALL SELECT 'Process explosion',           'process_count',              600,               10,              1800,               'info',                 NULL,           1
    UNION ALL SELECT 'Explorer stopped',            'process_stopped',              0,                5,               300,               'critical',             'explorer.exe', 0
    UNION ALL SELECT 'Task Manager opened',         'process_started',              0,                0,                60,               'info',                 'Taskmgr.exe',  0
)
WHERE NOT EXISTS (SELECT 1 FROM meta WHERE key = 'default_rules_seeded');

INSERT OR IGNORE INTO meta (key, value) VALUES ('default_rules_seeded', '1');
