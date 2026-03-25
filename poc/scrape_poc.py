"""
PoC scraper for the ZTE MC801A modem signal metrics.

Usage:
  python scrape_poc.py
  python scrape_poc.py --host 192.168.100.1
  python scrape_poc.py --host 192.168.100.1 --password <admin_password>
  python scrape_poc.py --host 192.168.100.1 --password <admin_password> --interval 10

What it does:
  1. Optionally authenticates with the modem (required if unauthenticated reads fail).
  2. Fetches key signal metrics from the modem's internal JSON API.
  3. Prints a formatted table of results to the console.
  4. If --interval is given, repeats forever at that interval.

Exit codes:
  0  At least one successful read.
  1  Could not reach the modem at all.
  2  Reached the modem but all fields came back empty/unavailable.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Modem API constants
# ---------------------------------------------------------------------------

# Fields to request — confirmed against firmware MC801A_Elisa3_B22.
# Field names differ from older ZTE CPE firmware: lte_rsrp/lte_rsrq/lte_snr
# replace the bare rsrp/rsrq/sinr used in older models, and nr5g_* replaces Z5g_*
# for some identifiers while Z5g_SINR / Z5g_rsrp remain as-is.
CMD_FIELDS = ",".join([
    # Network state
    "network_type",
    "ppp_status",
    "wan_ipaddr",
    "ipv6_wan_ipaddr",
    "network_provider",
    "signalbar",
    # LTE (anchor in NSA, or standalone fallback)
    "lte_rssi",
    "lte_rsrp",
    "lte_rsrq",
    "lte_snr",
    "lte_ca_pcell_band",
    "lte_ca_pcell_bandwidth",
    "lte_ca_scell_band",
    "lte_ca_scell_bandwidth",
    "lte_multi_ca_scell_info",
    "lte_pci",
    "cell_id",
    "wan_active_band",
    "wan_active_channel",
    "wan_lte_ca",
    # 5G NR
    "Z5g_rsrp",
    "Z5g_RSRQ",
    "Z5g_SINR",
    "nr5g_action_band",
    "nr5g_action_channel",
    "nr5g_pci",
    # Thermal (useful for spotting throttling)
    "pm_sensor_mdm",
    "pm_modem_5g",
    # Throughput
    "realtime_rx_thrpt",
    "realtime_tx_thrpt",
    "realtime_rx_bytes",
    "realtime_tx_bytes",
    "realtime_time",
])

UNAVAILABLE_SENTINEL = "---"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_session(host: str) -> requests.Session:
    session = requests.Session()
    # The modem checks the Referer header; use bare host URL (no /index.html)
    # as confirmed by both HAR capture and the reference library.
    session.headers.update({
        "Referer": f"http://{host}/",
        "User-Agent": "cellsentry/0.1 (PoC)",
    })
    # The modem expects an initial stok cookie (empty string before login)
    session.cookies.set("stok", "")
    return session


def _get_cmd(session: requests.Session, host: str, cmd: str, timeout: int = 10) -> dict:
    url = f"http://{host}/goform/goform_get_cmd_process"
    params = {"multi_data": "1", "cmd": cmd}
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

# ZTE firmware versions differ in their exact password hashing scheme.
# The variants below cover all schemes found in the wild for ZTE CPE routers.
# authenticate() tries them in order and stops at the first success.

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _md5_upper(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _sha256_upper(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest().upper()


def _hash_variants(plain_password: str, ld: str) -> list[tuple[str, str]]:
    """
    Return (label, hash_value) pairs for every known ZTE auth scheme.
    Tried in order; the first one that returns result='0' wins.

    Confirmed schemes (reverse-engineered from ZTE MC801A HAR capture):
      G  SHA256(SHA256(pwd).upper() + LD)       << MC801A confirmed (this firmware)
      H  SHA256(SHA256(pwd).upper() + LD).upper()

    Older ZTE CPE family (MD5-based, kept for compatibility):
      A  MD5(MD5(pwd).upper() + LD).upper()
      B  MD5(MD5(pwd).upper() + LD)
      C  MD5(md5(pwd) + LD).upper()
      D  MD5(md5(pwd) + LD)
      E  MD5(pwd).upper()                      — no LD (some minimal firmwares)
      F  MD5(pwd)
    """
    s1_upper = _sha256_upper(plain_password)
    m1_upper = _md5_upper(plain_password)
    m1_lower = _md5(plain_password)
    return [
        # SHA-256 variants first (confirmed for MC801A)
        ("G", _sha256(s1_upper + ld)),
        ("H", _sha256_upper(s1_upper + ld)),
        # MD5 variants (older ZTE firmware fallback)
        ("A", _md5_upper(m1_upper + ld)),
        ("B", _md5(m1_upper + ld)),
        ("C", _md5_upper(m1_lower + ld)),
        ("D", _md5(m1_lower + ld)),
        ("E", m1_upper),
        ("F", m1_lower),
    ]


def authenticate(session: requests.Session, host: str, password: str, debug: bool = False) -> bool:
    """
    Perform the ZTE modem login sequence, trying all known hash variants.
    Returns True on success, False if all variants fail.
    """
    # Step 1: fetch the login nonce (LD)
    try:
        ld_data = _get_cmd(session, host, "LD")
    except requests.RequestException as exc:
        print(f"[ERROR] Could not fetch LD nonce: {exc}", file=sys.stderr)
        return False

    ld = ld_data.get("LD", "")
    if not ld:
        print("[ERROR] Modem returned empty LD nonce.", file=sys.stderr)
        return False

    if debug:
        print(f"[DEBUG] LD nonce  : {ld}")
        print(f"[DEBUG] MD5(pwd)  : {_md5(password)}")

    login_base = f"http://{host}/goform/goform_set_cmd_process"

    variants = _hash_variants(password, ld)
    i = 0
    while i < len(variants):
        label, password_hash = variants[i]
        if debug:
            print(f"[DEBUG] Trying variant {label}: {password_hash}")

        # Login is sent as GET with query params (confirmed by reference library
        # and HAR capture — the modem accepts both GET and POST for set_cmd_process,
        # but GET is simpler and avoids Content-Type negotiation issues).
        login_url = f"{login_base}?isTest=false&goformId=LOGIN&password={password_hash}"
        try:
            response = session.get(login_url, timeout=10)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            print(f"[ERROR] Login request failed (variant {label}): {exc}", file=sys.stderr)
            i += 1
            continue

        code = result.get("result", "?")
        if code == "0":
            print(f"[AUTH] Login successful (hash variant {label}).")
            return True

        if debug:
            print(f"[DEBUG] Variant {label} → result={code}")

        # result='2' means already logged in on some firmware versions
        if code == "2":
            print(f"[AUTH] Modem reports already logged in (variant {label}). Proceeding.")
            return True

        # The LD nonce is invalidated after each failed attempt — fetch a fresh one
        # and recompute the remaining variants so subsequent attempts use the new nonce.
        try:
            ld_data = _get_cmd(session, host, "LD")
            new_ld = ld_data.get("LD", ld)
            if new_ld != ld:
                if debug:
                    print(f"[DEBUG] LD refreshed: {new_ld}")
                # Recompute all variants with the new nonce, skip those already tried
                all_fresh = _hash_variants(password, new_ld)
                tried_labels = {v[0] for v in variants[:i + 1]}
                variants = variants[:i + 1] + [v for v in all_fresh if v[0] not in tried_labels]
                ld = new_ld
        except requests.RequestException:
            pass  # keep using the old LD

        i += 1

    print("[AUTH] All hash variants failed. Check password and try --debug-auth.", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Data fetch and display
# ---------------------------------------------------------------------------

def fetch_signal(session: requests.Session, host: str) -> Optional[dict]:
    """
    Fetch all signal fields. Returns the raw dict, or None on network error.
    """
    try:
        return _get_cmd(session, host, CMD_FIELDS)
    except requests.Timeout:
        print("[ERROR] Request timed out.", file=sys.stderr)
    except requests.ConnectionError as exc:
        print(f"[ERROR] Connection error: {exc}", file=sys.stderr)
    except requests.RequestException as exc:
        print(f"[ERROR] HTTP error: {exc}", file=sys.stderr)
    return None


def _val(data: dict, key: str) -> str:
    """Return the value for key, or 'N/A' if missing or sentinel."""
    v = data.get(key, "N/A")
    if v == UNAVAILABLE_SENTINEL or v == "":
        return "N/A"
    return str(v)


def print_results(data: dict, timestamp: datetime) -> None:
    ts = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'─' * 52}")
    print(f"  ZTE MC801A Signal Report — {ts}")
    print(f"{'─' * 52}")

    print(f"  Operator       : {_val(data, 'network_provider')}")
    print(f"  Network type   : {_val(data, 'network_type')}")
    print(f"  Connection     : {_val(data, 'ppp_status')}")
    print(f"  Signal bars    : {_val(data, 'signalbar')}")
    print(f"  WAN IP (v4)    : {_val(data, 'wan_ipaddr')}")
    print(f"  WAN IP (v6)    : {_val(data, 'ipv6_wan_ipaddr')}")

    print()
    print("  ── LTE (anchor / fallback) ──")
    print(f"  Band           : B{_val(data, 'lte_ca_pcell_band')} / {_val(data, 'wan_active_band')}")
    print(f"  CA             : {_val(data, 'wan_lte_ca')}")
    ca_info = _val(data, 'lte_multi_ca_scell_info')
    if ca_info != 'N/A':
        print(f"  CA cells       : {ca_info}")
    print(f"  PCI            : {_val(data, 'lte_pci')}")
    print(f"  Cell ID        : {_val(data, 'cell_id')}")
    print(f"  RSSI           : {_val(data, 'lte_rssi')} dBm")
    print(f"  RSRP           : {_val(data, 'lte_rsrp')} dBm")
    print(f"  RSRQ           : {_val(data, 'lte_rsrq')} dB")
    print(f"  SNR            : {_val(data, 'lte_snr')} dB")

    print()
    print("  ── 5G NR ──")
    print(f"  Band           : {_val(data, 'nr5g_action_band')}")
    print(f"  Channel        : {_val(data, 'nr5g_action_channel')}")
    print(f"  PCI            : {_val(data, 'nr5g_pci')}")
    print(f"  RSRP           : {_val(data, 'Z5g_rsrp')} dBm")
    print(f"  RSRQ           : {_val(data, 'Z5g_RSRQ')} dB")
    print(f"  SINR           : {_val(data, 'Z5g_SINR')} dB")

    pm_mdm = _val(data, 'pm_sensor_mdm')
    pm_5g  = _val(data, 'pm_modem_5g')
    if pm_mdm != 'N/A' or pm_5g != 'N/A':
        print()
        print("  ── Temperature ──")
        print(f"  Modem (LTE)    : {pm_mdm} °C")
        print(f"  Modem (5G)     : {pm_5g} °C")

    print()
    print("  ── Throughput ──")
    rx = data.get("realtime_rx_thrpt", "N/A")
    tx = data.get("realtime_tx_thrpt", "N/A")
    print(f"  Down           : {_format_throughput(rx)}")
    print(f"  Up             : {_format_throughput(tx)}")
    uptime_s = data.get("realtime_time", "")
    if uptime_s and uptime_s not in ("", "---"):
        try:
            h, rem = divmod(int(uptime_s), 3600)
            m, s = divmod(rem, 60)
            print(f"  Session uptime : {h}h {m}m {s}s")
        except ValueError:
            pass
    print(f"{'─' * 52}\n")


def _format_throughput(raw: str) -> str:
    """Convert bytes/s string from modem to a human-readable value."""
    if raw in ("N/A", UNAVAILABLE_SENTINEL, ""):
        return "N/A"
    try:
        bps = int(raw)
        if bps >= 1_000_000:
            return f"{bps / 1_000_000:.1f} Mbps"
        if bps >= 1_000:
            return f"{bps / 1_000:.1f} kbps"
        return f"{bps} bps"
    except ValueError:
        return raw


def check_all_unavailable(data: dict) -> bool:
    """
    Returns True if every field in the response is the unavailability sentinel.
    This would indicate the API is reachable but returning no useful data.
    """
    values = list(data.values())
    if not values:
        return True
    return all(v in (UNAVAILABLE_SENTINEL, "", "N/A") for v in values)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Proof-of-concept scraper for ZTE MC801A signal metrics."
    )
    parser.add_argument(
        "--host",
        default="192.168.100.1",
        help="Modem IP address (default: 192.168.100.1)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Admin password (plain text). If omitted, unauthenticated access is tried first.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Poll interval in seconds. 0 (default) = run once.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Also print the raw JSON response from the modem.",
    )
    parser.add_argument(
        "--debug-auth",
        action="store_true",
        help="Print LD nonce, intermediate hashes, and per-variant results to help diagnose authentication failures.",
    )
    args = parser.parse_args()

    session = _make_session(args.host)

    # Optionally authenticate
    if args.password:
        ok = authenticate(session, args.host, args.password, debug=args.debug_auth)
        if not ok:
            print(
                "[WARN] All authentication variants failed. Attempting unauthenticated read anyway.",
                file=sys.stderr,
            )
    else:
        print("[INFO] No password provided. Trying unauthenticated access.")

    successful_reads = 0

    while True:
        data = fetch_signal(session, args.host)

        if data is None:
            print(f"[ERROR] Failed to reach modem at {args.host}.", file=sys.stderr)
            if successful_reads == 0 and args.interval == 0:
                return 1
        else:
            if args.raw:
                print("\n[RAW JSON]", json.dumps(data, indent=2))

            if check_all_unavailable(data):
                print(
                    "[WARN] Modem responded but all fields are unavailable. "
                    "This may indicate an authentication requirement.",
                    file=sys.stderr,
                )
                if successful_reads == 0 and args.interval == 0:
                    return 2
            else:
                print_results(data, datetime.now(timezone.utc))
                successful_reads += 1

        if args.interval <= 0:
            break

        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[INFO] Stopped by user.")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
