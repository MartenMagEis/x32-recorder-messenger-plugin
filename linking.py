"""QR-code device-linking flow (Signal's "link a new device" - the same mechanism as linking
Signal Desktop). Runs `signal-cli link` in a background thread, captures the sgnl://linkdevice
URI it prints to stdout, turns it into an inline SVG QR code, and exposes only a small in-memory
status - never persisted, never logged. The URI is discarded the moment linking finishes (success
or failure), so it's visible via plugin_config.py's link_qr_svg field only during the active
"waiting_scan" window, matching x32-recorder's "geheime Daten nur während der Einrichtung"
requirement (see plugins/PLUGIN_DEVELOPMENT.md's Geheimdaten-Hinweis)."""
import re
import subprocess
import threading
from io import BytesIO

import qrcode
import qrcode.image.svg

from .signal_backend import SIGNAL_CLI_DATA_DIR
from . import signal_cli_manager

LINK_TIMEOUT_S = 300  # how long we wait for the user to actually scan the QR
_URI_RE = re.compile(r"(sgnl://linkdevice\?\S+)")

_lock = threading.Lock()
_state = {"status": "idle", "qr_svg": None, "error": None}


def get_state():
    with _lock:
        return dict(_state)


def _set_state(**kwargs):
    with _lock:
        _state.update(kwargs)


def _qr_svg(data):
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def _mark_linked():
    from .models import MessengerSettings
    settings = MessengerSettings.get_solo()
    settings.linked = True
    settings.save(update_fields=["linked"])


def _run_link(device_name):
    SIGNAL_CLI_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not signal_cli_manager.is_java_available():
        _set_state(
            status="failed", qr_svg=None,
            error="Keine Java-Laufzeitumgebung (JRE) gefunden - signal-cli braucht Java, um zu "
                  "laufen. 'signal-cli installieren/aktualisieren' oben erneut ausführen (lädt "
                  "normalerweise automatisch eine passende JRE mit)."
        )
        return
    proc = None
    try:
        proc = subprocess.Popen(
            [signal_cli_manager.resolve_signal_cli_command(), "--config", str(SIGNAL_CLI_DATA_DIR),
             "link", "-n", device_name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=signal_cli_manager.build_subprocess_env(),
        )
        output_lines = []
        found_uri = False
        for line in proc.stdout:
            output_lines.append(line)
            match = _URI_RE.search(line)
            if match:
                _set_state(status="waiting_scan", qr_svg=_qr_svg(match.group(1)), error=None)
                found_uri = True
                break
        # wait() (not a second blocking readline loop) enforces LINK_TIMEOUT_S even if signal-cli
        # itself never times out waiting for the scan - only *after* it exits is a final
        # non-blocking read of any output printed since the URI match (e.g. once the phone
        # actually scans it) safe to do without risking hanging past the timeout ourselves.
        returncode = proc.wait(timeout=LINK_TIMEOUT_S)
        remaining = proc.stdout.read()
        if remaining:
            output_lines.append(remaining)
        if returncode == 0:
            _set_state(status="linked", qr_svg=None, error=None)
            _mark_linked()
        else:
            detail = "".join(output_lines).strip()[-500:] or f"Exit-Code {returncode}"
            prefix = "Verknüpfung fehlgeschlagen oder abgelaufen" if found_uri else "signal-cli-Fehler"
            _set_state(status="failed", qr_svg=None, error=f"{prefix}: {detail}" if detail else prefix)
    except FileNotFoundError:
        _set_state(
            status="failed", qr_svg=None,
            error="signal-cli wurde nicht gefunden - siehe 'signal-cli installieren/aktualisieren' oben"
        )
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
        _set_state(status="failed", qr_svg=None, error="Zeitüberschreitung beim Warten auf den QR-Scan")
    except Exception as e:
        _set_state(status="failed", qr_svg=None, error=str(e))


def start_linking(device_name):
    with _lock:
        if _state["status"] == "waiting_scan":
            return  # already in progress - let the running attempt finish
        _state.update(status="starting", qr_svg=None, error=None)
    threading.Thread(target=_run_link, args=(device_name,), daemon=True).start()
