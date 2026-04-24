---
name: ssh-pc
description: Bridge a hosted agent to Parth's PC over MCP SSH Manager via Pinggy. Use when work must run against the PC's shell or filesystem. Supports line-level reads and surgical edits (1 round trip), or full-file pull/edit/push for large refactors.
---

# SSH PC

Use this skill when the agent is running in a hosted sandbox but the real work must happen on Parth's PC.

This skill is designed around a split environment:

- The agent has its own filesystem and editor.
- The MCP SSH server runs on Parth's PC.
- Direct remote edits are fragile for large changes.

For that reason, prefer a round-trip workflow:

1. Inspect on the PC.
2. Pull the target file into the agent's workspace.
3. Edit it locally with the agent's normal tools.
4. Push it back with atomic replace and optional backup.

## Important constraint

Do not rely on MCP `ssh_download` or `ssh_upload` for hosted-agent round trips unless the agent and Parth's PC share the same filesystem. Those tools resolve `localPath` on the machine running the MCP server, not inside the hosted agent sandbox.

Use the bundled scripts instead. They bridge file contents through `ssh_execute` in bounded chunks.

Do not dump full file contents with raw `ssh_execute`, `remote_exec.py`, `cat`, or `base64`. Large stdout payloads can be truncated somewhere in the MCP, tunnel, or client chain. That failure can look like a normal successful read unless you chunk the transfer and verify the final checksum.

For reading small portions or making surgical edits, use `remote_read.py` and `remote_edit.py` instead of the full pull/edit/push cycle. These produce small, structured JSON outputs — safe from truncation.

## First steps

Ask the user for the current Pinggy base URL. The free tunnel changes every session. Example:

```bash
export SSH_PC_BASE_URL="https://apmlt-49-36-19-226.run.pinggy-free.link"
export SSH_PC_SERVER="mypc"
```

`SSH_PC_BASE_URL` may be either the base URL or the full `/mcp` URL. The scripts normalize it automatically.

## Available helper scripts

### `scripts/remote_exec.py`

Run a command on the PC.

```bash
python3 scripts/remote_exec.py --command 'pwd && git status --short'
python3 scripts/remote_exec.py --cwd /mnt/wsl/fastssd/myproject --command 'npm test'
```

Use this for:

- inspection
- git status and logs
- starting or checking tmux sessions
- tiny edits only

Do not use it to transfer full file contents.

### `scripts/remote_read.py`

Read content from a remote file. By default it shows line numbers and metadata. Use `--json` for structured output or `--bare` for raw text only.

```bash
# Read specific line range (lightweight, 1 round trip)
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --start 40 --end 60

# Search for a pattern with context
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --search "function\s+parseConfig" \
  --context 3

# Read entire file (falls back to chunked download)
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts

# JSON output for programmatic use
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --start 40 --end 60 \
  --json
```

Useful flags:

```bash
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --start 100  # read from line 100 to end

python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --end 50     # read first 50 lines

python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --search "TODO" --context 2
```

### `scripts/remote_edit.py`

Edit remote files with surgical line-level operations. Writes atomically with automatic backups. For edits > ~6KB, use pull/edit/push instead.

```bash
# Replace lines 45-47
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action replace --start 45 --end 47 \
  --content "const config = validate(input);\n  return config;"

# Insert after line 42
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action insert --at-line 42 --position after \
  --content "  // New validation logic\n  validateOrThrow(config);"

# Insert before line 1 (beginning of file)
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action insert --at-line 0 --position before \
  --content "#!/usr/bin/env node"

# Delete lines 50-55
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action delete --start 50 --end 55

# Preview changes without applying
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action replace --start 45 --end 47 \
  --content "updated code" \
  --dry-run

# Read content from a local file
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action replace --start 1 --end 0 \
  --file /tmp/new_version.ts
```

Useful flags:

```bash
# Skip backup
python3 scripts/remote_edit.py ... --no-backup

# Force skip SHA256 verification
python3 scripts/remote_edit.py ... --force

# Verify file hasn't changed since last read
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action replace --start 45 --end 47 \
  --content "updated code" \
  --expected-sha256 "abc123..."
```

### `scripts/pull_remote_file.py`

Download a remote file from the PC into the agent environment.

```bash
python3 scripts/pull_remote_file.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts
```

By default it writes to a workspace scratch path and creates a sidecar metadata file next to the pulled file. Today that scratch path is implemented as a temp directory unless the agent passes `--local-path` explicitly. The metadata lets `push_remote_file.py` verify that the remote file has not changed unexpectedly before replacing it.

This script is safe for large files because it reads the remote file in small chunks and verifies the assembled local file checksum against the remote checksum before returning success.

Useful flags:

```bash
python3 scripts/pull_remote_file.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --local-path /tmp/app.ts

python3 scripts/pull_remote_file.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --print-local-path
```

### `scripts/push_remote_file.py`

Upload a locally edited file back to the PC.

