<div align="center">

# System Process Monitor

**A modern desktop process monitor with a local watchdog agent. It records everything it sees into SQLite, learns what normal looks like, raises alerts when something deviates, forwards them as incidents over webhooks, and lets you interrogate the history with real SQL.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20mode-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![GUI](https://img.shields.io/badge/GUI-CustomTkinter-1F6FEB?style=for-the-badge)](https://github.com/TomSchimansky/CustomTkinter)
[![psutil](https://img.shields.io/badge/metrics-psutil-4B8BBE?style=for-the-badge)](https://github.com/giampaolo/psutil)
[![Docker](https://img.shields.io/badge/Docker-headless%20agent-2496ED?style=for-the-badge&logo=docker&logoColor=white)](#docker)

[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-0B0E14?style=flat-square)](#requirements)
[![Version](https://img.shields.io/badge/version-1.2.0-22D3EE?style=flat-square)](#changelog)
[![Agent](https://img.shields.io/badge/watchdog%20agent-100%25%20local-FB7185?style=flat-square)](#watchdog-agent)
[![Anomaly detection](https://img.shields.io/badge/anomaly%20detection-EWMA%20%2B%20z--score-F59E0B?style=flat-square)](#anomaly-detection)
[![Webhooks](https://img.shields.io/badge/webhooks-JSON%20incidents-0EA5E9?style=flat-square)](#webhooks)
[![License](https://img.shields.io/badge/license-MIT-34D399?style=flat-square)](LICENSE)
[![SQL](https://img.shields.io/badge/SQL-CTEs%20%C2%B7%20window%20functions%20%C2%B7%20views-A78BFA?style=flat-square)](sql/reports.sql)
[![Tests](https://img.shields.io/badge/tests-120%20passed-34D399?style=flat-square&logo=pytest&logoColor=white)](#running-the-tests)
[![Code style](https://img.shields.io/badge/code%20style-PEP%208-FBBF24?style=flat-square)](https://peps.python.org/pep-0008/)

<br>

<img src="docs/screenshots/dashboard.png" alt="Overview dashboard" width="900">

</div>

---

## Table of contents

- [Why](#why)
- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Command-line options](#command-line-options)
- [Watchdog agent](#watchdog-agent)
  - [Anomaly detection](#anomaly-detection)
  - [Webhooks](#webhooks)
- [Docker](#docker)
- [How it works](#how-it-works)
- [Database schema](#database-schema)
- [Built-in SQL reports](#built-in-sql-reports)
- [Writing your own queries](#writing-your-own-queries)
- [Project structure](#project-structure)
- [Running the tests](#running-the-tests)
- [Performance notes](#performance-notes)
- [Troubleshooting](#troubleshooting)
- [Changelog](#changelog)
- [License](#license)

---

## Why

Task Manager tells you what is happening **right now**. It cannot tell you which process
spiked the CPU twenty minutes ago, which one has been slowly leaking memory all afternoon,
or what the machine looked like during the last hour of a build.

System Process Monitor samples the whole machine every couple of seconds, keeps a rolling
history in a local SQLite database, and gives you both a polished live dashboard and a
full SQL console to answer those questions yourself.

## Features

| Area | What you get |
| --- | --- |
| **Live overview** | CPU, memory, disk I/O and network cards with sparklines, a 4-minute utilisation chart, per-core load bars, busiest processes and system facts. |
| **Process explorer** | Every running process with PID, user, CPU, memory, threads and start time. Click-to-sort columns, instant filter, a freeze switch and **End task** (with confirmation and an audit trail). |
| **Persistent history** | Machine-wide snapshots plus the heaviest processes per tick are written to SQLite in WAL mode, with automatic retention pruning. |
| **Watchdog agent** | A local agent evaluates rules on every sample (CPU, memory, disk, per-process load, memory leaks, processes starting or stopping), keeps alerts open until the condition clears, and notifies you with desktop popups and a sound. Runs inside the app or on its own with `--headless`. |
| **Anomaly detection** | Rolling EWMA baselines with z-scores for CPU, memory, process count, network and disk I/O. The agent alerts on unusual deviations instead of fixed limits, and the baselines survive restarts. |
| **Webhooks** | Alerts are forwarded as JSON incidents (`alert.fired` / `alert.resolved`) to any webhook or REST endpoint, with a stable `dedup_key`, retries and a delivery log in SQL. Works with Slack and Discord out of the box. |
| **Analytics** | Ten pre-built reports (top CPU, memory heavyweights, per-minute load, CPU spikes with culprits, memory growth, sessions, audit log, alert history, learned baselines, webhook deliveries) over a selectable time window. |
| **SQL console** | Syntax-highlighted editor, schema browser, example library, CSV export. The connection is opened **read-only**, and runaway queries are cancelled after 8 seconds. |
| **Fast on Windows** | Process data comes from a single `NtQuerySystemInformation` call, the same source Task Manager uses, instead of opening every process one by one. |
| **Restart friendly** | The dashboard charts are pre-filled from the database, so restarting the app does not leave you staring at empty graphs. |

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/processes.png" alt="Process explorer"><p align="center"><sub><b>Processes</b>: sortable, filterable, with End task</sub></p></td>
    <td width="50%"><img src="docs/screenshots/analytics.png" alt="Analytics reports"><p align="center"><sub><b>Analytics</b>: SQL reports over a time window</sub></p></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/console.png" alt="SQL console"><p align="center"><sub><b>SQL Console</b>: read-only, highlighted, exportable</sub></p></td>
    <td width="50%"><img src="docs/screenshots/alerts.png" alt="Alerts"><p align="center"><sub><b>Alerts</b>: feed, rules and agent status</sub></p></td>
  </tr>
</table>

## Quick start

### Requirements

- Python **3.9 or newer** with Tk (included in the standard Windows and macOS installers; on Debian/Ubuntu install `python3-tk`)
- Windows 10/11 is the primary target. Linux and macOS work through the generic psutil code path.

### Windows, one click

Double-click **`run.bat`**. It installs the two dependencies on first launch and starts the app.

### Any platform, manually

```bash
git clone <your-fork-url> SystemProcessMonitor
cd SystemProcessMonitor

python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

The database is created on first run at `data/monitor.db`.

## Command-line options

```text
python main.py [--interval SECONDS] [--db PATH] [--retention HOURS] [--top N]
               [--no-agent] [--no-sound] [--headless [--run-for SECONDS]]
               [--webhook URL ...] [--webhook-min-severity LEVEL]
```

| Option | Default | Description |
| --- | --- | --- |
| `--interval` | `2` | Sampling interval in seconds (minimum `0.5`). |
| `--db` | `data/monitor.db` | Location of the SQLite database file. |
| `--retention` | `24` | Hours of history to keep. Older rows are pruned at startup and every 300 ticks. |
| `--top` | `40` | Processes persisted per snapshot: the top N by CPU, merged with the top N by memory. |
| `--no-agent` | off | Do not start the watchdog agent. |
| `--no-sound` | off | Show alert popups silently. |
| `--headless` | off | Run the collector and the agent in the console, without a window. |
| `--run-for` | `0` | With `--headless`: exit after this many seconds (0 = until Ctrl+C). |
| `--webhook` | none | Forward alerts to this URL. Repeat it for several endpoints. Also read from `SPM_WEBHOOKS` (comma separated). |
| `--webhook-min-severity` | `warning` | Lowest severity sent to `--webhook` URLs: `info`, `warning` or `critical`. Also `SPM_WEBHOOK_MIN_SEVERITY`. |

Example: sample every second and keep a full week of data.

```bash
python main.py --interval 1 --retention 168
```

## Watchdog agent

The agent is a plain Python thread that lives on your machine. It does not use any cloud service,
API or language model: it reads the samples the collector produces, checks them against the rules in
the `alert_rules` table and writes what it finds to the `alerts` table. The only outgoing traffic is
to the webhook URLs you configure yourself.

<div align="center">
<img src="docs/screenshots/rule-editor.png" alt="Rule editor" width="380">
</div>

### How a rule behaves

| Field | Meaning |
| --- | --- |
| `metric` | What to measure (see the table below). |
| `threshold` | The rule matches when the value is **greater than or equal** to this. |
| `duration_s` | How long the condition must hold before an alert fires. For `memory_growth_mb` it is the observation window. |
| `cooldown_s` | Minimum time between two alerts of the same rule and target. |
| `severity` | `info`, `warning` or `critical`. Warning and critical alerts also play a system sound. |
| `target` | Optional case-insensitive glob on the process name, for example `chrome*.exe`. |

An alert stays **open** while the condition is true and is **resolved** automatically when it clears
(or when the process exits). Acknowledging an alert only marks it as seen.

| Metric | Scope | Example |
| --- | --- | --- |
| `system_cpu` | machine | Total CPU at or above 90 % for 30 s |
| `system_memory` | machine | RAM usage at or above 95 % for 60 s |
| `swap` | machine | Page file at or above 80 % |
| `disk_usage` | machine | System drive at or above 90 % full |
| `process_count` | machine | More than 600 processes |
| `process_cpu` | per process | Any process above 50 % CPU for a minute |
| `process_memory_mb` | per process | A process using more than 4 GB |
| `memory_growth_mb` | per process | A process that grew by 500 MB in 10 minutes |
| `process_started` | watch | `Taskmgr.exe` was opened |
| `process_stopped` | watch | No `explorer.exe` has been running for 5 s |
| `anomaly_system_cpu` | baseline | CPU 4 standard deviations above its learned normal for 20 s |
| `anomaly_system_memory` | baseline | Memory use jumped 4 sigma above normal |
| `anomaly_process_count` | baseline | Sudden surge in the number of processes |
| `anomaly_network` | baseline | Network traffic far above its usual level |
| `anomaly_disk_io` | baseline | Disk reads and writes far above their usual level |

Fifteen rules are seeded the first time a database is created: ten threshold rules and five anomaly rules
(four of them disabled, as examples). Upgrading from 1.1 adds only the anomaly rules.
Edit them on the **Alerts** page, or straight in SQL:

```sql
UPDATE alert_rules SET threshold = 80, duration_s = 20 WHERE name = 'High CPU load';

INSERT INTO alert_rules (name, metric, threshold, duration_s, cooldown_s, severity, target)
VALUES ('Chrome too hungry', 'process_memory_mb', 2048, 30, 600, 'warning', 'chrome.exe');
```

Changes made on the Alerts page apply immediately; changes made from outside apply the next time the app starts.

### Anomaly detection

Fixed limits either fire all the time on a machine that is always busy, or miss problems on a machine
that is usually idle. The anomaly rules compare each value with what is **normal for this machine**:

- For every series (CPU, memory, process count, network, disk I/O) the agent keeps an exponentially
  weighted moving average and variance (EWMA) with a 30-minute half-life.
- Each new value gets a z-score against the baseline *before* it is learned:
  `z = (value - mean) / max(std, floor)`. The rule fires when `z >= threshold` for `duration_s`.
- The first 60 samples (about two minutes) are a warm-up with no anomaly alerts. During warm-up the
  baseline behaves like a plain running mean and variance.
- A per-series floor on the standard deviation (for example 2 % for CPU, 1 % for memory) stops a very
  flat series from turning tiny wobbles into huge z-scores.
- Baselines are checkpointed to the `baselines` table every 30 samples and on shutdown, so a restart
  continues from where it stopped. A baseline older than six hours is relearned.

Because the baseline keeps moving, a slow shift (a machine that gets busier through the afternoon)
becomes the new normal, while a sudden jump stands out. It does not model daily or weekly seasonality:
the 9 am login rush is compared with the last half hour, not with yesterday at 9 am.

See what the agent has learned with the *Learned baselines* report, or:

```sql
SELECT series, ROUND(mean, 1) AS normal, ROUND(std, 2) AS std, samples FROM baselines;
```

### Webhooks

Every alert can be forwarded as an incident to a webhook or REST endpoint, for example a ticketing
system, Slack, Discord, Teams through a workflow, n8n or your own API.

```bash
python main.py --webhook https://example.com/incidents --webhook-min-severity warning
```

Endpoints can also live in the database, each with its own minimum severity:

```sql
INSERT INTO webhook_endpoints (name, url, min_severity)
VALUES ('ticketing', 'https://tickets.example.com/api/incidents', 'critical');
```

The agent already deduplicates: an incident is raised once, stays open while the condition persists, and
a cooldown applies afterwards. The dispatcher sends exactly one `alert.fired` when an incident opens and
one `alert.resolved` when it closes. One-shot events, such as a watched process starting, send only
`alert.fired`. Every incident carries a stable `dedup_key` (`host:rule:pid`, or `host:rule:system` for
machine-wide rules), so a ticketing system can merge repeats into one ticket.

```json
{
  "source": "system-process-monitor",
  "version": "1.2.0",
  "event": "alert.fired",
  "host": "build-server",
  "incident": {
    "id": "build-server-42",
    "dedup_key": "build-server:5:4120",
    "status": "open",
    "severity": "warning",
    "rule": "Runaway process",
    "metric": "process_cpu",
    "title": "Runaway process",
    "message": "game.exe (PID 4120): process cpu at 63.0% (limit 50%) for 60s",
    "value": 63.0,
    "pid": 4120,
    "process": "game.exe",
    "opened_at": "2026-11-22T22:14:33+00:00",
    "resolved_at": null
  },
  "text": "[WARNING] Runaway process on build-server: game.exe (PID 4120): ...",
  "content": "[WARNING] Runaway process on build-server: game.exe (PID 4120): ..."
}
```

`text` is what Slack and Mattermost display, and `content` is what Discord displays, so those work
without an adapter.

- Requests are `POST` with `Content-Type: application/json`. A 2xx response counts as delivered.
- Network errors, `429` and `5xx` responses are retried three times with exponential backoff. Other `4xx`
  responses are not retried.
- Deliveries run on their own thread, so a slow endpoint never delays monitoring. When the agent stops,
  the queue is drained first.
- Every delivery is logged in `webhook_deliveries`; see the *Webhook deliveries* report. Only the
  host name of the URL is stored, because webhook URLs often contain a secret token.
- **Send test alert** on the Alerts page posts an `alert.test` event to every endpoint, regardless of
  severity filters.

### Running only the agent

```bash
python main.py --headless
```

```text
Watchdog agent running locally: 11 enabled rules, sampling every 2s.
Database: data/monitor.db
Press Ctrl+C to stop.

2026-11-22 23:14:33  WARNING   Runaway process: game.exe (PID 4120): process cpu at 63.0% (limit 50%) for 60s
```

Alerts raised in headless mode land in the same database, so the desktop app shows them the next time
you open it. To start the agent with Windows, create a Task Scheduler task that runs
`pythonw main.py --headless` at logon.

## Docker

The image runs the collector and the watchdog agent in headless mode (a desktop window needs a display,
so the GUI stays on the host). It is based on `python:3.12-slim`, installs only `psutil`, runs as an
unprivileged user and keeps its database in the `/data` volume.

### With Docker Compose

```bash
docker compose up -d --build     # build and start in the background
docker compose logs -f           # follow the alerts
docker compose ps                # STATUS shows (healthy) once snapshots are flowing
docker compose down              # stop; the spm-data volume keeps the history
```

### With plain Docker

```bash
docker build -t system-process-monitor .
docker run -d --name spm-agent --pid=host --restart unless-stopped \
  -v spm-data:/data system-process-monitor

# override the defaults (they are passed after the image name)
docker run -d --name spm-agent --pid=host -v spm-data:/data \
  system-process-monitor --interval 5 --retention 72
```

### Details

| Topic | Behaviour |
| --- | --- |
| `--pid=host` | Lets the agent see the host's processes. Without it, it only sees its own container. |
| Health check | `python -m spm.healthcheck` reports unhealthy if no snapshot was written in the last 30 s. |
| Stopping | `docker stop` sends SIGTERM; the agent finishes the current sample, closes open alerts and exits. |
| Logs | Alerts are printed one per line, without colour codes, so `docker logs` stays readable. |
| Webhooks | Set `SPM_WEBHOOKS` (comma separated URLs) and optionally `SPM_WEBHOOK_MIN_SEVERITY`, for example `SPM_WEBHOOKS=https://example.com/hook docker compose up -d`. |
| Rules | Edit them with SQL: `docker exec -it spm-agent python -c "..."`, or copy the database out (below), edit it in the desktop app and copy it back. |

Copy the database out to open it in the desktop app or any SQLite tool:

```bash
docker cp spm-agent:/data/monitor.db ./monitor-from-docker.db
python main.py --db monitor-from-docker.db
```

> **Docker Desktop on Windows or macOS:** containers run inside a small Linux virtual machine, so even with
> `--pid=host` the agent monitors **that VM**, not your Windows or macOS processes. To watch the Windows
> machine itself, run the desktop app or `python main.py --headless` directly on the host. The container is
> the right tool on Linux servers, where the host PID namespace is the real machine.

## How it works

```mermaid
flowchart LR
    subgraph Collector thread
        A[NtQuerySystemInformation<br/>or psutil] --> B[Sample]
        B --> C[(SQLite<br/>WAL mode)]
    end
    B -- every sample --> W[Watchdog agent<br/>thread]
    W -- alerts, baselines --> C
    W -- new alerts --> D
    W -- fired / resolved --> X[Webhook dispatcher<br/>thread] --> Y([Webhook / REST<br/>endpoints])
    B -- latest sample --> D[Tk main loop<br/>polls every 250 ms]
    D --> E[Overview]
    D --> F[Processes]
    D --> P[Alerts page<br/>and popups]
    C -- read-only connection --> G[Analytics]
    C -- read-only connection --> H[SQL Console]
```

- A **daemon collector thread** owns its own writable SQLite connection. Every tick it reads
  machine-wide metrics, enumerates processes, inserts one `system_snapshots` row and a batch
  of `process_samples` rows inside a single transaction, then publishes the sample behind a lock.
- The **UI thread never blocks on sampling**. It polls the latest sample and updates only the
  visible page; the process table is updated incrementally, so selection and scroll position survive refreshes.
- **WAL journaling** lets the collector write while the Analytics view and the SQL console read concurrently.
- The SQL console uses a connection opened with `mode=ro`, so even a stray `DELETE` cannot touch your data.

## Database schema

```mermaid
erDiagram
    sessions ||--o{ system_snapshots : records
    system_snapshots ||--o{ process_samples : contains
    alert_rules ||--o{ alerts : raises
    alerts ||--o{ webhook_deliveries : forwarded
    sessions {
        INTEGER id PK
        REAL started_at
        TEXT hostname
        TEXT platform
        INTEGER cpu_count
        INTEGER total_memory
    }
    system_snapshots {
        INTEGER id PK
        INTEGER session_id FK
        REAL ts
        REAL cpu_percent
        REAL mem_percent
        REAL disk_read_rate
        REAL disk_write_rate
        REAL net_sent_rate
        REAL net_recv_rate
        INTEGER process_count
    }
    process_samples {
        INTEGER id PK
        INTEGER snapshot_id FK
        REAL ts
        INTEGER pid
        TEXT name
        REAL cpu_percent
        INTEGER mem_rss
        INTEGER threads
    }
    alert_rules {
        INTEGER id PK
        TEXT name
        TEXT metric
        REAL threshold
        REAL duration_s
        REAL cooldown_s
        TEXT severity
        TEXT target
        INTEGER enabled
    }
    alerts {
        INTEGER id PK
        REAL ts
        INTEGER rule_id FK
        TEXT severity
        TEXT message
        INTEGER pid
        REAL resolved_at
        INTEGER acknowledged
    }
    baselines {
        TEXT series PK
        REAL mean
        REAL var
        REAL std
        INTEGER samples
        REAL updated_at
    }
    webhook_endpoints {
        INTEGER id PK
        TEXT name
        TEXT url
        TEXT min_severity
        INTEGER enabled
    }
    webhook_deliveries {
        INTEGER id PK
        REAL ts
        INTEGER alert_id FK
        TEXT endpoint
        TEXT event
        INTEGER ok
        INTEGER status_code
        INTEGER attempts
    }
    process_events {
        INTEGER id PK
        REAL ts
        INTEGER pid
        TEXT name
        TEXT action
        TEXT result
    }
```

Two convenience views ship with the schema:

| View | Purpose |
| --- | --- |
| `v_process_stats` | All-time average/peak CPU and memory per executable, with first and last seen times. |
| `v_system_minutely` | Machine load rolled up to one row per minute. |

The full DDL lives in [`sql/schema.sql`](sql/schema.sql). Timestamps are Unix epoch seconds (`REAL`);
use `datetime(ts, 'unixepoch', 'localtime')` to make them readable.

## Built-in SQL reports

All reports live in [`sql/reports.sql`](sql/reports.sql) and are parsed at startup from
`-- name:`, `-- title:` and `-- description:` headers, so adding a report is just adding SQL.

| Report | Technique |
| --- | --- |
| Top CPU consumers | `GROUP BY` with `AVG`, `MAX`, `COUNT(DISTINCT pid)` |
| Memory heavyweights | CTE that sums all instances of an executable per tick before ranking |
| Load per minute | Time bucketing with `CAST(ts / 60 AS INTEGER)` and `strftime` |
| CPU spikes and culprits | `ROW_NUMBER() OVER (PARTITION BY snapshot ORDER BY cpu DESC)` |
| Memory growth | Two CTEs plus a self-join of first and last samples to find leaks |
| Recording sessions | `LEFT JOIN` aggregate over every run of the app |
| Action audit log | Every **End task** attempt and its outcome |
| Alert history | Conditional aggregates such as `SUM(resolved_at IS NULL)` and mean time to resolve per rule |
| Learned baselines | The EWMA state per series, with the level a 4-sigma alert would need |
| Webhook deliveries | Success rate, retries and last error per endpoint, using `CASE` inside `MAX` |

Here is the spike report, which pairs every CPU spike with the process responsible for it:

```sql
WITH ranked AS (
    SELECT s.ts, s.cpu_percent AS system_cpu, p.name, p.pid, p.cpu_percent AS process_cpu,
           ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY p.cpu_percent DESC) AS rn
    FROM system_snapshots AS s
    JOIN process_samples  AS p ON p.snapshot_id = s.id
    WHERE s.ts >= :since AND s.cpu_percent >= 40
)
SELECT datetime(ts, 'unixepoch', 'localtime') AS time,
       ROUND(system_cpu, 1) AS system_cpu, name AS top_process, pid,
       ROUND(process_cpu, 1) AS process_cpu
FROM ranked
WHERE rn = 1
ORDER BY ts DESC;
```

## Writing your own queries

Open **SQL Console**, type a query and press **Ctrl+Enter** (or select part of the text to run only that).
Click any table in the schema browser for a quick preview. A few ideas:

```sql
-- Which apps were open the longest in the last 6 hours?
SELECT name, ROUND((MAX(ts) - MIN(ts)) / 60.0) AS minutes_seen
FROM process_samples
WHERE ts >= CAST(strftime('%s', 'now') AS REAL) - 6 * 3600
GROUP BY name
ORDER BY minutes_seen DESC
LIMIT 15;

-- Hourly CPU profile of the whole recorded history
SELECT strftime('%H:00', ts, 'unixepoch', 'localtime') AS hour,
       ROUND(AVG(cpu_percent), 1) AS avg_cpu
FROM system_snapshots
GROUP BY hour
ORDER BY hour;

-- Is the index being used?
EXPLAIN QUERY PLAN
SELECT * FROM process_samples WHERE name = 'explorer.exe' AND ts > 0;
```

Results can be saved with **Export CSV**. The database is a plain SQLite file, so DB Browser for SQLite,
DBeaver, the `sqlite3` CLI or pandas can open it too.

## Project structure

```text
SystemProcessMonitor/
|-- main.py                 Entry point and CLI arguments
|-- run.bat                 One-click launcher for Windows
|-- requirements.txt
|-- requirements-dev.txt
|-- requirements-agent.txt  Minimal dependencies for the Docker image
|-- Dockerfile              Headless agent image
|-- docker-compose.yml
|-- .dockerignore
|-- pytest.ini
|-- tests/                  pytest suite (database, reports, collector, agent, baselines, webhooks, docker)
|-- sql/
|   |-- schema.sql          Tables, indexes and views (idempotent)
|   `-- reports.sql         Analytics reports with name/title/description headers
|-- spm/
|   |-- app.py              Main window, sidebar, page routing, polling loop
|   |-- collector.py        Background sampler thread
|   |-- agent.py            Local watchdog agent: rule engine and alert lifecycle
|   |-- baseline.py         EWMA baselines and z-scores for anomaly detection
|   |-- webhooks.py         Incident payloads, delivery with retries, dispatcher thread
|   |-- notify.py           Desktop popups and alert sounds
|   |-- headless.py         Console mode (--headless)
|   |-- healthcheck.py      Health probe used by the Docker image
|   |-- winproc.py          ctypes NtQuerySystemInformation process snapshot
|   |-- database.py         SQLite layer: inserts, pruning, reports, safe queries
|   |-- widgets.py          Canvas charts, core bars, cards, themed table, SQL highlighter
|   |-- theme.py            Palette, fonts, formatting helpers
|   `-- views/
|       |-- dashboard.py
|       |-- processes.py
|       |-- analytics.py
|       |-- console.py
|       `-- alerts.py
|-- docs/screenshots/
`-- data/                   Created at runtime (monitor.db)
```

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

| File | Covers |
| --- | --- |
| `tests/test_database.py` | Schema bootstrap, WAL mode, inserts, retention pruning, read-only enforcement, row limits, query timeout |
| `tests/test_reports.py` | Every report runs on an empty database, and returns the expected answers on a known fixture |
| `tests/test_collector.py` | Process enumeration, CPU accounting, the Windows fast path against psutil, the live collector thread |
| `tests/test_agent.py` | Every rule type including anomaly rules, durations, cooldowns, auto-resolve, rule reloads, baseline persistence, default rule seeding, the agent thread end to end |
| `tests/test_baseline.py` | EWMA maths: warm-up, plain mean/variance equivalence, half-life, noise versus spikes, the std floor |
| `tests/test_webhooks.py` | A real local HTTP server: payload shape, fired/resolved, deduplication, severity filter, retries, 4xx/5xx handling, secret-safe logging |
| `tests/test_docker.py` | The headless path never imports Tk, the health check, `.dockerignore` and Dockerfile consistency, a full headless run |
| `tests/test_theme.py` | Formatting helpers, colour blending, table sort order, the SQL tokenizer |

The tests use temporary databases and never touch `data/monitor.db`. No window is opened.

## Performance notes

- On Windows, enumerating ~350 processes through per-process `OpenProcess` calls took about **5 seconds**
  on the development machine. Reading one `SystemProcessInformation` snapshot through `ntdll` takes about
  **10 ms**. Per-process CPU percentages are derived from kernel and user time deltas between ticks,
  normalised across all logical cores (0-100 %, like Task Manager).
- Usernames are resolved once per process (keyed by PID and creation time) and cached.
- Only the heaviest processes per tick are persisted (`--top`), which keeps the database at roughly
  1-2 MB per hour at the default settings. Indexes on `ts`, `(name, ts)` and `snapshot_id` keep reports fast.
- Charts are drawn directly on `tk.Canvas`; there is no matplotlib dependency.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: customtkinter` or `psutil` | Run `pip install -r requirements.txt` with the same interpreter you use to start the app. |
| **End task** says "access denied" | System and service processes need elevated rights. Start the terminal or `run.bat` as administrator. |
| Text looks blurry on a high-DPI screen | Make sure you are using the regular `python.exe`, not a compatibility-mode shortcut. CustomTkinter enables per-monitor DPI awareness automatically. |
| Anomaly alerts right after the first start | The first two minutes are a warm-up, after that the baseline still needs some history to settle. Raise the threshold (for example to 5) if a rule is noisy on your machine. |
| Webhook not arriving | Check the *Webhook deliveries* report: it shows the HTTP status and error per endpoint. Use **Send test alert** to try it without waiting for a real alert. |
| Too many popups | Raise the threshold or cooldown of the noisy rule on the **Alerts** page, switch on *Mute popups*, or start with `--no-sound`. |
| "database is locked" | Two instances share one database file. Close one of them or start the second with `--db other.db`. |

## Changelog

### 1.2.0
- Anomaly detection: EWMA baselines with z-scores for CPU, memory, process count, network and disk I/O,
  persisted in the new `baselines` table, plus five seeded anomaly rules.
- Webhooks: JSON incidents (`alert.fired`, `alert.resolved`, `alert.test`) with a `dedup_key`, retries with
  backoff, a delivery log (`webhook_deliveries`) and endpoints from the CLI, the environment or SQL.
- New reports: *Learned baselines* and *Webhook deliveries*.

### 1.1.0
- Local watchdog agent with ten metrics, open/resolved alert lifecycle and cooldowns.
- Alerts page: feed, acknowledgements, rule editor, test alert, mute switch, sidebar badge.
- Desktop popups with sound for warning and critical alerts.
- Headless console mode (`--headless`, `--run-for`).
- New tables `alert_rules`, `alerts`, `meta` and the *Alert history* report.
- Docker image and Compose file for running the agent headless, with a health check.

### 1.0.0
- First release: live overview, process explorer, SQLite history, seven analytics reports,
  read-only SQL console with highlighting and CSV export, fast Windows process enumeration.

## License

Released under the [MIT License](LICENSE).
