"""Snapshot, event log and run metadata on disk.

state/
  snapshots/YYYY-MM-DD.csv   one row per model per day
  events.csv                 append-only event log
  meta.json                  run bookkeeping (last success, entry count, run history)
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from .core import EVENT_FIELDS, Model, dec_str

SNAPSHOT_FIELDS = [
    "snapshot_date",
    "model_id",
    "vendor",
    "input_per_1m_usd",
    "output_per_1m_usd",
    "deprecation_date",
    "price_source",
    "litellm_input_per_1m_usd",
    "litellm_output_per_1m_usd",
]

DECIMAL_EVENT_FIELDS = {
    "input_per_1m_usd",
    "output_per_1m_usd",
    "input_pct_vs_baseline",
    "output_pct_vs_baseline",
    "previous_input_per_1m_usd",
    "previous_output_per_1m_usd",
}

RUN_HISTORY_LIMIT = 120


class State:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.snapshots_dir = self.root / "snapshots"
        self.events_path = self.root / "events.csv"
        self.meta_path = self.root / "meta.json"

    # ------------------------------------------------------------ snapshots

    def snapshot_dates(self) -> list[str]:
        if not self.snapshots_dir.exists():
            return []
        return sorted(p.stem for p in self.snapshots_dir.glob("*.csv"))

    def last_snapshot_before(self, date: str) -> tuple[str | None, dict[str, Model]]:
        """The last good snapshot strictly before `date`, so a same-day re-run diffs
        against yesterday and de-duplication absorbs the repeat."""
        earlier = [d for d in self.snapshot_dates() if d < date]
        if not earlier:
            return None, {}
        return earlier[-1], self.read_snapshot(earlier[-1])

    def read_snapshot(self, date: str) -> dict[str, Model]:
        models = {}
        with open(self.snapshots_dir / f"{date}.csv", newline="") as fh:
            for row in csv.DictReader(fh):
                inp = Decimal(row["input_per_1m_usd"])
                out = Decimal(row["output_per_1m_usd"])
                # Snapshots written before the AWS check have no source columns:
                # their prices are LiteLLM's.
                ll_in = row.get("litellm_input_per_1m_usd")
                ll_out = row.get("litellm_output_per_1m_usd")
                models[row["model_id"]] = Model(
                    model_id=row["model_id"],
                    vendor=row["vendor"],
                    input_per_1m=inp,
                    output_per_1m=out,
                    deprecation_date=row["deprecation_date"] or None,
                    price_source=row.get("price_source") or "litellm",
                    litellm_input_per_1m=Decimal(ll_in) if ll_in else inp,
                    litellm_output_per_1m=Decimal(ll_out) if ll_out else out,
                )
        return models

    def write_snapshot(self, date: str, models: dict[str, Model]) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "snapshot_date": date,
                "model_id": m.model_id,
                "vendor": m.vendor,
                "input_per_1m_usd": dec_str(m.input_per_1m),
                "output_per_1m_usd": dec_str(m.output_per_1m),
                "deprecation_date": m.deprecation_date or "",
                "price_source": m.price_source,
                "litellm_input_per_1m_usd": dec_str(m.litellm_input_per_1m),
                "litellm_output_per_1m_usd": dec_str(m.litellm_output_per_1m),
            }
            for m in sorted(models.values(), key=lambda m: m.model_id)
        ]
        _atomic_csv(self.snapshots_dir / f"{date}.csv", SNAPSHOT_FIELDS, rows)

    # ------------------------------------------------------------ event log

    def read_events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        with open(self.events_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            for field in DECIMAL_EVENT_FIELDS:
                row[field] = Decimal(row[field]) if row.get(field) else None
            row["baseline_model_id"] = row.get("baseline_model_id") or None
            row["price_source"] = row.get("price_source") or "litellm"
        return rows

    def append_events(self, events: list[dict]) -> None:
        if not events:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_event_header()
        new_file = not self.events_path.exists()
        with open(self.events_path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=EVENT_FIELDS, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            for e in events:
                writer.writerow(
                    {k: dec_str(v) if isinstance(v, Decimal) else (v or "") for k, v in e.items()}
                )

    def _migrate_event_header(self) -> None:
        """Rewrite the log under the current header when columns were added since it
        was created, so appended rows line up. Old rows get the column's default."""
        if not self.events_path.exists():
            return
        with open(self.events_path, newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames == EVENT_FIELDS:
                return
            rows = list(reader)
        for row in rows:
            row.setdefault("price_source", "litellm")
            row["price_source"] = row["price_source"] or "litellm"
        _atomic_csv(self.events_path, EVENT_FIELDS, [{k: row.get(k, "") for k in EVENT_FIELDS} for row in rows])

    # ------------------------------------------------------------ meta

    def read_meta(self) -> dict:
        if not self.meta_path.exists():
            return {}
        return json.loads(self.meta_path.read_text())

    def write_meta(self, meta: dict) -> None:
        meta["runs"] = meta.get("runs", [])[-RUN_HISTORY_LIMIT:]
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.meta_path, json.dumps(meta, indent=2, sort_keys=True) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", newline="") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _atomic_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
