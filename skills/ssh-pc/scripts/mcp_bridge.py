#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLIENT_NAME = "ssh-pc-skill"
CLIENT_VERSION = "2.0.0"
DEFAULT_SERVER = "mypc"
DEFAULT_TIMEOUT = 60
DEFAULT_FILE_CHUNK_BYTES = 4096
UNSAFE_FILE_DUMP_RE = re.compile(r"^\s*(?:cat|base64)\s+\S+\s*$")

REMOTE_FILE_STAT_SCRIPT = r"""
import hashlib, json, pathlib, sys

path = pathlib.Path(sys.argv[1]).expanduser()
if not path.exists():
    print(json.dumps({"exists": False, "path": str(path)}))
    raise SystemExit(0)

stat = path.stat()
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)

print(json.dumps({
    "exists": True,
    "path": str(path),
    "size": stat.st_size,
    "mode": format(stat.st_mode & 0o777, "04o"),
    "sha256": digest.hexdigest(),
}))
"""

REMOTE_FILE_CHUNK_SCRIPT = r"""
import base64, json, pathlib, sys

path = pathlib.Path(sys.argv[1]).expanduser()
offset = int(sys.argv[2])
chunk_size = int(sys.argv[3])

with path.open("rb") as handle:
    handle.seek(offset)
    data = handle.read(chunk_size)

print(json.dumps({
    "offset": offset,
    "count": len(data),
    "data_b64": base64.b64encode(data).decode("ascii"),
}))
"""

REMOTE_APPEND_B64_SCRIPT = r"""
import pathlib, sys

path = pathlib.Path(sys.argv[1]).expanduser()
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("ab") as handle:
    handle.write(sys.argv[2].encode("ascii"))
"""

REMOTE_DECODE_SCRIPT = r"""
import base64, hashlib, json, os, pathlib, sys

b64_path = pathlib.Path(sys.argv[1]).expanduser()
tmp_file = pathlib.Path(sys.argv[2]).expanduser()
mode = sys.argv[3]

data = base64.b64decode(b64_path.read_bytes())
tmp_file.parent.mkdir(parents=True, exist_ok=True)
tmp_file.write_bytes(data)
if mode:
    os.chmod(tmp_file, int(mode, 8))

print(json.dumps({
    "tmp_file": str(tmp_file),
    "size": len(data),
    "sha256": hashlib.sha256(data).hexdigest(),
}))
"""

REMOTE_FINALIZE_SCRIPT = r"""
import json, os, pathlib, shutil, sys

tmp_file = pathlib.Path(sys.argv[1]).expanduser()
target = pathlib.Path(sys.argv[2]).expanduser()
backup_path = sys.argv[3]

target.parent.mkdir(parents=True, exist_ok=True)
if backup_path and target.exists():
    backup = pathlib.Path(backup_path).expanduser()
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)

os.replace(tmp_file, target)
print(json.dumps({
    "target": str(target),
    "backup_path": backup_path or None,
}))
"""

REMOTE_CLEANUP_SCRIPT = r"""
import pathlib, sys

for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path).expanduser()
    try:
        if path.exists():
            path.unlink()
    except FileNotFoundError:
        pass
"""

REMOTE_READ_LINES_SCRIPT = r"""
import hashlib, json, pathlib, sys

path = pathlib.Path(sys.argv[1]).expanduser()
if not path.exists():
    print(json.dumps({"exists": False, "path": str(path)}))
    raise SystemExit(0)

start = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else 1
end = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None

with path.open("r", encoding="utf-8") as handle:
    lines = handle.readlines()

total = len(lines)
start_idx = max(0, start - 1)
end_idx = min(total, end) if end is not None else total

result_lines = []
for i in range(start_idx, end_idx):
    result_lines.append({"num": i + 1, "text": lines[i].rstrip("\n").rstrip("\r")})

digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)

print(json.dumps({
    "exists": True,
    "path": str(path),
    "total_lines": total,
    "start": start_idx + 1,
    "end": end_idx,
    "lines": result_lines,
    "sha256": digest.hexdigest(),
}))
"""

