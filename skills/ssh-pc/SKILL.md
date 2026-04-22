---
name: SSH PC Access
version: 1.2.0
description: |
  Access Parth's local PC (Arch Linux on WSL2) remotely via MCP SSH Manager over a Pinggy tunnel.
  Use when you need to run shell commands, manage tmux sessions, read/write files, inspect processes,
  work with Docker, or interact with the development environment from outside the machine.
  Also loads local agent skills from ~/.agents/skills on the remote PC.
author: mewtwo
---

# SSH PC Access Skill

## Overview

This skill connects you to Parth's local PC through **MCP SSH Manager** exposed via a **Pinggy tunnel** (Streamable HTTP MCP transport).

You can:
- Run arbitrary shell commands and scripts
- Create and attach to **tmux sessions** for long-running work
- Read, write, and inspect files
- Manage processes, Docker containers, and services
- Work inside the development directories
- Load and use additional skills from `~/.agents/skills` on the remote PC

---

## Step 0 — Load local skills on the remote PC

The remote PC has a skills folder at:

```
~/.agents/skills/
```

Before starting work, list what's available:

```bash
ls ~/.agents/skills/
```

Each subfolder is a skill. Read its `SKILL.md` to understand what it does and whether it's relevant to the current task. Incorporate those skills into your approach as needed.

---

## Step 1 — Get the tunnel URL

The Pinggy free tunnel changes every session and expires every ~60 minutes.

**Always ask the user for the current base URL before connecting**, for example:
- `https://apmlt-49-36-19-226.run.pinggy-free.link`

Once you have it, the MCP endpoint is:
```
<BASE_URL>/mcp
```

---

## Step 2 — Initialize the MCP session

POST to `<BASE_URL>/mcp`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": { "name": "ssh-pc-skill", "version": "1.2.0" }
  }
}
```

Save the `Mcp-Session-Id` from the response headers and include it in all subsequent requests.

Optionally confirm available tools:

```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }
```

---

## Step 3 — Run commands with `ssh_execute`

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "ssh_execute",
    "arguments": {
      "server": "mypc",
      "command": "YOUR_COMMAND_HERE",
      "timeout": 15000
    }
  }
}
```

- `server` is always `"mypc"` unless the user says otherwise.
- `timeout` is in milliseconds. Increase for long-running commands.
- If PATH is limited, use absolute binary paths (see below).

---

## tmux — Long-running and persistent sessions

Use tmux for anything that takes more than a few seconds, or when you want to keep a process running after the command returns.

### Create a new named session
```bash
tmux new-session -d -s work
```

### Run a command inside a tmux session
```bash
tmux send-keys -t work "npm run dev" Enter
```

### List active sessions
```bash
tmux ls
```

### Capture output from a session
```bash
tmux capture-pane -pt work -S -100
```

### Kill a session
```bash
tmux kill-session -t work
```

### Run a long command and detach immediately
```bash
tmux new-session -d -s build -x 220 -y 50 \; send-keys "cd /mnt/wsl/fastssd/myproject && npm run build" Enter
```

Use tmux whenever:
- Starting dev servers (`npm run dev`, `python app.py`, etc.)
- Running builds or tests that take time
- You want output to persist for later inspection

---

## Bash scripting on the remote PC

You can send multi-line bash scripts as a single command:

```bash
bash -c '
  cd /mnt/wsl/fastssd/myproject
  git pull
  npm install
  npm run build
'
```

Or write a script file and execute it:

```bash
cat > /tmp/setup.sh << 'EOF'
#!/usr/bin/env bash
set -e
cd /mnt/wsl/fastssd/myproject
npm install
npm run dev &
echo "Dev server started"
EOF
bash /tmp/setup.sh
```

---

## Common Tasks

### System status
```bash
whoami && /usr/bin/uname -a && /usr/bin/uptime && free -h && df -h /
```

### List project files
```bash
ls -la /mnt/wsl/fastssd/
```

### Check running processes
```bash
ps aux --sort=-%cpu | head -20
```

### Check Docker containers
```bash
docker ps
```

### Check Docker logs
```bash
docker logs --tail 50 <container_name>
```

### Read a file
```bash
cat /path/to/file
```

### Write a file
```bash
cat > /path/to/file << 'EOF'
file content here
EOF
```

### Check open ports
```bash
ss -tlnp
```

### Check systemd service
```bash
systemctl status <service>
```

### Git status in a project
```bash
cd /mnt/wsl/fastssd/myproject && git status && git log --oneline -5
```

---

## Environment Details

| Property | Value |
|---|---|
| OS | Arch Linux (WSL2 on Windows) |
| SSH User | `mewtwo` |
| SSH Host | `127.0.0.1` |
| SSH Port | `22` |
| MCP server port | `3999` (local) |
| MCP transport | Streamable HTTP (`/mcp`) |
| Primary server name | `mypc` |

---

## Key Paths

| Path | Purpose |
|---|---|
| `/home/mewtwo/` | Home directory |
| `/mnt/wsl/fastssd/` | Main workspace (fast SSD) |
| `/mnt/wsl/fastssd/mcp-ssh-manager/` | MCP SSH Manager project |
| `/home/mewtwo/.ssh-mcp-config.toml` | SSH server config |
| `/home/mewtwo/.agents/skills/` | Local agent skills |
| `/mnt/c/Users/XMewtwoX/` | Windows user directory (via WSL) |
| `/mnt/c/Users/XMewtwoX/my-ii-skills/` | Skills repo (Windows-mounted) |

---

## Absolute Binary Paths (when PATH is limited)

If commands fail with "not found", use explicit paths:

```
/usr/bin/bash
/usr/bin/uname
/usr/bin/uptime
/usr/bin/ls
/usr/bin/cat
/usr/bin/ps
/usr/bin/grep
/usr/bin/find
/usr/bin/git
/usr/bin/docker
/usr/bin/tmux
/usr/bin/python3
/usr/bin/node
/usr/local/bin/node
```

---

## Safety Rules

Always confirm with the user before:
- `rm` on important paths
- `shutdown` or `reboot`
- Restarting services that affect active work
- Database restore or destructive migrations
- `git reset --hard` or `git clean -f`

Default to read-only inspection first. Write only when the user asks.

---

## Failure Handling

| Symptom | Action |
|---|---|
| Endpoint unreachable | Ask user to restart Pinggy and provide new URL |
| `initialize` succeeds but tools fail | Call `tools/list` to verify tool names |
| Command not found | Retry with absolute binary path |
| Timeout | Increase `timeout` value or use tmux for long commands |
| Session expired | Re-initialize with a new `initialize` request |

---

## Notes

- Transport is **Streamable HTTP MCP** — do not use `/sse` or `/message` endpoints.
- Tunnel URL is always runtime input — never hardcode it.
- Always check `~/.agents/skills/` for local skills that may extend what you can do on this machine.
