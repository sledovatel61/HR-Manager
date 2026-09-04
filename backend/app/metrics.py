"""Minimal in-process metrics for the ops contour (roadmap phase 7).

A tiny thread-safe registry exposes Prometheus-style text metrics WITHOUT
adding an observability platform dependency. Only aggregate signals are
recorded — never URL query strings, cookies, headers, bodies or any other
personal data, so the ``/ops/metrics`` endpoint contains no PII.

Metrics
-------
``hr_manager_http_requests_total{method,path,status}``   request counter
``hr_manager_http_errors_total{method,path,status}``     non-2xx/3xx counter
``hr_manager_http_duration_seconds_bucket{path,le}``     latency histogram
``hr_manager_uptime_seconds``                            process uptime
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from threading import Lock

from starlette.requests import Request
from starlette.responses import Response

START_TIME = time.monotonic()

# Latency histogram buckets (seconds).
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_lock = Lock()
_requests: Counter[tuple[str, str, str]] = Counter()
_latencies: Counter[tuple[str, float]] = Counter()


def route_label(request: Request) -> str:
    """Return the route template (never the raw URL with query string)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return "__unmatched__"


def uptime_seconds() -> float:
    """Process uptime in seconds (monotonic clock)."""
    return time.monotonic() - START_TIME


def observe_request(request: Request, response: Response, elapsed_seconds: float) -> None:
    """Record one finished HTTP exchange (called by the middleware)."""
    method = request.method
    path = route_label(request)
    status = str(response.status_code)
    with _lock:
        _requests[(method, path, status)] += 1
        for bucket in _BUCKETS:
            if elapsed_seconds <= bucket:
                _latencies[(path, bucket)] += 1


def render() -> str:
    """Render the registry in the Prometheus text exposition format."""
    now = time.monotonic()
    with _lock:
        requests = list(_requests.items())
        latencies = list(_latencies.items())

    lines = ["# HELP hr_manager_uptime_seconds Process uptime in seconds."]
    lines.append("# TYPE hr_manager_uptime_seconds gauge")
    lines.append(f"hr_manager_uptime_seconds {now - START_TIME:.3f}")
    lines.append(
        "# HELP hr_manager_http_requests_total HTTP requests by method, route template "
        "and status class."
    )
    lines.append("# TYPE hr_manager_http_requests_total counter")
    for (method, path, status), count in sorted(requests):
        lines.append(
            f'hr_manager_http_requests_total{{method="{method}",path="{path}",'
            f'status="{status}"}} {count}'
        )
    lines.append(
        "# HELP hr_manager_http_errors_total HTTP errors (status >= 400) by route template."
    )
    lines.append("# TYPE hr_manager_http_errors_total counter")
    error_totals: dict[tuple[str, str], int] = defaultdict(int)
    for (method, path, status), count in requests:
        if status.isdigit() and int(status) >= 400:
            error_totals[(method, path)] += count
    for (method, path), count in sorted(error_totals.items()):
        lines.append(f'hr_manager_http_errors_total{{method="{method}",path="{path}"}} {count}')
    lines.append(
        "# HELP hr_manager_http_duration_seconds_bucket Latency histogram buckets "
        "by route template."
    )
    lines.append("# TYPE hr_manager_http_duration_seconds_bucket histogram")
    for (path, bucket), count in sorted(latencies):
        lines.append(
            f'hr_manager_http_duration_seconds_bucket{{path="{path}",le="{bucket:g}"}} {count}'
        )
    return "\n".join(lines) + "\n"
