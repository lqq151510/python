"""Summarize request and operation events from stdin; accepts logging prefixes."""

import json
import math
import sys
from collections import Counter
from collections.abc import Iterable


def summarize(lines: Iterable[str]) -> dict[str, object]:
    """Count terminal events without retaining request IDs or application data."""
    requests: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    latency: list[float] = []
    invalid_events = 0
    for line in lines:
        start = line.find('{"event":')
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            invalid_events += 1
            continue
        if not isinstance(event, dict):
            invalid_events += 1
            continue
        if event.get("event") == "api_request":
            status, elapsed = event.get("status_code"), event.get("elapsed_ms")
            if (
                not isinstance(status, int)
                or isinstance(status, bool)
                or not 100 <= status <= 599
                or not isinstance(elapsed, (int, float))
                or isinstance(elapsed, bool)
                or not math.isfinite(elapsed)
                or elapsed < 0
            ):
                invalid_events += 1
                continue
            requests["total"] += 1
            requests["client_errors"] += 400 <= status < 500
            requests["server_errors"] += status >= 500
            latency.append(elapsed)
        elif event.get("event") == "operation_completed":
            operation, state = event.get("operation"), event.get("state")
            if not isinstance(operation, str) or state not in ("succeeded", "failed"):
                invalid_events += 1
                continue
            operations[operation + ":" + state] += 1
    return {
        "requests": dict(requests),
        "operations": dict(operations),
        "invalid_events": invalid_events,
        "request_latency_ms": {
            "mean": round(sum(latency) / len(latency), 3) if latency else None,
            "max": max(latency) if latency else None,
        },
    }


if __name__ == "__main__":
    sys.stdout.write(
        json.dumps(summarize(sys.stdin), ensure_ascii=False, indent=2) + "\n"
    )
