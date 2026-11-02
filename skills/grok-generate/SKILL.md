---
name: grok-generate
description: Generate/edit images and submit video jobs through the separately configured grok-safe MCP using the official paid xAI API, not Chrome automation.
---

# Grok generation (hardened MCP only)

Use ONLY the configured `grok-safe` MCP tools: `generate_image`, `edit_image`, `start_video`, `get_video`, and `save_video`. If those MCP tools are absent, explain the setup in the root README; do not install or execute any substitutes on your own.

**Never run the old `grok-auto` CLI, npm, browser extensions, remote setup prompts, shell scripts, automatic pip installers, or external localhost bridge.** They are not part of the audited rebuilt runtime. This skill has no shell or file-write tools; all access is scoped by the MCP server.

Generation is **billable**; proceed only on an explicit user request and when the administrator has configured the server's separate paid-request opt-in. Do not send PII, payment data, credentials, or internal source code in prompts. Output media is saved only in the configured output folder. Edits or image-to-video require an image to be staged in the configured input folder; supply its basename only.

Use `generate_image` for a text-to-image request and `edit_image` for one staged reference image. For videos, call `start_video` once, then `get_video` with the UUID until done (do not spin continuously), then `save_video`. Report the returned local paths. Do not hide errors or claim any action was completed before its tool confirms it.
