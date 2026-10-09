import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tracker import aws
from tracker.alerts import Alerter
from tracker.core import Model, diff, format_alert
from tracker.runner import carry_forward_aws, process
from tracker.state import State

D = Decimal


def fm_product(sku, model, desc, usd, unit="1M tokens", provider="Anthropic", feature="On-demand Inference"):
    return sku, {"attributes": {"provider": provider, "model": model, "feature": feature}}, {
        sku: {"t": {"priceDimensions": {"d": {"description": f"AWS Marketplace software usage|us-east-1|{desc}", "unit": unit, "pricePerUnit": {"USD": usd}}}}}
    }


def br_product(sku, model, usage, usd, unit="1K tokens", provider=None):
    attrs = {"model": model, "usagetype": usage}
    if provider:
        attrs["provider"] = provider
    return sku, {"attributes": attrs}, {sku: {"t": {"priceDimensions": {"d": {"description": usage, "unit": unit, "pricePerUnit": {"USD": usd}}}}}}


def offer(*items):
    products, terms = {}, {}
    for sku, product, term in items:
        products[sku] = product
        terms.update(term)
    return {"products": products, "terms": {"OnDemand": terms}}


FM = offer(
    fm_product("a1", "Claude Sonnet 4.6", "Million Input Tokens Regional CRIS", "3.3"),
    fm_product("a2", "Claude Sonnet 4.6", "Million Response Tokens Regional CRIS", "16.5"),
    fm_product("a3", "Claude Sonnet 4.6", "Million Input Tokens Global", "3.0"),
    fm_product("a4", "Claude Sonnet 4.6", "Million Response Tokens Global", "15.0"),
    # Traps: cache, long context and batch prices must not be read as standard input.
    fm_product("a5", "Claude Sonnet 4.6", "Million Cache Read Input Tokens Regional", "0.33"),
    fm_product("a6", "Claude Sonnet 4.6", "Input Tokens - Standard, Long Context", "6.6"),
    fm_product("a7", "Claude Sonnet 4.6", "Million Input Tokens Regional", "1.65", feature="Batch Inference"),
    # A model whose recognised prices disagree is unverifiable.
    fm_product("b1", "Claude Odd", "Million Input Tokens", "1"),
    fm_product("b2", "Claude Odd", "Million Input Tokens Standard", "2"),
    fm_product("b3", "Claude Odd", "Million Response Tokens", "5"),
)
BR = offer(
    br_product("n1", "Nova Micro", "USE1-NovaMicro-input-tokens", "0.000035"),
    br_product("n2", "Nova Micro", "USE1-NovaMicro-output-tokens", "0.00014"),
    br_product("n3", "Nova Micro", "USE1-NovaMicro-cache-read-input-token-count", "0.00000875"),
    br_product("n4", "Nova Micro", "USE1-NovaMicro-input-tokens-batch", "0.0000175"),
    br_product("g1", "Grok 4.7", "USE1-xai.grok-4.7-mantle-input-tokens-standard", "0.0022", provider="xAI"),
    br_product("g2", "Grok 4.7", "USE1-xai.grok-4.7-mantle-output-tokens-standard", "0.0066", provider="xAI"),
    br_product("g3", "Grok 4.7", "USE1-xai.grok-4.7-mantle-input-tokens-global-standard", "0.002", provider="xAI"),
    br_product("g4", "Grok 4.7", "USE1-xai.grok-4.7-mantle-output-tokens-global-standard", "0.006", provider="xAI"),
    br_product("g5", "Grok 4.7", "USE1-xai.grok-4.7-mantle-input-tokens-priority", "0.004", provider="xAI"),
)
OFFERS = {"AmazonBedrock": BR, "AmazonBedrockFoundationModels": FM}

