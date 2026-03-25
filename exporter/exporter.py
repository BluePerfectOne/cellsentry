"""
CellSentry Prometheus Exporter
Phase 1 — ZTE MC801A signal metrics → Prometheus

Environment variables:
  MODEM_HOST       Modem LAN IP            (default: 192.168.100.1)
  MODEM_PASSWORD   Admin password          (required for full data)
  SCRAPE_INTERVAL  Seconds between scrapes (default: 15)
  EXPORTER_PORT    Metrics server port     (default: 9101)
  LOG_LEVEL        Python logging level    (default: INFO)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from typing import Optional

import requests
from prometheus_client import Counter, Gauge, start_http_server

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEM_HOST      = os.environ.get("MODEM_HOST", "192.168.100.1")
MODEM_PASSWORD  = os.environ.get("MODEM_PASSWORD", "")
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", "15"))
EXPORTER_PORT   = int(os.environ.get("EXPORTER_PORT", "9101"))

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("cellsentry")

UNAVAILABLE = "---"

CMD_FIELDS = ",".join([
    "network_type", "ppp_status", "wan_ipaddr", "network_provider", "signalbar",
    "lte_rssi", "lte_rsrp", "lte_rsrq", "lte_snr",
    "lte_ca_pcell_band", "lte_ca_scell_band", "lte_pci", "cell_id",
    "wan_active_band", "wan_lte_ca",
    "Z5g_rsrp", "Z5g_RSRQ", "Z5g_SINR",
    "nr5g_action_band", "nr5g_action_channel", "nr5g_pci",
    "pm_sensor_mdm", "pm_modem_5g",
    "realtime_rx_thrpt", "realtime_tx_thrpt",
])

# ---------------------------------------------------------------------------
# Prometheus metric definitions
# ---------------------------------------------------------------------------

_INFO_LABELS = ["network_type", "band_lte", "band_5g", "pci_lte", "pci_5g", "cell_id"]

g_connection_up   = Gauge("cellsentry_connection_up",           "1 = ppp_connected, 0 = not connected")
g_signal_bars     = Gauge("cellsentry_signal_bars",             "Modem signal bar count (0-5)")
g_modem_info      = Gauge("cellsentry_modem_info",              "Active modem state (always 1); labels carry band/cell info", _INFO_LABELS)

g_lte_rssi        = Gauge("cellsentry_lte_rssi_dbm",            "LTE RSSI (dBm)")
g_lte_rsrp        = Gauge("cellsentry_lte_rsrp_dbm",            "LTE RSRP (dBm)")
g_lte_rsrq        = Gauge("cellsentry_lte_rsrq_db",             "LTE RSRQ (dB)")
g_lte_snr         = Gauge("cellsentry_lte_snr_db",              "LTE SNR (dB)")

g_5g_rsrp         = Gauge("cellsentry_5gnr_rsrp_dbm",           "5G NR RSRP (dBm)")
g_5g_rsrq         = Gauge("cellsentry_5gnr_rsrq_db",            "5G NR RSRQ (dB)")
g_5g_sinr         = Gauge("cellsentry_5gnr_sinr_db",            "5G NR SINR (dB)")

g_temperature     = Gauge("cellsentry_temperature_celsius",      "Modem temperature (Celsius)", ["sensor"])
g_throughput      = Gauge("cellsentry_throughput_bps",           "Real-time throughput (bytes/s)", ["direction"])

c_drops           = Counter("cellsentry_connection_drops_total", "Total connection drop events detected")
g_scrape_success  = Gauge("cellsentry_scrape_success",           "1 = last modem scrape succeeded, 0 = failed")
g_scrape_duration = Gauge("cellsentry_scrape_duration_seconds",  "Duration of last successful modem scrape")

# ---------------------------------------------------------------------------
# Modem HTTP client
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Referer": f"http://{MODEM_HOST}/",
        "User-Agent": "cellsentry/1.0",
    })
    s.cookies.set("stok", "")
    return s


def _get_cmd(session: requests.Session, cmd: str, timeout: int = 10) -> dict:
    url = f"http://{MODEM_HOST}/goform/goform_get_cmd_process"
    r = session.get(url, params={"multi_data": "1", "cmd": cmd}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Authentication (all known ZTE CPE hash variants)
# ---------------------------------------------------------------------------

def _sha256(s: str) -> str:       return hashlib.sha256(s.encode()).hexdigest()
def _sha256_upper(s: str) -> str: return hashlib.sha256(s.encode()).hexdigest().upper()
def _md5(s: str) -> str:          return hashlib.md5(s.encode()).hexdigest()
def _md5_upper(s: str) -> str:    return hashlib.md5(s.encode()).hexdigest().upper()


def _hash_variants(password: str, ld: str) -> list[tuple[str, str]]:
    s1u = _sha256_upper(password)
    m1u = _md5_upper(password)
    m1  = _md5(password)
    return [
        ("G", _sha256(s1u + ld)),        # MC801A confirmed (SHA-256)
        ("H", _sha256_upper(s1u + ld)),
        ("A", _md5_upper(m1u + ld)),     # Older ZTE CPE (MD5 fallbacks)
        ("B", _md5(m1u + ld)),
        ("C", _md5_upper(m1 + ld)),
        ("D", _md5(m1 + ld)),
        ("E", m1u),
        ("F", m1),
    ]


def authenticate(session: requests.Session) -> bool:
    if not MODEM_PASSWORD:
        log.debug("No password configured — skipping authentication.")
        return True  # attempt unauthenticated reads

    try:
        ld = _get_cmd(session, "LD").get("LD", "")
    except requests.RequestException as exc:
        log.error("Could not fetch LD nonce: %s", exc)
        return False

    if not ld:
        log.error("Modem returned empty LD.")
        return False

    login_url = f"http://{MODEM_HOST}/goform/goform_set_cmd_process"
    variants = _hash_variants(MODEM_PASSWORD, ld)
    i = 0
    while i < len(variants):
        label, pw_hash = variants[i]
        try:
            url = f"{login_url}?isTest=false&goformId=LOGIN&password={pw_hash}"
            code = session.get(url, timeout=10).json().get("result", "?")
        except (requests.RequestException, json.JSONDecodeError) as exc:
            log.warning("Login variant %s failed: %s", label, exc)
            i += 1
            continue

        if code in ("0", "2"):
            log.info("Authenticated (variant %s).", label)
            return True

        # LD is invalidated after a failed attempt — refresh and recompute remaining variants
        try:
            new_ld = _get_cmd(session, "LD").get("LD", ld)
            if new_ld != ld:
                tried = {v[0] for v in variants[: i + 1]}
                variants = variants[: i + 1] + [
                    v for v in _hash_variants(MODEM_PASSWORD, new_ld) if v[0] not in tried
                ]
                ld = new_ld
        except requests.RequestException:
            pass

        i += 1

    log.error("All authentication variants failed.")
    return False


# ---------------------------------------------------------------------------
# Metric update
# ---------------------------------------------------------------------------

def _fval(data: dict, key: str) -> Optional[float]:
    """Parse a numeric field from a modem response; return None if unavailable."""
    v = data.get(key, "")
    if not v or v == UNAVAILABLE:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _sval(data: dict, key: str) -> str:
    v = data.get(key, "")
    return "" if (not v or v == UNAVAILABLE) else str(v)


_prev_ppp_status: Optional[str] = None
_last_info_labels: Optional[tuple] = None


def update_metrics(data: dict) -> None:
    global _prev_ppp_status, _last_info_labels

    # --- Connection state & drop detection ---
    ppp = _sval(data, "ppp_status")
    g_connection_up.set(1 if ppp == "ppp_connected" else 0)
    if _prev_ppp_status == "ppp_connected" and ppp != "ppp_connected":
        log.warning("Connection drop detected (new status: %r).", ppp)
        c_drops.inc()
    _prev_ppp_status = ppp

    # --- Signal bars ---
    bars = _fval(data, "signalbar")
    if bars is not None:
        g_signal_bars.set(bars)

    # --- Modem info labels (band, cell, network type) ---
    # Remove old label combination first to avoid stale series when band/cell changes.
    info = (
        _sval(data, "network_type"),
        _sval(data, "lte_ca_pcell_band"),
        _sval(data, "nr5g_action_band"),
        _sval(data, "lte_pci"),
        _sval(data, "nr5g_pci"),
        _sval(data, "cell_id"),
    )
    if _last_info_labels is not None and _last_info_labels != info:
        try:
            g_modem_info.remove(*_last_info_labels)
        except Exception:
            pass
    g_modem_info.labels(*info).set(1)
    _last_info_labels = info

    # --- LTE signal ---
    for key, gauge in (
        ("lte_rssi", g_lte_rssi),
        ("lte_rsrp", g_lte_rsrp),
        ("lte_rsrq", g_lte_rsrq),
        ("lte_snr",  g_lte_snr),
    ):
        v = _fval(data, key)
        if v is not None:
            gauge.set(v)

    # --- 5G NR signal ---
    for key, gauge in (
        ("Z5g_rsrp", g_5g_rsrp),
        ("Z5g_RSRQ", g_5g_rsrq),
        ("Z5g_SINR", g_5g_sinr),
    ):
        v = _fval(data, key)
        if v is not None:
            gauge.set(v)

    # --- Temperature ---
    for key, label in (("pm_sensor_mdm", "lte"), ("pm_modem_5g", "5g")):
        v = _fval(data, key)
        if v is not None:
            g_temperature.labels(sensor=label).set(v)

    # --- Throughput ---
    for key, direction in (("realtime_rx_thrpt", "rx"), ("realtime_tx_thrpt", "tx")):
        v = _fval(data, key)
        if v is not None:
            g_throughput.labels(direction=direction).set(v)


# ---------------------------------------------------------------------------
# Scrape loop
# ---------------------------------------------------------------------------

def _all_unavailable(data: dict) -> bool:
    return bool(data) and all(v in (UNAVAILABLE, "", None) for v in data.values())


def scrape_loop() -> None:
    session = _make_session()
    authenticated = False

    while True:
        t0 = time.monotonic()
        try:
            if not authenticated:
                authenticated = authenticate(session)
                if not authenticated:
                    g_scrape_success.set(0)
                    log.warning("Authentication failed; will retry in %ds.", SCRAPE_INTERVAL)
                    time.sleep(SCRAPE_INTERVAL)
                    session = _make_session()
                    continue

            data = _get_cmd(session, CMD_FIELDS)

            if _all_unavailable(data):
                # Session likely expired — re-authenticate on next iteration
                log.info("All fields unavailable; session may have expired. Re-authenticating.")
                authenticated = False
                g_scrape_success.set(0)
            else:
                elapsed = time.monotonic() - t0
                g_scrape_duration.set(elapsed)
                update_metrics(data)
                g_scrape_success.set(1)
                log.debug("Scrape OK (%.3fs).", elapsed)

        except requests.Timeout:
            log.warning("Modem request timed out.")
            g_scrape_success.set(0)
        except requests.ConnectionError as exc:
            # Covers modem reboot (weekly scheduled restart included)
            log.warning("Connection error: %s", exc)
            g_scrape_success.set(0)
            authenticated = False  # new session needed after reconnect
        except requests.RequestException as exc:
            log.warning("HTTP error: %s", exc)
            g_scrape_success.set(0)
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
            g_scrape_success.set(0)

        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, SCRAPE_INTERVAL - elapsed))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not MODEM_PASSWORD:
        log.warning(
            "MODEM_PASSWORD not set. Unauthenticated reads will be attempted; "
            "most signal fields require authentication on this firmware."
        )
    log.info(
        "CellSentry exporter starting — host=%s port=%d interval=%ds",
        MODEM_HOST, EXPORTER_PORT, SCRAPE_INTERVAL,
    )
    start_http_server(EXPORTER_PORT)
    log.info("Metrics available at http://0.0.0.0:%d/metrics", EXPORTER_PORT)
    scrape_loop()


if __name__ == "__main__":
    main()
