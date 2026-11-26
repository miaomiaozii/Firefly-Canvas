"""Evaluation-only dispatch boundary around the product's Soon candidate."""
import asyncio
import json

import httpx

from tests.eval.trajectory.stub import ImageBudget, SafetyViolation

MODEL = 'gemini-3.1-flash-image-preview'
SUBMIT = 'https://api.soonstudio.ai/v1/images/generations?async=true'
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

    def _blocked(self, reason, error=SafetyViolation):
        self.journal.emit('soon_submit_blocked', scenario=self.scenario, reason=reason)
        raise error(f'Soon evaluation refused request: {reason}')

    async def handle_async_request(self, request):
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
        try:
            response = await self.upstream.handle_async_request(request)
        except BaseException:
            self.journal.emit('soon_submit_outcome', scenario=self.scenario, status='unknown')
            raise
        self.journal.emit('soon_submit_outcome', scenario=self.scenario, status=response.status_code)
        return response

    async def aclose(self):
        await self.upstream.aclose()
