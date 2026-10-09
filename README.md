# Bedrock Model Price Tracker

A daily job that tells us when a new text model shows up on Amazon Bedrock, and how its on-demand input and output token prices compare with the models we use today. The results are published as a dashboard on GitLab Pages.

This implements `Bedrock Model Price Tracker Spec.pdf`.

## What's here

| Path | What it is |
|---|---|
| `tracker/core.py` | Filter, normalize, diff, baseline selection, percent difference, alert text. Pure functions. |
| `tracker/aws.py` | The AWS Price List check: fetch, parse, and pick the AWS price per model. |
| `tracker/runner.py` | The daily job (steps 1–7), the heartbeat check, the optional `ListFoundationModels` step, and the history backfill. |
| `tracker/state.py` | The snapshot CSVs, the event log and the run metadata. |
| `tracker/site.py` | Builds `public/`: copies `site/` and writes `public/data/dashboard.json`. |
| `config/tracker.json` | The hand-edited config: prefix, watchlist, baselines, alert target and the AWS check's regions. |
| `config/aws_names.json` | Hand-edited map from Bedrock model ID to the model's name in the AWS Price List. |
| `state/` | Tracker state, committed by the scheduled job. `snapshots/YYYY-MM-DD.csv`, `events.csv`, `meta.json`. |
| `site/` | The static dashboard (plain HTML/CSS/JS, no build step). |
| `.gitlab-ci.yml` | Tests, the daily run, the heartbeat and the Pages deploy. |

Only the Python standard library is needed. `boto3` is needed only if you turn on the `ListFoundationModels` step.

## Run it locally

```sh
python3 -m tracker run --no-alerts      # the daily job; alerts print to the console
python3 -m tracker heartbeat            # alert if there's been no successful run in 48h
python3 -m tracker build-site           # writes public/
python3 -m http.server -d public 8000   # open http://localhost:8000
python3 -m unittest discover -s tests -t .
```

With python.org Python on macOS you may need `SSL_CERT_FILE=/etc/ssl/cert.pem` or the bundled *Install Certificates* command before HTTPS fetches work.

### Backfill

`state/` ships pre-seeded with 90 days of history. This was produced by replaying the LiteLLM file's real git history one day at a time, with alerts off:

```sh
python3 -m tracker backfill --days 90 --force   # wipes state/ first
```

It uses the GitHub API to list commits (60 requests/hour unauthenticated; set `GITHUB_TOKEN` to raise that). Without a backfill, the first run saves a snapshot and sends no alerts, as the spec says.

## Set up on GitLab

1. Push this repository to a GitLab project.
2. **Create a push token.** Under Settings → Access tokens, create a project access token with the `write_repository` scope. Give it a role that can push to the default branch: Maintainer if the branch is protected. The daily job uses it to commit `state/` back, with `-o ci.skip` so the push doesn't start another pipeline.
3. **Add CI/CD variables** under Settings → CI/CD → Variables. Mark both masked.
   - `TRACKER_PUSH_TOKEN`: the token from step 2.
   - `SLACK_WEBHOOK_URL`: a Slack incoming webhook. Leave it unset to write alerts to the job log only.
4. **Create two pipeline schedules** on the default branch, under Build → Pipeline schedules:
   - Daily run: for example `0 7 * * *` UTC, with variable `TRACKER_TASK=daily`.
   - Heartbeat: for example `0 19 * * *` UTC, with variable `TRACKER_TASK=heartbeat`.
5. Run the daily schedule once by hand (the ▶ button) to deploy Pages. The URL appears under Deploy → Pages. Set Pages access control there if the dashboard should be internal only.

The scheduled pipeline runs `track` and then `pages`. `pages` runs even when `track` fails, so a failed run shows up on the dashboard as "Last run failed". A failed run never overwrites the last good snapshot. Pushes to the default branch run the tests and redeploy Pages from the committed state.

## How it follows the spec

- **Filter:** keeps `litellm_provider` of `bedrock` or `bedrock_converse`, `mode` of `chat`, keys on the configured prefix, and entries with both token prices. Keys containing `/` are LiteLLM routing aliases, not model IDs, so they're skipped. Entries missing a price are counted as "skipped" in the run summary.
- **Validate:** the file must be JSON, and it must hold at least 80% of the last good run's Bedrock entry count (all modes and prefixes).
- **Events:** `new_model`, `price_change` and `removed`. De-duplication compares each event with the most recent logged event of the same model and type, by prices. A same-day re-run therefore adds nothing, but a price that goes A → B → A still reports both changes.
- **Baselines:**
  1. The first `baselines` glob that matches the ID, with or without the prefix.
  2. Otherwise, the same-vendor watchlist model with the closest input price.
  3. Otherwise, no baseline.

  If a pattern matches but its target has no price in the file, selection falls through to rule 2.
