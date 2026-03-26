#!/usr/bin/env python3
"""
CellSentry Report Generator
============================
Produces a self-contained PDF from Prometheus data for use as evidence
in a formal complaint to a mobile operator or communications regulator.

Usage:
  python reporter/reporter.py --from 2026-03-26 --to 2026-04-09 \\
      --operator "Telia Finland" --output report.pdf
"""

from __future__ import annotations

import argparse
import datetime
import io
import sys
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # must be before pyplot import
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm
CHART_W = PAGE_W - 2 * MARGIN   # ~481 pt
CHART_DPI = 150

# ---------------------------------------------------------------------------
# Signal metrics: key -> (prometheus_metric, display_label, unit)
# ---------------------------------------------------------------------------

_METRICS: dict[str, tuple[str, str, str]] = {
    "lte_rsrp":  ("cellsentry_lte_rsrp_dbm",  "LTE RSRP",    "dBm"),
    "lte_rsrq":  ("cellsentry_lte_rsrq_db",   "LTE RSRQ",    "dB"),
    "lte_snr":   ("cellsentry_lte_snr_db",    "LTE SNR",     "dB"),
    "lte_rssi":  ("cellsentry_lte_rssi_dbm",  "LTE RSSI",    "dBm"),
    "nr_rsrp":   ("cellsentry_5gnr_rsrp_dbm", "5G NR RSRP",  "dBm"),
    "nr_sinr":   ("cellsentry_5gnr_sinr_db",  "5G NR SINR",  "dB"),
}

# Quality thresholds: (excellent_min, good_min, fair_min, poor_min)
# None means the boundary does not apply for that metric.
_THRESHOLDS: dict[str, tuple] = {
    "lte_rsrp":  (-80,  -90,   -100, -110),
    "lte_rsrq":  (-10,  None,  -15,  None),
    "lte_snr":   (20,   13,    0,    None),
    "lte_rssi":  (-65,  -75,   -85,  -95),
    "nr_rsrp":   (-80,  -90,   -100, -110),
    "nr_sinr":   (20,   13,    0,    None),
}

_QUALITY_COLORS = {
    "Excellent": colors.HexColor("#c8e6c9"),
    "Good":      colors.HexColor("#dcedc8"),
    "Fair":      colors.HexColor("#fff9c4"),
    "Poor":      colors.HexColor("#ffcdd2"),
    "No data":   colors.white,
}


def _quality(key: str, value: float) -> str:
    exc, good, fair, poor = _THRESHOLDS[key]
    if exc is not None and value >= exc:
        return "Excellent"
    if good is not None and value >= good:
        return "Good"
    if fair is not None and value >= fair:
        return "Fair"
    return "Poor"


# ---------------------------------------------------------------------------
# Prometheus queries
# ---------------------------------------------------------------------------

