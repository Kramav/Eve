"""Native Windows toast notifications — no extra pip dependency.

Reminders already speak (TTS) and flash the HUD; a toast adds a persistent
entry in the Action Center for when the user is away from the screen or audio
is muted. We fire it through PowerShell's WinRT toast API so there's nothing to
install. Best-effort: any failure (no PowerShell, locked-down host) is swallowed
so a missing toast never breaks the reminder.
"""
import os
import subprocess

# PowerShell that builds a ToastGeneric notification and shows it under the
# Windows PowerShell AppUserModelID (already registered on every Win10/11 box,
# so the toast renders without registering our own shortcut). Title/body come
# in via env vars (EVE_TOAST_*) so there's no string-quoting or injection risk,
# and WinRT's XmlDocument.LoadXml escapes them into text nodes via [Security].
_PS = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]        | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]                     | Out-Null
$AppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$title = [Security.SecurityElement]::Escape($env:EVE_TOAST_TITLE)
$body  = [Security.SecurityElement]::Escape($env:EVE_TOAST_BODY)
$xml = "<toast><visual><binding template=`"ToastGeneric`"><text>$title</text><text>$body</text></binding></visual></toast>"
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
'''


def toast(title: str, message: str) -> bool:
    """Show a Windows toast. Returns True on success, False on any failure."""
    env = dict(os.environ)
    env["EVE_TOAST_TITLE"] = (title or "Eve").replace("\n", " ")
    env["EVE_TOAST_BODY"]  = (message or "").replace("\n", " ")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
            capture_output=True, timeout=10, env=env,
        )
        return proc.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    print("toast sent:", toast("Eve", "Toast notifications are working."))
