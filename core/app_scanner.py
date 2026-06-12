"""
Scans Windows for installed, user-launchable applications.

Two sources (combined and deduplicated by exe path):
  1. Start Menu .lnk shortcuts — the same set Steam uses for "Add Non-Steam Game".
     Resolved via a PowerShell one-liner so no extra Python deps are needed.
  2. Windows Uninstall registry keys — catches apps that ship without Start Menu
     shortcuts (many games, CLI tools, etc.) using DisplayIcon as the exe path.
"""

import json
import os
import re
import subprocess
import winreg
from pathlib import Path


# ── Public entry point ──────────────────────────────────────────────────────

def scan() -> list[dict]:
    """Return sorted list of {name, path, spoken} for discoverable apps."""
    seen:  dict[str, dict] = {}

    for entry in _scan_start_menu():
        key = entry['path'].lower()
        if key not in seen:
            seen[key] = entry

    for entry in _scan_registry():
        key = entry['path'].lower()
        if key not in seen:
            seen[key] = entry

    return sorted(seen.values(), key=lambda x: x['name'].lower())


# ── Source 1: Start Menu shortcuts ─────────────────────────────────────────

_PS_SCAN = r"""
$shell = New-Object -COM WScript.Shell
$dirs  = @(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
    "C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
)
$out = [System.Collections.Generic.List[object]]::new()
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) { continue }
    Get-ChildItem $dir -Recurse -Filter *.lnk -ErrorAction SilentlyContinue |
    ForEach-Object {
        try {
            $lnk = $shell.CreateShortcut($_.FullName)
            $t   = $lnk.TargetPath
            if ($t -and $t.ToLower().EndsWith('.exe') -and (Test-Path $t)) {
                $out.Add([PSCustomObject]@{ Name = $_.BaseName; Path = $t })
            }
        } catch {}
    }
}
$out | ConvertTo-Json -Compress -Depth 2
"""


def _scan_start_menu() -> list[dict]:
    try:
        res = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', _PS_SCAN],
            capture_output=True, text=True, timeout=20,
        )
        raw = res.stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return [
            {'name': d['Name'], 'path': d['Path'], 'spoken': _clean(d['Name'])}
            for d in data if d.get('Name') and d.get('Path')
        ]
    except Exception:
        return []


# ── Source 2: Uninstall registry ────────────────────────────────────────────

_REG_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
    (winreg.HKEY_CURRENT_USER,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
]


def _scan_registry() -> list[dict]:
    apps = []
    for hive, key_path in _REG_KEYS:
        try:
            key = winreg.OpenKey(hive, key_path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    name = _reg(sub, 'DisplayName')
                    icon = _reg(sub, 'DisplayIcon') or ''
                    winreg.CloseKey(sub)
                    if not name:
                        continue
                    # DisplayIcon is often "C:\path\app.exe,0" — strip the icon index
                    exe = icon.split(',')[0].strip().strip('"')
                    if exe.lower().endswith('.exe') and os.path.isfile(exe):
                        apps.append({'name': name, 'path': exe, 'spoken': _clean(name)})
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
    return apps


def _reg(key, value):
    try:
        return winreg.QueryValueEx(key, value)[0]
    except Exception:
        return None


# ── Spoken name cleanup ─────────────────────────────────────────────────────

_VERSION_RE = re.compile(r'\s+v?\d[\d.]*.*$', re.I)
_PAREN_RE   = re.compile(r'\s*\(.*?\)', re.I)
_SUFFIX_RE  = re.compile(r'\s+(x64|x86|64.?bit|32.?bit|amd64)$', re.I)


def _clean(name: str) -> str:
    name = _VERSION_RE.sub('', name)
    name = _PAREN_RE.sub('', name)
    name = _SUFFIX_RE.sub('', name)
    return name.strip().lower()