def _query_range(
    base_url: str,
    query: str,
    start_ts: int,
    end_ts: int,
    step: int = 60,
) -> list[tuple[float, float]]:
    """Query Prometheus range endpoint. Returns [(unix_ts, value), ...]."""
    resp = requests.get(
        f"{base_url}/api/v1/query_range",
        params={"query": query, "start": start_ts, "end": end_ts, "step": step},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "success":
        return []
    results = payload["data"]["result"]
    if not results:
        return []
    return [
        (float(ts), float(v))
        for ts, v in results[0]["values"]
        if v not in ("NaN", "+Inf", "-Inf")
    ]


def _query_instant_labels(base_url: str, query: str, ts: int) -> dict[str, str]:
    """Return metric labels from the first result of an instant query."""
    resp = requests.get(
        f"{base_url}/api/v1/query",
        params={"query": query, "time": ts},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "success":
        return {}
    results = payload["data"]["result"]
    if not results:
        return {}
    return results[0].get("metric", {})


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _values(series: list[tuple[float, float]]) -> list[float]:
    return [v for _, v in series]


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {k: None for k in ("min", "max", "mean", "median", "p5", "p95", "count")}
    a = np.array(vals, dtype=float)
    return {
        "min":    float(np.min(a)),
        "max":    float(np.max(a)),
        "mean":   float(np.mean(a)),
        "median": float(np.median(a)),
        "p5":     float(np.percentile(a, 5)),
        "p95":    float(np.percentile(a, 95)),
        "count":  len(vals),
    }


def _find_drops(
    conn_series: list[tuple[float, float]],
) -> list[tuple[float, Optional[float], float]]:
    """
    Scan connection_up time series and return drop events.
    Each event is (start_ts, end_ts_or_None, duration_s).
    end_ts is None if the drop was still ongoing at end of series.
    """
    drops: list[tuple[float, Optional[float], float]] = []
    in_drop = False
    drop_start: float = 0.0
    for ts, val in conn_series:
        if not in_drop and val == 0.0:
            in_drop = True
            drop_start = ts
        elif in_drop and val == 1.0:
            drops.append((drop_start, ts, ts - drop_start))
            in_drop = False
    if in_drop and conn_series:
        last_ts = conn_series[-1][0]
        drops.append((drop_start, None, last_ts - drop_start))
    return drops


def _uptime_pct(conn_series: list[tuple[float, float]]) -> float:
    if not conn_series:
        return float("nan")
    up = sum(1 for _, v in conn_series if v == 1.0)
    return 100.0 * up / len(conn_series)


def _heatmap_array(series: list[tuple[float, float]]) -> np.ndarray:
    """
    Returns a (7 days-of-week × 24 hours) array of mean values,
    NaN where no data. Day 0 = Monday, day 6 = Sunday.
    """
    buckets: list[list[list[float]]] = [[[] for _ in range(24)] for _ in range(7)]
    for ts, val in series:
        dt = datetime.datetime.fromtimestamp(ts, _UTC)
        buckets[dt.weekday()][dt.hour].append(val)
    result = np.full((7, 24), np.nan)
    for d in range(7):
        for h in range(24):
            if buckets[d][h]:
                result[d][h] = float(np.mean(buckets[d][h]))
    return result


# ---------------------------------------------------------------------------
# Chart helpers  (light / print-friendly theme)
# ---------------------------------------------------------------------------

_CHART_BG  = "#ffffff"
_PANEL_BG  = "#f8f9fa"
_GRID_COL  = "#dddddd"


def _apply_light_style(ax: plt.Axes) -> None:
    ax.set_facecolor(_PANEL_BG)
    ax.tick_params(colors="#333333", labelsize=7)
    ax.xaxis.label.set_color("#333333")
    ax.yaxis.label.set_color("#333333")
    ax.title.set_color("#0d1b2a")
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID_COL)
    ax.grid(True, alpha=0.6, color=_GRID_COL)


def _fig_to_image(fig: plt.Figure, width_pt: float) -> RLImage:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    buf.seek(0)
    img = RLImage(buf)
    scale = width_pt / img.imageWidth
    img.drawWidth  = width_pt
    img.drawHeight = img.imageHeight * scale
    return img


def _chart_connection(conn_series: list[tuple[float, float]]) -> Optional[RLImage]:
    if not conn_series:
        return None
    fig, ax = plt.subplots(figsize=(12, 2.5))
    fig.patch.set_facecolor(_CHART_BG)
    _apply_light_style(ax)
    ts = [datetime.datetime.fromtimestamp(t, _UTC) for t, _ in conn_series]
    vs = [v for _, v in conn_series]
    ax.fill_between(ts, vs,         step="post", color="#2e7d32", alpha=0.55, label="Connected")
    ax.fill_between(ts, [1 - v for v in vs],
                    step="post", color="#c62828", alpha=0.55, label="Disconnected")
    ax.set_ylim(-0.05, 1.15)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Down", "Up"], color="#333333", fontsize=8)
    ax.set_title("Connection State", color="#0d1b2a", fontsize=9)
    ax.legend(fontsize=7, facecolor="white", labelcolor="#333333", loc="upper right")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=20, ha="right")
    fig.tight_layout()
    return _fig_to_image(fig, CHART_W)


