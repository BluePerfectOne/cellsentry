# Project Overview

## Problem Statement

After switching mobile operators, a ZTE MC801A 5G home router (cost ~500 €) started experiencing frequent connection drops. The new operator's support team refused to investigate, citing the age of the device. Rather than accept that answer, this project will collect continuous, objective signal-quality metrics to determine whether:

1. The signal from the new operator's tower is weaker or more unstable than it should be.
2. Specific bands or channels are problematic.
3. The modem's behaviour changed in any other measurable way compared to the previous operator.

The data can then be used as evidence in a formal complaint, for comparison against published operator coverage metrics, or simply for personal understanding.

## Goals

| Priority | Goal |
| --- | --- |
| Must | Continuously read signal KPIs from the modem (RSRP, RSRQ, SINR, RSSI, band, channel). |
| Must | Record measurements with timestamps for trend analysis. |
| Must | Detect and log connection-drop events. |
| Should | Visualise trends in a local dashboard. |
| Should | Alert when signal degrades below configurable thresholds. |
| Nice-to-have | Correlate signal drops with operator outage reports or speed-test results. |

## Non-Goals

- Modifying the modem's firmware or configuration.
- Any form of network intrusion or unauthorised access beyond the modem's own admin interface using valid credentials.
- Cloud-hosted data collection (this is a local/home-network tool).

## Phases

### Phase 0 — Proof of Concept ✓ Done

**Question answered:** Yes — the modem exposes a full set of signal metrics over its local HTTP+JSON API without firmware modification.

**Validated on:** 2026-03-25, firmware `MC801A_Elisa3_B22`, operator Telia FI.

**Fields confirmed:** RSRP, RSRQ, SINR, SNR, RSSI, network type (ENDC), LTE band (B20), 5G NR band (n78), PCI, Cell ID, temperature (LTE + 5G), throughput.

### Phase 1 — Data Collection (current)

- Prometheus exporter service (`exporter/`) scrapes the modem every 15 s and serves metrics on `:9101/metrics`.
- Prometheus stores up to 90 days of time-series data.
- Grafana dashboard (`grafana/`) visualises signal quality, connection drops, temperature and throughput.
- Drop/recovery detection: `cellsentry_connection_drops_total` counter increments on every `ppp_connected → other` transition.
- Entire stack started with `docker compose up -d`.

### Phase 2 — Visualisation and Alerting

- Local Grafana dashboard (see [Monitoring Stack](monitoring-stack.md)).
- Configurable threshold alerts (e.g., RSRP below −110 dBm for more than 60 s).

### Phase 3 — Polish

- Docker Compose for easy deployment on any home server / Raspberry Pi.
- README with operator comparison instructions.
- Publish to GitHub.

## Modem Reference

**Model:** ZTE MC801A  
**Local address:** `http://192.168.100.1`  
**Interface:** Web GUI; behind it a ZTE-proprietary HTTP+JSON internal API (documented in [ZTE MC801A API Research](zte-mc801a-api.md)).
