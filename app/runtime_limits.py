"""Versioned runtime-limit cache, invalidated by file metadata changes."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from .config import read_json

LIMITS_PATH = Path(__file__).resolve().parents[1] / 'configs/runtime_limits.json'


@lru_cache(maxsize=4)
def _load(path, modified_ns, changed_ns, size, inode):
    return read_json(Path(path))


def runtime_limits():
    path = LIMITS_PATH
    stat = path.stat()
    # Return an independent mapping; callers must not mutate cached defaults.
    return deepcopy(_load(str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
