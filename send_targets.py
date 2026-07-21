"""Opt-in convention consumed by x32-recorder core (see recorder.plugin_send /
plugins/PLUGIN_DEVELOPMENT.md) - lets DownloadDropdown.vue's "Senden"-Button discover and use
this plugin without the core knowing anything about Signal."""
from pathlib import Path

from recorder.models import Recording
from recorder.storage import get_recording_path

from . import signal_backend
from .models import MessengerSettings, SendLog, SignalTarget


def playback_mp3_path(recording):
    """Same convention as the core playback endpoints (see api_views.py's stream/
    download_playback_mp3) - only the recording's own browser-mix is ever sent, never raw WAVs."""
    return Path(get_recording_path()) / str(recording.uuid) / "playback.mp3"


def list_targets():
    if not MessengerSettings.get_solo().enabled:
        return []
    return [{"id": t.id, "label": t.name} for t in SignalTarget.objects.filter(enabled=True)]


def send(recording, target_id):
    if not MessengerSettings.get_solo().enabled:
        raise RuntimeError("Messenger-Plugin ist nicht aktiviert")
    target = SignalTarget.objects.filter(id=target_id, enabled=True).first()
    if not target:
        raise RuntimeError("Ziel nicht gefunden oder deaktiviert")
    if recording.playback_status != Recording.PLAYBACK_READY:
        raise RuntimeError("Browser-Mix ist noch nicht bereit")
    mp3_path = playback_mp3_path(recording)
    if not mp3_path.exists():
        raise RuntimeError("Browser-Mix-Datei nicht gefunden")

    text = recording.name or f"Aufnahme {recording.id}"
    try:
        signal_backend.send(target.group_id, text, mp3_path)
    except Exception as e:
        SendLog.objects.create(recording=recording, target=target, success=False, error=str(e)[:500])
        raise
    SendLog.objects.create(recording=recording, target=target, success=True)
