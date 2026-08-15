"""HTTP adapter that keeps project-media download ownership for the response."""
from __future__ import annotations

from typing import Any

from fastapi.responses import FileResponse

from app.project_activity import begin_project_download, finish_project_download


class ProjectMediaFileResponse(FileResponse):
    """Hold a cleanup owner until ASGI file delivery finishes or aborts."""

    def __init__(self, path: Any, *, project_id: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._project_id = str(project_id or "").strip()
        self._download_released = False
        begin_project_download(self._project_id)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._download_released:
                self._download_released = True
                finish_project_download(self._project_id)
