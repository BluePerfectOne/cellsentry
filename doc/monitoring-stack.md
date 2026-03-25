# Monitoring Stack

## The Prometheus + Grafana Pattern

Several open-source home router monitoring projects use the **Prometheus + Grafana** stack, and it is a reasonable and well-proven choice. Here is what each component does and how they fit together.

### Component Roles

```
┌─────────────────────────┐
│  ZTE MC801A (modem)     │  HTTP JSON API (LAN only)
└────────────┬────────────┘
             │  scrape every N seconds
             ▼
┌─────────────────────────┐
│  Exporter service       │  Custom scraper (Python or Rust)
│  :9090/metrics          │  Translates modem JSON → Prometheus text format
└────────────┬────────────┘
             │  pull every N seconds
             ▼
┌─────────────────────────┐
│  Prometheus             │  Time-series database + query engine
│  :9090                  │  Stores metrics, evaluates alert rules
└────────────┬────────────┘
             │  query (PromQL)
             ▼
┌─────────────────────────┐
│  Grafana                │  Visualisation and alerting UI
│  :3000                  │  Dashboards, alert notifications
└─────────────────────────┘
```

### Prometheus

Prometheus is a **pull-based time-series database**. It periodically scrapes any HTTP endpoint that serves metrics in its text format. You write an "exporter" — a small HTTP server that fetches modem data and formats it as Prometheus metrics — and Prometheus handles the rest: storage, retention, querying (PromQL), and alert evaluation.

**Strengths:**
- Industry-standard; rich ecosystem of dashboards and alerting integrations.
- The `prometheus_client` (Python) and `prometheus` (Rust) libraries make writing exporters trivial.
- PromQL is expressive for rate/delta/threshold queries.
- Proven, well-documented, widely used for exactly this type of edge monitoring.

**Weaknesses:**
- Prometheus's storage is optimised for recent/current data; long-term retention requires extra configuration or remote write to a separate store.
- The pull model introduces a ~scrape-interval lag (usually 15 s); for catching transient drop events this may miss very short glitches.
- One more service to run and configure.

### Grafana

Grafana is a **visualisation and dashboarding platform**. It connects to Prometheus (and many other sources) and renders time-series graphs, stat panels, heatmaps, and more. It also has a built-in alerting engine that can send notifications over email, Slack, PagerDuty, etc.

For this project it provides: trend graphs for RSRP/RSRQ/SINR over hours/days, drop-event annotations, threshold-based alerts.

---

## Alternatives

### InfluxDB + Grafana (push model)

Instead of Prometheus, use **InfluxDB** (a purpose-built time-series database) with a scraper that writes directly into it.

| | Prometheus | InfluxDB |
| --- | --- | --- |
| Model | Pull (Prometheus scrapes exporter) | Push (scraper writes to DB) |
| Query language | PromQL | Flux (v2) / InfluxQL (v1) |
| Long-term retention | Needs configuration / remote storage | Built-in downsampling / retention policies |
| Ecosystem | Larger; more pre-built dashboards | Smaller but growing |
| Deployment | Two services (Prometheus + Grafana) | Two services (InfluxDB + Grafana) |

For a home signal monitor either works equally well. InfluxDB's push model can be slightly simpler to code (no exporter HTTP server needed — just write a data point), and its retention/downsampling policies are more convenient for multi-month data. However, far more community ZTE/router dashboards exist for Prometheus+Grafana.

### No External Stack — SQLite + Simple Web UI

The simplest possible architecture:
- The scraper writes rows to a local **SQLite** database.
- A minimal HTML/Python web page (e.g., using Plotly or Chart.js) reads and plots the data.

**When this makes sense:** You want zero external dependencies, will query the data manually, and don't need alerting. Good for Phase 0/1 while waiting to decide on the full stack.

### Netdata

[Netdata](https://www.netdata.cloud/) is a lightweight, zero-configuration monitoring agent designed for single-node home/server use. It has a built-in time-series engine and dashboards. A custom collector plugin (in Python or bash) could feed modem metrics into it. Lower operational overhead than Prometheus+Grafana but less flexible for custom dashboards.

---

## Recommendation

**Use Prometheus + Grafana, deployed with Docker Compose.**

Rationale:
- The most common stack for exactly this use case — community dashboards and exporter examples exist.
- Docker Compose means the entire stack (exporter + Prometheus + Grafana) starts with one command, no manual service installation.
- Once the PoC validates the data source, wiring the exporter into this stack is straightforward.
- Grafana's alerting covers the primary non-functional requirement (notification when signal degrades).

### Proposed Docker Compose layout (Phase 1+)

```
docker-compose.yml
├── exporter/          # Our custom scraper running on :9101/metrics
├── prometheus/
│   └── prometheus.yml # scrape configs
└── grafana/
    └── provisioning/  # auto-provision the dashboard
```

---

## Scrape Interval Considerations

| Goal | Suggested interval |
| --- | --- |
| Trend analysis (hours/days) | 30–60 s |
| Drop event detection | 10–15 s |
| Near real-time debugging | 5 s |

The ZTE MC801A's internal refresh rate for signal metrics appears to be ~2 s. Scraping faster than 5 s is unlikely to yield meaningful additional data and adds unnecessary load to the modem's web server.
