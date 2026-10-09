import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tracker.alerts import Alerter
from tracker.core import (
    Model,
    diff,
    drop_duplicates,
    filter_and_normalize,
    format_alert,
    pct_diff,
    per_1m,
    select_baseline,
    should_alert,
    vendor_of,
)
from tracker.names import display_name
from tracker.runner import RunFailed, process
from tracker.state import State

CONFIG = {
    "prefix": "us.",
    "watchlist": ["us.anthropic.claude-sonnet-4-6", "us.meta.llama-a", "us.meta.llama-b"],
    "baselines": {"anthropic.claude-sonnet-*": "us.anthropic.claude-sonnet-4-6"},
    "validation_min_ratio": 0.8,
}


def entry(inp, out, provider="bedrock_converse", mode="chat", **extra):
    return {"litellm_provider": provider, "mode": mode, "input_cost_per_token": inp, "output_cost_per_token": out, **extra}


def m(mid, inp, out, vendor=None):
    return Model(mid, vendor or vendor_of(mid, "us."), Decimal(str(inp)), Decimal(str(out)))


SPEC_FILE = {
    "anthropic.claude-sonnet-5": entry(2e-06, 1e-05),
    "global.anthropic.claude-sonnet-5": entry(2e-06, 1e-05),
    "us.anthropic.claude-sonnet-5": entry(2.2e-06, 1.1e-05),
    "us.anthropic.claude-sonnet-4-6": entry(3.3e-06, 1.65e-05),
    "bedrock/us-east-1/anthropic.claude-sonnet-5": entry(2e-06, 1e-05),
    "us.twelvelabs.pegasus": entry(None, 7.5e-06),
    "us.amazon.titan-embed": entry(1e-07, 0, mode="embedding"),
    "us.openai.gpt-x": entry(1e-06, 2e-06, provider="openai"),
    "sample_spec": {"note": "not a model"},
}


class FilterTests(unittest.TestCase):
    def test_per_1m_has_no_float_noise(self):
        self.assertEqual(per_1m(2.2e-06), Decimal("2.2"))
        self.assertEqual(per_1m(3.5e-08), Decimal("0.035"))

    def test_filters_to_prefix_chat_bedrock(self):
        models, skipped = filter_and_normalize(SPEC_FILE, "us.")
        self.assertEqual(set(models), {"us.anthropic.claude-sonnet-5", "us.anthropic.claude-sonnet-4-6"})
        self.assertEqual(skipped, 1)  # pegasus has no input price
        sonnet5 = models["us.anthropic.claude-sonnet-5"]
        self.assertEqual((sonnet5.input_per_1m, sonnet5.output_per_1m), (Decimal("2.2"), Decimal("11")))
        self.assertEqual(sonnet5.vendor, "anthropic")

    def test_bare_prefix_excludes_region_ids(self):
        models, _ = filter_and_normalize(SPEC_FILE, "")
        self.assertEqual(set(models), {"anthropic.claude-sonnet-5"})
        self.assertEqual(models["anthropic.claude-sonnet-5"].input_per_1m, Decimal("2"))


class CompareTests(unittest.TestCase):
    known = {
        "us.anthropic.claude-sonnet-4-6": m("us.anthropic.claude-sonnet-4-6", 3.3, 16.5),
        "us.meta.llama-a": m("us.meta.llama-a", 0.2, 0.2),
        "us.meta.llama-b": m("us.meta.llama-b", 2.0, 2.0),
    }

    def test_pct_rounds_half_up_negative_is_cheaper(self):
        self.assertEqual(pct_diff(Decimal("2.2"), Decimal("3.3")), Decimal("-33.3"))
        self.assertEqual(pct_diff(Decimal("1.0005"), Decimal("1")), Decimal("0.1"))
        self.assertIsNone(pct_diff(Decimal("1"), Decimal("0")))

    def test_rule1_pattern(self):
        new = m("us.anthropic.claude-sonnet-5", 2.2, 11)
        self.assertEqual(select_baseline(new, CONFIG, self.known), "us.anthropic.claude-sonnet-4-6")

    def test_rule2_closest_input_same_vendor(self):
        self.assertEqual(select_baseline(m("us.meta.llama-c", 1.5, 1.5), CONFIG, self.known), "us.meta.llama-b")
        self.assertEqual(select_baseline(m("us.meta.llama-d", 0.3, 0.3), CONFIG, self.known), "us.meta.llama-a")

    def test_rule3_no_baseline(self):
        self.assertIsNone(select_baseline(m("us.xai.grok", 2, 6), CONFIG, self.known))


class DiffTests(unittest.TestCase):
    def test_three_event_types(self):
        prev = {"us.a.x": m("us.a.x", 1, 2), "us.a.y": m("us.a.y", 1, 2), "us.a.gone": m("us.a.gone", 1, 1)}
        curr = {"us.a.x": m("us.a.x", 1, 2), "us.a.y": m("us.a.y", 1, 3), "us.a.new": m("us.a.new", 5, 5)}
        events = diff(prev, curr, CONFIG, "2026-10-09")
        self.assertEqual([(e["event_type"], e["model_id"]) for e in events],
                         [("new_model", "us.a.new"), ("price_change", "us.a.y"), ("removed", "us.a.gone")])
        change = events[1]
        self.assertEqual(change["previous_output_per_1m_usd"], Decimal("2"))

    def test_dedupe_against_latest_logged_event(self):
        event = {"event_type": "price_change", "model_id": "us.a.y", "input_per_1m_usd": Decimal("1"), "output_per_1m_usd": Decimal("3")}
        log = [{**event, "input_per_1m_usd": Decimal("1.0"), "output_per_1m_usd": Decimal("3.00")}]
        self.assertEqual(drop_duplicates([event], log), [])
        # A -> B -> A: the return to A is a real change.
        log.append({**event, "output_per_1m_usd": Decimal("4")})
        self.assertEqual(drop_duplicates([event], log), [event])

    def test_removed_alerts_only_for_watchlist(self):
        self.assertFalse(should_alert({"event_type": "removed", "model_id": "us.x.y"}, CONFIG))
        self.assertTrue(should_alert({"event_type": "removed", "model_id": "us.meta.llama-a"}, CONFIG))


