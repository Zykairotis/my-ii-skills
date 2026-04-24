#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from mcp_bridge import McpBridge, print_json


def format_line_number(num: int, width: int) -> str:
    return f"{num:>{width}d}"


def output_lines(result: dict, args: argparse.Namespace) -> None:
    if not result.get("exists"):
        print(f"File not found: {result.get('path')}", file=sys.stderr)
        sys.exit(1)

    total_lines = result.get("total_lines", 0)
    lines = result.get("lines", [])

    if not lines:
        print(f"# {result['path']}  (empty file or no lines in range)")
        return

    if args.bare:
        for line in lines:
            print(line["text"])
        return

    max_num = max(line["num"] for line in lines) if lines else 0
    width = len(str(max_num))

    print(f"# {result['path']}  lines {result['start']}-{result['end']}/{total_lines}")
    for line in lines:
        print(f"  {format_line_number(line['num'], width)} │ {line['text']}")


def output_search(result: dict, args: argparse.Namespace) -> None:
    if not result.get("exists"):
        print(f"File not found: {result.get('path')}", file=sys.stderr)
        sys.exit(1)

    matches = result.get("matches", [])
    count = result.get("count", 0)
    returned = result.get("returned", 0)

    if not matches:
        print(f"No matches found for pattern: {result['pattern']}")
        return

    print(f"# {result['path']}  pattern: {result['pattern']}")
    print(f"# Found {count} match(es), showing {returned}")
    print()

    for match in matches:
        print(f"  [{match['line']:>4d}]  {match['text']}")
        for ctx in match.get("context_before", []):
            print(f"  {ctx['num']:>4d} │ {ctx['text']}")
        print(f"  {match['line']:>4d} ▶ {match['text']}")
        for ctx in match.get("context_after", []):
            print(f"  {ctx['num']:>4d} │ {ctx['text']}")
        print()


def output_json(result: dict) -> None:
    print_json(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read content from a remote file on Parth's PC.")
    parser.add_argument("--base-url", help="Pinggy base URL or full /mcp URL.")
    parser.add_argument("--server", default=None, help="Remote server name. Defaults to SSH_PC_SERVER or mypc.")
    parser.add_argument("--remote-path", required=True, help="Remote file path on Parth's PC.")
    parser.add_argument("--start", type=int, help="1-indexed start line.")
    parser.add_argument("--end", type=int, help="1-indexed end line (inclusive).")
    parser.add_argument("--search", help="Regex pattern to search for.")
    parser.add_argument("--context", type=int, default=2, help="Context lines for search (default: 2).")
    parser.add_argument("--max-matches", type=int, default=20, help="Max matches for search (default: 20).")
    parser.add_argument("--json", action="store_true", help="Print full JSON response.")
    parser.add_argument("--bare", action="store_true", help="Print only line text, no line numbers or metadata.")
    args = parser.parse_args()

    bridge = McpBridge(base_url=args.base_url, server=args.server)

    if args.json:
        if args.search:
            result = bridge.search_in_file(
                args.remote_path,
                args.search,
                context=args.context,
                max_matches=args.max_matches,
            )
        else:
            start = args.start if args.start is not None else 1
            end = args.end
            result = bridge.read_lines(args.remote_path, start, end)
        output_json(result)
        return 0

    if args.search:
        result = bridge.search_in_file(
            args.remote_path,
            args.search,
            context=args.context,
            max_matches=args.max_matches,
        )
        output_search(result, args)
        return 0

    if args.start is not None or args.end is not None:
        start = args.start or 1
        end = args.end
        line_count = (end - start + 1) if end is not None else None
        if line_count is not None and line_count > 200:
            print(
                f"Warning: Requesting {line_count} lines. Output may be truncated by the MCP server (10K char limit).",
                file=sys.stderr,
            )
        result = bridge.read_lines(args.remote_path, start, end)
        output_lines(result, args)
        return 0

    # No range or search: read full file using chunked download
    text = bridge.read_remote_text_file(args.remote_path)
    if args.bare:
        sys.stdout.write(text)
        return 0

    lines = text.splitlines()
    total = len(lines)
    max_num = total
    width = len(str(max_num))
    print(f"# {args.remote_path}  {total} line(s)")
    for i, line in enumerate(lines, start=1):
        print(f"  {format_line_number(i, width)} │ {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
