---
name: SSH PC Access
description: Access Parth's local PC (WSL2/Arch Linux) via SSH through a Pinggy tunnel. Run commands, read files, manage processes, and interact with the dev environment.
tools:
  - execute_command
  - read_file
  - write_file
version: 1.0.0
author: mewtwo
---

# SSH PC Access Skill

## Overview
This skill gives you direct access to Parth's local PC (WSL2/Arch Linux) via SSH through a Pinggy tunnel. Use this to run commands, read files, manage processes, and interact with the development environment.

## Connection Details
- **Tunnel URL**: `https://mwokk-49-36-19-226.run.pinggy-free.link`
- **SSH Host**: `127.0.0.1`
- **SSH User**: `mewtwo`
- **SSH Port**: `22`
- **OS**: Arch Linux (WSL2 on Windows)
- **Home Directory**: `/home/mewtwo`

## How to Execute Commands

### Run a shell command
Send a JSON-RPC request to invoke the `execute_command` tool:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "execute_command",
    "arguments": {
      "server_id": "mypc",
      "command": "YOUR_COMMAND_HERE"
    }
  },
  "id": 1
}
```

## Common Tasks

### Check system status
```bash
command: "uptime && free -h && df -h"
```

### List running processes
```bash
command: "ps aux --sort=-%cpu | head -20"
```

### Read a file
```bash
command: "cat /path/to/file"
```

### Check Docker containers
```bash
command: "docker ps"
```

### Check project files
```bash
command: "cd /mnt/wsl/fastssd && ls"
```

## Key Paths
- **Main project**: `/mnt/wsl/fastssd/`
- **MCP SSH Manager**: `/mnt/wsl/fastssd/mcp-ssh-manager/`
- **SSH config**: `/home/mewtwo/.ssh-mcp-config.toml`
- **Home**: `/home/mewtwo/`

## Important Notes
- The Pinggy free tunnel **expires every 60 minutes** — if connection fails, ask Parth to restart the tunnel and provide the new URL
- The MCP server runs on port `3999` locally, exposed via Pinggy
- SSE endpoint: `/sse`, message endpoint: `/message`
- Always confirm before running destructive commands (rm, shutdown, etc.)
- If the tunnel is down, notify the user to restart Pinggy
