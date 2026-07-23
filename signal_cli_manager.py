"""Downloads/updates signal-cli itself from its GitHub releases
(https://github.com/AsamK/signal-cli), so this plugin doesn't require a manual signal-cli install
step - same "check periodically, never auto-apply without an explicit click" philosophy as
x32-recorder's own plugin_updates.py/app_update.py, just against GitHub Releases instead of git
history.

Bundled JRE: signal-cli releases occasionally bump their minimum Java version (discovered live -
v0.14.0 started requiring Java 25, while v0.13.24 only needed Java 21). Originally this module only
walked backwards through older signal-cli releases to find one compatible with whatever Java
happened to already be installed (see install_latest()) - but that has a sharp edge: Signal's own
servers can reject linking/registration from a signal-cli build old enough to be missing protocol
capabilities they now require (a 409/MissingCapabilitiesException - see AsamK/signal-cli#1226,
discovered live when v0.13.24 downloaded and ran fine but couldn't actually complete a device link).
So install_latest() now tries to download a matching Temurin JRE from Adoptium
(https://adoptium.net) into its own directory first and always prefers running the genuine latest
signal-cli release under that - the old version-walkback logic still exists as a fallback for
platforms Adoptium doesn't publish a JRE for, but is no longer the common path.

Linux ARM64 (Raspberry Pi) native-library patch: signal-cli's bundled libsignal-client-*.jar only
ships native libsignal_jni builds for amd64 Linux/Windows and macOS (both arches) - never aarch64
Linux, i.e. every 64-bit Raspberry Pi OS install. Confirmed by inspecting the jar directly, and a
well-known issue in signal-cli's own tracker (AsamK/signal-cli#1106) with an established community
fix: download a matching prebuilt native library from exquo/signal-libs-build and swap it into the
jar. install_latest() applies this automatically and unconditionally on Linux aarch64 - unlike the
Java-version fallback above, this isn't a "try until one works" situation, every official release
needs it on this platform.

Downloads/extracts always happen inside a temp dir anchored under BIN_DIR (this plugin's own
folder, on the real disk) rather than Python's tempfile default (the system temp dir) - discovered
live that /tmp is a small (454M) RAM-backed tmpfs on Raspberry Pi OS, which filled up completely
mid-debugging from a signal-cli+JRE download and broke tar extraction outright."""
import json
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

BIN_DIR = Path(__file__).resolve().parent / "signal-cli-bin"
CURRENT_DIR = BIN_DIR / "current"
VERSION_FILE = BIN_DIR / "VERSION"
JRE_DIR = BIN_DIR / "jre"
JRE_VERSION_FILE = BIN_DIR / "JRE_VERSION"

RELEASES_API_URL = "https://api.github.com/repos/AsamK/signal-cli/releases"
LATEST_RELEASE_API_URL = RELEASES_API_URL + "/latest"
REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "x32-recorder-messenger-plugin",
}
API_TIMEOUT_S = 15
DOWNLOAD_TIMEOUT_S = 120
VERIFY_TIMEOUT_S = 20

NATIVE_LIB_RELEASES_API_URL = "https://api.github.com/repos/exquo/signal-libs-build/releases/tags/libsignal_v{version}"
# signal-cli's current minimum as of v0.14.0 (see module docstring) - bumping this is a code change,
# not automatic, but a too-old bundled JRE just means install_latest() falls back to the
# version-walkback path instead of failing outright.
JRE_FEATURE_VERSION = 25
ADOPTIUM_ASSET_API_URL = "https://api.adoptium.net/v3/assets/latest/{feature}/hotspot"

# How many releases (newest-first) install_latest() is willing to try before giving up. Not just
# "a handful" - discovered live that a single Java-requirement bump (v0.14.0) stayed in place for
# 8 consecutive releases before the next one (v0.13.24 was the last one before it, i.e. the 9th
# release back) - too small a value here means silently never reaching a working version at all.
MAX_COMPAT_ATTEMPTS = 15

# Release cadence is slow (a handful of releases a year) - no need to hammer the (unauthenticated,
# 60 requests/hour) GitHub API more than a few times a day. Same trade-off x32-recorder's own
# app_update.py already makes for its own update check.
UPDATE_CHECK_INTERVAL_S = 6 * 60 * 60

