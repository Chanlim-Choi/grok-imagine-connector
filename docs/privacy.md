# Privacy in the secure MCP build

This fork is **not** the upstream Chrome extension. No website/Chrome session is accessed. No browser cookies are handled. No analytics or telemetry is implemented in the Python MCP process, and there is no remotely controlled selector configuration.

When you invoke a generation operation, **the text prompt and any staged input image are sent to xAI over HTTPS**, under xAI's own privacy and retention terms. Video outputs are downloaded from an xAI-controlled host to the local output directory. The service is not offline, and the data does leave the computer for generation. Do not send payment-card data, credentials, customer PII, or company-confidential information.

Configuration values and the xAI API key are read from the MCP subprocess environment. The key is not intentionally logged. Generated images and videos are saved in the local `GROK_OUTPUT_DIR`. Input images remain in `GROK_INPUT_DIR`; remove them when not needed.

This policy describes only the new `secure_mcp/server.py`, not unaudited versions of the upstream extension/CLI, the Codex host, or the xAI service.