def _chart_signal(
    lte_data: dict[str, list],
    nr_data: dict[str, list],
) -> RLImage:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    fig.patch.set_facecolor(_CHART_BG)
    for ax in (ax1, ax2):
        _apply_light_style(ax)

    lte_plot = [
        ("lte_rsrp", "LTE RSRP", "#1565c0"),
        ("lte_rsrq", "LTE RSRQ", "#2e7d32"),
        ("lte_snr",  "LTE SNR",  "#e65100"),
    ]
    for key, label, color in lte_plot:
        series = lte_data.get(key, [])
        if series:
            ts = [datetime.datetime.fromtimestamp(t, _UTC) for t, _ in series]
            ax1.plot(ts, [v for _, v in series], lw=0.8, color=color, label=label, alpha=0.9)
    ax1.set_ylabel("dBm / dB", color="#333333", fontsize=8)
    ax1.set_title("LTE Signal Quality", color="#0d1b2a", fontsize=9)
    ax1.legend(fontsize=7, facecolor="white", labelcolor="#333333", loc="upper right")

    nr_plot = [
        ("nr_rsrp", "5G NR RSRP", "#6a1b9a"),
        ("nr_sinr", "5G NR SINR", "#00695c"),
    ]
    for key, label, color in nr_plot:
        series = nr_data.get(key, [])
        if series:
            ts = [datetime.datetime.fromtimestamp(t, _UTC) for t, _ in series]
            ax2.plot(ts, [v for _, v in series], lw=0.8, color=color, label=label, alpha=0.9)
    ax2.set_ylabel("dBm / dB", color="#333333", fontsize=8)
    ax2.set_title("5G NR Signal Quality", color="#0d1b2a", fontsize=9)
    ax2.legend(fontsize=7, facecolor="white", labelcolor="#333333", loc="upper right")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=20, ha="right")
    fig.tight_layout()
    return _fig_to_image(fig, CHART_W)


def _chart_heatmap(data: np.ndarray, title: str, unit: str) -> RLImage:
    fig, ax = plt.subplots(figsize=(12, 3))
    fig.patch.set_facecolor(_CHART_BG)
    _apply_light_style(ax)
    masked = np.ma.masked_invalid(data)
    im = ax.imshow(masked, aspect="auto", cmap="RdYlGn", interpolation="nearest")
    cbar = fig.colorbar(im, ax=ax, pad=0.01, shrink=0.8)
    cbar.set_label(unit, color="#333333", fontsize=8)
    cbar.ax.tick_params(colors="#333333")
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ax.set_yticks(range(7))
    ax.set_yticklabels(days, color="#333333", fontsize=8)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 2)], color="#333333", fontsize=7)
    ax.set_title(title, color="#0d1b2a", fontsize=9)
    fig.tight_layout()
    return _fig_to_image(fig, CHART_W)


# ---------------------------------------------------------------------------
# PDF styles
# ---------------------------------------------------------------------------

def _make_styles() -> dict:
    base = getSampleStyleSheet()

    def _add(name: str, **kw) -> ParagraphStyle:
        if name not in base:
            base.add(ParagraphStyle(name=name, **kw))
        return base[name]

    _add("ReportTitle",
         parent=base["Title"],
         fontSize=22, spaceAfter=6,
         textColor=colors.HexColor("#0d1b2a"),
         alignment=TA_CENTER)
    _add("ReportSubtitle",
         parent=base["Normal"],
         fontSize=11, spaceAfter=4,
         textColor=colors.HexColor("#2b4c7e"),
         alignment=TA_CENTER)
    _add("ReportH1",
         parent=base["Heading1"],
         fontSize=13, spaceBefore=14, spaceAfter=4,
         textColor=colors.HexColor("#0d1b2a"))
    _add("ReportH2",
         parent=base["Heading2"],
         fontSize=10, spaceBefore=8, spaceAfter=2,
         textColor=colors.HexColor("#2b4c7e"))
    _add("ReportBody",
         parent=base["Normal"],
         fontSize=9, leading=13, spaceAfter=3)
    _add("Note",
         parent=base["Normal"],
         fontSize=8, leading=11,
         textColor=colors.HexColor("#555555"), spaceAfter=3)
    _add("Verdict",
         parent=base["Normal"],
         fontSize=10, leading=15,
         spaceBefore=6, spaceAfter=6,
         backColor=colors.HexColor("#fff3cd"),
         borderColor=colors.HexColor("#ffc107"),
         borderWidth=1, borderPad=8,
         textColor=colors.HexColor("#333333"))
    return base


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

