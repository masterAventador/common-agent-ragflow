"""Thread-safe access to python-tika's lazily started local server."""

import threading

from tika import parser as _tika_parser


_startup_lock = threading.Lock()
_startup_complete = False


def _from_buffer(blob):
    return _tika_parser.from_buffer(blob)


def from_buffer(blob):
    """Serialize the first successful local Tika startup in this process.

    python-tika 2.6 checks port 9998 and starts its Java server lazily without
    synchronizing callers. Concurrent cold calls can therefore launch multiple
    servers. Once one guarded parse succeeds, later calls retain normal Tika
    request concurrency. A failed startup remains retryable under the lock.
    """
    global _startup_complete

    if _startup_complete:
        return _from_buffer(blob)

    with _startup_lock:
        if _startup_complete:
            return _from_buffer(blob)
        parsed = _from_buffer(blob)
        _startup_complete = True
        return parsed


def _reset_tika_startup_state_for_test():
    global _startup_complete

    with _startup_lock:
        _startup_complete = False
