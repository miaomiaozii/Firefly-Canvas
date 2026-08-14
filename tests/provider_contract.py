from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def load_provider_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("provider contract fixture must be a JSON object")
    return contract


def normalize_captured_request(request: dict[str, Any]) -> dict[str, Any]:
    files = []
    for field, (filename, payload, mime_type) in request.get("files") or []:
        files.append({
            "field": str(field),
            "filename": str(filename),
            "mime_type": str(mime_type),
            "sha256": hashlib.sha256(bytes(payload)).hexdigest(),
        })
    return {
        "method": str(request["method"]).upper(),
        "url": str(request["url"]),
        "header_names": sorted(str(name).lower() for name in (request.get("headers") or {})),
        "json": copy.deepcopy(request.get("json")),
        "data": copy.deepcopy(request.get("data")),
        "files": files,
    }


class ContractResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self._payload = copy.deepcopy(payload)

    def json(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)

    def raise_for_status(self) -> None:
        return None


class ProviderContractCapture:
    def __init__(self, response_payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.requests: list[dict[str, Any]] = []
        payloads = response_payload if isinstance(response_payload, list) else [response_payload]
        self._response_payloads = copy.deepcopy(payloads)

    def async_client(self, *args, **kwargs):
        return _CapturingAsyncClient(self, *args, **kwargs)

    def only_request(self) -> dict[str, Any]:
        if len(self.requests) != 1:
            raise AssertionError(f"expected exactly one provider request, observed {len(self.requests)}")
        return self.requests[0]

    def next_response(self) -> ContractResponse:
        if not self._response_payloads:
            raise AssertionError("provider contract response script exhausted")
        return ContractResponse(self._response_payloads.pop(0))


class _CapturingAsyncClient:
    def __init__(self, capture: ProviderContractCapture, *args, **kwargs) -> None:
        del args, kwargs
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False

    @staticmethod
    def _freeze_files(files) -> list[tuple[str, tuple[str, bytes, str]]]:
        frozen = []
        for field, upload in files or []:
            filename, stream, mime_type = upload
            if hasattr(stream, "read"):
                payload = stream.read()
            else:
                payload = bytes(stream)
            frozen.append((str(field), (str(filename), bytes(payload), str(mime_type))))
        return frozen

    def _capture(self, method: str, url: str, **kwargs) -> None:
        self.capture.requests.append({
            "method": method.upper(),
            "url": str(url),
            "headers": dict(kwargs.get("headers") or {}),
            "json": copy.deepcopy(kwargs.get("json")),
            "data": copy.deepcopy(kwargs.get("data")),
            "files": self._freeze_files(kwargs.get("files")),
        })

    async def post(self, url: str, **kwargs) -> ContractResponse:
        self._capture("POST", url, **kwargs)
        return self.capture.next_response()

    async def get(self, url: str, **kwargs) -> ContractResponse:
        self._capture("GET", url, **kwargs)
        return self.capture.next_response()

    async def request(self, method: str, url: str, **kwargs) -> ContractResponse:
        self._capture(method, url, **kwargs)
        return self.capture.next_response()

    def build_request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        return {"method": method, "url": url, "kwargs": kwargs}

    async def send(self, request: dict[str, Any]) -> ContractResponse:
        self._capture(request["method"], request["url"], **request["kwargs"])
        return self.capture.next_response()