```bash
python3 scripts/push_remote_file.py --local-path /tmp/app.ts
```

If the pull metadata sidecar exists, the script will:

- infer the original remote path
- verify the remote file still matches the pulled checksum
- upload via a temp file
- atomically replace the target
- create a backup by default

It also verifies the fully assembled remote file checksum after upload and before reporting success.

## Programmatic use

If you are writing custom Python around the bridge, use these methods instead of raw file-dump commands:

```python
from mcp_bridge import McpBridge

bridge = McpBridge(base_url="https://example.run.pinggy.link")

# Read specific lines (lightweight)
ctx = bridge.read_lines("/mnt/wsl/fastssd/myproject/app.ts", start=40, end=60)
for line in ctx["lines"]:
    print(f"{line['num']:4d} | {line['text']}")

# Search for a pattern
matches = bridge.search_in_file(
    "/mnt/wsl/fastssd/myproject/app.ts",
    pattern=r"function\s+\w+Config",
    context=2,
)

# Replace lines atomically
bridge.replace_lines(
    "/mnt/wsl/fastssd/myproject/app.ts",
    start=45, end=47,
    new_text="const config = validate(input);\n  return config;",
    expected_sha256=ctx["sha256"],
)

# Insert after a line
bridge.insert_lines(
    "/mnt/wsl/fastssd/myproject/app.ts",
    at_line=42,
    new_text="// New validation logic\nvalidateOrThrow(config);",
    position="after",
)

# Full-file operations (chunked transfer)
content = bridge.read_remote_text_file("/mnt/wsl/fastssd/myproject/app.ts")
bridge.upload_remote_file_atomic(
    "/workspace/app.ts",
    "/mnt/wsl/fastssd/myproject/app.ts",
    expected_remote_sha256="...",
)
```

Avoid this pattern:

```python
bridge.ssh_execute(f"cat {remote_path}")
```

`McpBridge.ssh_execute()` now rejects obvious raw file-dump commands and points callers at the safe helpers.

Useful flags:

```bash
python3 scripts/push_remote_file.py \
  --local-path /tmp/app.ts \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts

python3 scripts/push_remote_file.py \
  --local-path /tmp/app.ts \
  --force \
  --no-backup
```

## Quick edits (line-level, 1 round trip)

For surgical changes to a few lines, skip the full pull/edit/push cycle:

```bash
# Read context
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --start 40 --end 60

# Edit a few lines
python3 scripts/remote_edit.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --action replace --start 45 --end 47 \
  --content "const config = validate(input);\n  return config;"

# Verify
python3 scripts/remote_read.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts \
  --start 40 --end 50
```

## Default workflow for real edits

For large refactors, multi-section changes, or edits > ~50 lines, use the full round-trip:

1. Inspect:

```bash
python3 scripts/remote_exec.py \
  --cwd /mnt/wsl/fastssd/myproject \
  --command 'git status --short && sed -n "1,220p" src/app.ts'
```

2. Pull:

```bash
python3 scripts/pull_remote_file.py \
  --remote-path /mnt/wsl/fastssd/myproject/src/app.ts
```

3. Edit the pulled local file with the agent's normal coding workflow.

4. Push:

```bash
python3 scripts/push_remote_file.py --local-path /tmp/whatever/app.ts
```

5. Verify remotely:

```bash
python3 scripts/remote_exec.py \
  --cwd /mnt/wsl/fastssd/myproject \
  --command 'git diff -- src/app.ts && npm test -- --runInBand'
```

## When to choose which path

| Scenario | Tool |
|---|---|
| Read a line range or search | `remote_read.py` |
| Edit up to ~50 lines surgically | `remote_edit.py` |
| Inspect, git, tests, tmux | `remote_exec.py` |
| Large refactor, generated file, > 50 lines changed | `pull_remote_file.py` + edit + `push_remote_file.py` |
| Replace an entire small file atomically | `remote_edit.py --action replace --start 1 --end 0 --file /tmp/new.ts` |

## Remote skills on the PC

The PC also has local skills under `~/.agents/skills/`. If the task happens on the PC itself, inspect those before doing work:

```bash
python3 scripts/remote_exec.py --command 'ls ~/.agents/skills'
```

Read the relevant remote `SKILL.md` files with `remote_exec.py` before using them.

## tmux examples

```bash
python3 scripts/remote_exec.py --command 'tmux ls'
python3 scripts/remote_exec.py --command 'tmux new-session -d -s work'
python3 scripts/remote_exec.py --command 'tmux send-keys -t work "cd /mnt/wsl/fastssd/myproject && npm run dev" Enter'
python3 scripts/remote_exec.py --command 'tmux capture-pane -pt work -S -120'
```

## Environment details

- Remote OS: Arch Linux on WSL2
- Remote user: `mewtwo`
- Primary server name: `mypc`
- Main workspace: `/mnt/wsl/fastssd/`
- Remote skills: `/home/mewtwo/.agents/skills/`