_state_lock = threading.Lock()
_last_check = {"latest_version": None, "error": None}


def _temp_dir():
    """tempfile.TemporaryDirectory() anchored under BIN_DIR instead of the system default - see
    module docstring for why (a full /tmp broke extraction mid-download on a live Pi)."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=BIN_DIR)


def installed_version():
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return None


def installed_jre_version():
    if JRE_VERSION_FILE.exists():
        return JRE_VERSION_FILE.read_text(encoding="utf-8").strip()
    return None


def resolve_java_home():
    """Path to use as JAVA_HOME if this module's own bundled JRE was successfully installed, else
    None (callers fall back to whatever `java`/JAVA_HOME the environment already provides)."""
    java_bin = JRE_DIR / "bin" / "java"
    if java_bin.exists():
        return str(JRE_DIR)
    return None


def is_java_available():
    """Whether SOME usable Java - bundled or system - is available at all. Checked proactively by
    signal_backend.py/linking.py before invoking signal-cli, so a genuinely missing JRE (bundling
    failed, e.g. no network or an unsupported architecture) produces one clear message immediately
    instead of a raw shell error buried in signal-cli's own output - `java` is a step removed from
    the process Python invokes directly (bin/signal-cli is a shell script that calls out to it), so
    a missing JRE never raises Python's own FileNotFoundError."""
    return resolve_java_home() is not None or shutil.which("java") is not None


def build_subprocess_env():
    """Environment for any subprocess invocation of signal-cli - overrides JAVA_HOME to the bundled
    JRE if one is installed, otherwise a plain copy of this process's own environment (system java
    via PATH/JAVA_HOME, as before bundling existed)."""
    env = dict(os.environ)
    java_home = resolve_java_home()
    if java_home:
        env["JAVA_HOME"] = java_home
    return env


def resolve_signal_cli_command():
    """Prefers the version this module downloaded itself; falls back to a system-wide `signal-cli`
    on PATH for anyone who installed it manually instead."""
    bin_path = CURRENT_DIR / "bin" / "signal-cli"
    if bin_path.exists():
        return str(bin_path)
    return "signal-cli"


def _adoptium_arch():
    return {"aarch64": "aarch64", "arm64": "aarch64", "x86_64": "x64", "amd64": "x64"}.get(
        platform.machine().lower()
    )


