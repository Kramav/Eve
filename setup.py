"""
Eve first-time setup. Run once after cloning:

    python setup.py

Re-running is safe — all steps check before acting.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))   # let setup import the project's own modules


# ── output helpers ────────────────────────────────────────────────────────────

def ok(msg):    print(f"  [OK] {msg}")
def info(msg):  print(f"       {msg}")
def warn(msg):  print(f"  [!!] {msg}")
def step(title):
    print(f"\n{title}")
    print("-" * len(title))

def die(msg):
    print(f"\n  [ERROR] {msg}")
    sys.exit(1)


# ── steps ─────────────────────────────────────────────────────────────────────

def check_python():
    step("Python version")
    if sys.version_info < (3, 11):
        die(f"Python 3.11+ required. Found: {sys.version.split()[0]}")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def install_packages():
    step("Python packages")
    info("Running pip install -r requirements.txt ...")
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')],
        )
        ok("Packages installed")
    except subprocess.CalledProcessError:
        die("pip install failed. Check the output above for details.")


def download_wake_words():
    step("Wake word models")
    try:
        import openwakeword
        # Check if the hey_jarvis model already exists
        resources = Path(openwakeword.__file__).parent / 'resources' / 'models'
        if any(resources.glob('hey_jarvis*.onnx')):
            ok("Wake word models already present")
            return
        info("Downloading pre-trained wake word models ...")
        openwakeword.utils.download_models()
        ok("Wake word models downloaded")
    except Exception as e:
        warn(f"Wake word download failed: {e}")
        warn("Run manually: python -c \"import openwakeword; openwakeword.utils.download_models()\"")


def download_voice_model():
    step("Piper TTS voice model")
    voices_dir = ROOT / 'models' / 'voices'
    voices_dir.mkdir(parents=True, exist_ok=True)

    # Read configured voice from config.py
    try:
        sys.path.insert(0, str(ROOT))
        from config import TTS_DEFAULT_VOICE
    except Exception:
        TTS_DEFAULT_VOICE = 'en_US-lessac-medium'

    onnx = voices_dir / f'{TTS_DEFAULT_VOICE}.onnx'
    meta = voices_dir / f'{TTS_DEFAULT_VOICE}.onnx.json'

    if onnx.exists() and meta.exists():
        ok(f"Voice model already present: {TTS_DEFAULT_VOICE}")
        return

    # Parse voice name → HuggingFace path
    # e.g. en_US-lessac-medium → en/en_US/lessac/medium
    parts = TTS_DEFAULT_VOICE.split('-')
    if len(parts) < 3:
        warn(f"Cannot parse voice name '{TTS_DEFAULT_VOICE}'. Download manually.")
        _print_voice_instructions(TTS_DEFAULT_VOICE, voices_dir)
        return

    lang_region = parts[0]
    lang        = lang_region.split('_')[0]
    speaker     = parts[1]
    quality     = parts[2]
    hf_base     = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/main"
        f"/{lang}/{lang_region}/{speaker}/{quality}"
    )

    for fname, dest in [
        (f'{TTS_DEFAULT_VOICE}.onnx',      onnx),
        (f'{TTS_DEFAULT_VOICE}.onnx.json', meta),
    ]:
        if dest.exists():
            ok(f"{fname} already present")
            continue
        url = f"{hf_base}/{fname}"
        info(f"Downloading {fname} ...")
        try:
            _download_with_progress(url, dest)
        except Exception as e:
            warn(f"Download failed: {e}")
            _print_voice_instructions(TTS_DEFAULT_VOICE, voices_dir)
            return

    ok(f"Voice model ready: {TTS_DEFAULT_VOICE}")


def _download_with_progress(url: str, dest: Path):
    def reporthook(count, block, total):
        if total <= 0:
            return
        pct = min(count * block * 100 / total, 100)
        filled = int(pct / 5)
        print(f"\r       [{'#' * filled}{' ' * (20 - filled)}] {pct:3.0f}%", end='', flush=True)
    # Download to a temp file and rename only on full success, so an interrupted
    # download can't leave a corrupt file that later passes the exists() check.
    tmp = dest.parent / (dest.name + '.part')
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=reporthook)
        print()
        tmp.replace(dest)
    except BaseException:
        print()
        if tmp.exists():
            tmp.unlink()
        raise


def _print_voice_instructions(voice: str, voices_dir: Path):
    info(f"Download manually from: https://huggingface.co/rhasspy/piper-voices")
    info(f"Place {voice}.onnx and {voice}.onnx.json in:")
    info(f"  {voices_dir}")


def npm_install():
    step("Electron (Node.js UI)")
    ui_dir = ROOT / 'ui'

    npm = shutil.which('npm')
    if not npm:
        warn("npm not found. Install Node.js from https://nodejs.org/ then re-run setup.py")
        return

    if (ui_dir / 'node_modules' / 'electron').exists():
        ok("Electron already installed")
        return

    node = shutil.which('node')
    if node:
        try:
            v = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()
            major = int(v.lstrip('v').split('.')[0])
            if major < 18:
                warn(f"Node {v} is old — Electron 42 needs Node 18+. Consider updating.")
        except Exception:
            pass

    info("Running npm install in ui/ ...")
    try:
        subprocess.check_call(
            [npm, 'install'],
            cwd=ui_dir,
            shell=(sys.platform == 'win32'),
        )
        ok("Electron installed")
    except subprocess.CalledProcessError:
        warn("npm install failed. Run manually: cd ui && npm install")


def check_mpv():
    step("mpv (YouTube playback)")
    if shutil.which('mpv'):
        result = subprocess.run(['mpv', '--version'], capture_output=True, text=True)
        version = result.stdout.splitlines()[0] if result.stdout else 'unknown version'
        ok(f"mpv found — {version}")
    else:
        warn("mpv not found on PATH — YouTube playback won't work")
        info("Install:  winget install mpv")
        info("Then restart your terminal for PATH to update, and re-run setup.py to verify")


def check_firefox():
    step("Firefox (web-search fallback)")
    try:
        from commands.apps import find_firefox
        found = find_firefox()
    except Exception:
        found = None
    if found:
        ok(f"Firefox found — {found}")
    else:
        warn("Firefox not found — the in-app-search-off fallback opens search in Firefox")
        info("Install from https://www.mozilla.org/firefox/ (or keep in-app search ON in the overlay)")


def create_defaults():
    step("Default config files")

    apps_file = ROOT / 'apps.json'
    if not apps_file.exists():
        apps_file.write_text('[]')
        ok("Created apps.json (empty — populate via App Manager or edit directly)")
    else:
        ok("apps.json already exists")

    features_file = ROOT / 'features.json'
    if not features_file.exists():
        # Import the single source of truth so this never drifts from the app.
        from core.features import DEFAULTS
        features_file.write_text(json.dumps(DEFAULTS, indent=2))
        ok("Created features.json (defaults)")
    else:
        ok("features.json already exists")

    dk = ROOT / 'discord_keys.json'
    dk_example = ROOT / 'discord_keys.example.json'
    if not dk.exists() and dk_example.exists():
        shutil.copyfile(dk_example, dk)
        ok("Created discord_keys.json from example")


def smoke_test():
    step("Smoke test")
    import importlib
    mods = ('core.dispatcher', 'core.display', 'core.listener',
            'core.speaker', 'core.transcriber')
    try:
        for m in mods:
            importlib.import_module(m)
        ok("Core modules import cleanly")
    except Exception as e:
        die(f"Core import failed: {e}\n"
            "         Setup is incomplete — resolve the above before running Eve.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("Eve Setup")
    print("=" * 48)

    check_python()
    install_packages()
    download_wake_words()
    download_voice_model()
    npm_install()
    check_mpv()
    check_firefox()
    create_defaults()
    smoke_test()

    print("\n" + "=" * 48)
    print("Done. Start Eve with:\n")
    print("    python main.py\n")


if __name__ == '__main__':
    main()
