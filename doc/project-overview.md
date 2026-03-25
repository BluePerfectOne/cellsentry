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

### Phase 0 — Proof of Concept (current)

**Question to answer:** Can we extract signal data from the modem's local web interface programmatically?

**Done when:** A script can fetch at least RSRP, RSRQ, SINR, and network type from `192.168.100.1` and print them to the console.

**Go/No-go decision:** If the API is locked down, requires firmware-level access, or returns no useful data, the project does not make sense to continue.

### Phase 1 — Data Collection

Once the PoC is validated:

- Formalise the scraper as a small service (see [Language Selection](language-selection.md)).
- Persist data to a time-series store.
- Basic drop/recovery detection.

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