REMOTE_REPLACE_LINES_SCRIPT = r"""
import base64, hashlib, json, os, pathlib, shutil, sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1]).expanduser()
start = int(sys.argv[2])
end = int(sys.argv[3])
new_text_b64 = sys.argv[4]
backup = sys.argv[5].lower() == "true" if len(sys.argv) > 5 else True

new_text = base64.b64decode(new_text_b64).decode("utf-8")
new_lines = new_text.split("\n") if new_text else []

with path.open("r", encoding="utf-8") as handle:
    lines = handle.readlines()

total = len(lines)
start_idx = max(0, start - 1)
end_idx = min(total, end)

prev_digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        prev_digest.update(chunk)

result_lines = lines[:start_idx] + [l + "\n" for l in new_lines] + lines[end_idx:]
content = "".join(result_lines)
new_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

backup_path = ""
if backup and path.exists():
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = str(path) + ".bak." + ts
    shutil.copy2(path, pathlib.Path(backup_path))

tmp = pathlib.Path(str(path) + ".tmp." + os.urandom(4).hex())
tmp.write_text(content, encoding="utf-8")
if path.exists():
    os.chmod(tmp, path.stat().st_mode & 0o777)
os.replace(tmp, path)

print(json.dumps({
    "path": str(path),
    "replaced_lines": end_idx - start_idx,
    "new_line_count": len(new_lines),
    "total_lines": len(result_lines),
    "sha256": new_sha256,
    "backup_path": backup_path,
    "previous_sha256": prev_digest.hexdigest(),
}))
"""

REMOTE_INSERT_LINES_SCRIPT = r"""
import base64, hashlib, json, os, pathlib, shutil, sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1]).expanduser()
at_line = int(sys.argv[2])
new_text_b64 = sys.argv[3]
position = sys.argv[4] if len(sys.argv) > 4 else "after"
backup = sys.argv[5].lower() == "true" if len(sys.argv) > 5 else True

new_text = base64.b64decode(new_text_b64).decode("utf-8")
new_lines = new_text.split("\n") if new_text else []

with path.open("r", encoding="utf-8") as handle:
    lines = handle.readlines()

total = len(lines)
insert_idx = max(0, at_line) if position == "after" else max(0, at_line - 1)

prev_digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        prev_digest.update(chunk)

result_lines = lines[:insert_idx] + [l + "\n" for l in new_lines] + lines[insert_idx:]
content = "".join(result_lines)
new_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

backup_path = ""
if backup and path.exists():
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = str(path) + ".bak." + ts
    shutil.copy2(path, pathlib.Path(backup_path))

tmp = pathlib.Path(str(path) + ".tmp." + os.urandom(4).hex())
tmp.write_text(content, encoding="utf-8")
if path.exists():
    os.chmod(tmp, path.stat().st_mode & 0o777)
os.replace(tmp, path)

print(json.dumps({
    "path": str(path),
    "inserted_at_line": at_line,
    "position": position,
    "new_line_count": len(new_lines),
    "total_lines": len(result_lines),
    "sha256": new_sha256,
    "backup_path": backup_path,
    "previous_sha256": prev_digest.hexdigest(),
}))
"""

REMOTE_FILE_SEARCH_SCRIPT = r"""
import hashlib, json, pathlib, re, sys

path = pathlib.Path(sys.argv[1]).expanduser()
pattern = sys.argv[2]
context = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 2
max_matches = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else 20

if not path.exists():
    print(json.dumps({"exists": False, "path": str(path)}))
    raise SystemExit(0)

with path.open("r", encoding="utf-8") as handle:
    text = handle.read()
    lines = text.splitlines()

compiled = re.compile(pattern)
count = 0
matches = []

for match in compiled.finditer(text):
    count += 1
    if len(matches) >= max_matches:
        continue
    line_num = text[:match.start()].count("\n") + 1
    line_idx = line_num - 1
    start_ctx = max(0, line_idx - context)
    end_ctx = min(len(lines), line_idx + context + 1)
    ctx_before = [{"num": i + 1, "text": lines[i]} for i in range(start_ctx, line_idx)]
    ctx_after = [{"num": i + 1, "text": lines[i]} for i in range(line_idx + 1, end_ctx)]
    matches.append({
        "line": line_num,
        "text": lines[line_idx] if line_idx < len(lines) else "",
        "context_before": ctx_before,
        "context_after": ctx_after,
    })

digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)

print(json.dumps({
    "exists": True,
    "path": str(path),
    "pattern": pattern,
    "count": count,
    "returned": len(matches),
    "matches": matches,
    "sha256": digest.hexdigest(),
}))
"""


def normalize_base_url(value: str | None) -> str:
    if not value:
        raise SystemExit("Missing base URL. Pass --base-url or set SSH_PC_BASE_URL.")

    url = value.rstrip("/")
    if not url.endswith("/mcp"):
        url = f"{url}/mcp"
    return url


