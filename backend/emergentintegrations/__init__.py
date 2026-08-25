"""Local compatibility shim for the proprietary `emergentintegrations` package.

Reimplements the small surface used by this project on top of the official
OpenAI SDK, so the backend runs without the Emergent platform.
"""

__all__ = ["llm"]
