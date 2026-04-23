#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp_bridge import McpBridge, build_remote_python_command, print_json, run_remote_python_json, sha256_file


DEFAULT_CHUNK_BYTES = 4096

REMOTE_STAT_SCRIPT = r"""
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
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES, help="Bytes to upload per chunk before base64 encoding.")
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
    local_sha256 = sha256_file(local_path)
    remote_mode = sidecar.get("remote_mode")
    if remote_mode is not None and not isinstance(remote_mode, str):
        remote_mode = None

    remote_before = run_remote_python_json(bridge, REMOTE_STAT_SCRIPT, remote_path)
    expected_remote_sha256 = sidecar.get("remote_sha256")
    if (
        not args.force
        and expected_remote_sha256
        and isinstance(expected_remote_sha256, str)
        and remote_before.get("exists")
        and remote_before.get("sha256") != expected_remote_sha256
    ):
        raise SystemExit(
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
    backup_path = ""
    if not args.no_backup:
        backup_path = f"{remote_path}.bak.{timestamp}"

    try:
        bridge.ssh_execute(build_remote_python_command(REMOTE_CLEANUP_SCRIPT, str(remote_tmp_b64), str(remote_tmp_file)))

        with local_path.open("rb") as handle:
            while True:
                chunk = handle.read(args.chunk_bytes)
                if not chunk:
                    break
                chunk_b64 = base64.b64encode(chunk).decode("ascii")
                command = build_remote_python_command(REMOTE_APPEND_B64_SCRIPT, str(remote_tmp_b64), chunk_b64)
                result = bridge.ssh_execute(command)
                if result.get("code") != 0:
                    raise RuntimeError(
                        "Failed to append upload chunk.\n"
                        f"stdout:\n{result.get('stdout', '')}\n"
                        f"stderr:\n{result.get('stderr', '')}"
                    )

        decoded = run_remote_python_json(
            bridge,
            REMOTE_DECODE_SCRIPT,
            str(remote_tmp_b64),
            str(remote_tmp_file),
            remote_mode or "",
        )
        if decoded.get("sha256") != local_sha256:
            raise SystemExit(
                "Checksum mismatch after remote staging.\n"
                f"Local:   {local_sha256}\n"
                f"Remote:  {decoded.get('sha256')}"
            )

        finalized = run_remote_python_json(
            bridge,
            REMOTE_FINALIZE_SCRIPT,
            str(remote_tmp_file),
            remote_path,
            backup_path,
        )
        remote_after = run_remote_python_json(bridge, REMOTE_STAT_SCRIPT, remote_path)
        if remote_after.get("sha256") != local_sha256:
            raise SystemExit(
                "Checksum mismatch after remote replace.\n"
                f"Local:   {local_sha256}\n"
                f"Remote:  {remote_after.get('sha256')}"
            )
    finally:
        try:
            bridge.ssh_execute(build_remote_python_command(REMOTE_CLEANUP_SCRIPT, str(remote_tmp_b64), str(remote_tmp_file)))
        except RuntimeError:
            pass

    if sidecar_path:
        updated_sidecar = dict(sidecar)
        updated_sidecar["base_url"] = bridge.base_url
        updated_sidecar["server"] = bridge.server
        updated_sidecar["remote_path"] = remote_path
        updated_sidecar["remote_sha256"] = local_sha256
        updated_sidecar["remote_mode"] = remote_after.get("mode", remote_mode)
        updated_sidecar["remote_size"] = remote_after.get("size")
        updated_sidecar["last_pushed_at"] = datetime.now(timezone.utc).isoformat()
        sidecar_path.write_text(json.dumps(updated_sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "local_path": str(local_path),
        "remote_path": remote_path,
        "sha256": local_sha256,
        "backup_path": finalized.get("backup_path"),
    }

    if args.json:
        print_json(summary)
    else:
        print(f"Uploaded {local_path} -> {remote_path}")
        if finalized.get("backup_path"):
            print(f"Backup: {finalized['backup_path']}")
        print(f"SHA256: {local_sha256}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