def build_remote_python_command(script: str, *args: object) -> str:
    # Do not inject "--" here. In `python -c`, CPython includes that literal
    # separator in sys.argv, which shifts the real arguments by one position.
    parts = ["python3", "-c", script, *[str(arg) for arg in args]]
    return " ".join(shlex.quote(part) for part in parts)


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def looks_like_unsafe_file_dump(command: str) -> bool:
    return bool(UNSAFE_FILE_DUMP_RE.match(command))


def _parse_http_payload(raw_body: bytes) -> Any:
    text = raw_body.decode("utf-8")
    stripped = text.strip()
    if not stripped:
        raise RuntimeError("Empty response from MCP server.")

    if stripped.startswith("data:") or "\ndata:" in stripped:
        candidates: list[str] = []
        for line in stripped.splitlines():
            if not line.startswith("data:"):
                continue
            candidate = line[5:].strip()
            if candidate and candidate != "[DONE]":
                candidates.append(candidate)
        for candidate in reversed(candidates):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise RuntimeError(f"Unable to parse streamable HTTP response: {stripped[:200]}")

    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse JSON response: {stripped[:200]}") from exc


def _unwrap_rpc_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if len(payload) != 1:
            raise RuntimeError(f"Unexpected batch RPC payload: {payload!r}")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected RPC payload type: {type(payload).__name__}")
    return payload


def extract_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    text_parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(item.get("text", ""))
    return "".join(text_parts)


