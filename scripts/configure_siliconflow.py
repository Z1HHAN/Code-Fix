"""Interactive hidden input. No key accepted via command arguments or printed."""
import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_credentials import save_local

if __name__ == '__main__':
    keys = {f'CODE_FIX_SF_ACCOUNT{n}_KEY': getpass.getpass(f'Account {n} API key (hidden): ') for n in (1, 2)}
    try:
        save_local(keys)
        print('Saved two keys with Windows DPAPI for this user only; no paid API call.')
    except (ValueError, OSError) as exc:
        print('Configuration blocked: ' + type(exc).__name__ + '; existing credentials are never overwritten.')
        raise SystemExit(2)
