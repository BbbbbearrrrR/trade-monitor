#!/usr/bin/env python3
import json
import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def read_json(path, default, retries=3, delay=0.05):
    path = Path(path)
    if not path.exists():
        return default
    for attempt in range(retries):
        try:
            text = path.read_text("utf-8")
            if not text.strip():
                raise json.JSONDecodeError("empty json file", text, 0)
            return json.loads(text)
        except json.JSONDecodeError:
            if attempt == retries - 1:
                return default
            time.sleep(delay)
    return default


@contextmanager
def _lock(path):
    path = Path(path)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_json(path, value):
    path = Path(path)
    data = json.dumps(value, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path, value):
    with _lock(path):
        _write_json(path, value)


def update_json(path, default, updater):
    """Atomically apply updater to the latest value and return the stored value."""
    with _lock(path):
        current = read_json(path, default)
        updated = updater(current)
        if updated is None:
            updated = current
        _write_json(path, updated)
        return updated
