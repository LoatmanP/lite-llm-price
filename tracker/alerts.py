"""Alert delivery. One message per call.

alert_target in config:
  {"type": "slack", "webhook_env": "SLACK_WEBHOOK_URL"}  post to a Slack incoming webhook
  {"type": "log"}                                        print to the job log only

The webhook URL is read from the environment (a masked CI/CD variable), never from
the committed config. With type "slack" and the variable unset, alerts fall back
to the job log so a local run never fails for want of a webhook.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


class AlertError(RuntimeError):
    pass


class Alerter:
    def __init__(self, target: dict | None, enabled: bool = True):
        self.target = target or {"type": "log"}
        self.enabled = enabled
        self.sent: list[str] = []
        self.failures: list[str] = []

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        self.sent.append(text)
        print("----- alert -----\n" + text + "\n-----------------", flush=True)
        if self.target.get("type") != "slack":
            return
        url = os.environ.get(self.target.get("webhook_env", "SLACK_WEBHOOK_URL"), "")
        if not url:
            return
        body = json.dumps({"text": f"```\n{text}\n```"}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
        except Exception as exc:  # keep going; the run reports failures at the end
            msg = f"alert delivery failed: {exc}"
            print(msg, file=sys.stderr)
            self.failures.append(msg)
