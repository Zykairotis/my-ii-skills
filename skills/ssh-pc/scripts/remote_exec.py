#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from mcp_bridge import McpBridge


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a remote command on Parth's PC over MCP.")
    parser.add_argument("--base-url", help="Pinggy base URL or full /mcp URL.")
    parser.add_argument("--server", default=None, help="Remote server name. Defaults to SSH_PC_SERVER or mypc.")
    parser.add_argument("--cwd", help="Remote working directory.")
    parser.add_argument("--command", required=True, help="Shell command to execute remotely.")
    parser.add_argument("--json", action="store_true", help="Print the raw ssh_execute JSON result.")
    args = parser.parse_args()

    bridge = McpBridge(base_url=args.base_url, server=args.server)
    result = bridge.ssh_execute(args.command, cwd=args.cwd)

    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stdout:
            sys.stdout.write(stdout)
            if not stdout.endswith("\n"):
                sys.stdout.write("\n")
        if stderr:
            sys.stderr.write(stderr)
            if not stderr.endswith("\n"):
                sys.stderr.write("\n")

    code = result.get("code")
    return int(code) if isinstance(code, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