- **Percent difference:** `(new − baseline) / baseline × 100`, rounded half-up to one decimal place. Input and output are kept separate.
- **Alerts:** the layout matches the spec's example exactly; there's a test for it. A `removed` alert is sent only for watchlist models. A watchlist model missing from the file alerts once, then stays quiet until it returns.
- **Heartbeat:** a separate schedule alerts when there has been no successful run for `heartbeat_max_age_hours` (48).

### AWS price check

The spec makes LiteLLM the primary source and AWS the check. The tracker now runs that check automatically, using AWS's public Price List API (`pricing.us-east-1.amazonaws.com/offers/...`), which needs no credentials. LiteLLM still drives detection (model IDs, new and removed models, deprecation dates). For each model's price:

| Situation | Price reported | Mark |
|---|---|---|
| AWS lists the model and agrees with LiteLLM | AWS | ✓ AWS (verified) |
| AWS lists it but disagrees | AWS, with LiteLLM's figure shown | ≠ AWS |
| Not in `config/aws_names.json`, or not in AWS's list | LiteLLM | LiteLLM only |

- **Regions.** The check reads `us-east-1`, `us-east-2` and `us-west-2`, the regions a `us.` ID routes to. A `us.` ID gets AWS's regional price; a `global.` ID gets the global price. If you change `prefix` to `eu.` or similar, change `aws_price_check.regions` to match.
- **Strict parsing.** Only standard on-demand input and output prices are read, matched against an allowlist of AWS's price descriptions. Cache, batch, priority, flex, long-context and provisioned prices are ignored. If AWS publishes two different prices for the same model, variant and token type, in one region or across regions, the model is reported as LiteLLM only rather than guessed.
- **Mapping.** `config/aws_names.json` lists the 49 models AWS currently prices. The run log warns when a mapped name disappears from AWS's list. Add a line when AWS starts listing a new model; until then it stays LiteLLM only.
- **Alerts.** Every alert ends with its price source. When AWS and LiteLLM disagree on a watchlist model, a separate alert goes out once, then stays quiet until the figures change.
- **Outages.** If AWS's Price List can't be reached, the run still succeeds. Models keep their last AWS price, so an outage doesn't show up as price changes. The dashboard shows "AWS check unreachable".
- **History.** The event log and snapshots record each price's source (`price_source`). Snapshots also record LiteLLM's price alongside it. Older files are read as LiteLLM, and the event log's header is migrated in place the first time a new row is written. The backfilled history is LiteLLM only; verification starts with the first daily run.

The 9 models AWS doesn't list today are 8 OpenAI GPT models (AWS publishes their prices on documentation pages, not in the Price List) and GLM-5.3. They stay LiteLLM only.

### Dashboard

- **Spotlight:** the latest new models.
- **Run health:** recent runs and their status.
- **Lineups:** Claude and GPT model lineups.
- **Price landscape:** a log-log scatter of input vs output price.
- **Event stream:** each event with its exact alert text.
- **Percent-difference bars:** against the per-model rule baseline, or against any model picked in the filter bar.
- **Model registry:** a sortable table with CSV downloads. Every price carries its verification badge, and an "AWS prices only" filter hides LiteLLM-only models.
- **Model drawer:** price history for each model.

The view state is in the URL (`?baseline=…&vendor=…#m=<model id>`), so a link reproduces what you see.

Vendor logos come from [Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT) and are trademarks of their owners. The pixel critters (Claude families) and celestial glyphs (OpenAI Luna/Terra/Sol/Astra) are original artwork drawn in `site/assets/sigils.js`.

## Open decisions from the spec

These have placeholder answers in `config/tracker.json`. Confirm or change them.

| Decision | Current setting |
|---|---|
| Model ID prefix | `us.`, as the spec assumes |
| Watchlist and baselines | Placeholder: Sonnet 4.6, Opus 4.6, Haiku 4.5, GPT-5.4, Nova Pro, Llama 3.3 70B. Family patterns for Sonnet, Opus and Haiku. **Replace these with the models we actually use.** |
| Where alerts go | Slack webhook, via a CI/CD variable |
| Where it runs and stores state | GitLab scheduled pipeline committing CSVs to this repo |
| `ListFoundationModels` in v1 | Off (`list_foundation_models.enabled: false`). Turning it on needs `boto3` in the job image and AWS credentials in CI. |
| Org policy on fetching from GitHub | Not yet confirmed. The job now also fetches `pricing.us-east-1.amazonaws.com`. |

### Known gap

With prefix `us.`, models that Bedrock offers only under a bare ID are never tracked. In today's file that includes most Mistral, Qwen, Cohere, AI21 and NVIDIA models: 58 models on `us.` against roughly 100 bare IDs. This is how the spec defines it ("follows one prefix"), but it means a new model released only under a bare ID won't alert.
