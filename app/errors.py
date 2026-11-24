"""Shared error types. Owned by the team lead - do not define your own error classes.

Adapters (CrewAI, AutoGen, LangChain chain) raise these; the LangGraph workflow
catches them and handles retries / escalation.
"""


class AdapterError(Exception):
    """Generic adapter failure (anything not covered below)."""


class AdapterTimeout(AdapterError):
    """The model / agent call took too long."""


class AdapterBadOutput(AdapterError):
    """The model returned invalid or unparseable output."""