_TBL_HEADER = colors.HexColor("#0d1b2a")
_TBL_ROW1   = colors.HexColor("#eef2f7")
_TBL_ROW2   = colors.white
_TBL_TEXT   = colors.white


def _base_style(has_header: bool = True) -> list:
    style = [
        ("FONTSIZE",       (0, 0), (-1, -1), 8),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, int(has_header)), (-1, -1), [_TBL_ROW1, _TBL_ROW2]),
        ("LEFTPADDING",    (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 5),
        ("TOPPADDING",     (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
    ]
    if has_header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), _TBL_HEADER),
            ("TEXTCOLOR",  (0, 0), (-1, 0), _TBL_TEXT),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    return style


_UTC = datetime.timezone.utc


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, _UTC).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _p(text: str, style) -> Paragraph:
    return Paragraph(str(text), style)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _section_cover(
    styles, args, conn_series, drops, signal_data,
    start_dt: datetime.datetime, end_dt: datetime.datetime,
) -> list:
    body = styles["ReportBody"]
    note = styles["Note"]
    items: list = []

    items.append(Spacer(1, 1.5 * cm))
    items.append(Paragraph("CellSentry", styles["ReportTitle"]))
    items.append(Paragraph("Signal Quality Measurement Report", styles["ReportSubtitle"]))
    items.append(Spacer(1, 0.5 * cm))
    items.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0d1b2a")))
    items.append(Spacer(1, 0.5 * cm))

    meta = [
        ["Measurement period",
         f"{start_dt.strftime('%Y-%m-%d')} – {end_dt.strftime('%Y-%m-%d')}"],
        ["Report generated",
         datetime.datetime.now(_UTC).strftime("%Y-%m-%d %H:%M UTC")],
        ["Modem",            "ZTE MC801A"],
        ["Operator",         args.operator or "—"],
        ["Service address",  args.address or "—"],
        ["Contract / ref",   args.contract_ref or "—"],
    ]
    col2 = CHART_W - 5 * cm
    items.append(Table(
        [[_p(k, body), _p(v, body)] for k, v in meta],
        colWidths=[5 * cm, col2],
        style=_base_style(has_header=False),
    ))
    items.append(Spacer(1, 0.5 * cm))

    # Summary verdict
    period_days = (end_dt - start_dt).days + 1
    n_drops = len(drops)
    uptime  = _uptime_pct(conn_series)

    lte_rsrp_vals = _values(signal_data.get("lte_rsrp", []))
    lte_st = _stats(lte_rsrp_vals)
    nr_rsrp_vals  = _values(signal_data.get("nr_rsrp", []))
    nr_st  = _stats(nr_rsrp_vals)

    parts = []
    if not conn_series:
        parts.append("No connection data available for the selected period.")
    elif n_drops == 0:
        parts.append(
            f"No connection drops recorded over {period_days} day(s) "
            f"({uptime:.1f}% uptime)."
        )
    else:
        parts.append(
            f"<b>{n_drops} connection drop(s)</b> recorded over {period_days} day(s) "
            f"({uptime:.1f}% uptime)."
        )
    if lte_st["count"]:
        poor_pct = 100.0 * sum(1 for v in lte_rsrp_vals if v < -100) / lte_st["count"]
        parts.append(
            f"LTE RSRP below −100 dBm (Poor threshold) for "
            f"<b>{poor_pct:.1f}%</b> of the period "
            f"(median {lte_st['median']:.1f} dBm)."
        )
    if nr_st["count"]:
        parts.append(
            f"5G NR RSRP median: {nr_st['median']:.1f} dBm "
            f"(quality: {_quality('nr_rsrp', nr_st['median'])})."
        )

    items.append(Paragraph(" ".join(parts), styles["Verdict"]))
    items.append(Spacer(1, 0.3 * cm))
    items.append(Paragraph(
        "This report was produced by CellSentry, a passive monitoring tool that reads "
        "the modem's internal diagnostic API at 15-second intervals. No active tests "
        "(speed tests, pings) were performed; all measurements are native modem values "
        "stored in Prometheus.",
        note,
    ))
    return items


