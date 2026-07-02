#!/usr/bin/env python3
import json
import os
import time
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


def write_json(path, value):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    data = json.dumps(value, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(data)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
