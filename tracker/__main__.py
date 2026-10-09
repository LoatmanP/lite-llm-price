"""python -m tracker {run,heartbeat,backfill,build-site}"""

import argparse
import sys
from pathlib import Path

from . import runner, site


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tracker", description="Bedrock model price tracker")
    parser.add_argument("--config", type=Path, default=runner.DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=runner.DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="the daily job: fetch, validate, diff, alert, save")
    p_run.add_argument("--date", type=runner._parse_date, help="override the run date (YYYY-MM-DD)")
    p_run.add_argument("--no-alerts", action="store_true", help="print nothing to Slack")

    sub.add_parser("heartbeat", help="alert if no successful run within heartbeat_max_age_hours")

    p_back = sub.add_parser("backfill", help="replay LiteLLM git history into state, alerts off")
    p_back.add_argument("--days", type=int, default=90)
    p_back.add_argument("--force", action="store_true", help="wipe existing state first")

    p_site = sub.add_parser("build-site", help="build the dashboard into public/")
    p_site.add_argument("--out", type=Path, default=runner.ROOT / "public")

    args = parser.parse_args(argv)
    if args.command == "run":
        return runner.run(args.config, args.state, args.date, alerts=not args.no_alerts)
    if args.command == "heartbeat":
        return runner.heartbeat(args.config, args.state)
    if args.command == "backfill":
        return runner.backfill(args.days, args.config, args.state, force=args.force)
    if args.command == "build-site":
        site.build(args.out, args.config, args.state)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