def _section_connection(styles, conn_series, drops) -> list:
    body = styles["ReportBody"]
    h1   = styles["ReportH1"]
    h2   = styles["ReportH2"]
    items: list = []

    items.append(PageBreak())
    items.append(Paragraph("1. Connection Reliability", h1))
    items.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    items.append(Spacer(1, 0.2 * cm))

    uptime  = _uptime_pct(conn_series)
    n_drops = len(drops)
    total_s = (conn_series[-1][0] - conn_series[0][0]) if len(conn_series) >= 2 else 0

    if n_drops:
        durs = [d for _, _, d in drops]
        mtbf = _fmt_dur(total_s / n_drops)
        mttr = _fmt_dur(float(np.mean(durs)))
        longest = _fmt_dur(max(durs))
    else:
        mtbf = mttr = longest = "N/A (no drops)"

    summary = [
        ["Metric", "Value"],
        ["Uptime",                          f"{uptime:.2f}%" if conn_series else "—"],
        ["Total connection drops",          str(n_drops)],
        ["Mean time between failures (MTBF)", mtbf],
        ["Mean time to restore (MTTR)",     mttr],
        ["Longest outage",                  longest],
    ]
    items.append(Table(
        [[_p(r[0], body), _p(r[1], body)] for r in summary],
        colWidths=[9 * cm, CHART_W - 9 * cm],
        style=_base_style(has_header=True),
    ))
    items.append(Spacer(1, 0.4 * cm))

    chart = _chart_connection(conn_series)
    if chart:
        items.append(chart)
    items.append(Spacer(1, 0.4 * cm))

    items.append(Paragraph("Drop Events", h2))
    if n_drops == 0:
        items.append(Paragraph(
            "No connection drops were recorded during the measurement period.", body))
    else:
        col = CHART_W / 4
        drop_rows = [["#", "Start (UTC)", "End (UTC)", "Duration"]]
        for i, (s, e, d) in enumerate(drops, 1):
            drop_rows.append([
                str(i), _fmt_ts(s),
                _fmt_ts(e) if e is not None else "ongoing",
                _fmt_dur(d),
            ])
        items.append(Table(
            [[_p(c, body) for c in row] for row in drop_rows],
            colWidths=[1.2 * cm, col + 0.6 * cm, col + 0.6 * cm, col - 0.4 * cm],
            style=_base_style(has_header=True),
        ))
    return items


