"""Cooperative process termination, installed only by the CLI."""

import signal


class ExtractionStopped(Exception):
    """Extraction was stopped at a request/checkpoint boundary."""


class GracefulStop:
    def __init__(self):
        self.requested = False
        self.previous = {}

    def _handle(self, signum, frame):
        # Do not raise, log, acquire locks, or perform I/O in a signal handler.
        self.requested = True

    def __enter__(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            self.previous[signum] = signal.signal(signum, self._handle)
        return self

    def __exit__(self, *exc):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)
