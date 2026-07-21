"""Thin subprocess wrapper around signal-cli (https://github.com/AsamK/signal-cli) - the de facto
standard tool for programmatic Signal messaging, built on the same protocol library Signal's own
apps use (unofficial/reverse-engineered, not an official Signal API). Every call here is a
one-shot subprocess - no persistent daemon needed for this plugin's send-a-file-once use case.

--config points at a data directory local to this plugin folder rather than signal-cli's system
default, so the linked device's identity/session data stays self-contained here (see
plugins/PLUGIN_DEVELOPMENT.md's Geheimdaten-Hinweis) - nothing in this module ever reads that
directory's contents back out, only ever passes it as a CLI argument.

Note: exact flag names/placement (esp. -o/--output for JSON) can shift between signal-cli
versions - verify against `signal-cli --help` / `signal-cli listGroups --help` on first real
install (see the plugin's README "Testen")."""
import json
import subprocess
from pathlib import Path

SIGNAL_CLI_DATA_DIR = Path(__file__).resolve().parent / "signal-cli-data"
SEND_TIMEOUT_S = 30
LIST_GROUPS_TIMEOUT_S = 15


def _run(args, timeout, global_args=()):
    SIGNAL_CLI_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["signal-cli", "--config", str(SIGNAL_CLI_DATA_DIR), *global_args, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("signal-cli ist nicht installiert oder nicht im PATH")
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"signal-cli fehlgeschlagen: {message[-2000:]}")
    return result.stdout


def send(group_id, text, attachment_path):
    """Raises RuntimeError with a user-facing message on failure - callers (send_targets.py,
    auto_send.py) don't need to know anything about signal-cli's own error format."""
    _run(["send", "-g", group_id, "-m", text, "-a", str(attachment_path)], timeout=SEND_TIMEOUT_S)


def list_groups():
    """[{'id': ..., 'name': ...}, ...] - used by the Settings UI to help pick a group_id instead
    of typing it blind."""
    output = _run(["listGroups"], timeout=LIST_GROUPS_TIMEOUT_S, global_args=["-o", "json"])
    groups = json.loads(output)
    return [{"id": g["id"], "name": g.get("name") or g["id"]} for g in groups]