def _fetch_jre_asset():
    """(version, download_url) for the latest Temurin JRE matching this platform, or None if
    unsupported (install_jre() treats that as non-fatal) or the API is unreachable. Linux only,
    deliberately - x32-recorder's production target is always Linux (see CLAUDE.md), and Adoptium's
    Linux assets are .tar.gz like everything else this module already extracts; Windows/macOS ship
    .zip instead, which would silently fail against this module's tarfile-only extraction if ever
    reached from the Windows dev machine."""
    if platform.system() != "Linux":
        return None
    arch = _adoptium_arch()
    if not arch:
        return None
    url = (
        ADOPTIUM_ASSET_API_URL.format(feature=JRE_FEATURE_VERSION)
        + f"?architecture={arch}&image_type=jre&os=linux"
    )
    request = urllib.request.Request(url, headers={**REQUEST_HEADERS, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    if not data:
        return None
    return data[0]["release_name"], data[0]["binary"]["package"]["link"]


def install_jre():
    """Downloads+extracts a Temurin JRE for this platform into JRE_DIR. Raises RuntimeError if
    Adoptium doesn't publish one for this architecture/OS or the download fails - install_latest()
    treats that as non-fatal and falls back to the version-walkback path against whatever Java is
    already on the system."""
    asset = _fetch_jre_asset()
    if not asset:
        raise RuntimeError("Kein passender JRE-Build für diese Plattform bei Adoptium gefunden")
    version, url = asset

    with _temp_dir() as tmp:
        archive_path = Path(tmp) / "jre.tar.gz"
        request = urllib.request.Request(url, headers=REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response, \
                    open(archive_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
            extract_dir = Path(tmp) / "extracted"
            with tarfile.open(archive_path) as tar:
                tar.extractall(extract_dir)
        except (OSError, tarfile.TarError) as e:
            raise RuntimeError(f"JRE-Download/Entpacken fehlgeschlagen: {e}")

        top_level_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(top_level_dirs) != 1:
            raise RuntimeError("Unerwarteter Aufbau des JRE-Archivs")

        if JRE_DIR.exists():
            shutil.rmtree(JRE_DIR)
        shutil.move(str(top_level_dirs[0]), str(JRE_DIR))

    JRE_VERSION_FILE.write_text(version, encoding="utf-8")
    return version


def _release_asset(release):
    """(version, download_url) for a release's plain cross-platform .tar.gz asset, or None if this
    release doesn't have one. Releases also ship "-json-schemas"/"-Linux-client"/"-Linux-native"
    variants and .asc signature files alongside it - match the plain one exactly rather than the
    first *.tar.gz found."""
    tag = release.get("tag_name", "")
    version = tag[1:] if tag.startswith("v") else tag
    if not version:
        return None
    expected_name = f"signal-cli-{version}.tar.gz"
    asset = next(
        (a for a in release.get("assets", []) if a["name"] == expected_name),
        None
    )
    if not asset:
        return None
    return version, asset["browser_download_url"]


def _fetch_latest_release():
    """Just the newest release's version+url - used for the informational "latest available"
    display (refresh_latest_version). Not necessarily what install_latest() actually ends up
    installing, see its docstring."""
    request = urllib.request.Request(LATEST_RELEASE_API_URL, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
            data = json.load(response)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"signal-cli-Release-Info konnte nicht geladen werden: {e}")
    result = _release_asset(data)
    if not result:
        raise RuntimeError("Kein passendes signal-cli-Release-Archiv gefunden")
    return result


def _fetch_recent_releases(count):
    """Newest-first (version, url) list - used by install_latest() to walk backwards for a
    Java-compatible build."""
    request = urllib.request.Request(f"{RELEASES_API_URL}?per_page={count}", headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
            releases = json.load(response)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"signal-cli-Release-Liste konnte nicht geladen werden: {e}")
    return [r for r in (_release_asset(release) for release in releases) if r]


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


def _download_and_extract(url, dest_dir):
    """Downloads+extracts into dest_dir/candidate (overwriting any previous candidate), returns
    that path."""
    with _temp_dir() as tmp:
        archive_path = Path(tmp) / "signal-cli.tar.gz"
        request = urllib.request.Request(url, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response, \
                open(archive_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        extract_dir = Path(tmp) / "extracted"
        with tarfile.open(archive_path) as tar:
            tar.extractall(extract_dir)

        top_level_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(top_level_dirs) != 1:
            raise RuntimeError("Unerwarteter Aufbau des signal-cli-Release-Archivs")

        candidate_dir = dest_dir / "candidate"
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        shutil.move(str(top_level_dirs[0]), str(candidate_dir))
        return candidate_dir


def _is_linux_aarch64():
    return platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64")


def _find_bundled_libsignal_client_jar(candidate_dir):
    """(jar_path, version) for the libsignal-client-*.jar bundled in this signal-cli build - the
    native-library patch below needs to match this exact version, not signal-cli's own version."""
    matches = list((candidate_dir / "lib").glob("libsignal-client-*.jar"))
    if len(matches) != 1:
        raise RuntimeError(
            f"libsignal-client-*.jar nicht eindeutig gefunden ({len(matches)} Treffer statt 1)"
        )
    jar_path = matches[0]
    version = jar_path.stem[len("libsignal-client-"):]
    return jar_path, version


def _patch_native_library_for_linux_aarch64(candidate_dir):
    """Swaps signal-cli's bundled libsignal-client jar's native library for a prebuilt aarch64
    Linux one from exquo/signal-libs-build (see module docstring) - raises RuntimeError if no
    build matching the exact bundled libsignal-client version exists yet (community builds can lag
    a new signal-cli release by a bit), letting install_latest()'s caller fall back to the
    next-older signal-cli release instead."""
    jar_path, version = _find_bundled_libsignal_client_jar(candidate_dir)

    request = urllib.request.Request(
        NATIVE_LIB_RELEASES_API_URL.format(version=version), headers=REQUEST_HEADERS
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
            release = json.load(response)
    except (OSError, ValueError) as e:
        raise RuntimeError(
            f"Kein aarch64-Linux-Native-Build für libsignal-client {version} gefunden "
            f"(exquo/signal-libs-build): {e}"
        )
    asset_name = f"libsignal_jni.so-v{version}-aarch64-unknown-linux-gnu.tar.gz"
    asset = next((a for a in release.get("assets", []) if a["name"] == asset_name), None)
    if not asset:
        raise RuntimeError(f"Kein aarch64-Linux-Native-Build für libsignal-client {version} gefunden")

    with _temp_dir() as tmp:
        archive_path = Path(tmp) / "libsignal_jni.tar.gz"
        req = urllib.request.Request(asset["browser_download_url"], headers=REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as response, \
                open(archive_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        extract_dir = Path(tmp) / "extracted"
        with tarfile.open(archive_path) as tar:
            tar.extractall(extract_dir)
        so_path = extract_dir / "libsignal_jni.so"
        if not so_path.exists():
            raise RuntimeError("libsignal_jni.so nicht im heruntergeladenen Archiv gefunden")

        # Rewrite the jar: drop every existing native signal_jni entry (amd64/macOS builds this
        # platform could never use anyway) and add the aarch64 one - plain zipfile, no dependency
        # on a shell zip/unzip binary being installed.
        patched_path = jar_path.with_suffix(".jar.patched")
        with zipfile.ZipFile(jar_path, "r") as src, \
                zipfile.ZipFile(patched_path, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if "signal_jni" in item.filename:
                    continue
                dst.writestr(item, src.read(item.filename))
            dst.write(so_path, "libsignal_jni.so")
        patched_path.replace(jar_path)


def _verify_runs(bin_path):
    """True if this signal-cli build actually starts under the available Java (bundled JRE
    preferred, see build_subprocess_env()) - this is how a Java-version mismatch (see module
    docstring) gets detected, without this module needing to know Java version numbers itself."""
    try:
        result = subprocess.run(
            [str(bin_path), "--version"], capture_output=True, text=True, timeout=VERIFY_TIMEOUT_S,
            env=build_subprocess_env()
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_latest():
    """Ensures a bundled JRE is available (best-effort - failure here just means falling back to
    whatever Java is already on the system, same as before bundling existed), then downloads the
    newest signal-cli release and verifies it actually runs, falling back to the next-older
    release(s) if not (see module docstring - with a bundled JRE this should succeed on the first/
    newest candidate in the common case, the walkback is now mainly a safety net). Blocks for the
    duration of the download(s) - same trade-off as the already-synchronous git-clone-on-plugin-add
    core endpoint. Raises RuntimeError if none of the last MAX_COMPAT_ATTEMPTS releases run at all."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        install_jre()
    except RuntimeError as e:
        logger.info(f"Messenger: kein gebündeltes JRE installiert, nutze System-Java: {e}")

    releases = _fetch_recent_releases(MAX_COMPAT_ATTEMPTS)
    if not releases:
        raise RuntimeError("Keine passenden signal-cli-Releases gefunden")

    last_error = None
    for version, url in releases:
        try:
            candidate_dir = _download_and_extract(url, BIN_DIR)
        except (OSError, tarfile.TarError, RuntimeError) as e:
            last_error = str(e)
            continue

        bin_path = candidate_dir / "bin" / "signal-cli"
        if bin_path.exists():
            bin_path.chmod(0o755)  # tarball permissions aren't always preserved consistently

        if _is_linux_aarch64():
            try:
                _patch_native_library_for_linux_aarch64(candidate_dir)
            except RuntimeError as e:
                shutil.rmtree(candidate_dir, ignore_errors=True)
                last_error = str(e)
                continue

        if _verify_runs(bin_path):
            if CURRENT_DIR.exists():
                shutil.rmtree(CURRENT_DIR)
            shutil.move(str(candidate_dir), str(CURRENT_DIR))
            VERSION_FILE.write_text(version, encoding="utf-8")
            with _state_lock:
                _last_check.update(latest_version=releases[0][0], error=None)
            return version

        shutil.rmtree(candidate_dir, ignore_errors=True)
        last_error = f"Version {version} startet nicht mit der verfügbaren Java-Laufzeitumgebung"

    raise RuntimeError(
        f"Keine der letzten {len(releases)} signal-cli-Versionen läuft mit der verfügbaren "
        f"Java-Laufzeitumgebung. Letzter Fehler: {last_error}"
    )


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