def _section_signal(styles, signal_data: dict) -> list:
    body = styles["ReportBody"]
    h1   = styles["ReportH1"]
    h2   = styles["ReportH2"]
    note = styles["Note"]
    items: list = []

    items.append(PageBreak())
    items.append(Paragraph("2. Signal Quality Statistics", h1))
    items.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    items.append(Spacer(1, 0.2 * cm))

    stat_rows = [["Metric", "Unit", "Min", "Mean", "Median", "P5", "P95", "Quality\n(median)"]]
    stat_style = _base_style(has_header=True)
    for row_i, (key, (_, label, unit)) in enumerate(list(_METRICS.items()), 1):
        st = _stats(_values(signal_data.get(key, [])))
        if st["count"] is None or st["count"] == 0:
            row = [label, unit, "—", "—", "—", "—", "—", "No data"]
            q   = "No data"
        else:
            q   = _quality(key, st["median"])
            row = [
                label, unit,
                f"{st['min']:.1f}",    f"{st['mean']:.1f}",
                f"{st['median']:.1f}", f"{st['p5']:.1f}",
                f"{st['p95']:.1f}",    q,
            ]
        stat_rows.append(row)
        stat_style.append(
            ("BACKGROUND", (-1, row_i), (-1, row_i), _QUALITY_COLORS.get(q, colors.white))
        )

    items.append(Table(
        [[_p(c, body) for c in row] for row in stat_rows],
        colWidths=[3.0*cm, 1.0*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.6*cm, 2.4*cm],
        style=stat_style,
    ))
    items.append(Spacer(1, 0.15 * cm))
    items.append(Paragraph(
        "Quality assessment uses 3GPP TS 36.133 / TS 38.133 engineering conventions "
        "(not regulatory requirements). P5/P95 = 5th and 95th percentiles.",
        note,
    ))
    items.append(Spacer(1, 0.4 * cm))

    # 3GPP reference table
    items.append(Paragraph("3GPP Quality Reference Thresholds", h2))
    thresh_style = _base_style(has_header=True)
    thresh_style += [
        ("BACKGROUND", (1, 1), (1, -1), _QUALITY_COLORS["Excellent"]),
        ("BACKGROUND", (2, 1), (2, -1), _QUALITY_COLORS["Good"]),
        ("BACKGROUND", (3, 1), (3, -1), _QUALITY_COLORS["Fair"]),
        ("BACKGROUND", (4, 1), (4, -1), _QUALITY_COLORS["Poor"]),
    ]
    thresh_rows = [
        ["Metric",       "Excellent",  "Good",        "Fair",         "Poor"],
        ["LTE RSRP",     "> −80 dBm",  "−80 to −90",  "−90 to −100",  "< −100 dBm"],
        ["LTE RSRQ",     "> −10 dB",   "—",           "−10 to −15",   "< −15 dB"],
        ["LTE SNR",      "> 20 dB",    "13 – 20",     "0 – 13",       "< 0 dB"],
        ["LTE RSSI",     "> −65 dBm",  "−65 to −75",  "−75 to −85",   "< −85 dBm"],
        ["5G NR RSRP",   "> −80 dBm",  "−80 to −90",  "−90 to −100",  "< −100 dBm"],
        ["5G NR SINR",   "> 20 dB",    "13 – 20",     "0 – 13",       "< 0 dB"],
    ]
    cw = CHART_W / 5
    items.append(Table(
        [[_p(c, body) for c in row] for row in thresh_rows],
        colWidths=[cw * 1.2] + [cw * 0.95] * 4,
        style=thresh_style,
    ))
    items.append(Spacer(1, 0.4 * cm))

    items.append(Paragraph("Signal Time Series", h2))
    lte_data = {k: signal_data.get(k, []) for k in ["lte_rsrp", "lte_rsrq", "lte_snr"]}
    nr_data  = {k: signal_data.get(k, []) for k in ["nr_rsrp", "nr_sinr"]}
    items.append(_chart_signal(lte_data, nr_data))
    return items


def _section_heatmap(styles, signal_data: dict) -> list:
    body = styles["ReportBody"]
    h1   = styles["ReportH1"]
    h2   = styles["ReportH2"]
    note = styles["Note"]
    items: list = []

    items.append(PageBreak())
    items.append(Paragraph("3. Time-of-Day Analysis", h1))
    items.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    items.append(Spacer(1, 0.2 * cm))
    items.append(Paragraph(
        "Each cell shows the mean signal value for that day-of-week × hour-of-day (UTC). "
        "Consistent degradation at specific hours may indicate peak-hour congestion "
        "rather than a coverage or equipment fault. Grey cells = no data collected.",
        body,
    ))
    items.append(Spacer(1, 0.3 * cm))

    for key, title, unit in [
        ("lte_rsrp", "LTE RSRP — Time-of-Day Mean (dBm)",   "dBm"),
        ("nr_rsrp",  "5G NR RSRP — Time-of-Day Mean (dBm)", "dBm"),
        ("nr_sinr",  "5G NR SINR — Time-of-Day Mean (dB)",  "dB"),
    ]:
        series = signal_data.get(key, [])
        if not series:
            items.append(Paragraph(f"{title}: no data available.", note))
            continue
        items.append(Paragraph(title, h2))
        items.append(_chart_heatmap(_heatmap_array(series), title, unit))
        items.append(Spacer(1, 0.3 * cm))

    items.append(Paragraph(
        "Note: all timestamps are UTC. Finnish local time is UTC+2 (winter) or UTC+3 "
        "(summer/EEST). Adjust when interpreting time-of-day patterns in relation to "
        "peak usage hours.",
        note,
    ))
    return items


