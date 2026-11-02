"""Finite mock model calls and a disabled OpenAI-compatible adapter.

No environment variable enables live calls. Candidate code is never written or
executed. This budget covers mock model attempts only, not a paid team ledger.
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from .model_context import ModelContext
from .repair_output import InvalidModelOutput, parse_locator_output, parse_repair_output
from .security import PermissionViolation, validate_changes


class TransportError(RuntimeError):
    pass


class LiveCallsBlocked(TransportError):
    pass


class CallBudget:
    """Share one instance across all mock calls in a single run."""
    def __init__(self, max_calls: int, max_attempts_per_call: int):
        if any(type(x) is not int or x <= 0 for x in (max_calls, max_attempts_per_call)):
            raise ValueError('调用与尝试上限必须为正整数')
        self.max_calls = max_calls
        self.max_attempts_per_call = max_attempts_per_call
        self._calls_used = 0
        self._lock = Lock()

    @property
    def calls_used(self):
        with self._lock:
            return self._calls_used

    def reserve(self):
        with self._lock:
            if self._calls_used >= self.max_calls:
                return False
            self._calls_used += 1
            return True


class MockTransport:
    mode = 'mock'

    def __init__(self, responses):
        self._responses = deque(responses)

    def send(self, messages):
        if not self._responses:
            raise TransportError('mock responses exhausted')
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, str):
            raise TransportError('mock response must be text')
        return response


class OpenAICompatibleTransport:
    """Request/normalization adapter only. Its send() is permanently blocked.

    prepare_client requires an explicitly supplied fake factory for offline
    tests; it never imports or constructs the real SDK in this release.
    """
    mode = 'live_disabled'

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float, base_url: str | None = None):
        if not isinstance(model, str) or not model.strip():
            raise ValueError('模型名不能为空')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds < float('inf'):
            raise ValueError('必须设置有限正请求超时')
        self._api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def prepare_client(self, *, sdk_factory=None):
        if sdk_factory is None:
            raise LiveCallsBlocked('真实 SDK 尚未启用；本轮仅允许离线 fake factory')
        options = {'api_key': self._api_key, 'timeout': self.timeout_seconds, 'max_retries': 0}
        if self.base_url:
            options['base_url'] = self.base_url
        return sdk_factory(**options)

    def build_request(self, messages):
        return {'model': self.model, 'messages': list(messages), 'timeout': self.timeout_seconds}

    @staticmethod
    def normalize_response(response):
        try:
            text = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise TransportError('SDK 响应缺少内容') from exc
        if not isinstance(text, str) or not text.strip():
            raise TransportError('SDK 响应内容为空')
        usage = getattr(response, 'usage', None)
        def tokens(name):
            value = getattr(usage, name, None)
            return value if type(value) is int and value >= 0 else None
        return {'raw_text': text, 'usage': {'input_tokens': tokens('prompt_tokens'), 'output_tokens': tokens('completion_tokens')},
                'cost_status': 'unknown', 'cost_amount': None}

    def send(self, messages):
        raise LiveCallsBlocked('本轮禁止真实付费调用；预算与隔离仍待确认')


class ModelClient:
    def __init__(self, transport=None, budget: CallBudget | None = None):
        self.transport = transport
        self.budget = budget

    def invoke(self, context: ModelContext, *, run_id: str, call_id: str) -> dict:
        started = time.monotonic()
        is_mock = type(self.transport) is MockTransport
        record = {'run_id': run_id, 'call_id': call_id, 'role': getattr(context, 'role', None),
                  'code_version': getattr(context, 'code_version', None), 'mode': 'mock' if is_mock else 'live_disabled',
                  'status': 'blocked', 'failure_class': 'live_calls_disabled', 'raw_text': None,
                  'parsed_output': None, 'attempts': 0, 'attempt_records': [], 'calls_used': 0,
                  'usage': {'input_tokens': None, 'output_tokens': None},
                  'cost_status': 'not_applicable' if is_mock else 'unknown', 'cost_amount': None,
                  'budget_scope': 'single_run_mock_model_attempts', 'model_api_called': False, 'duration_ms': 0}

        def finish(status, failure):
            record.update(status=status, failure_class=failure,
                          calls_used=self.budget.calls_used if isinstance(self.budget, CallBudget) else 0,
                          duration_ms=round((time.monotonic() - started) * 1000))
            return record

        if not is_mock:
            return finish('blocked', 'live_calls_disabled')
        if not isinstance(self.budget, CallBudget):
            return finish('blocked', 'budget_unconfirmed')
        if not isinstance(context, ModelContext) or context.role not in ('locator', 'repairer'):
            return finish('blocked', 'invalid_context')
        for attempt in range(1, self.budget.max_attempts_per_call + 1):
            if not self.budget.reserve():
                return finish('blocked', 'budget_exhausted')
            record['attempts'] += 1
            entry = {'attempt': attempt, 'status': 'failed', 'failure_class': 'model_transport_error'}
            record['attempt_records'].append(entry)
            try:
                raw = self.transport.send(context.messages)
            except TransportError as exc:
                # Exception messages may contain credentials or provider payloads.
                entry['error_type'] = type(exc).__name__
                continue
            except Exception as exc:
                entry.update(error_type=type(exc).__name__, failure_class='model_client_error')
                return finish('failed', 'model_client_error')
            record['raw_text'] = raw
            try:
                parser = parse_locator_output if context.role == 'locator' else parse_repair_output
                parsed = parser(raw, allowed_evidence_ids=context.evidence_ids,
                                allowed_knowledge_ids=context.knowledge_ids)
                changes = parsed['changes'] if context.role == 'repairer' else (
                    [{'path': parsed['file'], 'new_content': ''}] if parsed['file'] else [])
                if changes:
                    paths = validate_changes(changes, allowed_paths=context.allowed_paths,
                                             forbidden_paths=context.forbidden_paths, work_root=context.work_root)
                    if len(set(paths)) != len(paths):
                        raise PermissionViolation('路径归一化后重复')
            except InvalidModelOutput:
                entry['failure_class'] = 'invalid_model_output'
                return finish('failed', 'invalid_model_output')
            except (PermissionViolation, OSError):
                entry['failure_class'] = 'permission_violation'
                return finish('blocked', 'permission_violation')
            record['parsed_output'] = parsed
            entry.update(status='ok', failure_class=None)
            return finish('ok', None)
        return finish('failed', 'model_transport_error')
