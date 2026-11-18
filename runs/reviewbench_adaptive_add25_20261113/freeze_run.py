from functools import cache
from pathlib import Path

from experiment import ROOT, load, sha


@cache
def check(path, stamp, expected):
    if sha(path) != expected:
        raise ValueError(f'Frozen input changed: {path}')


def verify():
    record = load(ROOT/'freeze.json')
    for path, expected in record['hashes'].items():
        stat = Path(path).stat()
        check(path,(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns),expected)
    return record
