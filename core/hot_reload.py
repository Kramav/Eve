import importlib
import os
import sys
import threading
import time

# Auto-reload is a *developer* convenience (re-import command modules when their
# source changes on disk). It costs a 1-second filesystem poll for the life of
# the process, which is pure waste for someone just *using* Eve and never editing
# source. So it's opt-in: set EVE_DEV=1 (or EVE_HOT_RELOAD=1) to enable it.
def _dev_enabled() -> bool:
    return bool(os.environ.get("EVE_DEV") or os.environ.get("EVE_HOT_RELOAD"))


# Reload command modules first (dispatcher imports them), then dispatcher itself.
# Skills (skills/*.py, incl. youtube) aren't hot-reloaded — they're loaded once
# at startup and hold live state (mpv process, Display handle) reload would wipe.
_COMMAND_MODULES = [
    "commands.apps",
    "commands.system",
    "commands.search",
    "commands.reminders",
]
_DISPATCHER = "core.dispatcher"
_ALL = _COMMAND_MODULES + [_DISPATCHER]


def _mtime(module_name: str) -> float:
    mod = sys.modules.get(module_name)
    if mod and getattr(mod, "__file__", None):
        try:
            return os.path.getmtime(mod.__file__)
        except OSError:
            pass
    return 0.0


def _reload_all() -> None:
    for name in _COMMAND_MODULES:
        mod = sys.modules.get(name)
        if mod:
            try:
                importlib.reload(mod)
            except Exception as e:
                print(f"Hot-reload error ({name}): {e}")

    dispatcher = sys.modules.get(_DISPATCHER)
    if dispatcher:
        try:
            importlib.reload(dispatcher)
            print("Hot-reload: commands reloaded")
        except Exception as e:
            print(f"Hot-reload error (dispatcher): {e}")


def start() -> None:
    """Start the background source-file watcher. Call once at startup.

    No-op unless EVE_DEV / EVE_HOT_RELOAD is set — see module docstring. This
    keeps the always-on 1s poll out of normal use."""
    if not _dev_enabled():
        return
    print("Hot-reload: watching command modules (EVE_DEV)")
    mtimes = {name: _mtime(name) for name in _ALL}

    def _watch():
        while True:
            time.sleep(1)
            if any(_mtime(name) != mtimes[name] for name in _ALL):
                for name in _ALL:
                    mtimes[name] = _mtime(name)
                _reload_all()

    threading.Thread(target=_watch, daemon=True).start()
