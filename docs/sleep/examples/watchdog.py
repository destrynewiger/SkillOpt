#!/usr/bin/env python3
"""Minimal supervisor that runs the SkillOpt-Sleep cycle on a fixed interval.

Sanitized example (see docs/sleep/openai-compatible-endpoints.md). On Windows,
register this under a Scheduled Task so it survives logout; on Linux/macOS a
systemd timer or cron entry serves the same purpose and is usually preferable to
a long-lived process.
"""
import os
import sys
import time
import subprocess
import datetime
import traceback

INTERVAL_SECONDS = int(os.environ.get("SKILLOPT_WATCHDOG_INTERVAL", str(4 * 3600)))
RUNNER = os.environ.get("SKILLOPT_RUNNER", os.path.join(os.path.dirname(__file__), "runner.py"))
LOG_FILE = os.environ.get("SKILLOPT_WATCHDOG_LOG", "brain/watchdog.log")


def log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
    line = f"[{datetime.datetime.now().isoformat()}] {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def run_once() -> None:
    log("Invoking skillopt-sleep run via runner.py...")
    try:
        result = subprocess.run([sys.executable, RUNNER, "run"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            log("Successfully completed run.")
        else:
            log(f"Run failed (exit {result.returncode}).")
            log(f"STDERR: {result.stderr}")
    except Exception as e:
        log(f"Exception while running skillopt: {e}")
        log(traceback.format_exc())


def main() -> None:
    log(f"Watchdog started. Interval: {INTERVAL_SECONDS}s.")
    while True:
        try:
            run_once()
        except Exception as e:
            log(f"Unexpected error in watchdog loop: {e}")
        log(f"Sleeping for {INTERVAL_SECONDS}s...")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
