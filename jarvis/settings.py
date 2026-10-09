"""Settings changed from the UI (stub; WP12 fills it in).

An override layer on top of config: values saved from the settings dialog
are applied to config at startup. Sensitive ones (permission mode, workdir,
MCP config, OpenAI key) can only change through the UI, never a voice tool.
"""
from fastapi import APIRouter

router = APIRouter()


def apply_overrides():
    """Copy saved UI settings onto config (nothing saved yet)."""
