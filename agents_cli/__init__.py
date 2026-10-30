"""Local bus so Codex, Gemini, Claude and Grok can send messages to each other."""

__version__ = "0.1.0"

AGENTS = ("codex", "gemini", "claude", "grok")
ALIASES = {
    "codex": "codex",
    "chatgpt": "codex",
    "gpt": "codex",
    "gemini": "gemini",
    "agy": "gemini",
    "antigravity": "gemini",
    "claude": "claude",
    "claude-code": "claude",
    "grok": "grok",
    "grok-build": "grok",
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULTS = {
    "grok": {"model": "grok-4.6", "effort": "high"},
    "claude": {"model": "sonnet", "effort": "high"},
    "gemini": {"model": "gemini-3.8-flash", "effort": "high"},
    "codex": {"model": "gpt-6.1-sol", "effort": "high"},
}
PROTOCOL = 1
