"""Read-only Prometheus exporter for AXEL.

The trading process is intentionally not an HTTP service: it is a set of
short-lived scripts and long-running workers. This exporter therefore runs as a
separate, read-only process. It snapshots durable state (database + the
kill-switch/halt state files) on every scrape and never submits orders, mutates
risk state, or writes to the database.

Endpoints
---------
``/metrics``
    Prometheus text exposition (``text/plain; version=0.0.4``).
``/healthz``
    Process liveness. Always ``200`` while the exporter is running.
``/readyz``
    Readiness: the database must answer ``SELECT 1``. Returns ``503`` otherwise.
``/``
    Small human-readable index with links to the endpoints.

Failure semantics are fail-visible: a scrape that cannot read the database still
returns HTTP 200 with ``axel_exporter_last_scrape_success 0`` so the alert rule
fires, instead of silently disappearing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import perf_counter
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from axel.core.logging import logger
from axel.dashboard.metrics import collect_metrics, format_prometheus
from axel.execution.broker_base import BrokerAdapter
from axel.risk.halt import HaltController
from axel.risk.killswitch import KillSwitch

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_EXPORTER_VERSION = "0.1.0"


def _exporter_metrics(
    *, success: bool, duration_seconds: float, environment: str
) -> str:
    """Exporter self-metrics appended after the AXEL state metrics."""
    lines = [
        "# HELP axel_exporter_up 1 while the exporter process is serving requests",
        "# TYPE axel_exporter_up gauge",
        "axel_exporter_up 1",
        "# HELP axel_exporter_last_scrape_success 1 if the last state collection succeeded",
        "# TYPE axel_exporter_last_scrape_success gauge",
        f"axel_exporter_last_scrape_success {1 if success else 0}",
        "# HELP axel_exporter_scrape_duration_seconds Duration of the last collection",
        "# TYPE axel_exporter_scrape_duration_seconds gauge",
        f"axel_exporter_scrape_duration_seconds {duration_seconds:.6f}",
        "# HELP axel_environment_info Active AXEL environment",
        "# TYPE axel_environment_info gauge",
        f'axel_environment_info{{environment="{environment}"}} 1',
        "# HELP axel_exporter_build_info Exporter build information",
        "# TYPE axel_exporter_build_info gauge",
        f'axel_exporter_build_info{{version="{_EXPORTER_VERSION}"}} 1',
        "",
    ]
    return "\n".join(lines)


@dataclass
class MetricsExporter:
    """Snapshot AXEL state for Prometheus.

    ``session_factory`` is called per scrape and the session is always closed,
    so a failed scrape cannot leak a connection.
    """

    session_factory: Callable[[], Session]
    broker: BrokerAdapter
    kill_switch: KillSwitch
    halt: HaltController
    environment: str = "paper"
    live_enabled: bool = False

    def render(self) -> str:
        """Collect and render one scrape. Never raises for collection errors."""
        started = perf_counter()
        session: Session | None = None
        success = True
        try:
            session = self.session_factory()
            snapshot = collect_metrics(
                session,
                self.broker,
                self.kill_switch,
                None,
                halt=self.halt,
                live_enabled=self.live_enabled,
            )
            body = format_prometheus(snapshot)
        except Exception as exc:  # noqa: BLE001 - a scrape must never crash the server
            success = False
            logger.error("Metrics exporter scrape failed", extra={"error": str(exc)})
            body = (
                "# HELP axel_exporter_error 1 when the last scrape raised an exception\n"
                "# TYPE axel_exporter_error gauge\n"
                "axel_exporter_error 1\n"
            )
        finally:
            if session is not None:
                session.close()

        duration = perf_counter() - started
        return body + _exporter_metrics(
            success=success,
            duration_seconds=duration,
            environment=self.environment,
        )

    def ready(self) -> tuple[bool, str]:
        """Check the one dependency that makes the metrics meaningful: the DB."""
        session: Session | None = None
        try:
            session = self.session_factory()
            session.execute(text("SELECT 1"))
            return True, "ok"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        finally:
            if session is not None:
                session.close()


def make_handler(exporter: MetricsExporter) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to a specific exporter instance."""

    class MetricsHandler(BaseHTTPRequestHandler):
        server_version = "AXELMetricsExporter/0.1"

        def _write(self, status: int, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/metrics":
                self._write(200, exporter.render(), _PROMETHEUS_CONTENT_TYPE)
            elif path == "/healthz":
                self._write(200, "ok\n", "text/plain; charset=utf-8")
            elif path == "/readyz":
                ready, detail = exporter.ready()
                self._write(
                    200 if ready else 503,
                    f"{'ready' if ready else 'not ready'}: {detail}\n",
                    "text/plain; charset=utf-8",
                )
            elif path == "/":
                self._write(
                    200,
                    (
                        "<!doctype html><title>AXEL metrics exporter</title>"
                        "<h1>AXEL metrics exporter</h1>"
                        "<ul>"
                        '<li><a href="/metrics">/metrics</a></li>'
                        '<li><a href="/healthz">/healthz</a></li>'
                        '<li><a href="/readyz">/readyz</a></li>'
                        "</ul>"
                    ),
                    "text/html; charset=utf-8",
                )
            else:
                self._write(404, "not found\n", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args: object) -> None:
            logger.debug("Metrics exporter request", extra={"line": fmt % args})

    return MetricsHandler


def run_server(exporter: MetricsExporter, host: str, port: int) -> None:
    """Serve until interrupted. Intended for a dedicated container/process."""
    server = ThreadingHTTPServer((host, port), make_handler(exporter))
    server.daemon_threads = True
    logger.info("AXEL metrics exporter listening", extra={"host": host, "port": port})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("AXEL metrics exporter stopping")
    finally:
        server.server_close()
