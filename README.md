# Grok Imagine Connector — secure Codex MCP rebuild

This fork **replaces** the original browser/extension/Node.js bridge design with a small, auditable **Python standard-library-only MCP server** that uses the documented **paid xAI API**. It is a materially different integration, not a drop-in repair of the original free Chrome automation. No software can be guaranteed exploit-free.

## Architecture

`Codex -> local Python stdio MCP -> https://api.x.ai/v1/ -> (optional) https://vidgen.x.ai/ for returned MP4`

There is **no listening port**, browser session, Chrome extension, `npm install`, remotely updated selector script, dynamic code execution, or subprocess launcher. The MCP tool schemas expose only fixed operations. File output is restricted to one explicit folder; input images must be pre-staged in another. Video URL redirects are rejected, and the allowed media host is exact-match `vidgen.x.ai`.

## Prerequisites

- Python **3.11+** on the computer running Codex (no pip dependencies).
- An official **xAI API key** with Imagine access. **API usage costs money.**
- An existing, trusted output folder; optionally an existing input folder for editing/animation.
- Review `docs/SECURITY.md` and all code before enabling it for company use.

## Configure on Windows PowerShell

Clone **this** fork into a folder you trust, and review the code. Create staging/output directories that do not contain corporate secrets:

```powershell
New-Item -ItemType Directory -Force "$HOME\GrokOutput", "$HOME\GrokInput" | Out-Null
$env:GROK_OUTPUT_DIR = "$HOME\GrokOutput"
$env:GROK_INPUT_DIR = "$HOME\GrokInput"
$env:GROK_ALLOW_PAID_REQUESTS = "YES"
$secureKey = Read-Host "xAI API key" -AsSecureString
$env:XAI_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
```

The API key is kept in the process environment, not in the repository or Codex configuration. It is still accessible to other software running with sufficient privileges as your user. Do not commit it, print it, or configure it in shell history or a project-level `.codex/config.toml`.

In your **user-level** `~/.codex/config.toml`, configure the absolute script path (example only; change it to your local checkout):

```toml
[mcp_servers.grok-safe]
command = "python"
args = ["C:\\Path\\To\\grok-imagine-connector\\secure_mcp\\server.py"]
env_vars = ["XAI_API_KEY", "GROK_OUTPUT_DIR", "GROK_INPUT_DIR", "GROK_ALLOW_PAID_REQUESTS"]
```

Start Codex from the same configured PowerShell session and verify the server appears in `codex mcp list`. A manual test is possible without an API key using `python -m unittest discover -s tests -v`.

To prevent generation, remove the `GROK_ALLOW_PAID_REQUESTS` environment variable or set it to anything other than `YES` before starting Codex.

## Available MCP tools

| Tool | Behavior | Billing |
| --- | --- | --- |
| `generate_image` | Text-to-image, 1 image per request, base64 output persisted locally | Paid |
| `edit_image` | Image-to-image using a single staged PNG/JPEG/WebP | Paid |
| `start_video` | Submit text-to-video or image-to-video job; returns UUID | Paid |
| `get_video` | Poll existing job status by UUID | Read-only status |
| `save_video` | Fetch completed MP4 from exact trusted media host into output folder | Downloads only |

Call `start_video` then `get_video` then `save_video`. No tools accept arbitrary HTTP addresses, commands, filesystem paths, or output directories. Do not add arbitrary shell execution to an MCP tool.

For staged image use `image_name: "reference.png"` after placing the file inside `GROK_INPUT_DIR`. For output use a filename **stem**, e.g. `name: "fox_scene"`, not an absolute path or extension. Files are created exclusively and never overwritten.

Each server process permits a maximum of **5 attempted paid generations**. This is a *guardrail*, not a persisted billing limit: restarting the process resets the counter. Configure limits at your xAI account as well.

## Explicit scope cuts from upstream

This build intentionally removes the `grok-auto` npm CLI, Chrome extension, Grok website automation, auto-extension, batch queuing, video merging, watermark editing, and optional heavyweight third-party inpainting dependency. The original binary/extension code was not part of the public GitHub tree and is therefore **not trusted or installed** by this fork. The `tools/inpaint.py` script is removed from the active rebuild.

## Security and status

`docs/SECURITY.md` contains the threat model and remaining risks. Offline tests exercise protocol negotiation, secret masking, filesystem traversal and symlink rejection, outbound URL validation, download bounds, cost opt-in, and command/argument restrictions. No live generation has been performed; actual API-key integration must be tested by the account owner. The full system is **not certified exploit-free**.
