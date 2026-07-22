import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rag.utils import tika_parser


def test_first_tika_parse_is_serialized(monkeypatch):
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_from_buffer(blob):
        calls.append(blob)
        if blob == b"first":
            first_entered.set()
            assert release_first.wait(timeout=2)
        return {"content": str(blob)}

    monkeypatch.setattr(tika_parser, "_from_buffer", fake_from_buffer)
    tika_parser._reset_tika_startup_state_for_test()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(tika_parser.from_buffer, b"first")
        assert first_entered.wait(timeout=2)
        second = executor.submit(tika_parser.from_buffer, b"second")

        assert calls == [b"first"]
        release_first.set()
        assert first.result(timeout=2)["content"] == "b'first'"
        assert second.result(timeout=2)["content"] == "b'second'"


def test_failed_startup_stays_serialized_and_can_be_retried(monkeypatch):
    calls = []

    def fake_from_buffer(blob):
        calls.append(blob)
        if len(calls) == 1:
            raise RuntimeError("startup failed")
        return {"content": "ready"}

    monkeypatch.setattr(tika_parser, "_from_buffer", fake_from_buffer)
    tika_parser._reset_tika_startup_state_for_test()

    try:
        tika_parser.from_buffer(b"first")
    except RuntimeError as error:
        assert str(error) == "startup failed"
    else:
        raise AssertionError("failed Tika startup unexpectedly succeeded")

    assert tika_parser.from_buffer(b"second") == {"content": "ready"}
    assert calls == [b"first", b"second"]


def test_tika_parse_is_concurrent_after_first_success(monkeypatch):
    active = 0
    max_active = 0
    both_entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()

    monkeypatch.setattr(tika_parser, "_from_buffer", lambda blob: {"content": str(blob)})
    tika_parser._reset_tika_startup_state_for_test()
    tika_parser.from_buffer(b"warmup")

    def fake_from_buffer(blob):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_entered.set()
        assert release.wait(timeout=2)
        with state_lock:
            active -= 1
        return {"content": str(blob)}

    monkeypatch.setattr(tika_parser, "_from_buffer", fake_from_buffer)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(tika_parser.from_buffer, b"first")
        second = executor.submit(tika_parser.from_buffer, b"second")
        assert both_entered.wait(timeout=2)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert max_active == 2


def test_every_production_tika_call_uses_the_startup_guard():
    repository_root = Path(__file__).resolve().parents[4]
    guarded_callers = (
        "rag/flow/parser/parser.py",
        "rag/app/laws.py",
        "rag/app/naive.py",
        "rag/app/presentation.py",
        "rag/app/book.py",
        "rag/app/one.py",
    )

    for relative_path in guarded_callers:
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert "from tika import parser as tika_parser" not in source
        assert "from rag.utils import tika_parser" in source
        assert "tika_parser.from_buffer" in source
