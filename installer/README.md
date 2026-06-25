# Packaging Eve into an installer

Goal: a single `Eve-Setup.exe` an end user can double-click — no Python, no
Node, no `setup.py`. Two stages: **build** a self-contained `dist\Eve\` folder,
then **wrap** it with Inno Setup ([eve.iss](eve.iss)).

> Status: the Inno Setup script is ready. The build stage below has not been run
> in CI — it's the recipe to produce `dist\Eve\`. Expect to iterate on the
> PyInstaller `--add-data`/`--hidden-import` flags the first time.

## 1. Build the Electron UI

```sh
cd ui
npm install          # pulls in electron + node_modules
npm run build        # if a build script exists; otherwise the src/ is used as-is
cd ..
```

The packaged app launches Electron from `ui/node_modules/.bin/electron` (see
`core/display.run_loop`), so `ui/` **with its `node_modules`** must end up inside
`dist\Eve\ui\`.

## 2. Freeze the Python side with PyInstaller

```sh
pip install pyinstaller
pyinstaller main.py --name Eve --onedir --noconsole ^
  --add-data "config.py;." ^
  --add-data "features.json;." ^
  --add-data "ui;ui" ^
  --add-data "models;models" ^
  --add-data "skills;skills" ^
  --collect-all openwakeword ^
  --collect-all piper ^
  --hidden-import sounddevice
```

Notes / likely tweaks:
- `--collect-all` for `openwakeword` and the Piper TTS package pulls in their
  bundled `.onnx`/data files (they load models by path at runtime).
- `whisper`/`faster-whisper` model files are large — ship them under `models\`
  and confirm `core/transcriber.py` resolves them relative to the exe, not CWD.
- `sounddevice` needs the PortAudio DLL; PyInstaller usually grabs it, verify.
- The result is `dist\Eve\Eve.exe` plus a folder of dependencies.

Copy the built `ui/` (with `node_modules`) into `dist\Eve\ui\` if PyInstaller's
`--add-data "ui;ui"` didn't include `node_modules` (it skips some dotfolders).

## 3. Compile the installer

Install Inno Setup 6 (<https://jrsoftware.org/isdl.php>), then:

```sh
iscc installer\eve.iss
```

Output: `installer\Output\Eve-Setup-0.1.0.exe`. Override the source folder or
version without editing the script:

```sh
iscc /DAppDir=..\dist\Eve /DMyAppVersion=0.2.0 installer\eve.iss
```

The installer offers an optional **desktop icon** and **run-at-login** task
(the latter writes the same HKCU `Run` key as `core/autostart.py`, and removes
it on uninstall).

## Alternative: winget

Once `Eve-Setup.exe` is attached to a GitHub Release, a winget manifest
(`Publisher.Eve`) pointing at the release asset makes `winget install eve`
work. Generate it with `wingetcreate new <release-url>` after the first release.
