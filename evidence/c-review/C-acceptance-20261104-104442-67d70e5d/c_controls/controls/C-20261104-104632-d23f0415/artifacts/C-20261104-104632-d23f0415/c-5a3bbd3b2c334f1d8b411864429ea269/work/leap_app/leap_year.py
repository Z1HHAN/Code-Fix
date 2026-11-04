import sys
import time

def wait_forever_for_control():
    print("C_TIMEOUT_STDOUT_FLUSHED", flush=True)
    print("C_TIMEOUT_STDERR_FLUSHED", file=sys.stderr, flush=True)
    time.sleep(30)
