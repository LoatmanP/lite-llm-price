"""Pure tracker logic: filter, normalize, diff, baseline selection and alert text.

Nothing in this module touches the network or the filesystem, so it is what the
tests exercise.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

BEDROCK_PROVIDERS = ("bedrock", "bedrock_converse")

# Cross-region inference-profile prefixes seen in the LiteLLM file. Used to tell
# a bare ID ("anthropic.claude-...") from a prefixed one when prefix is "".
REGION_PREFIXES = ("us.", "eu.", "apac.", "global.", "au.", "jp.", "ca.", "us-gov.", "in.")

PRICING_URL = "aws.amazon.com/bedrock/pricing"

EVENT_FIELDS = [
    "event_date",
    "event_type",
    "model_id",
    "baseline_model_id",
    "input_per_1m_usd",
    "output_per_1m_usd",
    "input_pct_vs_baseline",
    "output_pct_vs_baseline",
    "previous_input_per_1m_usd",
    "previous_output_per_1m_usd",
    "price_source",
]


@dataclass(frozen=True)
class Model:
    model_id: str
    vendor: str
    input_per_1m: Decimal
    output_per_1m: Decimal
    deprecation_date: str | None = None
    # "aws" when the price came from the AWS Price List, else "litellm".
    price_source: str = "litellm"
    # LiteLLM's price, kept for comparison when the AWS price is used.
    litellm_input_per_1m: Decimal | None = None
    litellm_output_per_1m: Decimal | None = None

    @property
    def verification(self) -> str:
        """verified, mismatch (AWS and LiteLLM disagree; AWS used) or litellm_only."""
        if self.price_source != "aws":
            return "litellm_only"
        same = (self.litellm_input_per_1m, self.litellm_output_per_1m) == (self.input_per_1m, self.output_per_1m)
        return "verified" if same else "mismatch"


def per_1m(cost_per_token) -> Decimal:
    """USD per token -> USD per 1M tokens, without float noise."""
    value = Decimal(str(cost_per_token)) * 1_000_000
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()


def dec_str(value: Decimal | None) -> str:
    """Plain (non-scientific) decimal string; empty for None."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


def has_region_prefix(key: str) -> bool:
    return key.startswith(REGION_PREFIXES)


def matches_prefix(key: str, prefix: str) -> bool:
    if prefix:
        return key.startswith(prefix)
    return not has_region_prefix(key)


def vendor_of(model_id: str, prefix: str) -> str:
    """The part of the ID before the first dot after the prefix."""
    rest = model_id[len(prefix):] if prefix and model_id.startswith(prefix) else model_id
    return rest.split(".", 1)[0]


def strip_prefix(model_id: str, prefix: str) -> str:
    return model_id[len(prefix):] if prefix and model_id.startswith(prefix) else model_id


def count_bedrock_entries(raw: dict) -> int:
    """All Bedrock entries in the file, any mode or prefix. Used for validation."""
    return sum(
        1
        for value in raw.values()
        if isinstance(value, dict) and value.get("litellm_provider") in BEDROCK_PROVIDERS
    )


def filter_and_normalize(raw: dict, prefix: str) -> tuple[dict[str, Model], int]:
    """Steps 3 and 4: Bedrock chat entries on the prefix, normalized.

    Returns (models by ID, number of entries skipped for a missing token price).
    """
    models: dict[str, Model] = {}
    skipped = 0
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        if value.get("litellm_provider") not in BEDROCK_PROVIDERS:
            continue
        if value.get("mode") != "chat":
            continue
        # Keys with a slash are LiteLLM routing aliases (bedrock/<region>/..., invoke/...),
        # not Bedrock model IDs.
        if "/" in key or not matches_prefix(key, prefix):
            continue
        if "." not in strip_prefix(key, prefix):
            continue
        inp = value.get("input_cost_per_token")
        out = value.get("output_cost_per_token")
        if inp is None or out is None:
            skipped += 1
            continue
        models[key] = Model(
            model_id=key,
            vendor=vendor_of(key, prefix),
            input_per_1m=per_1m(inp),
            output_per_1m=per_1m(out),
            deprecation_date=value.get("deprecation_date") or None,
            litellm_input_per_1m=per_1m(inp),
            litellm_output_per_1m=per_1m(out),
        )
    return models, skipped


