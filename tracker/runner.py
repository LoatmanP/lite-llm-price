"""The daily job, the heartbeat check and the history backfill."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import aws
from .alerts import Alerter
from .core import (
    Model,
    count_bedrock_entries,
    dec_str,
    diff,
    drop_duplicates,
    filter_and_normalize,
    fmt_usd,
    format_alert,
    should_alert,
)
from .state import State

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "tracker.json"
DEFAULT_STATE = ROOT / "state"

LITELLM_REPO = "BerriAI/litellm"
LITELLM_PATH = "model_prices_and_context_window.json"


class RunFailed(Exception):
    pass


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    return json.loads(Path(path).read_text())


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ------------------------------------------------------------------ fetch


def http_get(url: str, timeout: int = 60, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bedrock-price-tracker", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_with_retries(url: str, retries: int = 3, backoff: float = 5.0) -> str:
    """Step 1. One attempt plus `retries` retries, then raise RunFailed."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            return http_get(url).decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            print(f"fetch attempt {attempt + 1} failed: {exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
    raise RunFailed(f"could not fetch the LiteLLM price file after {retries} retries ({last_error})")


# ------------------------------------------------------------------ one run


def process(
    text: str,
    run_date: str,
    state: State,
    config: dict,
    alerter: Alerter,
    source: str = "scheduled",
    aws_check: dict | None = None,
) -> dict:
    """Steps 2 to 7 on an already-fetched file. Raises RunFailed without touching
    the snapshot or event log when validation fails.

    aws_check is the result of load_aws_check(), or None when the AWS check is off.
    """
    meta = state.read_meta()
    prefix = config.get("prefix", "")
    watchlist = config.get("watchlist") or []

    # Step 2: validate.
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunFailed(f"price file is not valid JSON ({exc})")
    if not isinstance(raw, dict):
        raise RunFailed("price file is not a JSON object")
    entry_count = count_bedrock_entries(raw)
    previous_count = meta.get("last_bedrock_entry_count")
    min_ratio = config.get("validation_min_ratio", 0.8)
    if previous_count and entry_count < min_ratio * previous_count:
        raise RunFailed(
            f"price file has {entry_count} Bedrock entries, under {min_ratio:.0%} of "
            f"the last good run's {previous_count}"
        )

    # Steps 3 and 4: filter and normalize.
    current, skipped = filter_and_normalize(raw, prefix)
    if not current:
        raise RunFailed(f"no Bedrock chat models found on prefix {prefix!r}")

    prev_date, previous = state.last_snapshot_before(run_date)
    first_run = prev_date is None

    # AWS check: AWS's price wins wherever AWS lists the model.
    aws_status = (aws_check or {}).get("status", "off")
    if aws_status == "ok":
        current = aws.apply(current, aws_check["names"], aws_check["prices"], prefix)
    elif aws_status == "unavailable":
        current = carry_forward_aws(current, previous)

    # Step 5 and 6: diff and compare.
    events = [] if first_run else diff(previous, current, config, run_date)
    events = drop_duplicates(events, state.read_events())
    known = {**previous, **current}

    # Step 7: alert, then save.
    for event in events:
        if should_alert(event, config):
            alerter.send(format_alert(event, known))

    missing = sorted(set(watchlist) - current.keys())
    was_missing = set(meta.get("missing_watchlist", []))
    removed_now = {e["model_id"] for e in events if e["event_type"] == "removed"}
    if not first_run:
        for mid in missing:
            if mid not in was_missing and mid not in removed_now:
                alerter.send(f"Watchlist model missing from the LiteLLM price file: {mid}")
        for mid in sorted(was_missing - set(missing)):
            alerter.send(f"Watchlist model is back in the LiteLLM price file: {mid}")

    mismatches = meta.get("aws_mismatches", {})
    if aws_status == "ok":
        mismatches = {
            mid: [dec_str(m.input_per_1m), dec_str(m.output_per_1m), dec_str(m.litellm_input_per_1m), dec_str(m.litellm_output_per_1m)]
            for mid, m in current.items()
            if m.verification == "mismatch"
        }
        if not first_run:
            was = meta.get("aws_mismatches", {})
            for mid in watchlist:
                if mid in mismatches and was.get(mid) != mismatches[mid]:
                    alerter.send(mismatch_alert(current[mid]))
    elif aws_status == "off":
        mismatches = {}

    pending = list_foundation_models_step(raw, config, meta, alerter, quiet=first_run)

    state.write_snapshot(run_date, current)
    state.append_events(events)

    counts = {t: sum(1 for e in events if e["event_type"] == t) for t in ("new_model", "price_change", "removed")}
    now = utc_now().isoformat()
    meta.update(
        {
            "last_run_at": now,
            "last_status": "ok",
            "last_success_at": now,
            "last_success_date": run_date,
            "last_bedrock_entry_count": entry_count,
            # The first run is quiet, so leave the list empty and let the next run alert.
            "missing_watchlist": [] if first_run else missing,
            "price_pending": pending,
            # As with missing watchlist models, the first run records nothing so the next run alerts.
            "aws_mismatches": {} if first_run else mismatches,
            "aws_check": aws_summary(aws_check, current),
            "last_summary": {
                "date": run_date,
                "models": len(current),
                "skipped_no_price": skipped,
                "first_run": first_run,
                **counts,
            },
        }
    )
    meta.setdefault("first_run_date", run_date)
    meta.setdefault("runs", []).append(
        {"date": run_date, "at": now, "status": "ok", "source": source, "models": len(current), "events": len(events)}
    )
    state.write_meta(meta)

    print(
        f"{run_date}: {len(current)} models on prefix {prefix!r}, "
        f"{counts['new_model']} new, {counts['price_change']} price changes, {counts['removed']} removed, "
        f"{skipped} skipped for a missing token price"
        + (" (first run: snapshot saved, no alerts)" if first_run else "")
    )
    if aws_status != "off":
        a = meta["aws_check"]
        print(
            f"AWS price check ({aws_status}): {a['verified']} verified, {a['mismatch']} differ from LiteLLM, "
            f"{a['litellm_only']} LiteLLM only" + (f"; {a['error']}" if a.get("error") else "")
        )
    return meta["last_summary"]


def carry_forward_aws(current: dict[str, Model], previous: dict[str, Model]) -> dict[str, Model]:
    """When AWS can't be reached, keep yesterday's AWS price for models that had one.

    Falling back to LiteLLM instead would report a price change on every model where
    the two disagree, and another one when AWS comes back.
    """
    out = {}
    for mid, m in current.items():
        prev = previous.get(mid)
        if prev is not None and prev.price_source == "aws":
            out[mid] = Model(
                model_id=m.model_id,
                vendor=m.vendor,
                input_per_1m=prev.input_per_1m,
                output_per_1m=prev.output_per_1m,
                deprecation_date=m.deprecation_date,
                price_source="aws",
                litellm_input_per_1m=m.litellm_input_per_1m,
                litellm_output_per_1m=m.litellm_output_per_1m,
            )
        else:
            out[mid] = m
    return out


def mismatch_alert(m: Model) -> str:
    return "\n".join(
        [
            f"AWS and LiteLLM disagree on a watchlist model: {m.model_id}",
            "",
            "          AWS      LiteLLM",
            f"Input     {fmt_usd(m.input_per_1m):<9}{fmt_usd(m.litellm_input_per_1m)}",
            f"Output    {fmt_usd(m.output_per_1m):<9}{fmt_usd(m.litellm_output_per_1m)}",
            "(USD per 1M tokens, on-demand list price)",
            "",
            "The tracker reports the AWS price. Confirm at aws.amazon.com/bedrock/pricing",
        ]
    )


def aws_summary(aws_check: dict | None, current: dict[str, Model]) -> dict:
    counts = {"verified": 0, "mismatch": 0, "litellm_only": 0}
    for m in current.values():
        counts[m.verification] += 1
    if not aws_check:
        return {"status": "off", **counts}
    return {
        "status": aws_check["status"],
        "regions": aws_check.get("regions"),
        "error": aws_check.get("error"),
        "published": aws_check.get("published", {}),
        "conflicts": len(aws_check.get("conflicts", [])),
        "checked_at": utc_now().isoformat(),
        **counts,
    }


def load_aws_check(config: dict) -> dict | None:
    """Fetch and parse the AWS Price List when aws_price_check.enabled. Never raises:
    an outage comes back as status "unavailable"."""
    opts = config.get("aws_price_check") or {}
    if not opts.get("enabled"):
        return None
    regions = opts.get("regions", ["us-east-1"])
    names = aws.load_names(ROOT / opts.get("names_file", "config/aws_names.json"))
    try:
        offer_sets = aws.fetch_offers(regions, lambda url, timeout: fetch_with_retries(url, retries=2, backoff=5.0))
    except aws.AwsUnavailable as exc:
        aws.warn(f"unavailable, keeping the last AWS prices ({exc})")
        return {"status": "unavailable", "regions": regions, "names": names, "error": str(exc)}
    prices, conflicts = aws.parse_prices(offer_sets)
    # Only conflicts on models we track matter; legacy models conflict routinely.
    tracked = set(names.values())
    conflicts = [c for c in conflicts if any(c.startswith(f"{key} ") for key in tracked)]
    for c in conflicts:
        aws.warn(f"conflicting prices, model not verified: {c}")
    unknown = sorted(v for v in names.values() if v not in prices)
    for key in unknown:
        aws.warn(f"mapped name not in the AWS Price List for {', '.join(regions)}: {key}")
    return {
        "status": "ok",
        "regions": regions,
        "names": names,
        "prices": prices,
        "conflicts": conflicts,
        "published": aws.publication_dates(offer_sets, regions),
    }


def record_failure(state: State, run_date: str, reason: str, alerter: Alerter, source: str = "scheduled") -> None:
    meta = state.read_meta()
    kept = meta.get("last_success_date", "none")
    alerter.send(f"Bedrock price tracker run failed on {run_date}: {reason}. Kept the last good snapshot ({kept}).")
    now = utc_now().isoformat()
    meta.update({"last_run_at": now, "last_status": "failed", "last_failure": {"at": now, "reason": reason}})
    meta.setdefault("runs", []).append({"date": run_date, "at": now, "status": "failed", "source": source, "reason": reason})
    state.write_meta(meta)


def run(config_path=DEFAULT_CONFIG, state_dir=DEFAULT_STATE, run_date: str | None = None, alerts: bool = True) -> int:
    config = load_config(config_path)
    state = State(state_dir)
    run_date = run_date or utc_now().date().isoformat()
    alerter = Alerter(config.get("alert_target"), enabled=alerts)
    try:
        text = fetch_with_retries(config["source_url"])
        process(text, run_date, state, config, alerter, aws_check=load_aws_check(config))
    except RunFailed as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        record_failure(state, run_date, str(exc), alerter)
        return 1
    return 2 if alerter.failures else 0


# ------------------------------------------------------------------ optional same-day detection


def list_foundation_models_step(raw: dict, config: dict, meta: dict, alerter: Alerter, quiet: bool) -> list[str]:
    """Alert on Bedrock model IDs that LiteLLM does not have yet, marked "price pending".

    Off unless config.list_foundation_models.enabled; needs boto3 and AWS credentials.
    Alerts once per ID and returns the current pending list.
    """
    opts = config.get("list_foundation_models") or {}
    if not opts.get("enabled"):
        return []
    try:
        import boto3  # optional dependency

        client = boto3.client("bedrock", region_name=opts.get("region", "us-east-1"))
        summaries = client.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
    except Exception as exc:
        print(f"ListFoundationModels step skipped: {exc}", file=sys.stderr)
        return meta.get("price_pending", [])

    keys = list(raw.keys())
    pending = []
    for summary in summaries:
        mid = summary["modelId"]
        if mid.count(":") > 1:  # context-window variants such as ...:0:200k
            continue
        if not any(k == mid or k.endswith("." + mid) or k.endswith("/" + mid) for k in keys):
            pending.append(mid)
    already = set(meta.get("price_pending", []))
    if not quiet:
        for mid in pending:
            if mid not in already:
                alerter.send(f"New on Bedrock (price pending): {mid}\nNot in the LiteLLM price file yet. Check aws.amazon.com/bedrock/pricing")
    return sorted(pending)


# ------------------------------------------------------------------ heartbeat


def heartbeat(config_path=DEFAULT_CONFIG, state_dir=DEFAULT_STATE) -> int:
    config = load_config(config_path)
    meta = State(state_dir).read_meta()
    max_hours = config.get("heartbeat_max_age_hours", 48)
    last = meta.get("last_success_at")
    alerter = Alerter(config.get("alert_target"))
    if not last:
        alerter.send("Bedrock price tracker heartbeat: no successful run recorded yet.")
        return 1
    age = utc_now() - datetime.fromisoformat(last)
    hours = age.total_seconds() / 3600
    if hours > max_hours:
        alerter.send(f"Bedrock price tracker heartbeat: no successful run for {hours:.0f} hours (last {last}).")
        return 1
    print(f"heartbeat ok: last successful run {hours:.1f} hours ago")
    return 0


# ------------------------------------------------------------------ backfill


def _github_json(url: str):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(http_get(url, headers=headers))


def _commits(since: str | None = None, until: str | None = None, per_page: int = 100, max_pages: int = 30) -> list[dict]:
    out = []
    for page in range(1, max_pages + 1):
        q = f"path={LITELLM_PATH}&per_page={per_page}&page={page}"
        if since:
            q += f"&since={since}"
        if until:
            q += f"&until={until}"
        batch = _github_json(f"https://api.github.com/repos/{LITELLM_REPO}/commits?{q}")
        out.extend(batch)
        if len(batch) < per_page or per_page == 1:
            break
    return out


def backfill(days: int, config_path=DEFAULT_CONFIG, state_dir=DEFAULT_STATE, force: bool = False) -> int:
    """Replay the LiteLLM file's git history one day at a time, alerts off.

    Gives the dashboard real history on day one. Each day uses the file as of the
    last commit on or before that day (UTC).
    """
    config = load_config(config_path)
    state = State(state_dir)
    if state.snapshot_dates() and not force:
        print("state already has snapshots; pass --force to replay over an empty state", file=sys.stderr)
        return 1
    if force:
        for p in state.snapshots_dir.glob("*.csv"):
            p.unlink()
        for p in (state.events_path, state.meta_path):
            if p.exists():
                p.unlink()

    today = utc_now().date()
    start = today - timedelta(days=days)
    start_iso = f"{start.isoformat()}T00:00:00Z"

    print(f"listing commits to {LITELLM_PATH} since {start}...")
    commits = _commits(since=start_iso)
    before = _commits(until=start_iso, per_page=1, max_pages=1)

    # Last commit per UTC day.
    by_day: dict[str, str] = {}
    for c in sorted(commits, key=lambda c: c["commit"]["committer"]["date"]):
        by_day[c["commit"]["committer"]["date"][:10]] = c["sha"]
    print(f"{len(commits)} commits on {len(by_day)} days")

    sha = before[0]["sha"] if before else None
    cached_sha, text = None, None
    alerter = Alerter(None, enabled=False)
    day = start
    while day <= today:
        iso = day.isoformat()
        sha = by_day.get(iso, sha)
        if sha is None:
            day += timedelta(days=1)
            continue
        if sha != cached_sha:
            url = f"https://raw.githubusercontent.com/{LITELLM_REPO}/{sha}/{LITELLM_PATH}"
            text = fetch_with_retries(url, retries=3, backoff=2.0)
            cached_sha = sha
        try:
            process(text, iso, state, config, alerter, source="backfill")
        except RunFailed as exc:
            print(f"{iso}: skipped ({exc})", file=sys.stderr)
            record_failure(state, iso, str(exc), alerter, source="backfill")
        day += timedelta(days=1)
    return 0


def _parse_date(value: str) -> str:
    return Date.fromisoformat(value).isoformat()
