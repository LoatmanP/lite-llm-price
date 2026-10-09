"""AWS Price List check: standard on-demand text token prices for Bedrock models.

Reads AWS's public Price List API (no credentials) for the configured regions
(the regions a cross-region ID such as us. routes to), keeps only the
standard on-demand input and output token prices, and splits them into "regional"
(in-region and geographic cross-region, which is what us./eu./... IDs pay) and
"global" (global cross-region, the global. IDs).

The parser only accepts price descriptions it recognises. Anything else (cache,
batch, priority, flex, long context, provisioned throughput, media) is ignored,
and a model whose recognised prices disagree with each other, within a region or
across regions, is dropped as unverifiable rather than guessed.
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal

from .core import Model

OFFERS = ("AmazonBedrock", "AmazonBedrockFoundationModels")
OFFER_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer}/current/{region}/index.json"

UNIT_SCALE = {"1k tokens": Decimal(1000), "1m tokens": Decimal(1)}

# AmazonBedrockFoundationModels (Marketplace models): the last "|" segment of the
# price description, lower-cased, must fully match one of these.
FM_PATTERNS = [
    (re.compile(r"million input tokens( (standard|regional|regional cris|global))?"), "in"),
    (re.compile(r"million response tokens( (standard|regional|regional cris|global))?"), "out"),
    (re.compile(r"input tokens - standard(, global)?"), "in"),
    (re.compile(r"output tokens - standard(, global)?"), "out"),
    (re.compile(r"price per 1 million input tokens"), "in"),
    (re.compile(r"price per 1 million output tokens"), "out"),
    (re.compile(r"input_tokens(_global)?_standard"), "in"),
    (re.compile(r"output_tokens(_global)?_standard"), "out"),
    (re.compile(r"output token count( global)?"), "out"),
]

# AmazonBedrock (first-party and some partner models): the usage type must end in
# one of these suffixes.
BR_USAGETYPE = re.compile(r"-(?P<kind>input|output)-tokens?(-count)?(?P<rest>-[a-z-]+)?$")
BR_RESTS = {
    None: "regional",
    "-standard": "regional",
    "-cross-region-global": "global",
    "-global-standard": "global",
}
# Note "mantle" usage types are kept: for some models (Grok, Kimi K3) they carry the
# only standard on-demand price.
BR_SKIP = ("cache", "video", "image", "audio", "sonic", "speech", "custom")


class AwsUnavailable(Exception):
    pass


def fetch_offers(regions: list[str], http_get) -> list[dict]:
    """Download both Bedrock offers for each region. Raises AwsUnavailable."""
    out = []
    for region in regions:
        offers = {}
        for offer in OFFERS:
            url = OFFER_URL.format(offer=offer, region=region)
            try:
                offers[offer] = json.loads(http_get(url, timeout=120))
            except Exception as exc:
                raise AwsUnavailable(f"{offer} for {region}: {exc}") from exc
        out.append(offers)
    return out


def _on_demand_dims(offer: dict, sku: str):
    for term in offer.get("terms", {}).get("OnDemand", {}).get(sku, {}).values():
        yield from term.get("priceDimensions", {}).values()


def _per_1m(dim: dict) -> Decimal | None:
    scale = UNIT_SCALE.get(dim.get("unit", "").strip().lower())
    usd = dim.get("pricePerUnit", {}).get("USD")
    if scale is None or usd is None:
        return None
    return (Decimal(usd) * scale).quantize(Decimal("0.000001")).normalize()


def parse_prices(offer_sets: list[dict] | dict) -> tuple[dict, list[str]]:
    """Return ({"Provider|Model": {"regional": {"in": D, "out": D}, "global": {...}}}, conflicts).

    offer_sets is one {offer name: offer JSON} dict per region.
    """
    if isinstance(offer_sets, dict):
        offer_sets = [offer_sets]
    seen: dict[tuple, set] = {}

    def add(key, variant, kind, price):
        seen.setdefault((key, variant, kind), set()).add(price)

    for offers in offer_sets:
        _collect(offers, add)

    prices: dict = {}
    conflicts = []
    bad = set()
    for (key, variant, kind), values in seen.items():
        if len(values) > 1:
            conflicts.append(f"{key} {variant} {kind}: {sorted(str(v) for v in values)}")
            bad.add((key, variant))
            continue
        prices.setdefault(key, {}).setdefault(variant, {})[kind] = next(iter(values))
    for key, variant in bad:
        prices.get(key, {}).pop(variant, None)
    return prices, sorted(conflicts)


def _collect(offers: dict, add) -> None:
    fm = offers.get("AmazonBedrockFoundationModels") or {}
    for sku, product in fm.get("products", {}).items():
        a = product.get("attributes", {})
        if a.get("feature") != "On-demand Inference" or "model" not in a:
            continue
        key = f"{a.get('provider', '')}|{a['model']}"
        for dim in _on_demand_dims(fm, sku):
            desc = dim.get("description", "").split("|")[-1].strip().lower()
            kind = next((k for pattern, k in FM_PATTERNS if pattern.fullmatch(desc)), None)
            price = _per_1m(dim)
            if kind and price is not None:
                add(key, "global" if "global" in desc else "regional", kind, price)

    br = offers.get("AmazonBedrock") or {}
    for sku, product in br.get("products", {}).items():
        a = product.get("attributes", {})
        usage = a.get("usagetype", "").lower()
        if "model" not in a or any(s in usage for s in BR_SKIP):
            continue
        m = BR_USAGETYPE.search(usage)
        if not m or m.group("rest") not in BR_RESTS:
            continue
        # First-party Nova products carry no provider attribute.
        key = f"{a.get('provider') or 'Amazon'}|{a['model']}"
        kind = "in" if m.group("kind") == "input" else "out"
        for dim in _on_demand_dims(br, sku):
            price = _per_1m(dim)
            if price is not None:
                add(key, BR_RESTS[m.group("rest")], kind, price)


def publication_dates(offer_sets: list[dict], regions: list[str]) -> dict:
    return {
        f"{region} {name}": offer.get("publicationDate")
        for region, offers in zip(regions, offer_sets)
        for name, offer in offers.items()
    }


def variant_for_prefix(prefix: str) -> str:
    return "global" if prefix == "global." else "regional"


def aws_price(model_id: str, names: dict, prices: dict, prefix: str) -> tuple[Decimal, Decimal] | None:
    """AWS (input, output) per 1M for a tracked model, or None when it can't be verified."""
    key = names.get(model_id)
    if not key:
        return None
    entry = prices.get(key, {}).get(variant_for_prefix(prefix), {})
    if "in" not in entry or "out" not in entry:
        return None
    return entry["in"], entry["out"]


def apply(models: dict[str, Model], names: dict, prices: dict, prefix: str) -> dict[str, Model]:
    """Use the AWS price wherever AWS lists the model; keep LiteLLM's price otherwise.

    Every returned model records its source and the LiteLLM price it was compared with.
    """
    out = {}
    for mid, m in models.items():
        found = aws_price(mid, names, prices, prefix)
        if found is None:
            out[mid] = m
            continue
        out[mid] = Model(
            model_id=m.model_id,
            vendor=m.vendor,
            input_per_1m=found[0],
            output_per_1m=found[1],
            deprecation_date=m.deprecation_date,
            price_source="aws",
            litellm_input_per_1m=m.litellm_input_per_1m,
            litellm_output_per_1m=m.litellm_output_per_1m,
        )
    return out


def load_names(path) -> dict:
    data = json.loads(open(path).read())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def warn(msg: str) -> None:
    print(f"AWS price check: {msg}", file=sys.stderr)
