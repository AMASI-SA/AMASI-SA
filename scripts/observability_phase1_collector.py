"""Opt-in, external stdlib collector. No scheduler or production configuration installed."""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, ProxyHandler

MAX_PAYLOAD = 65536
MAX_FILES = 8
FILE_BYTES = 4 * 1024 * 1024
RETENTION_SECONDS = 7 * 86400
MAX_TARGETS = 16
HISTOGRAMS = {"event_loop.lag"}
HISTOGRAMS.update(f"api.duration.{kind}" for kind in ("ready", "supplier_invoice", "shipping", "other"))
HISTOGRAMS.update(f"mongo.command.{kind}.{outcome}" for kind in ("find", "aggregate", "getMore", "write", "commitTransaction", "other") for outcome in ("ok", "error"))
HISTOGRAMS.update(f"mongo.pool.wait.{outcome}" for outcome in ("ok", "error"))
HISTOGRAMS.update(f"governor.wait.{scope}.{kind}" for scope in ("kind", "global") for kind in ("snapchat", "ads", "dashboard", "startup", "other"))
HISTOGRAMS.update(f"governor.hold.{kind}" for kind in ("snapchat", "ads", "dashboard", "startup", "other"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) <= 1e30


def sanitize(payload):
    """Ignore all arbitrary strings/keys and legacy diagnostics query shapes."""
    phase = payload.get("phase1", {}) if isinstance(payload, dict) else {}
    if not isinstance(phase, dict):
        return {}
    result = {"enabled": phase.get("enabled") is True}
    allowed = {
        "worker": {"pid", "started_at", "cpu_seconds", "rss_bytes", "uptime_seconds", "restart_count"},
        "api": {"active", "oldest_seconds", "overflow"},
        "counters": {"rejected_metric"} | {f"api.status.{code}" for code in ("1xx", "2xx", "3xx", "4xx", "500", "502", "503", "504", "5xx", "cancelled")},
    }
    for section, keys in allowed.items():
        raw = phase.get(section, {})
        if isinstance(raw, dict):
            result[section] = {key: raw[key] for key in keys if key in raw and number(raw[key])}
    histograms = {}
    raw = phase.get("histograms", {})
    if isinstance(raw, dict):
        for key in sorted(HISTOGRAMS):
            item = raw.get(key)
            if not isinstance(item, dict):
                continue
            clean = {k: item[k] for k in ("count", "sum", "p50", "p95", "p99") if k in item and number(item[k])}
            for k in ("bounds", "buckets"):
                values = item.get(k)
                if isinstance(values, list) and len(values) <= 64 and all(number(v) for v in values):
                    clean[k] = values
            histograms[key] = clean
    result["histograms"] = histograms
    for section, keys in {
        "memory": ("current_bytes", "max_bytes", "peak_bytes", "process_rss_bytes", "oom", "oom_kill"),
        "mongo": ("configured_max_pool_size", "active_connections", "checked_out_connections", "checkout_timeouts", "operation_timeouts"),
    }.items():
        raw = payload.get(section, {})
        if isinstance(raw, dict):
            result[section] = {k: raw[k] for k in keys if k in raw and number(raw[k])}
            if section == "memory" and isinstance(raw.get("events"), dict):
                result[section]["events"] = {k: raw["events"][k] for k in ("oom", "oom_kill", "high", "max") if k in raw["events"] and number(raw["events"][k])}
    result["governor"] = {k: payload[k] for k in ("global_heavy_capacity", "global_heavy_in_use", "global_heavy_available", "global_heavy_waiters") if k in payload and number(payload[k])}
    return result


def validate_origin(origin, *, private):
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("invalid origin")
    if private:
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ValueError("worker origin requires a private literal IP") from None
        if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
            raise ValueError("worker origin must be private")
        if parsed.scheme != "https" and not address.is_loopback:
            raise ValueError("nonloopback worker origins require HTTPS")
    elif parsed.scheme != "https":
        raise ValueError("external health origin requires HTTPS")
    return origin.rstrip("/")


def fetch(origin, path, token=None):
    started = time.monotonic()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Mezan-Diagnostics-Token"] = token
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(origin + path, headers=headers), timeout=2) as response:
            body = response.read(MAX_PAYLOAD + 1)
            if len(body) > MAX_PAYLOAD:
                result = {"outcome": "oversized", "status": response.status}
            else:
                result = {"outcome": "ok", "status": response.status}
                if token:
                    result["metrics"] = sanitize(json.loads(body))
    except HTTPError as exc:
        result = {"outcome": "http_error", "status": exc.code}
        exc.close()
    except Exception:
        result = {"outcome": "unavailable"}
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result


class BoundedStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def oldest_time(path):
        try:
            with path.open("rb") as stream:
                first = json.loads(stream.readline(MAX_PAYLOAD + 1))
            value = first.get("_stored_at")
            if number(value):
                return value
        except (ValueError, OSError, AttributeError):
            pass
        return path.stat().st_mtime

    def append(self, record):
        now = time.time()
        for path in self.directory.glob("phase1-*.jsonl"):
            if path.is_file() and now - self.oldest_time(path) >= RETENTION_SECONDS:
                path.unlink()
        encoded = (json.dumps({**record, "_stored_at": now}, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(encoded) > MAX_PAYLOAD:
            return
        active = self.directory / "phase1-0.jsonl"
        if active.exists() and (active.stat().st_size + len(encoded) > FILE_BYTES or now - self.oldest_time(active) >= 86400):
            oldest = self.directory / f"phase1-{MAX_FILES - 1}.jsonl"
            oldest.unlink(missing_ok=True)
            for index in range(MAX_FILES - 2, -1, -1):
                source = self.directory / f"phase1-{index}.jsonl"
                if source.exists():
                    source.replace(self.directory / f"phase1-{index + 1}.jsonl")
        with active.open("ab") as stream:
            stream.write(encoded)
        try:
            active.chmod(0o600)
        except OSError:
            pass


class WorkerBoots:
    """Observed boot changes only; unavailable samples never count as restarts."""
    def __init__(self):
        self.boots = {}
        self.changes = {}

    def annotate(self, alias, sample):
        worker = sample.get("metrics", {}).get("worker", {})
        boot = (worker.get("pid"), worker.get("started_at"))
        if not all(number(value) for value in boot):
            return sample
        if alias not in self.boots and len(self.boots) >= MAX_TARGETS:
            return sample
        changed = alias in self.boots and self.boots[alias] != boot
        self.changes[alias] = self.changes.get(alias, 0) + int(changed)
        self.boots[alias] = boot
        return {**sample, "observed_boot_change": changed, "observed_restarts": self.changes[alias]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--inventory")
    parser.add_argument("--output")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not args.enabled:
        return 0
    if args.interval < 30 or not args.inventory or not args.output:
        parser.error("enabled collector requires inventory/output and interval >= 30")
    token = os.environ.get("INTERNAL_DIAGNOSTICS_TOKEN", "")
    if not token:
        parser.error("diagnostics token environment variable required")
    try:
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        workers = inventory["workers"]
        if not isinstance(workers, dict) or not 1 <= len(workers) <= MAX_TARGETS:
            raise ValueError()
        targets = []
        for alias, origin in workers.items():
            if not re.fullmatch(r"worker-[0-9]{1,2}", alias):
                raise ValueError()
            targets.append((alias, validate_origin(origin, private=True)))
        health = validate_origin(inventory["health_origin"], private=False)
    except Exception:
        parser.error("invalid inventory; use bounded worker-N aliases and private IP origins")
    store = BoundedStore(args.output)
    boots = WorkerBoots()
    next_health = 0.0
    while True:
        for alias, origin in targets:
            sample = boots.annotate(alias, fetch(origin, "/api/health/diagnostics", token))
            store.append({"at": time.time(), "target": alias, **sample})
        if time.monotonic() >= next_health:
            store.append({"at": time.time(), "target": "external-health", **fetch(health, "/api/live")})
            next_health = time.monotonic() + 60
        if args.once:
            return 0
        time.sleep(args.interval)  # No catch-up/retries; one in-flight request total.


if __name__ == "__main__":
    raise SystemExit(main())
