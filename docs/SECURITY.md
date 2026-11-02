# Security model and verification checklist

## Scope

This branch is a clean-room, official-API, stdio MCP alternative to a browser-driving project. The previous npm bridge and Chrome extension binaries **were not audited** and are **not used**. In particular, no browser session credentials, extension permissions, or loopback API sockets are required.

## Trust boundaries

1. **Codex -> MCP (local process):** stdio JSON-RPC, protocol tool allowlist, fixed schemas, no arbitrary command tools, 1 MiB frame cap. MCP cannot prevent a fully compromised Codex process or a malicious local user from invoking its tools.
2. **Local process -> xAI:** the official HTTPS `api.x.ai` endpoint is hard-coded. TLS certificate validation uses the Python default. Proxy environment variables are ignored; redirects are rejected. The xAI API receives prompt text and staged reference image bytes.
3. **xAI -> local disk:** images are requested as base64 and size-limited; video jobs are polled by canonical UUID. Completed MP4 downloads are accepted only from HTTPS `vidgen.x.ai`, with no redirects. Content length and actual bytes are both bounded. These checks do not make a media decoder immune to maliciously crafted files.
4. **Filesystem:** input basenames in a pre-existing `GROK_INPUT_DIR`; output stems in a pre-existing `GROK_OUTPUT_DIR`. Input symlinks are rejected, regular-file type and length checked, and outputs use exclusive creation with restrictive permissions where supported. Do not configure writable roots on shared or attacker-controlled directories. Security of intermediate parent folders remains the operating system administrator's responsibility.
5. **Secrets and spending:** API key from `XAI_API_KEY` environment variable only, never from tools or config files. No requests until `GROK_ALLOW_PAID_REQUESTS=YES`; at most five attempted paid requests per process. This is not a hard account-wide cost ceiling and does not prevent duplicate charges after restarts/timeouts.

## Explicit non-goals and remaining risks

- It is **not** possible to guarantee zero vulnerabilities. Residual risks include Python/runtime and OS vulnerabilities, compromised xAI service or account, DNS/TLS interception, authenticated local-process misuse, and malicious output media.
- No end-to-end live integration test was performed in this branch because no user API key is available. API payloads and media host were checked against public xAI documentation (Nov 2026); the API may change.
- Prompt data **leaves the workstation** for xAI. Do not submit customer PII, payment-card data, authentication credentials, or confidential enterprise source code.
- Python's memory copies and environment variables are not memory-locked. An untrusted program with equal/higher OS privileges can read these; use an isolated account/VM for higher assurance.
- Deny unnecessary outbound network at the host firewall: only api.x.ai:443 and vidgen.x.ai:443 are needed. The code-level allowlist is not a substitute for OS egress policy.
- The staging and output directories are trusted admin-configured roots. A malicious actor with write access to their parent directories may create race conditions that cannot be fully excluded with cross-platform Python stdlib alone.
- The media download host is a strict single-host allowlist: if xAI changes its media CDN hostname, downloads deliberately fail closed until reviewed.
- No telemetry or auto-updaters are included in the Python code. Dependencies are Python standard library only; your Python installation and host still need patching.

## Before approving for corporate deployment

1. Review all source changes and pin the audited commit SHA. Require code review and passing tests on PRs.
2. Run `python -m unittest discover -s tests -v` in an isolated, updated Python environment, on Windows and Linux.
3. Test a single low-cost image generation and short video with a dedicated, low-privilege xAI API key. Verify actual save paths and network egress independently.
4. Confirm OS firewall/proxy policy, least-privilege account, restricted output folders, and API usage limits. Never run Codex or this MCP process elevated.
5. Check secrets are not committed, logged, or forwarded to model prompts. Treat generated media as untrusted until scanned/verified.
6. If enabling a browser-based free variant later, do it as a **separate project** and audit actual extension code, manifest, message origins, CSRF controls, dependency provenance, and local bridge authentication.
