"""Local, redacted exception evidence. Never sent as agent context."""
import json
import logging
import os
from pathlib import Path
import traceback
import uuid

LOGGER = logging.getLogger(__name__)


def record_exception(directory, exc, *, model=None):
    # Do not capture frame locals: these can contain credentials and final data.
    detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    redact = getattr(model, 'redact', lambda value: value)
    detail = redact(detail)
    from .redaction import redact as redact_credentials
    detail = redact_credentials(detail)
    for name, value in os.environ.items():
        if value and any(marker in name.upper() for marker in ('KEY', 'SECRET', 'TOKEN', 'PASSWORD')):
            detail = detail.replace(value, '[REDACTED]')
    LOGGER.error('Unexpected task exception:\n%s', detail)
    relative = 'diagnostics/' + uuid.uuid4().hex + '.json'
    result = {'error_type': type(exc).__name__}
    try:
        target = Path(directory) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({**result, 'traceback': detail}, ensure_ascii=False, indent=2), encoding='utf-8')
        result['diagnostic_path'] = relative
    except OSError as write_error:
        # A full disk must not prevent the service from attempting a terminal state.
        result['diagnostic_write_error'] = type(write_error).__name__
        LOGGER.error('Could not persist exception evidence: %s', type(write_error).__name__)
    return result
