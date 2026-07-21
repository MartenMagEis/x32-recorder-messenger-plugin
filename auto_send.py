"""Background poll loop (started once from MessengerPluginConfig.ready(), same
threading.Thread(daemon=True) pattern as x32-recorder-ups-plugin's monitor). Automatically sends
freshly-cut Song-Clips to every target with auto_send_on_clip_export=True, once their own
browser-mix (playback.mp3) is ready.

Why the extra "does it need prepare_playback?" step: x32-recorder's clip_export.py has a
reuse_playback optimization - a clip's playback_status is set straight to PLAYBACK_READY at cut
time if its *parent* recording already had a ready browser-mix, otherwise the clip starts at
PLAYBACK_NONE and needs its own mixdown run first (the C controller does the actual work once
playback_status is set to PLAYBACK_PROCESSING, same as a manual "Vorbereiten" click on the
frontend - see x32-recorder's api_views.py prepare_playback action, mirrored here directly via
the ORM since there's no need to go through the HTTP API from inside the same process)."""
import logging
import time
from pathlib import Path

from recorder.models import Recording
from recorder.storage import get_recording_path

from .models import MessengerSettings, SendLog, SignalTarget
from .send_targets import playback_mp3_path
from . import signal_backend

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 15


def _needs_playback_prep(clip):
    if clip.playback_status == Recording.PLAYBACK_PROCESSING:
        return False  # already running - just keep waiting
    if clip.state != Recording.STOPPED:
        return False
    recording_dir = Path(get_recording_path()) / str(clip.uuid)
    return recording_dir.exists() and any(recording_dir.glob("*.wav"))


def _trigger_playback_prep(clip):
    clip.playback_status = Recording.PLAYBACK_PROCESSING
    clip.save(update_fields=["playback_status"])


def _pending_clips():
    return Recording.objects.filter(
        source=Recording.SOURCE_CLIP,
        parent_recording__clip_export_status=Recording.CLIPS_READY,
    )


def _tick():
    settings = MessengerSettings.get_solo()
    if not settings.enabled:
        return
    targets = list(SignalTarget.objects.filter(enabled=True, auto_send_on_clip_export=True))
    if not targets:
        return

    already_sent = set(
        SendLog.objects.filter(success=True, target__in=targets).values_list("recording_id", "target_id")
    )

    for clip in _pending_clips():
        pending_targets = [t for t in targets if (clip.id, t.id) not in already_sent]
        if not pending_targets:
            continue

        if clip.playback_status != Recording.PLAYBACK_READY:
            if _needs_playback_prep(clip):
                _trigger_playback_prep(clip)
            continue  # wait for the controller to finish the mixdown, retry next tick

        mp3_path = playback_mp3_path(clip)
        if not mp3_path.exists():
            continue

        text = clip.name or f"Song-Clip {clip.id}"
        for target in pending_targets:
            try:
                signal_backend.send(target.group_id, text, mp3_path)
                SendLog.objects.create(recording=clip, target=target, success=True)
            except Exception as e:
                logger.error(f"Messenger: Auto-Senden an {target.name} fehlgeschlagen: {e}")
                SendLog.objects.create(recording=clip, target=target, success=False, error=str(e)[:500])


def run_auto_send_poller():
    while True:
        try:
            _tick()
        except Exception:
            logger.exception("Messenger: unerwarteter Fehler im Auto-Send-Poller")
        time.sleep(POLL_INTERVAL_S)
