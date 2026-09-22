#!/usr/bin/env python3
"""Measure Home Assistant light state latency around a command.

This intentionally measures HA-observed state change, not photons hitting a
sensor. It is useful for comparing typed Hermes commands, voice-submitted
commands, and direct HA calls with the same target entity.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class StateSnapshot:
    state: str
    last_changed: str
    attributes: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def monotonic() -> float:
    return time.perf_counter()


def request_json(base_url: str, token: str, path: str, timeout: float) -> Any:
    req = Request(
        base_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def call_service(
    base_url: str,
    token: str,
    domain: str,
    service: str,
    payload: dict[str, Any],
    timeout: float,
) -> Any:
    req = Request(
        f"{base_url.rstrip('/')}/api/services/{domain}/{service}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def get_state(base_url: str, token: str, entity_id: str, timeout: float) -> StateSnapshot:
    data = request_json(base_url, token, f"/api/states/{entity_id}", timeout)
    return StateSnapshot(
        state=data["state"],
        last_changed=data["last_changed"],
        attributes=data.get("attributes", {}),
    )


def wait_for_state(
    base_url: str,
    token: str,
    entity_id: str,
    target_state: str,
    timeout: float,
    poll_interval: float,
    request_timeout: float,
) -> tuple[StateSnapshot | None, float | None, str | None]:
    deadline = monotonic() + timeout
    last_error = None
    while monotonic() < deadline:
        try:
            snapshot = get_state(base_url, token, entity_id, request_timeout)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = repr(exc)
        else:
            if snapshot.state == target_state:
                return snapshot, monotonic(), None
        time.sleep(poll_interval)
    return None, None, last_error


def run_command(command: str) -> tuple[int, float, str, str]:
    started = monotonic()
    completed = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
    )
    return completed.returncode, monotonic() - started, completed.stdout, completed.stderr


def format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default=os.getenv("HOMEASSISTANT_URL", "http://localhost:18124"))
    parser.add_argument("--token", default=os.getenv("HOMEASSISTANT_TOKEN"))
    parser.add_argument("--entity", required=True, help="Entity to watch, e.g. light.tv_lights")
    parser.add_argument("--target-state", required=True, choices=["on", "off"])
    parser.add_argument("--command", help="Shell command to run after t0, e.g. a Hermes oneshot")
    parser.add_argument(
        "--direct-ha-service",
        action="store_true",
        help="Call light.turn_on/off directly instead of running --command",
    )
    parser.add_argument("--brightness", type=int, default=255, help="Brightness for direct turn_on")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument(
        "--wait-for-enter",
        action="store_true",
        help="Wait for Enter before t0. Useful for voice/manual tests.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("HOMEASSISTANT_TOKEN is required, or pass --token")
    if bool(args.command) == bool(args.direct_ha_service):
        raise SystemExit("Pass exactly one of --command or --direct-ha-service")

    before = get_state(args.ha_url, args.token, args.entity, args.request_timeout)

    if args.wait_for_enter:
        input("Press Enter to start timing...")

    t0_mono = monotonic()
    t0_iso = now_iso()

    command_rc = None
    command_elapsed = None
    command_stdout = ""
    command_stderr = ""

    if args.direct_ha_service:
        service = "turn_on" if args.target_state == "on" else "turn_off"
        payload: dict[str, Any] = {"entity_id": args.entity}
        if service == "turn_on":
            payload["brightness"] = args.brightness
        call_service(args.ha_url, args.token, "light", service, payload, args.request_timeout)
    else:
        command_rc, command_elapsed, command_stdout, command_stderr = run_command(args.command)

    observed, observed_mono, observed_error = wait_for_state(
        args.ha_url,
        args.token,
        args.entity,
        args.target_state,
        args.timeout,
        args.poll_interval,
        args.request_timeout,
    )
    t_end_mono = monotonic()

    result = {
        "entity": args.entity,
        "target_state": args.target_state,
        "start_time_utc": t0_iso,
        "before": {
            "state": before.state,
            "last_changed": before.last_changed,
            "brightness": before.attributes.get("brightness"),
        },
        "observed": None
        if observed is None
        else {
            "state": observed.state,
            "last_changed": observed.last_changed,
            "brightness": observed.attributes.get("brightness"),
        },
        "command": args.command,
        "command_returncode": command_rc,
        "command_elapsed_seconds": command_elapsed,
        "observed_elapsed_seconds": None if observed_mono is None else observed_mono - t0_mono,
        "total_elapsed_seconds": t_end_mono - t0_mono,
        "observed_error": observed_error,
        "command_stdout": command_stdout.strip(),
        "command_stderr": command_stderr.strip(),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if observed is not None and (command_rc in (None, 0)) else 1

    print(f"Entity: {args.entity}")
    print(f"Target state: {args.target_state}")
    print(f"Start UTC: {t0_iso}")
    print(f"Before: {before.state} brightness={before.attributes.get('brightness')} last_changed={before.last_changed}")
    if args.command:
        print(f"Command elapsed: {format_seconds(command_elapsed)} rc={command_rc}")
    if observed:
        print(
            "Observed: "
            f"{observed.state} brightness={observed.attributes.get('brightness')} "
            f"last_changed={observed.last_changed}"
        )
        print(f"Command -> observed state: {format_seconds(observed_mono - t0_mono if observed_mono else None)}")
    else:
        print(f"Observed: target state not reached within {args.timeout}s")
        if observed_error:
            print(f"Last observation error: {observed_error}")
    print(f"Total measurement time: {format_seconds(t_end_mono - t0_mono)}")

    if command_stdout:
        print("\n--- command stdout ---")
        print(command_stdout.strip())
    if command_stderr:
        print("\n--- command stderr ---", file=sys.stderr)
        print(command_stderr.strip(), file=sys.stderr)

    return 0 if observed is not None and (command_rc in (None, 0)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
