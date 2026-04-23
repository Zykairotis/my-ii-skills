#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from mcp_bridge import DEFAULT_FILE_CHUNK_BYTES, McpBridge, print_json


def resolve_local_path(remote_path: str, requested_local_path: str | None) -> Path:
    remote_name = Path(remote_path).name or "remote-file"

    if requested_local_path:
        candidate = Path(requested_local_path).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate / remote_name
        if requested_local_path.endswith("/"):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate / remote_name
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    temp_root = Path(tempfile.mkdtemp(prefix="ssh-pc-"))
    return temp_root / remote_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull a remote file from Parth's PC into the local agent workspace.")
    parser.add_argument("--base-url", help="Pinggy base URL or full /mcp URL.")
    parser.add_argument("--server", default=None, help="Remote server name. Defaults to SSH_PC_SERVER or mypc.")
    parser.add_argument("--remote-path", required=True, help="Remote file path on Parth's PC.")
    parser.add_argument("--local-path", help="Local destination file path or directory.")
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_FILE_CHUNK_BYTES, help="Bytes to fetch per chunk.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite the local file if it already exists.")
    parser.add_argument("--print-local-path", action="store_true", help="Print only the local file path.")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary.")
    args = parser.parse_args()

    local_path = resolve_local_path(args.remote_path, args.local_path)
    if local_path.exists() and not args.overwrite:
        raise SystemExit(f"Local path already exists: {local_path}. Pass --overwrite to replace it.")

    bridge = McpBridge(base_url=args.base_url, server=args.server)
    remote_meta = bridge.download_remote_file(args.remote_path, local_path, chunk_bytes=args.chunk_bytes)

    sidecar_path = local_path.with_name(f"{local_path.name}.ssh-pc.json")
    sidecar = {
        "base_url": bridge.base_url,
        "server": bridge.server,
        "remote_path": remote_meta["remote_path"],
        "remote_sha256": remote_meta["sha256"],
        "remote_mode": remote_meta["mode"],
        "remote_size": remote_meta["size"],
        "chunk_bytes": args.chunk_bytes,
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.print_local_path:
        print(local_path)
        return 0

    summary = {
        "local_path": str(local_path),
        "metadata_path": str(sidecar_path),
        "remote_path": remote_meta["remote_path"],
        "sha256": remote_meta["sha256"],
        "size": remote_meta["size"],
    }

    if args.json:
        print_json(summary)
    else:
        print(f"Downloaded {remote_meta['remote_path']} -> {local_path}")
        print(f"Metadata: {sidecar_path}")
        print(f"SHA256: {remote_meta['sha256']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