def _section_identity(styles, modem_labels: dict) -> list:
    body = styles["ReportBody"]
    h1   = styles["ReportH1"]
    note = styles["Note"]
    items: list = []

    items.append(PageBreak())
    items.append(Paragraph("4. Band and Cell Identity", h1))
    items.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    items.append(Spacer(1, 0.2 * cm))
    items.append(Paragraph(
        "The values below confirm which specific tower sector served this address during "
        "the measurement period. The operator requires these identifiers to investigate "
        "on their infrastructure side.",
        body,
    ))
    items.append(Spacer(1, 0.3 * cm))

    rows = [
        ["Field",               "Value",                          "Notes"],
        ["Network type",        modem_labels.get("network_type", "—"), "ENDC = 5G NSA mode"],
        ["LTE band",            modem_labels.get("band_lte", "—"),     ""],
        ["LTE PCI (hex)",       modem_labels.get("pci_lte", "—"),      "Physical Cell ID"],
        ["LTE Cell ID (hex)",   modem_labels.get("cell_id", "—"),      "28-bit ECI"],
        ["5G NR band",          modem_labels.get("band_5g", "—"),      ""],
        ["5G NR PCI (hex)",     modem_labels.get("pci_5g", "—"),       "Physical Cell ID"],
    ]
    items.append(Table(
        [[_p(c, body) for c in row] for row in rows],
        colWidths=[5 * cm, 5 * cm, CHART_W - 10 * cm],
        style=_base_style(has_header=True),
    ))
    items.append(Spacer(1, 0.3 * cm))
    items.append(Paragraph(
        "If these values changed frequently during the period, it indicates handovers "
        "or cell re-attachment events that may correlate with drop events.",
        note,
    ))
    return items


