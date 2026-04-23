---
name: ssh-pc
description: Bridge a hosted agent to Parth's PC over MCP SSH Manager via Pinggy. Use when work must run against the PC's shell or filesystem, especially when the agent needs to pull files into its own workspace, edit locally, and push them back atomically.
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

## Default workflow for real edits

For any non-trivial file change, use this exact sequence:

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

- Use direct remote commands for inspection and tiny one-line changes.
- Use pull/edit/push for multi-line edits, formatted code, generated files, structured configs, or anything where local tooling is safer.
- Use tmux for long-running processes or servers.

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
