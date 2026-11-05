"""Generate or verify the sha256sum-compatible manifest for the 2026-11-05 delivery batch.

The manifest lists repository-relative POSIX paths sorted by path, one line per file:
    <sha256>  <relative/path>

Usage:
    python scripts/evidence_sha256.py            # write evidence/2026-11-05/SHA256SUMS.txt
    python scripts/evidence_sha256.py --check    # recompute and compare against the manifest
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evidence/2026-11-05/SHA256SUMS.txt"

TARGETS = [
    "app/docker_runner.py",
    "app/split_runner.py",
    "docker/candidate_rpc.py",
    "scripts/docker_baselines.py",
    "scripts/docker_isolation_extra_tests.ps1",
    "scripts/docker_timeout_and_stop.py",
    "scripts/evidence_sha256.py",
    "scripts/p0_malicious_candidates.py",
    "tests/fixtures/malicious",
    "tests/fixtures/runtime",
    "evidence/p0-malicious/P0-20261105-013011-17d221c1",
    "evidence/baseline/BASELINE-20261105-122233-5c2c546e",
    "evidence/runtime/RUNTIME-20261105-123528-c590ec08",
    "docs/2026-11-03_项目持续维护/持续交付与变更记录.md",
]

SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect() -> list[tuple[str, Path]]:
    entries: dict[str, Path] = {}
    for target in TARGETS:
        path = ROOT / target
        if not path.exists():
            raise SystemExit(f"Missing manifest target: {target}")
        files = [path] if path.is_file() else [
            p for p in path.rglob("*")
            if p.is_file() and not (set(p.relative_to(path).parts) & SKIP_DIRS)
        ]
        for item in files:
            rel = item.relative_to(ROOT).as_posix()
            if item.resolve() == OUTPUT.resolve():
                continue
            entries[rel] = item
    return sorted(entries.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify instead of write")
    args = parser.parse_args()

    entries = collect()
    lines = [f"{sha256_file(p)}  {rel}" for rel, p in entries]

    if args.check:
        if not OUTPUT.exists():
            print(f"MISSING manifest: {OUTPUT}")
            return 1
        recorded = {}
        for line in OUTPUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            recorded[rel] = digest
        expected = {rel: sha256_file(path) for rel, path in entries}
        problems = []
        for rel in sorted(set(recorded) | set(expected)):
            if rel not in recorded:
                problems.append(f"NOT-RECORDED {rel}")
            elif rel not in expected:
                problems.append(f"NOT-ON-DISK {rel}")
            elif recorded[rel] != expected[rel]:
                problems.append(f"MISMATCH {rel}")
        for rel, digest in sorted(expected.items()):
            if recorded.get(rel) == digest:
                print(f"OK {rel}")
        for problem in problems:
            print(problem)
        print(f"checked={len(expected)} problems={len(problems)}")
        return 0 if not problems else 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    total = sum(p.stat().st_size for _, p in entries)
    print(f"wrote {OUTPUT} entries={len(entries)} bytes_hashed={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
