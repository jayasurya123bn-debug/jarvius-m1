"""
Antigravity IDE Plugin for JARVIS.

Enables JARVIS to interface directly with the Google Antigravity IDE environment,
query workspace health, execute module diagnostics, run automated test suites, and control tasks.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from actions.antigravity_bridge import antigravity_control

PLUGIN = {
    "name": "antigravity_control",
    "description": (
        "Directly interfaces with the Google Antigravity IDE and JARVIS workspace environment. "
        "Use this tool whenever the user asks about Antigravity, wants to access or open Antigravity IDE, "
        "check Antigravity status or workspace health, run workspace tests, inspect Antigravity agents, "
        "run module diagnostics, or close Antigravity. "
        "Trigger phrases: 'antigravity access', 'open antigravity', 'antigravity status', 'antigravity health', 'antigravity test', 'run workspace tests', 'diagnostics'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Specific Antigravity control action: 'access', 'open', 'status', 'health', 'test', 'diagnostics', 'list', 'run', 'close'."
            },
            "module": {
                "type": "STRING",
                "description": "Optional module name for testing or execution (e.g. 'system_monitor', 'open_app', 'weather_report')."
            },
            "args": {
                "type": "STRING",
                "description": "Optional JSON arguments string for module execution."
            }
        },
        "required": ["action"],
    },
}


def run(parameters: dict[str, Any], player=None, session_memory=None) -> str:
    """
    Executes the Antigravity control action and logs results to the JARVIS UI.
    """
    action = str(parameters.get("action", "status")).lower().strip()
    module = str(parameters.get("module", "")).strip()
    args = str(parameters.get("args", "")).strip()

    # Normalize colloquial triggers
    if any(k in action for k in ("test", "verify", "unit test", "tests")):
        action = "test"
    elif any(k in action for k in ("diagnostic", "diagnose", "check")):
        action = "diagnostics"
    elif any(k in action for k in ("list", "modules", "catalog")):
        action = "list"
    elif any(k in action for k in ("health", "load", "cpu")):
        action = "health"
    elif any(k in action for k in ("access", "open", "launch", "start", "focus", "show")):
        action = "access"
    elif any(k in action for k in ("close", "exit", "quit", "stop", "terminate", "kill")):
        action = "close"

    try:
        result_text = antigravity_control(action=action, module=module, args=args)
    except Exception as e:
        result_text = f"Sir, Antigravity action '{action}' encountered an issue: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result_text}")
        except Exception:
            pass

    return result_text
