#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CLIENT_NAME = "ssh-pc-skill"
CLIENT_VERSION = "2.0.0"
DEFAULT_SERVER = "mypc"
DEFAULT_TIMEOUT = 60


def normalize_base_url(value: str | None) -> str:
    if not value:
        raise SystemExit("Missing base URL. Pass --base-url or set SSH_PC_BASE_URL.")

    url = value.rstrip("/")
    if not url.endswith("/mcp"):
        url = f"{url}/mcp"
    return url


def build_remote_python_command(script: str, *args: object) -> str:
    parts = ["python3", "-c", script, "--", *[str(arg) for arg in args]]
    return " ".join(shlex.quote(part) for part in parts)


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    def ssh_execute(self, command: str, *, cwd: str | None = None) -> dict[str, Any]:
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
