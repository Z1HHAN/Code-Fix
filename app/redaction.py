"""Credentials observed in this task context; never read extra secrets to redact."""
from contextlib import contextmanager
from contextvars import ContextVar

_secrets = ContextVar('task_secrets', default=frozenset())


def register_secret(value):
    if isinstance(value, str) and value:
        _secrets.set(_secrets.get() | {value})
    return value


def redact(value):
    if isinstance(value, str):
        for secret in sorted(_secrets.get(), key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
    elif isinstance(value, dict):
        value = {k: redact(v) for k, v in value.items()}
    elif isinstance(value, list):
        value = [redact(v) for v in value]
    return value


@contextmanager
def credential_scope():
    token = _secrets.set(frozenset())
    try:
        yield
    finally:
        _secrets.reset(token)
