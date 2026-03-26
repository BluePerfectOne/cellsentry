# Report Generator

## Purpose

The report generator produces a self-contained **PDF complaint document** from data stored in Prometheus. It is designed to be submitted to a mobile operator or a national communications regulator (e.g. Traficom in Finland) as objective, timestamped evidence of service quality failures.

The output is a formal document, not a dashboard export. It uses only the device's own diagnostic API as a data source, with no third-party test servers or subjective measurements involved.

---

## CLI

```bash
python reporter/reporter.py \
  --from 2026-03-26 \
  --to 2026-04-09 \
  --operator "Telia Finland" \
  --output telia-complaint-2026-04.pdf
```

| Argument | Required | Description |
|---|---|---|
| `--from` | yes | Start of measurement period (YYYY-MM-DD) |
| `--to` | yes | End of measurement period (YYYY-MM-DD, inclusive) |
| `--operator` | no | Operator name for the cover page |
| `--address` | no | Service address for the cover page |
| `--contract-ref` | no | Contract or customer reference number |
| `--prometheus` | no | Prometheus base URL (default: `http://localhost:9090`) |
| `--output` | no | Output file path (default: `report.pdf`) |

---

## Report Structure

### 1. Cover Page

- Measurement period and report generation timestamp
- Modem model and firmware version
- Operator name, service address, contract reference (if supplied)
- **One-line summary verdict** — e.g. *"47 connection drops recorded over 14 days; LTE RSRP below −100 dBm for 31 % of the measurement period."*

### 2. Connection Reliability

Derived from the `cellsentry_connection_up` and `cellsentry_connection_drops_total` timeseries.

| Metric | Description |
|---|---|
| Total uptime % | Time `connection_up == 1` divided by total period |
| Total number of drops | Count of 1 → 0 transitions |
| Drop event table | Start time, end time, and duration for every individual drop |
| Mean time between failures (MTBF) | Total uptime ÷ number of drops |
| Mean time to restore (MTTR) | Average duration of a drop event |
| Longest outage | Single longest 1 → 0 → 1 event |

### 3. Signal Quality Statistics

For each metric, the report includes minimum, mean, median, 5th percentile, and 95th percentile over the measurement period. The 3GPP reference threshold for each metric is marked.

#### Metrics covered

- LTE: RSRP, RSRQ, SNR, RSSI
- 5G NR: RSRP, RSRQ, SINR

#### 3GPP reference thresholds (objective baseline)

These are standardised values from 3GPP TS 36.133 / TS 38.133 and are independent of the operator. Values below the "Poor" threshold represent objectively substandard service.

| Metric | Excellent | Good | Fair | Poor | Unusable |
|---|---|---|---|---|---|
| LTE RSRP | > −80 dBm | −80 to −90 | −90 to −100 | −100 to −110 | < −110 dBm |
| LTE RSRQ | > −10 dB | — | −10 to −15 | < −15 dB | — |
| LTE SNR | > 13 dB | — | 0 to 13 | < 0 dB | — |
| 5G NR RSRP | > −80 dBm | −80 to −90 | −90 to −100 | −100 to −110 | < −110 dBm |
| 5G NR SINR | > 20 dB | 13 to 20 | 0 to 13 | < 0 dB | — |

### 4. Time-of-Day Analysis

A heatmap of signal quality (RSRP) and drop events bucketed by hour of day × day of week over the measurement period. This distinguishes between:

- **Peak-hour congestion** — consistent degradation at 17:00–21:00, indicating the operator's cell is oversubscribed.
- **Equipment or tower fault** — random drops with no time-of-day correlation.
- **Interference** — time-correlated but not aligned with peak hours.

### 5. Band and Cell Identity

Stable presence of band, Physical Cell ID (PCI), and Cell ID confirms which specific tower sector is serving the address. This information is required by the operator to investigate on their infrastructure side.

| Field | Purpose |
|---|---|
| LTE band / PCI / Cell ID | Identifies the exact LTE sector |
| 5G NR band / PCI | Identifies the 5G NR cell |
| Frequency of band changes | Elevated rate indicates signal instability or handover issues |

### 6. Methodology (Appendix)

- Description of CellSentry: a passive monitoring tool that reads the modem's own internal diagnostic API at 15-second intervals. No active tests (speed tests, pings) are performed; all measurements are native modem values.
- Modem model: ZTE MC801A
- Firmware version: collected at runtime from the modem API
- Scrape interval and Prometheus retention settings
- Link to the open-source repository for full auditability
- Statement that no firmware modifications were made

---

## Implementation Plan

### Location

```
reporter/
  reporter.py          # main script
  requirements.txt     # requests, matplotlib, reportlab
```

### Data source

All data is fetched from the Prometheus HTTP API:

```
GET /api/v1/query_range?query=<metric>&start=<unix>&end=<unix>&step=60
```

No direct database access is required.

### PDF library choice

**reportlab** — produces PDF directly from Python without a browser or headless renderer. Well-suited for structured documents with embedded matplotlib charts. Final choice subject to evaluation during implementation.

---

## Minimum Useful Collection Period

| Period | Value |
|---|---|
| 7 days | Captures the weekly scheduled modem reboot (Tuesday 01:00–03:00) and a full week of daily usage patterns |
| 14 days | Enough for statistical weight on hourly heatmaps; two scheduled reboots |
| 30 days | Strong evidence; captures monthly billing cycle patterns and provides ample p5/p95 statistics |

**Data collection started: 2026-03-26.** Target first report generation: 2026-04-09 (14 days).
