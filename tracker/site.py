"""Build the static dashboard: copy site/ to public/ and write public/data/dashboard.json."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

from .core import Model, compare, format_alert
from .names import display_name, family, vendor_name
from .runner import DEFAULT_CONFIG, DEFAULT_STATE, ROOT, load_config, utc_now
from .state import State

SITE_DIR = ROOT / "site"


def _num(value) -> float | None:
    return None if value is None else float(value)


def build(out_dir: Path, config_path=DEFAULT_CONFIG, state_dir=DEFAULT_STATE) -> Path:
    config = load_config(config_path)
    state = State(state_dir)
    prefix = config.get("prefix", "")
    watchlist = config.get("watchlist") or []
    dates = state.snapshot_dates()
    meta = state.read_meta()

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(SITE_DIR, out_dir)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Price history per model, compressed to change points.
    history: dict[str, list] = {}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    latest: dict[str, Model] = {}
    for d in dates:
        snap = state.read_snapshot(d)
        for mid, m in snap.items():
            first_seen.setdefault(mid, d)
            last_seen[mid] = d
            point = [d, _num(m.input_per_1m), _num(m.output_per_1m)]
            h = history.setdefault(mid, [])
            if not h or h[-1][1:] != point[1:]:
                h.append(point)
        latest = snap

    events = state.read_events()
    known = dict(latest)
    # Removed models still need names and prices for the event feed.
    for d in dates:
        for mid, m in state.read_snapshot(d).items():
            known.setdefault(mid, m)

    def model_meta(mid: str) -> dict:
        m = known.get(mid)
        vendor = m.vendor if m else mid.removeprefix(prefix).split(".", 1)[0]
        return {
            "id": mid,
            "name": display_name(mid, prefix),
            "vendor": vendor,
            "family": family(mid, vendor),
        }

    models = []
    for mid, m in sorted(latest.items()):
        cmp = compare(m, config, latest)
        models.append(
            {
                **model_meta(mid),
                "input": _num(m.input_per_1m),
                "output": _num(m.output_per_1m),
                "deprecation_date": m.deprecation_date,
                "verification": m.verification,
                "litellm_input": _num(m.litellm_input_per_1m),
                "litellm_output": _num(m.litellm_output_per_1m),
                "watchlist": mid in watchlist,
                "baseline": cmp["baseline_model_id"],
                "input_pct": _num(cmp["input_pct_vs_baseline"]),
                "output_pct": _num(cmp["output_pct_vs_baseline"]),
                "first_seen": first_seen.get(mid),
                "history": history.get(mid, []),
            }
        )

    event_rows = []
    for e in events:
        alert_event = {**e, "_deprecation_date": known[e["model_id"]].deprecation_date if e["model_id"] in known else None}
        event_rows.append(
            {
                **model_meta(e["model_id"]),
                "date": e["event_date"],
                "type": e["event_type"],
                "baseline": e["baseline_model_id"],
                "baseline_name": display_name(e["baseline_model_id"], prefix) if e["baseline_model_id"] else None,
                "input": _num(e["input_per_1m_usd"]),
                "output": _num(e["output_per_1m_usd"]),
                "input_pct": _num(e["input_pct_vs_baseline"]),
                "output_pct": _num(e["output_pct_vs_baseline"]),
                "prev_input": _num(e["previous_input_per_1m_usd"]),
                "prev_output": _num(e["previous_output_per_1m_usd"]),
                "watchlist": e["model_id"] in watchlist,
                "price_source": e.get("price_source") or "litellm",
                "alert": format_alert(alert_event, known),
            }
        )
    event_rows.sort(key=lambda r: (r["date"], r["type"] != "new_model", r["id"]), reverse=True)

    vendors = sorted({m["vendor"] for m in models})
    count_series = []
    for d in dates:
        count_series.append([d, sum(1 for mid in last_seen if first_seen[mid] <= d <= last_seen[mid])])

    data = {
        "generated_at": utc_now().isoformat(),
        "config": {
            "prefix": prefix,
            "watchlist": watchlist,
            "baselines": config.get("baselines") or {},
            "source_url": config.get("source_url"),
            "heartbeat_max_age_hours": config.get("heartbeat_max_age_hours", 48),
        },
        "meta": {k: v for k, v in meta.items() if k != "runs"},
        "runs": meta.get("runs", [])[-60:],
        "snapshot_dates": [dates[0], dates[-1]] if dates else [],
        "snapshot_count": len(dates),
        "model_count_series": count_series,
        "vendors": [{"id": v, "name": vendor_name(v), "count": sum(1 for m in models if m["vendor"] == v)} for v in vendors],
        "models": models,
        "events": event_rows,
    }
    (data_dir / "dashboard.json").write_text(json.dumps(data, separators=(",", ":"), default=_json_default))

    # Raw state for download.
    if state.events_path.exists():
        shutil.copy(state.events_path, data_dir / "events.csv")
    if dates:
        shutil.copy(state.snapshots_dir / f"{dates[-1]}.csv", data_dir / "snapshot-latest.csv")
    print(f"built {out_dir} ({len(models)} models, {len(event_rows)} events, {len(dates)} snapshots)")
    return out_dir


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value))
