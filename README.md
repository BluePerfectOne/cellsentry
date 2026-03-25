# cellsentry

Local 5G/LTE signal monitor for ZTE MC801A. Collects RSRP, RSRQ, SINR and connection state over time so you have data when your operator doesn't.

## Background

After switching mobile operators, frequent connection drops started occurring on a ZTE MC801A 5G home router. Operator support declined to assist ("modem is too old"), so this project exists to collect objective, time-series signal quality data to either prove or disprove a signal/configuration problem independently.

## Project Status

**Phase 0 — Proof of Concept** (current): Validate that the modem exposes machine-readable signal data via its local web interface before committing to a full implementation.

## Documentation

All design decisions and research live in [`doc/`](doc/):

| Document | Summary |
| --- | --- |
| [Project Overview](doc/project-overview.md) | Goals, scope, phased plan |
| [ZTE MC801A API Research](doc/zte-mc801a-api.md) | How to extract signal data from the modem |
| [Language Selection](doc/language-selection.md) | Python vs Rust vs others — rationale |
| [Monitoring Stack](doc/monitoring-stack.md) | Prometheus + Grafana evaluation and alternatives |

## Quick Start (PoC)

```bash
cd poc
pip install -r requirements.txt
python scrape_poc.py --host 192.168.100.1
```

The PoC will attempt to fetch signal metrics from the modem and print them to the console. Run it with `--help` to see options including password authentication.

## Modem

**ZTE MC801A** at `192.168.100.1`

## Acknowledgements

- **[nicjac/python-zte-mc801a](https://github.com/nicjac/python-zte-mc801a)** — Python library for the ZTE MC801A. The authentication flow (SHA-256 hash scheme) and confirmed API field names used in this project were reverse-engineered and validated with help from that codebase.
- **[Miononno](https://miononno.it/)** — Original JS-based research that inspired the nicjac library and, transitively, this project.

## License

MIT