class McpBridge:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        server: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = normalize_base_url(base_url or os.environ.get("SSH_PC_BASE_URL"))
        self.server = server or os.environ.get("SSH_PC_SERVER", DEFAULT_SERVER)
        self.timeout = timeout
        self.session_id: str | None = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _http_json(self, payload: dict[str, Any], *, include_session: bool, allow_empty: bool = False) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request = urllib.request.Request(self.base_url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read()
                maybe_session_id = response.headers.get("Mcp-Session-Id")
                if maybe_session_id:
                    self.session_id = maybe_session_id
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"MCP HTTP error {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unable to reach MCP server at {self.base_url}: {exc}") from exc

        if allow_empty and not response_body.strip():
            return {}

        parsed = _unwrap_rpc_payload(_parse_http_payload(response_body))
        if "error" in parsed:
            raise RuntimeError(f"MCP error: {parsed['error']}")
        return parsed

    def initialize(self) -> None:
        if self.session_id:
            return

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": CLIENT_NAME,
                    "version": CLIENT_VERSION,
                },
            },
        }
        self._http_json(payload, include_session=False)
        self._http_json(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            include_session=True,
            allow_empty=True,
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        response = self._http_json(payload, include_session=True)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected tool result payload: {response!r}")
        return result

    def ssh_execute(self, command: str, *, cwd: str | None = None, allow_unsafe_file_dump: bool = False) -> dict[str, Any]:
        if not allow_unsafe_file_dump and looks_like_unsafe_file_dump(command):
            raise RuntimeError(
                "Refusing to stream full file contents through ssh_execute.\n"
                "Use McpBridge.download_remote_file(), McpBridge.read_remote_text_file(),\n"
                "or scripts/pull_remote_file.py instead."
            )

        args: dict[str, Any] = {"server": self.server, "command": command}
        if cwd:
            args["cwd"] = cwd

        result = self.call_tool("ssh_execute", args)
        text = extract_text(result)
        if not text:
            raise RuntimeError("ssh_execute returned no text content.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ssh_execute returned non-JSON text: {text[:200]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Unexpected ssh_execute payload: {parsed!r}")
        return parsed

    def remote_file_stat(self, remote_path: str) -> dict[str, Any]:
        return run_remote_python_json(self, REMOTE_FILE_STAT_SCRIPT, remote_path)

    def download_remote_file(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        chunk_bytes: int = DEFAULT_FILE_CHUNK_BYTES,
    ) -> dict[str, Any]:
        destination = Path(local_path).expanduser()
        remote_meta = self.remote_file_stat(remote_path)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            offset = 0
            while offset < int(remote_meta["size"]):
                chunk = run_remote_python_json(
                    self,
                    REMOTE_FILE_CHUNK_SCRIPT,
                    remote_path,
                    offset,
                    chunk_bytes,
                )
                count = int(chunk["count"])
                if count <= 0:
                    raise RuntimeError(f"Remote transfer stalled at offset {offset}.")
                handle.write(base64.b64decode(chunk["data_b64"]))
                offset += count

        local_sha256 = sha256_file(destination)
        if local_sha256 != remote_meta["sha256"]:
            raise RuntimeError(
                "Checksum mismatch after download.\n"
                f"Remote: {remote_meta['sha256']}\n"
                f"Local:  {local_sha256}"
            )

        return {
            "local_path": str(destination),
            "remote_path": remote_meta["path"],
            "sha256": remote_meta["sha256"],
            "size": remote_meta["size"],
            "mode": remote_meta["mode"],
            "exists": remote_meta["exists"],
            "chunk_bytes": chunk_bytes,
        }

    def read_remote_file_bytes(
        self,
        remote_path: str,
        *,
        chunk_bytes: int = DEFAULT_FILE_CHUNK_BYTES,
    ) -> bytes:
        remote_meta = self.remote_file_stat(remote_path)
        data = bytearray()
        offset = 0
        while offset < int(remote_meta["size"]):
            chunk = run_remote_python_json(
                self,
                REMOTE_FILE_CHUNK_SCRIPT,
                remote_path,
                offset,
                chunk_bytes,
            )
            count = int(chunk["count"])
            if count <= 0:
                raise RuntimeError(f"Remote transfer stalled at offset {offset}.")
            data.extend(base64.b64decode(chunk["data_b64"]))
            offset += count

        payload = bytes(data)
        local_sha256 = sha256_bytes(payload)
        if local_sha256 != remote_meta["sha256"]:
            raise RuntimeError(
                "Checksum mismatch after download.\n"
                f"Remote: {remote_meta['sha256']}\n"
                f"Local:  {local_sha256}"
            )
        return payload

    def read_remote_text_file(
        self,
        remote_path: str,
        *,
        encoding: str = "utf-8",
        chunk_bytes: int = DEFAULT_FILE_CHUNK_BYTES,
    ) -> str:
        return self.read_remote_file_bytes(remote_path, chunk_bytes=chunk_bytes).decode(encoding)

    def upload_remote_file_atomic(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        expected_remote_sha256: str | None = None,
        remote_mode: str | None = None,
        chunk_bytes: int = DEFAULT_FILE_CHUNK_BYTES,
        force: bool = False,
        backup: bool = True,
    ) -> dict[str, Any]:
        source = Path(local_path).expanduser()
        if not source.is_file():
            raise RuntimeError(f"Local path is not a file: {source}")

        local_sha256 = sha256_file(source)
        remote_before = self.remote_file_stat(remote_path)
        if (
            not force
            and expected_remote_sha256
            and remote_before.get("exists")
            and remote_before.get("sha256") != expected_remote_sha256
        ):
            raise RuntimeError(
                "Remote file changed since pull. Refusing to overwrite.\n"
                f"Expected: {expected_remote_sha256}\n"
                f"Actual:   {remote_before.get('sha256')}\n"
                "Pass --force to override."
            )

        token = uuid.uuid4().hex[:12]
        target_name = Path(remote_path).name or "remote-file"
        remote_tmp_root = Path("/tmp/ssh-pc-bridge")
        remote_tmp_b64 = remote_tmp_root / f"{token}.b64"
        remote_tmp_file = remote_tmp_root / f"{token}.{target_name}"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = f"{remote_path}.bak.{timestamp}" if backup else ""

        try:
            self.ssh_execute(build_remote_python_command(REMOTE_CLEANUP_SCRIPT, str(remote_tmp_b64), str(remote_tmp_file)))

            with source.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_bytes)
                    if not chunk:
                        break
                    chunk_b64 = base64.b64encode(chunk).decode("ascii")
                    result = self.ssh_execute(
                        build_remote_python_command(REMOTE_APPEND_B64_SCRIPT, str(remote_tmp_b64), chunk_b64)
                    )
                    if result.get("code") != 0:
                        raise RuntimeError(
                            "Failed to append upload chunk.\n"
                            f"stdout:\n{result.get('stdout', '')}\n"
                            f"stderr:\n{result.get('stderr', '')}"
                        )

            decoded = run_remote_python_json(
                self,
                REMOTE_DECODE_SCRIPT,
                str(remote_tmp_b64),
                str(remote_tmp_file),
                remote_mode or "",
            )
            if decoded.get("sha256") != local_sha256:
                raise RuntimeError(
                    "Checksum mismatch after remote staging.\n"
                    f"Local:   {local_sha256}\n"
                    f"Remote:  {decoded.get('sha256')}"
                )

            finalized = run_remote_python_json(
                self,
                REMOTE_FINALIZE_SCRIPT,
                str(remote_tmp_file),
                remote_path,
                backup_path,
            )
            remote_after = self.remote_file_stat(remote_path)
            if remote_after.get("sha256") != local_sha256:
                raise RuntimeError(
                    "Checksum mismatch after remote replace.\n"
                    f"Local:   {local_sha256}\n"
                    f"Remote:  {remote_after.get('sha256')}"
                )
        finally:
            try:
                self.ssh_execute(build_remote_python_command(REMOTE_CLEANUP_SCRIPT, str(remote_tmp_b64), str(remote_tmp_file)))
            except RuntimeError:
                pass

        return {
            "local_path": str(source),
            "remote_path": remote_path,
            "sha256": local_sha256,
            "backup_path": finalized.get("backup_path"),
            "remote_mode": remote_after.get("mode", remote_mode),
            "remote_size": remote_after.get("size"),
            "previous_sha256": remote_before.get("sha256"),
        }

    def read_lines(
        self,
        remote_path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        return run_remote_python_json(
            self,
            REMOTE_READ_LINES_SCRIPT,
            remote_path,
            start if start is not None else "",
            end if end is not None else "",
        )

    def replace_lines(
        self,
        remote_path: str,
        start: int,
        end: int,
        new_text: str,
        *,
        backup: bool = True,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        if expected_sha256:
            current = self.remote_file_stat(remote_path)
            if current.get("sha256") != expected_sha256:
                raise RuntimeError(
                    "Remote file changed since last read. Refusing to edit.\n"
                    f"Expected: {expected_sha256}\n"
                    f"Actual:   {current.get('sha256')}"
                )

        new_text_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
        if len(new_text_b64) > 8192:
            raise RuntimeError(
                "Replacement text too large for line-level edit.\n"
                f"Size: {len(new_text_b64)} bytes base64.\n"
                "Use pull_remote_file.py + edit locally + push_remote_file.py instead."
            )

        return run_remote_python_json(
            self,
            REMOTE_REPLACE_LINES_SCRIPT,
            remote_path,
            start,
            end,
            new_text_b64,
            "true" if backup else "false",
        )

    def insert_lines(
        self,
        remote_path: str,
        at_line: int,
        new_text: str,
        *,
        position: str = "after",
        backup: bool = True,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        if expected_sha256:
            current = self.remote_file_stat(remote_path)
            if current.get("sha256") != expected_sha256:
                raise RuntimeError(
                    "Remote file changed since last read. Refusing to edit.\n"
                    f"Expected: {expected_sha256}\n"
                    f"Actual:   {current.get('sha256')}"
                )

        new_text_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
        if len(new_text_b64) > 8192:
            raise RuntimeError(
                "Insert text too large for line-level edit.\n"
                f"Size: {len(new_text_b64)} bytes base64.\n"
                "Use pull_remote_file.py + edit locally + push_remote_file.py instead."
            )

        return run_remote_python_json(
            self,
            REMOTE_INSERT_LINES_SCRIPT,
            remote_path,
            at_line,
            new_text_b64,
            position,
            "true" if backup else "false",
        )

    def delete_lines(
        self,
        remote_path: str,
        start: int,
        end: int,
        *,
        backup: bool = True,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        result = self.replace_lines(
            remote_path, start, end, "",
            backup=backup, expected_sha256=expected_sha256,
        )
        result["deleted_lines"] = result.pop("replaced_lines", 0)
        return result

    def search_in_file(
        self,
        remote_path: str,
        pattern: str,
        *,
        context: int = 2,
        max_matches: int = 20,
    ) -> dict[str, Any]:
        return run_remote_python_json(
            self,
            REMOTE_FILE_SEARCH_SCRIPT,
            remote_path,
            pattern,
            context,
            max_matches,
        )


def run_remote_python_json(bridge: McpBridge, script: str, *args: object, cwd: str | None = None) -> dict[str, Any]:
    command = build_remote_python_command(script, *args)
    result = bridge.ssh_execute(command, cwd=cwd)
    if result.get("code") != 0:
        raise RuntimeError(
            "Remote command failed.\n"
            f"stdout:\n{result.get('stdout', '')}\n"
            f"stderr:\n{result.get('stderr', '')}"
        )

    stdout = result.get("stdout", "")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Remote stdout was not valid JSON: {stdout[:200]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected remote JSON payload: {payload!r}")
    return payload


def print_json(data: dict[str, Any]) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
