# cellsentry

Local 5G/LTE signal monitor for ZTE MC801A. Collects RSRP, RSRQ, SINR and connection state over time so you have data when your operator doesn't.

## Background

After switching mobile operators, frequent connection drops started occurring on a ZTE MC801A 5G home router. Operator support declined to assist ("modem is too old"), so this project exists to collect objective, time-series signal quality data to either prove or disprove a signal/configuration problem independently.

## Project Status

**Phase 1 — Data Collection** (current): Prometheus exporter + Grafana dashboard running in Docker Compose, collecting signal metrics continuously.

**Phase 0 — Proof of Concept** ✓ Done: Confirmed the modem exposes machine-readable signal data (RSRP, RSRQ, SINR, network type, temperature, throughput) via its local HTTP+JSON API.

## Documentation

All design decisions and research live in [`doc/`](doc/):

| Document | Summary |
| --- | --- |
| [Project Overview](doc/project-overview.md) | Goals, scope, phased plan |
| [ZTE MC801A API Research](doc/zte-mc801a-api.md) | How to extract signal data from the modem |
| [Language Selection](doc/language-selection.md) | Python vs Rust vs others — rationale |
| [Monitoring Stack](doc/monitoring-stack.md) | Prometheus + Grafana evaluation and alternatives |

## Prerequisites

| Requirement | Version used | Notes |
| --- | --- | --- |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 4.x | Includes Docker Compose v2; Linux containers mode |
| Python | 3.12 | Only needed to run the Phase 0 PoC scraper directly |
| ZTE MC801A | firmware `MC801A_Elisa3_B22` | Other ZTE CPE firmware may work; see `doc/zte-mc801a-api.md` |

No other local installation is required — the full stack runs inside Docker.

## Quick Start

### Phase 1 — Full stack (Prometheus + Grafana)

```bash
cp .env.example .env
# Edit .env and set MODEM_PASSWORD
docker compose up -d
```

| Service | URL |
| --- | --- |
| Grafana dashboard | http://localhost:3000 (admin / see `.env`) |
| Prometheus | http://localhost:9090 |
| Exporter metrics | http://localhost:9101/metrics |

### Phase 0 — PoC scraper (console output only)

```bash
cd poc
pip install -r requirements.txt
python scrape_poc.py --host 192.168.100.1 --password <password>
```

## Modem

**ZTE MC801A** at `192.168.100.1`

## Acknowledgements

- **[nicjac/python-zte-mc801a](https://github.com/nicjac/python-zte-mc801a)** — Python library for the ZTE MC801A. The authentication flow (SHA-256 hash scheme) and confirmed API field names used in this project were reverse-engineered and validated with help from that codebase.
- **[Miononno](https://miononno.it/)** — Original JS-based research that inspired the nicjac library and, transitively, this project.

## License

MIT
