"""Evaluation-only dispatch boundary around the product's Soon candidate."""
import asyncio
import json
import re

import httpx

from tests.eval.trajectory.stub import ImageBudget, SafetyViolation

MODEL = 'gemini-3.1-flash-image-preview'
HOST = 'api.soonstudio.ai'
SUBMIT = f'https://{HOST}/v1/images/generations?async=true'
_POLL_PREFIX = f'https://{HOST}/v1/images/tasks/'
_TASK_ID = re.compile(r'[A-Za-z0-9_:-][A-Za-z0-9_.:-]{0,199}')
_PAYLOAD_FIELDS = {'model', 'prompt', 'response_format', 'n', 'image', 'size', 'aspect_ratio', 'resolution', 'image_size'}


class SoonBudgetExhausted(SafetyViolation):
    """The first eligible POST was not sent; the caller may use its normal stub."""


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('duplicate JSON fields')
    return fields


class SoonPostBudget(httpx.AsyncBaseTransport):
    """One evaluation-owned transport per logical image, never reused for another job."""

    def __init__(self, upstream, *, budget, scenario, journal):
        if scenario not in ImageBudget.COLUMNS:
            raise ValueError('Unknown Soon budget scope')
        self.upstream, self.budget, self.scenario, self.journal = upstream, budget, scenario, journal
        self.claimed = False
        self.dispatched = False
        self.poll_url = None
        self.polls = 0

    def _blocked(self, reason, error=SafetyViolation):
        self.journal.emit('soon_submit_blocked', scenario=self.scenario, reason=reason)
        raise error(f'Soon evaluation refused request: {reason}')

    async def handle_async_request(self, request):
        if request.method == 'GET' and str(request.url) == self.poll_url:
            return await self._poll(request)
        if request.method != 'POST' or str(request.url) != SUBMIT:
            self._blocked('unexpected_destination')
        if self.claimed:
            self._blocked('repeat_post')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            self._blocked('unsupported_payload')
        try:
            body = json.loads(await request.aread(), object_pairs_hook=_unique_fields)
        except (ValueError, TypeError):
            self._blocked('unsupported_payload')
        if (not isinstance(body, dict) or not body.keys() <= _PAYLOAD_FIELDS or type(body.get('n')) is not int
                or body['n'] != 1 or body.get('model') != MODEL or body.get('response_format') != 'url'
                or not isinstance(body.get('prompt'), str) or not body['prompt'].strip()):
            self._blocked('unsupported_payload')
        # Claim before waiting on SQLite so simultaneous sends cannot share a single-image scope.
        if self.claimed:
            self._blocked('repeat_post')
        self.claimed = True
        if not await asyncio.to_thread(self.budget.reserve, self.scenario):
            self._blocked('budget_exhausted', SoonBudgetExhausted)
        self.journal.emit('soon_submit', scenario=self.scenario, method='POST', images=1, model=MODEL)
        self.dispatched = True
        try:
            response = await self.upstream.handle_async_request(request)
        except BaseException:
            self.journal.emit('soon_submit_outcome', scenario=self.scenario, status='unknown')
            raise
        self.journal.emit('soon_submit_outcome', scenario=self.scenario, status=response.status_code)
        if response.is_success:
            from app.core import extract_task_id

            try:
                await response.aread()
                task_id = extract_task_id(response.json())
            except (ValueError, TypeError, AttributeError):
                task_id = None
            if task_id and _TASK_ID.fullmatch(task_id):
                self.poll_url = _POLL_PREFIX + task_id
        return response

    async def _poll(self, request):
        if self.polls >= 120:
            self._blocked('poll_limit')
        self.polls += 1
        # A transport error or failed HTTP poll must not retry automatically.
        url, self.poll_url = self.poll_url, None
        try:
            response = await self.upstream.handle_async_request(request)
        except BaseException:
            self.journal.emit('soon_poll', scenario=self.scenario, status='unknown')
            raise
        self.journal.emit('soon_poll', scenario=self.scenario, status=response.status_code)
        if response.is_success:
            self.poll_url = url
        return response

    async def aclose(self):
        await self.upstream.aclose()
