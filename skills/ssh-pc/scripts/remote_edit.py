#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys

from mcp_bridge import McpBridge, print_json


def resolve_content(args: argparse.Namespace) -> str:
    if args.file:
        from pathlib import Path
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise SystemExit(f"File not found: {path}")
        return path.read_text(encoding="utf-8")

    if args.content:
        return args.content

    # Read from stdin
    return sys.stdin.read()


def do_dry_run(bridge: McpBridge, args: argparse.Namespace, content: str) -> None:
    result = bridge.read_lines(args.remote_path)
    if not result.get("exists"):
        raise SystemExit(f"File not found: {result.get('path')}")

    total_lines = result.get("total_lines", 0)
    lines_data = result.get("lines", [])

    if args.action == "replace":
        start = max(1, args.start) if args.start is not None else 1
        end = min(total_lines, args.end) if args.end is not None else total_lines
        print(f"DRY RUN — no changes made.\n")
        print(f"  Action:      replace")
        print(f"  File:        {args.remote_path}")
        print(f"  Lines:       {start}-{end} ({end - start + 1} lines)")
        print(f"  Replacement: {content.count(chr(10)) + 1 if content else 0} lines")
        print()
        if lines_data:
            print("  Lines to be removed:")
            for line in lines_data:
                if start <= line["num"] <= end:
                    print(f"  {line['num']:>4d} │ {line['text']}")
            print()
            print("  Lines to be inserted:")
            for i, line_text in enumerate(content.split("\n"), start=1):
                print(f"  {i:>4d} │ {line_text}")
        print()
        print(f"  New total: {total_lines - (end - start + 1) + (content.count(chr(10)) + 1 if content else 0)} lines")

    elif args.action == "insert":
        at_line = args.at_line if args.at_line is not None else 0
        position = args.position or "after"
        print(f"DRY RUN — no changes made.\n")
        print(f"  Action:     insert")
        print(f"  File:       {args.remote_path}")
        print(f"  At line:    {at_line}")
        print(f"  Position:   {position}")
        print(f"  Inserted:   {content.count(chr(10)) + 1 if content else 0} lines")
        print()
        print("  Lines to be inserted:")
        for i, line_text in enumerate(content.split("\n"), start=1):
            print(f"  {i:>4d} │ {line_text}")
        print()
        print(f"  New total: {total_lines + (content.count(chr(10)) + 1 if content else 0)} lines")

    elif args.action == "delete":
        start = args.start if args.start is not None else 1
        end = args.end if args.end is not None else total_lines
        print(f"DRY RUN — no changes made.\n")
        print(f"  Action:     delete")
        print(f"  File:       {args.remote_path}")
        print(f"  Lines:      {start}-{end} ({end - start + 1} lines)")
        print()
        if lines_data:
            print("  Lines to be deleted:")
            for line in lines_data:
                if start <= line["num"] <= end:
                    print(f"  {line['num']:>4d} │ {line['text']}")
        print()
        print(f"  New total: {total_lines - (end - start + 1)} lines")


def main() -> int:
    parser = argparse.ArgumentParser(description="Edit remote files on Parth's PC with line-level operations.")
    parser.add_argument("--base-url", help="Pinggy base URL or full /mcp URL.")
    parser.add_argument("--server", default=None, help="Remote server name. Defaults to SSH_PC_SERVER or mypc.")
    parser.add_argument("--remote-path", required=True, help="Remote file path on Parth's PC.")
    parser.add_argument("--action", required=True, choices=["replace", "insert", "delete"], help="Edit action.")
    parser.add_argument("--start", type=int, help="Start line (1-indexed, for replace and delete).")
    parser.add_argument("--end", type=int, help="End line (1-indexed, for replace and delete).")
    parser.add_argument("--at-line", type=int, help="Target line (for insert).")
    parser.add_argument("--position", choices=["before", "after"], default="after", help="Insert position (default: after).")
    parser.add_argument("--content", help="Content to insert or replace.")
    parser.add_argument("--file", help="Read content from a local file.")
    parser.add_argument("--expected-sha256", help="Verify file hasn't changed before editing.")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating a backup.")
    parser.add_argument("--force", action="store_true", help="Skip SHA256 verification (deprecated, use with --no-backup).")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would change without modifying.")
    parser.add_argument("--json", action="store_true", help="Print full JSON response.")
    args = parser.parse_args()

    bridge = McpBridge(base_url=args.base_url, server=args.server)

    # Validate arguments
    if args.action in ("replace", "delete"):
        if args.start is None or args.end is None:
            raise SystemExit(f"--action {args.action} requires --start and --end.")
    if args.action == "insert":
        if args.at_line is None:
            raise SystemExit("--action insert requires --at-line.")

    try:
        content = resolve_content(args)
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"Failed to read content: {exc}")

    if args.dry_run:
        do_dry_run(bridge, args, content)
        return 0

    # Size check for line-level operations
    if args.action in ("replace", "insert"):
        content_b64_len = len(base64.b64encode(content.encode("utf-8")).decode())
        if content_b64_len > 8192:
            print(f"Error: Content too large for line-level edit.", file=sys.stderr)
            print(f"Base64 size: {content_b64_len} bytes (max: 8192).", file=sys.stderr)
            print("Use pull_remote_file.py + edit locally + push_remote_file.py for large edits.", file=sys.stderr)
            sys.exit(3)

    try:
        if args.action == "replace":
            result = bridge.replace_lines(
                args.remote_path,
                args.start,
                args.end,
                content,
                backup=not args.no_backup,
                expected_sha256=args.expected_sha256,
            )
        elif args.action == "insert":
            result = bridge.insert_lines(
                args.remote_path,
                args.at_line,
                content,
                position=args.position,
                backup=not args.no_backup,
                expected_sha256=args.expected_sha256,
            )
        elif args.action == "delete":
            result = bridge.delete_lines(
                args.remote_path,
                args.start,
                args.end,
                backup=not args.no_backup,
                expected_sha256=args.expected_sha256,
            )
    except RuntimeError as exc:
        error_text = str(exc)
        if "changed since" in error_text:
            print(f"Error: {error_text}", file=sys.stderr)
            sys.exit(2)
        raise

    if args.json:
        print_json(result)
    else:
        action_label = args.action.capitalize()
        print(f"{action_label}d {args.remote_path}")
        if result.get("backup_path"):
            print(f"Backup: {result['backup_path']}")
        print(f"SHA256: {result['sha256']}")
        if "total_lines" in result:
            print(f"Lines:  {result['total_lines']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
