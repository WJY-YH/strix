"""Entrypoint for the private Strix runner."""

from __future__ import annotations

import signal
import threading

from deploy.runner.app import create_server
from deploy.runner.config import RunnerConfig
from deploy.runner.jobs import ScanManager
from deploy.runner.preflight import collect_preflight


def main() -> None:
    config = RunnerConfig.from_env()
    manager = ScanManager(config)
    server = create_server(config, manager, lambda: collect_preflight(config))

    def request_shutdown(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
