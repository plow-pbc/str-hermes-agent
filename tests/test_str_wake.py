"""Behavior tests for bin/str_wake.py, the wake writer and drain alarm the pollers share."""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("str_wake", ROOT / "bin" / "str_wake.py")
str_wake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(str_wake)


def test_wake_is_atomic_and_shaped(tmp_path):
    p = str_wake.wake(tmp_path, "cht_x", "Turnover", "hello")
    assert p.parent == tmp_path / "plow_chat_wakes" and p.suffix == ".json"
    assert p.name.startswith("turnover-")
    assert json.loads(p.read_text()) == {"chat": "cht_x", "from": "Turnover", "text": "hello"}
    assert not list(p.parent.glob("*.tmp"))


def test_rejected_wake_raises(tmp_path):
    inbox = tmp_path / "plow_chat_wakes"
    inbox.mkdir()
    (inbox / "turnover-1.rejected").write_text("{}")
    with pytest.raises(RuntimeError, match="turnover-1.rejected"):
        str_wake.require_drained(tmp_path)


def test_stale_json_raises_fresh_does_not(tmp_path):
    p = str_wake.wake(tmp_path, "c", "Hostex", "t")
    str_wake.require_drained(tmp_path, now=p.stat().st_mtime + 599)
    with pytest.raises(RuntimeError):
        str_wake.require_drained(tmp_path, now=p.stat().st_mtime + 601)


def test_missing_inbox_is_drained(tmp_path):
    str_wake.require_drained(tmp_path)
