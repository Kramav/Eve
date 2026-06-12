import importlib
import os
import sys
import threading
import time

# Reload command modules first (dispatcher imports them), then dispatcher itself.
# commands.youtube is excluded — it holds live Selenium driver + display state
# that would be wiped on reload.
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
    """Start the background file watcher. Call once at startup."""
    mtimes = {name: _mtime(name) for name in _ALL}

    def _watch():
        while True:
            time.sleep(1)
            if any(_mtime(name) != mtimes[name] for name in _ALL):
                for name in _ALL:
                    mtimes[name] = _mtime(name)
                _reload_all()

    threading.Thread(target=_watch, daemon=True).start()