class AlertFormatTests(unittest.TestCase):
    def test_matches_spec_example(self):
        models, _ = filter_and_normalize(SPEC_FILE, "us.")
        prev = {k: v for k, v in models.items() if k != "us.anthropic.claude-sonnet-5"}
        (event,) = diff(prev, models, CONFIG, "2026-10-09")
        expected = (
            "New on Bedrock: us.anthropic.claude-sonnet-5\n"
            "Baseline:       us.anthropic.claude-sonnet-4-6\n"
            "\n"
            "          New      Baseline   Difference\n"
            "Input     $2.20    $3.30      -33.3%\n"
            "Output    $11.00   $16.50     -33.3%\n"
            "(USD per 1M tokens, on-demand list price)\n"
            "\n"
            "Source: LiteLLM price file only. Confirm at aws.amazon.com/bedrock/pricing"
        )
        self.assertEqual(format_alert(event, models), expected)

    def test_price_change_uses_previous_and_small_prices_keep_precision(self):
        event = {"event_type": "price_change", "model_id": "us.amazon.nova-micro", "input_per_1m_usd": Decimal("0.035"),
                 "output_per_1m_usd": Decimal("0.14"), "previous_input_per_1m_usd": Decimal("0.07"),
                 "previous_output_per_1m_usd": Decimal("0.14")}
        text = format_alert(event)
        self.assertIn("Previous", text)
        self.assertIn("Input     $0.035   $0.07      -50.0%", text)
        self.assertIn("$0.14      0.0%", text)

    def test_removed_is_one_line(self):
        self.assertEqual(format_alert({"event_type": "removed", "model_id": "us.a.b"}).count("\n"), 0)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_saves_snapshot_without_alerts(self):
        alerter = Alerter(None)
        process(json.dumps(SPEC_FILE), "2026-10-08", self.state, CONFIG, alerter)
        self.assertEqual(alerter.sent, [])
        self.assertEqual(self.state.snapshot_dates(), ["2026-10-08"])
        self.assertEqual(self.state.read_events(), [])

    def test_second_run_alerts_and_rerun_is_deduped(self):
        day1 = {k: v for k, v in SPEC_FILE.items() if k != "us.anthropic.claude-sonnet-5"}
        process(json.dumps(day1), "2026-10-08", self.state, CONFIG, Alerter(None))
        alerter = Alerter(None)
        process(json.dumps(SPEC_FILE), "2026-10-09", self.state, CONFIG, alerter)
        new_alerts = [s for s in alerter.sent if s.startswith("New on Bedrock")]
        self.assertEqual(len(new_alerts), 1)
        self.assertTrue(new_alerts[0].startswith("New on Bedrock: us.anthropic.claude-sonnet-5"))
        again = Alerter(None)
        process(json.dumps(SPEC_FILE), "2026-10-09", self.state, CONFIG, again)
        self.assertEqual(again.sent, [])
        self.assertEqual(len(self.state.read_events()), 1)

    def test_shrunken_file_fails_validation_and_keeps_state(self):
        process(json.dumps(SPEC_FILE), "2026-10-08", self.state, CONFIG, Alerter(None))
        tiny = {"us.anthropic.claude-sonnet-5": SPEC_FILE["us.anthropic.claude-sonnet-5"]}
        with self.assertRaises(RunFailed):
            process(json.dumps(tiny), "2026-10-09", self.state, CONFIG, Alerter(None))
        self.assertEqual(self.state.snapshot_dates(), ["2026-10-08"])
        with self.assertRaises(RunFailed):
            process("not json", "2026-10-09", self.state, CONFIG, Alerter(None))

    def test_missing_watchlist_alerts_once(self):
        process(json.dumps(SPEC_FILE), "2026-10-07", self.state, CONFIG, Alerter(None))
        first, second = Alerter(None), Alerter(None)
        process(json.dumps(SPEC_FILE), "2026-10-08", self.state, CONFIG, first)
        process(json.dumps(SPEC_FILE), "2026-10-09", self.state, CONFIG, second)
        # llama-a and llama-b were never in the file; first run is quiet, then one alert each.
        self.assertEqual(len([s for s in first.sent if "missing" in s]), 2)
        self.assertEqual(second.sent, [])


class NameTests(unittest.TestCase):
    def test_display_names(self):
        cases = {
            "us.anthropic.claude-sonnet-4-6": "Claude Sonnet 4.6",
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0": "Claude 3.5 Sonnet v2",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0": "Claude Haiku 4.5",
            "us.meta.llama3-3-70b-instruct-v1:0": "Llama 3.3 70B Instruct",
            "us.openai.gpt-5.6-luna": "GPT-5.6 Luna",
            "us.deepseek.r1-v1:0": "DeepSeek R1",
            "us.zai.glm-5.3": "GLM-5.3",
        }
        for mid, name in cases.items():
            self.assertEqual(display_name(mid, "us."), name, mid)


if __name__ == "__main__":
    unittest.main()