def pct_diff(new: Decimal, base: Decimal) -> Decimal | None:
    """(new - base) / base * 100, rounded half-up to one decimal. Negative is cheaper."""
    if base is None or new is None or base == 0:
        return None
    return ((new - base) / base * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def select_baseline(
    model: Model,
    config: dict,
    known: dict[str, Model],
) -> str | None:
    """Baseline selection, in the spec's order.

    1. The first `baselines` pattern that matches the model ID (with or without the
       prefix), if that baseline has a price we know.
    2. Otherwise the watchlist model from the same vendor with the closest input price.
    3. Otherwise None.
    """
    prefix = config.get("prefix", "")
    bare = strip_prefix(model.model_id, prefix)
    for pattern, target in (config.get("baselines") or {}).items():
        if fnmatch.fnmatchcase(model.model_id, pattern) or fnmatch.fnmatchcase(bare, pattern):
            if target in known:
                return target
            break  # pattern matched but its baseline has no price; fall through to rule 2

    candidates = [
        known[mid]
        for mid in config.get("watchlist") or []
        if mid in known and mid != model.model_id and known[mid].vendor == model.vendor
    ]
    if not candidates:
        return None
    # min() keeps the first of equal distances, so watchlist order breaks ties.
    best = min(candidates, key=lambda m: abs(m.input_per_1m - model.input_per_1m))
    return best.model_id


def compare(model: Model, config: dict, known: dict[str, Model]) -> dict:
    base_id = select_baseline(model, config, known)
    if base_id is None:
        return {"baseline_model_id": None, "input_pct_vs_baseline": None, "output_pct_vs_baseline": None}
    base = known[base_id]
    return {
        "baseline_model_id": base_id,
        "input_pct_vs_baseline": pct_diff(model.input_per_1m, base.input_per_1m),
        "output_pct_vs_baseline": pct_diff(model.output_per_1m, base.output_per_1m),
    }


def diff(
    previous: dict[str, Model],
    current: dict[str, Model],
    config: dict,
    event_date: str,
) -> list[dict]:
    """Steps 5 and 6: new_model, price_change and removed events with baseline comparison."""
    # Baselines can come from today's file or, if a watchlist model just vanished,
    # from the last snapshot.
    known = {**previous, **current}
    events: list[dict] = []

    for mid in sorted(current.keys() - previous.keys()):
        model = current[mid]
        events.append(_event(event_date, "new_model", model, compare(model, config, known)))

    for mid in sorted(current.keys() & previous.keys()):
        old, new = previous[mid], current[mid]
        if old.input_per_1m != new.input_per_1m or old.output_per_1m != new.output_per_1m:
            event = _event(event_date, "price_change", new, compare(new, config, known))
            event["previous_input_per_1m_usd"] = old.input_per_1m
            event["previous_output_per_1m_usd"] = old.output_per_1m
            events.append(event)

    for mid in sorted(previous.keys() - current.keys()):
        model = previous[mid]
        events.append(
            _event(
                event_date,
                "removed",
                model,
                {"baseline_model_id": None, "input_pct_vs_baseline": None, "output_pct_vs_baseline": None},
            )
        )
    return events


def _event(event_date: str, event_type: str, model: Model, comparison: dict) -> dict:
    return {
        "event_date": event_date,
        "event_type": event_type,
        "model_id": model.model_id,
        "baseline_model_id": comparison["baseline_model_id"],
        "input_per_1m_usd": model.input_per_1m,
        "output_per_1m_usd": model.output_per_1m,
        "input_pct_vs_baseline": comparison["input_pct_vs_baseline"],
        "output_pct_vs_baseline": comparison["output_pct_vs_baseline"],
        "previous_input_per_1m_usd": None,
        "previous_output_per_1m_usd": None,
        "price_source": model.price_source,
        # Not log columns; carried for the alert text.
        "_deprecation_date": model.deprecation_date,
        "_verification": model.verification,
        "_litellm_input": model.litellm_input_per_1m,
        "_litellm_output": model.litellm_output_per_1m,
    }


def dedupe_key(event: dict) -> tuple:
    return (
        event["event_type"],
        event["model_id"],
        dec_str(_as_dec(event.get("input_per_1m_usd"))),
        dec_str(_as_dec(event.get("output_per_1m_usd"))),
    )


def drop_duplicates(events: list[dict], log: list[dict]) -> list[dict]:
    """De-duplicate on model ID, event type and prices.

    An event is a duplicate when the most recent logged event for the same model and
    type carries the same prices. Comparing only to the latest one means a price that
    goes A -> B -> A still reports the second change.
    """
    latest: dict[tuple, tuple] = {}
    for row in log:
        latest[(row["event_type"], row["model_id"])] = dedupe_key(row)
    return [e for e in events if latest.get((e["event_type"], e["model_id"])) != dedupe_key(e)]


def _as_dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


# ---------------------------------------------------------------- alert text


def fmt_usd(value) -> str:
    value = _as_dec(value)
    if value is None:
        return "-"
    # Two decimals, more only when the price needs them ($0.035, $0.0875).
    text = format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f").rstrip("0")
    whole, _, frac = text.partition(".")
    frac = frac.ljust(2, "0")
    return f"${int(whole):,}.{frac}"


def fmt_pct(value) -> str:
    value = _as_dec(value)
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value}%"


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        cells = [r[0].ljust(10)] + [c.ljust(widths[i] + 3) for i, c in enumerate(r) if i > 0]
        lines.append("".join(cells).rstrip())
    return lines


