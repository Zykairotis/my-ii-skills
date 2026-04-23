#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from mcp_bridge import DEFAULT_FILE_CHUNK_BYTES, McpBridge, print_json


def load_sidecar(local_path: Path, metadata_path: str | None) -> tuple[Path | None, dict[str, object]]:
    if metadata_path:
        sidecar_path = Path(metadata_path).expanduser()
    else:
        sidecar_path = local_path.with_name(f"{local_path.name}.ssh-pc.json")

    if not sidecar_path.exists():
        return None, {}

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Metadata sidecar is not an object: {sidecar_path}")
    return sidecar_path, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Push a locally edited file back to Parth's PC.")
    parser.add_argument("--base-url", help="Pinggy base URL or full /mcp URL.")
    parser.add_argument("--server", default=None, help="Remote server name. Defaults to SSH_PC_SERVER or mypc.")
    parser.add_argument("--local-path", required=True, help="Local file path in the agent environment.")
    parser.add_argument("--remote-path", help="Remote destination path. Defaults to the pull metadata path.")
    parser.add_argument("--metadata-path", help="Explicit pull metadata sidecar path.")
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_FILE_CHUNK_BYTES, help="Bytes to upload per chunk before base64 encoding.")
    parser.add_argument("--force", action="store_true", help="Skip remote checksum verification against the pull metadata.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a backup before replacing the remote file.")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary.")
    args = parser.parse_args()

    local_path = Path(args.local_path).expanduser()
    if not local_path.is_file():
        raise SystemExit(f"Local path is not a file: {local_path}")

    sidecar_path, sidecar = load_sidecar(local_path, args.metadata_path)
    remote_path = args.remote_path or sidecar.get("remote_path")
    if not isinstance(remote_path, str) or not remote_path:
        raise SystemExit("Missing remote path. Pass --remote-path or use a file pulled with pull_remote_file.py.")

    bridge = McpBridge(
        base_url=args.base_url or sidecar.get("base_url"),
        server=args.server or sidecar.get("server"),
    )
    remote_mode = sidecar.get("remote_mode")
    if remote_mode is not None and not isinstance(remote_mode, str):
        remote_mode = None

    expected_remote_sha256 = sidecar.get("remote_sha256")
    finalized = bridge.upload_remote_file_atomic(
        local_path,
        remote_path,
        expected_remote_sha256=expected_remote_sha256 if isinstance(expected_remote_sha256, str) else None,
        remote_mode=remote_mode,
        chunk_bytes=args.chunk_bytes,
        force=args.force,
        backup=not args.no_backup,
    )

    if sidecar_path:
        updated_sidecar = dict(sidecar)
        updated_sidecar["base_url"] = bridge.base_url
        updated_sidecar["server"] = bridge.server
        updated_sidecar["remote_path"] = remote_path
        updated_sidecar["remote_sha256"] = finalized["sha256"]
        updated_sidecar["remote_mode"] = finalized.get("remote_mode", remote_mode)
        updated_sidecar["remote_size"] = finalized.get("remote_size")
        updated_sidecar["last_pushed_at"] = datetime.now(timezone.utc).isoformat()
        sidecar_path.write_text(json.dumps(updated_sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "local_path": str(local_path),
        "remote_path": remote_path,
        "sha256": finalized["sha256"],
        "backup_path": finalized.get("backup_path"),
    }

    if args.json:
        print_json(summary)
    else:
        print(f"Uploaded {local_path} -> {remote_path}")
        if finalized.get("backup_path"):
            print(f"Backup: {finalized['backup_path']}")
        print(f"SHA256: {finalized['sha256']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