NAMES = {
    "us.anthropic.claude-sonnet-4-6": "Anthropic|Claude Sonnet 4.6",
    "us.amazon.nova-micro-v1:0": "Amazon|Nova Micro",
    "us.xai.grok-4.7": "xAI|Grok 4.7",
    "us.anthropic.claude-odd": "Anthropic|Claude Odd",
}


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.prices, self.conflicts = aws.parse_prices(OFFERS)

    def test_standard_prices_by_variant(self):
        sonnet = self.prices["Anthropic|Claude Sonnet 4.6"]
        self.assertEqual(sonnet["regional"], {"in": D("3.3"), "out": D("16.5")})
        self.assertEqual(sonnet["global"], {"in": D("3"), "out": D("15")})

    def test_per_1k_units_and_cache_skus_ignored(self):
        self.assertEqual(self.prices["Amazon|Nova Micro"]["regional"], {"in": D("0.035"), "out": D("0.14")})

    def test_mantle_prices_kept_priority_dropped(self):
        self.assertEqual(self.prices["xAI|Grok 4.7"]["regional"], {"in": D("2.2"), "out": D("6.6")})
        self.assertEqual(self.prices["xAI|Grok 4.7"]["global"], {"in": D("2"), "out": D("6")})

    def test_conflicting_prices_are_unverifiable(self):
        self.assertNotIn("regional", self.prices.get("Anthropic|Claude Odd", {}))
        self.assertTrue(any("Claude Odd" in c for c in self.conflicts))

    def test_regions_must_agree(self):
        other = json.loads(json.dumps(OFFERS))
        other["AmazonBedrock"]["terms"]["OnDemand"]["n1"]["t"]["priceDimensions"]["d"]["pricePerUnit"]["USD"] = "0.00004"
        prices, conflicts = aws.parse_prices([OFFERS, other])
        self.assertNotIn("regional", prices.get("Amazon|Nova Micro", {}))
        self.assertIn("Anthropic|Claude Sonnet 4.6", prices)

    def test_prefix_picks_variant(self):
        self.assertEqual(aws.aws_price("us.xai.grok-4.7", NAMES, self.prices, "us."), (D("2.2"), D("6.6")))
        self.assertEqual(aws.aws_price("global.xai.grok-4.7", {"global.xai.grok-4.7": "xAI|Grok 4.7"}, self.prices, "global."), (D("2"), D("6")))
        self.assertIsNone(aws.aws_price("us.anthropic.claude-odd", NAMES, self.prices, "us."))
        self.assertIsNone(aws.aws_price("us.unmapped.model", NAMES, self.prices, "us."))


def lm(mid, inp, out):
    return Model(mid, mid.split(".")[1], D(inp), D(out), litellm_input_per_1m=D(inp), litellm_output_per_1m=D(out))


class ApplyTests(unittest.TestCase):
    prices, _ = aws.parse_prices(OFFERS)

    def test_verified_mismatch_and_litellm_only(self):
        models = {
            "us.anthropic.claude-sonnet-4-6": lm("us.anthropic.claude-sonnet-4-6", "3.3", "16.5"),
            "us.xai.grok-4.7": lm("us.xai.grok-4.7", "2", "6"),
            "us.zai.glm-5.3": lm("us.zai.glm-5.3", "1.848", "5.808"),
        }
        out = aws.apply(models, NAMES, self.prices, "us.")
        self.assertEqual(out["us.anthropic.claude-sonnet-4-6"].verification, "verified")
        grok = out["us.xai.grok-4.7"]
        self.assertEqual(grok.verification, "mismatch")
        self.assertEqual((grok.input_per_1m, grok.litellm_input_per_1m), (D("2.2"), D("2")))
        self.assertEqual(out["us.zai.glm-5.3"].verification, "litellm_only")

    def test_alert_source_lines(self):
        prev = {}
        curr = aws.apply({"us.xai.grok-4.7": lm("us.xai.grok-4.7", "2", "6")}, NAMES, self.prices, "us.")
        (event,) = diff(prev, curr, {"prefix": "us."}, "2026-10-09")
        self.assertEqual(event["price_source"], "aws")
        text = format_alert(event, curr)
        self.assertIn("Input     $2.20", text)
        self.assertIn("Source: AWS Price List. LiteLLM differs: $2.00 input, $6.00 output. Using AWS.", text)
        verified = aws.apply({"us.xai.grok-4.7": lm("us.xai.grok-4.7", "2.2", "6.6")}, NAMES, self.prices, "us.")
        (event,) = diff(prev, verified, {"prefix": "us."}, "2026-10-09")
        self.assertIn("Source: AWS Price List (verified, matches the LiteLLM price file).", format_alert(event, verified))

    def test_outage_carries_last_aws_price_forward(self):
        yesterday = aws.apply({"us.xai.grok-4.7": lm("us.xai.grok-4.7", "2", "6")}, NAMES, self.prices, "us.")
        today = carry_forward_aws({"us.xai.grok-4.7": lm("us.xai.grok-4.7", "2", "6")}, yesterday)
        self.assertEqual(diff(yesterday, today, {"prefix": "us."}, "2026-10-10"), [])
        self.assertEqual(today["us.xai.grok-4.7"].price_source, "aws")


