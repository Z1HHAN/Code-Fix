"""Trusted bootstrap: wait for controller job assignment before spawning."""
import subprocess
import sys

if __name__ == '__main__':
    if sys.stdin.buffer.readline(3) != b'go\n':
        raise SystemExit(2)
    raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))