def _section_methodology(styles, args) -> list:
    body = styles["ReportBody"]
    h1   = styles["ReportH1"]
    h2   = styles["ReportH2"]
    items: list = []

    items.append(PageBreak())
    items.append(Paragraph("5. Methodology", h1))
    items.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    items.append(Spacer(1, 0.2 * cm))

    items.append(Paragraph("Data Collection Method", h2))
    items.append(Paragraph(
        "CellSentry is a passive monitoring tool. It reads the modem's internal "
        "diagnostic HTTP+JSON API at 15-second intervals and stores all values in "
        "Prometheus (a time-series database). No firmware modifications were made. "
        "No active tests (speed tests, pings, traffic generation) were performed; "
        "all measurements are native modem diagnostic values reported by the device.",
        body,
    ))

    items.append(Paragraph("Measurement Infrastructure", h2))
    infra = [
        ["Component",       "Detail"],
        ["Modem",           "ZTE MC801A at 192.168.100.1"],
        ["Exporter",        "Python 3.12 / prometheus_client 0.21.1"],
        ["Prometheus",      "v2.51.2 — 90-day retention"],
        ["Scrape interval", "15 seconds"],
        ["Data source",     args.prometheus],
        ["Repository",      "https://github.com/BluePerfectOne/cellsentry"],
    ]
    items.append(Table(
        [[_p(k, body), _p(v, body)] for k, v in infra],
        colWidths=[5 * cm, CHART_W - 5 * cm],
        style=_base_style(has_header=True),
    ))
    items.append(Spacer(1, 0.3 * cm))

    items.append(Paragraph("Signal Measurement Field Names", h2))
    fields = [
        ["Prometheus Metric",          "Signal",       "Modem API field"],
        ["cellsentry_lte_rsrp_dbm",    "LTE RSRP",     "lte_rsrp"],
        ["cellsentry_lte_rsrq_db",     "LTE RSRQ",     "lte_rsrq"],
        ["cellsentry_lte_snr_db",      "LTE SNR",      "lte_snr"],
        ["cellsentry_lte_rssi_dbm",    "LTE RSSI",     "lte_rssi"],
        ["cellsentry_5gnr_rsrp_dbm",   "5G NR RSRP",   "Z5g_rsrp"],
        ["cellsentry_5gnr_sinr_db",    "5G NR SINR",   "Z5g_SINR"],
        ["cellsentry_connection_up",   "Connection",   "ppp_status (1=connected, 0=not)"],
    ]
    items.append(Table(
        [[_p(c, body) for c in row] for row in fields],
        colWidths=[6 * cm, 3 * cm, CHART_W - 9 * cm],
        style=_base_style(has_header=True),
    ))
    return items


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a PDF signal-quality report from Prometheus data."
    )
    p.add_argument("--from",         required=True,  dest="date_from",
                   help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--to",           required=True,  dest="date_to",
                   help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--operator",     default="",
                   help="Operator name for the cover page")
    p.add_argument("--address",      default="",
                   help="Service address for the cover page")
    p.add_argument("--contract-ref", default="",     dest="contract_ref",
                   help="Contract or customer reference number")
    p.add_argument("--prometheus",   default="http://localhost:9090",
                   help="Prometheus base URL (default: http://localhost:9090)")
    p.add_argument("--output",       default=None,
                   help="Output PDF path (default: cellsentry-report-<YYYYMMDDTHHMMSS>.pdf)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    try:
        start_dt = datetime.datetime.strptime(args.date_from, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.strptime(args.date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )
    except ValueError as exc:
        print(f"ERROR: Invalid date — {exc}", file=sys.stderr)
        sys.exit(1)

    if end_dt < start_dt:
        print("ERROR: --to must be on or after --from", file=sys.stderr)
        sys.exit(1)

    if args.output is None:
        stamp = datetime.datetime.now(_UTC).strftime("%Y%m%dT%H%M%S")
        args.output = f"cellsentry-report-{stamp}.pdf"

    start_ts = int(start_dt.timestamp())
    end_ts   = int(end_dt.timestamp())
    prom     = args.prometheus.rstrip("/")

    print(f"CellSentry Report Generator")
    print(f"  Period    : {start_dt.date()} – {end_dt.date()}")
    print(f"  Prometheus: {prom}")
    print(f"  Output    : {args.output}")
    print()

    # --- Fetch signal data ---
    signal_data: dict[str, list] = {}
    for key, (metric, label, _unit) in _METRICS.items():
        print(f"  Fetching {label} ...", end="", flush=True)
        series = _query_range(prom, metric, start_ts, end_ts, step=60)
        signal_data[key] = series
        print(f" {len(series)} points")

    # --- Fetch connection state at higher resolution ---
    print("  Fetching connection state ...", end="", flush=True)
    conn_series = _query_range(prom, "cellsentry_connection_up", start_ts, end_ts, step=15)
    print(f" {len(conn_series)} points")

    # --- Fetch modem identity labels ---
    mid_ts = (start_ts + end_ts) // 2
    modem_labels = _query_instant_labels(prom, "cellsentry_modem_info", mid_ts)
    print(f"  Modem labels: {modem_labels}")

    # --- Analyse ---
    drops = _find_drops(conn_series)
    print(f"  Detected {len(drops)} drop event(s)")
    print()

    # --- Build PDF ---
    print("Building PDF ...")
    styles = _make_styles()
    doc = SimpleDocTemplate(
        args.output,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="CellSentry Signal Quality Report",
        author="CellSentry",
        subject=f"Signal quality {start_dt.date()} – {end_dt.date()}",
    )

    story: list = []
    story += _section_cover(styles, args, conn_series, drops, signal_data, start_dt, end_dt)
    story += _section_connection(styles, conn_series, drops)
    story += _section_signal(styles, signal_data)
    story += _section_heatmap(styles, signal_data)
    story += _section_identity(styles, modem_labels)
    story += _section_methodology(styles, args)

    doc.build(story)
    print(f"Done: {args.output}")


if __name__ == "__main__":
    main()
