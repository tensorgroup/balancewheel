"""Names under a runtime's config dir that load as code.

Both the lead key and the seat key hash this file. Inside a runtime's writable state dir, what
loads as code or config in the user's NEXT, unsandboxed session stays read-only: a contained
agent must not plant a hook there. Sessions, caches, logs, and the vendor's own credential
refresh stay writable.
"""

KIMI_CODE = ["bin", "config.toml", "tui.toml", "mcp.json", "AGENTS.md", "skills", "plugins", "hooks", "agents"]
PI_CODE = ["extensions", "settings.json", "models.json", "skills", "prompts", "themes", "AGENTS.md", "SYSTEM.md", "APPEND_SYSTEM.md"]