def litellm_file(grok_in=2.2e-06, grok_out=6.6e-06):
    e = lambda i, o: {"litellm_provider": "bedrock_converse", "mode": "chat", "input_cost_per_token": i, "output_cost_per_token": o}
    return json.dumps({"us.xai.grok-4.7": e(grok_in, grok_out), "us.anthropic.claude-sonnet-4-6": e(3.3e-06, 1.65e-05)})


class RunTests(unittest.TestCase):
    config = {"prefix": "us.", "watchlist": ["us.xai.grok-4.7"], "baselines": {}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name))
        prices, _ = aws.parse_prices(OFFERS)
        self.check = {"status": "ok", "names": NAMES, "prices": prices, "regions": ["us-east-1"]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_mismatch_on_watchlist_alerts_once(self):
        process(litellm_file(2e-06, 6e-06), "2026-10-07", self.state, self.config, Alerter(None), aws_check=self.check)
        first, second = Alerter(None), Alerter(None)
        process(litellm_file(2e-06, 6e-06), "2026-10-08", self.state, self.config, first, aws_check=self.check)
        process(litellm_file(2e-06, 6e-06), "2026-10-09", self.state, self.config, second, aws_check=self.check)
        self.assertEqual(len([s for s in first.sent if s.startswith("AWS and LiteLLM disagree")]), 1)
        self.assertEqual(second.sent, [])
        meta = self.state.read_meta()
        self.assertEqual(meta["aws_check"]["mismatch"], 1)
        self.assertEqual(meta["aws_check"]["verified"], 1)

    def test_aws_outage_reports_no_price_change(self):
        process(litellm_file(2e-06, 6e-06), "2026-10-08", self.state, self.config, Alerter(None), aws_check=self.check)
        alerter = Alerter(None)
        process(litellm_file(2e-06, 6e-06), "2026-10-09", self.state, self.config, alerter,
                aws_check={"status": "unavailable", "names": NAMES, "error": "timeout"})
        self.assertEqual(self.state.read_events(), [])
        self.assertEqual(self.state.read_snapshot("2026-10-09")["us.xai.grok-4.7"].input_per_1m, D("2.2"))
        self.assertEqual(self.state.read_meta()["aws_check"]["status"], "unavailable")

    def test_old_state_files_still_read_and_migrate(self):
        snap = self.state.snapshots_dir
        snap.mkdir(parents=True)
        (snap / "2026-10-08.csv").write_text(
            "snapshot_date,model_id,vendor,input_per_1m_usd,output_per_1m_usd,deprecation_date\n"
            "2026-10-08,us.xai.grok-4.7,xai,2.2,6.6,\n"
        )
        self.state.events_path.write_text(
            "event_date,event_type,model_id,baseline_model_id,input_per_1m_usd,output_per_1m_usd,"
            "input_pct_vs_baseline,output_pct_vs_baseline,previous_input_per_1m_usd,previous_output_per_1m_usd\n"
            "2026-10-01,new_model,us.xai.grok-4.7,,2.2,6.6,,,,\n"
        )
        old = self.state.read_snapshot("2026-10-08")["us.xai.grok-4.7"]
        self.assertEqual((old.price_source, old.litellm_input_per_1m), ("litellm", D("2.2")))
        process(litellm_file(), "2026-10-09", self.state, self.config, Alerter(None), aws_check=self.check)
        events = self.state.read_events()
        # The migrated row defaults to litellm; Sonnet 4.6 is new today and AWS lists it.
        self.assertEqual([(e["model_id"], e["price_source"]) for e in events],
                         [("us.xai.grok-4.7", "litellm"), ("us.anthropic.claude-sonnet-4-6", "aws")])
        self.assertTrue(self.state.events_path.read_text().splitlines()[0].endswith(",price_source"))


if __name__ == "__main__":
    unittest.main()
