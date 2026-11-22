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