def format_alert(event: dict, known: dict[str, Model] | None = None) -> str:
    """Alert text in the spec's layout. `known` supplies baseline prices."""
    known = known or {}
    etype, mid = event["event_type"], event["model_id"]
    footer = [
        "(USD per 1M tokens, on-demand list price)",
        "",
        source_line(event),
    ]

    if etype == "removed":
        return f"Removed from Bedrock price file: {mid} (watchlist model)"

    if etype == "new_model":
        lines = [f"New on Bedrock: {mid}"]
        base_id = event.get("baseline_model_id")
        if base_id and base_id in known:
            base = known[base_id]
            lines.append(f"Baseline:       {base_id}")
            lines.append("")
            lines += _table(
                [
                    ("", "New", "Baseline", "Difference"),
                    ("Input", fmt_usd(event["input_per_1m_usd"]), fmt_usd(base.input_per_1m), fmt_pct(event["input_pct_vs_baseline"])),
                    ("Output", fmt_usd(event["output_per_1m_usd"]), fmt_usd(base.output_per_1m), fmt_pct(event["output_pct_vs_baseline"])),
                ]
            )
        else:
            lines.append("Baseline:       none (no watchlist model from this vendor)")
            lines.append("")
            lines += _table(
                [
                    ("", "New"),
                    ("Input", fmt_usd(event["input_per_1m_usd"])),
                    ("Output", fmt_usd(event["output_per_1m_usd"])),
                ]
            )
    elif etype == "price_change":
        prev_in = event.get("previous_input_per_1m_usd")
        prev_out = event.get("previous_output_per_1m_usd")
        lines = [f"Price change on Bedrock: {mid}", ""]
        lines += _table(
            [
                ("", "New", "Previous", "Difference"),
                ("Input", fmt_usd(event["input_per_1m_usd"]), fmt_usd(prev_in), fmt_pct(pct_diff(_as_dec(event["input_per_1m_usd"]), _as_dec(prev_in)))),
                ("Output", fmt_usd(event["output_per_1m_usd"]), fmt_usd(prev_out), fmt_pct(pct_diff(_as_dec(event["output_per_1m_usd"]), _as_dec(prev_out)))),
            ]
        )
    else:
        raise ValueError(f"unknown event type {etype!r}")

    lines += footer
    dep = event.get("_deprecation_date")
    if dep:
        lines.insert(len(lines) - 2, f"Deprecation date: {dep}")
    return "\n".join(lines)


def source_line(event: dict) -> str:
    verification = event.get("_verification")
    if verification is None:
        # Rebuilt from the log, which records the source but not LiteLLM's price then.
        if event.get("price_source") == "aws":
            return "Source: AWS Price List."
        verification = "litellm_only"
    if verification == "verified":
        return "Source: AWS Price List (verified, matches the LiteLLM price file)."
    if verification == "mismatch":
        return (
            f"Source: AWS Price List. LiteLLM differs: {fmt_usd(event.get('_litellm_input'))} input, "
            f"{fmt_usd(event.get('_litellm_output'))} output. Using AWS."
        )
    return f"Source: LiteLLM price file only. Confirm at {PRICING_URL}"


def should_alert(event: dict, config: dict) -> bool:
    """A removed alert is sent only for watchlist models; every other event alerts."""
    if event["event_type"] == "removed":
        return event["model_id"] in (config.get("watchlist") or [])
    return True
