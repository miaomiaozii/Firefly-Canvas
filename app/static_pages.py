"""Serve a fresh asset URL namespace per Firefly server process (#452)."""
import re
from pathlib import Path
from uuid import uuid4

from starlette.responses import Response
from starlette.staticfiles import StaticFiles

# Keep the checked-in ?v= as a fallback for consumers that read HTML directly.
# Only replace the version value; scoped revisions, other parameters and fragments
# must survive byte-for-byte (including HTML entities such as &amp;).
_ASSET_VERSION = re.compile(rb"""(\s(?:href|src)\s*=\s*["']/static/[^"'?#<>\s]+\.(?:css|js)\?v=)[^"'&#<>\s]+""")
_RUNTIME_VERSION = str(uuid4().int).encode("ascii")


def html_response(path, *, head: bool = False, status_code: int = 200) -> Response:
    body = _ASSET_VERSION.sub(rb"\g<1>" + _RUNTIME_VERSION, Path(path).read_bytes())
    # A file-mtime validator could return 304 after a restart even though the
    # rendered page has a new version. Re-fetch the HTML, not the old cached body.
    return Response(
        b"" if head else body,
        status_code=status_code,
        media_type="text/html",
        headers={"Cache-Control": "no-cache", "Content-Length": str(len(body))},
    )


class FireflyStaticFiles(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        # Let StaticFiles handle path validation and retain its normal HTTP
        # behavior (ETag, Range, etc.) for JS, CSS and other unmodified files.
        if Path(full_path).suffix.lower() == ".html":
            return html_response(full_path, head=scope["method"] == "HEAD", status_code=status_code)
        return super().file_response(full_path, stat_result, scope, status_code)
