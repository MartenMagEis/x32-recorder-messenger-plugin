"""Downloads/updates signal-cli itself from its GitHub releases
(https://github.com/AsamK/signal-cli), so this plugin doesn't require a manual signal-cli install
step - same "check periodically, never auto-apply without an explicit click" philosophy as
x32-recorder's own plugin_updates.py/app_update.py, just against GitHub Releases instead of git
history.

What this can and can't bundle: signal-cli's standard release is a Java application (a `bin/
signal-cli` shell script + JARs) - downloading and extracting it is exactly what this module does,
but the Java runtime (JRE) itself is a large, platform-specific dependency this deliberately does
NOT try to auto-install. See the plugin's README for the one-time `apt install default-jre`
(or equivalent) step that's still required."""
import json
import logging
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

BIN_DIR = Path(__file__).resolve().parent / "signal-cli-bin"
CURRENT_DIR = BIN_DIR / "current"
VERSION_FILE = BIN_DIR / "VERSION"

RELEASES_API_URL = "https://api.github.com/repos/AsamK/signal-cli/releases/latest"
REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "x32-recorder-messenger-plugin",
}
API_TIMEOUT_S = 15
DOWNLOAD_TIMEOUT_S = 120

# Release cadence is slow (a handful of releases a year) - no need to hammer the (unauthenticated,
# 60 requests/hour) GitHub API more than a few times a day. Same trade-off x32-recorder's own
# app_update.py already makes for its own update check.
UPDATE_CHECK_INTERVAL_S = 6 * 60 * 60

_state_lock = threading.Lock()
_last_check = {"latest_version": None, "error": None}


def installed_version():
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return None


def resolve_signal_cli_command():
    """Prefers the version this module downloaded itself; falls back to a system-wide `signal-cli`
    on PATH for anyone who installed it manually instead."""
    bin_path = CURRENT_DIR / "bin" / "signal-cli"
    if bin_path.exists():
        return str(bin_path)
    return "signal-cli"


def _fetch_latest_release():
    """Returns (version, download_url) for the plain cross-platform .tar.gz asset - raises
    RuntimeError with a user-facing message on any failure."""
    request = urllib.request.Request(RELEASES_API_URL, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
            data = json.load(response)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"signal-cli-Release-Info konnte nicht geladen werden: {e}")

    tag = data.get("tag_name", "")
    version = tag[1:] if tag.startswith("v") else tag
    # Releases also ship "-json-schemas"/"-Linux-client"/"-Linux-native" variants and .asc
    # signature files alongside the plain cross-platform build - match that one exactly rather
    # than the first *.tar.gz found.
    expected_name = f"signal-cli-{version}.tar.gz"
    asset = next(
        (a for a in data.get("assets", []) if a["name"] == expected_name),
        None
    )
    if not version or not asset:
        raise RuntimeError("Kein passendes signal-cli-Release-Archiv gefunden")
    return version, asset["browser_download_url"]


def get_last_check():
    with _state_lock:
        return dict(_last_check)


def refresh_latest_version():
    """On-demand version of the periodic check below - same result, just immediate."""
    try:
        version, _url = _fetch_latest_release()
        with _state_lock:
            _last_check.update(latest_version=version, error=None)
    except Exception as e:
        with _state_lock:
            _last_check.update(error=str(e))
    return get_last_check()


def install_latest():
    """Downloads + extracts the latest release, replacing any previously installed one. Blocks for
    the duration of the download (a few seconds to under a minute on a Pi's connection) - same
    trade-off as the already-synchronous git-clone-on-plugin-add core endpoint."""
    version, url = _fetch_latest_release()
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "signal-cli.tar.gz"
        request = urllib.request.Request(url, headers=REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response, \
                    open(archive_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
            extract_dir = Path(tmp) / "extracted"
            with tarfile.open(archive_path) as tar:
                tar.extractall(extract_dir)
        except (OSError, tarfile.TarError) as e:
            raise RuntimeError(f"signal-cli-Download/Entpacken fehlgeschlagen: {e}")

        top_level_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(top_level_dirs) != 1:
            raise RuntimeError("Unerwarteter Aufbau des signal-cli-Release-Archivs")

        if CURRENT_DIR.exists():
            shutil.rmtree(CURRENT_DIR)
        shutil.move(str(top_level_dirs[0]), str(CURRENT_DIR))

    bin_path = CURRENT_DIR / "bin" / "signal-cli"
    if bin_path.exists():
        bin_path.chmod(0o755)  # tarball permissions aren't always preserved consistently
    VERSION_FILE.write_text(version, encoding="utf-8")
    with _state_lock:
        _last_check.update(latest_version=version, error=None)
    return version


def run_update_check_poller():
    """Background loop (started once from MessengerPluginConfig.ready()) - only ever records
    whether a newer version exists, never installs it on its own (see handle_config_action's
    'install_signal_cli' action for the explicit, user-triggered install/update)."""
    while True:
        try:
            refresh_latest_version()
        except Exception:
            logger.exception("Messenger: unerwarteter Fehler beim signal-cli-Update-Check")
        time.sleep(UPDATE_CHECK_INTERVAL_S)
