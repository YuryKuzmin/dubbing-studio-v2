# Building DubbingStudioV2.exe for your team

## One-time setup

```
pip install pyinstaller
```
(already installed on this machine)

## Build

1. Create `secrets.json` next to `build_exe.py` with your real key:
   ```json
   {"elevenlabs_api_key": "xi-..."}
   ```
2. Run:
   ```
   python build_exe.py
   ```
3. Send `dist\DubbingStudioV2.exe` (~8 MB) to your teammates.
   `secrets.json` stays on your machine — it is not bundled as a file.

## What teammates experience

Double-click the exe → a console window opens (this is the app; closing it
quits) → the browser opens the studio automatically. No API-key field is shown;
the key is built in. If the default port is taken (e.g. two instances), the app
picks the next free port automatically.

## Security expectations — read this

- The key is embedded **obfuscated, not encrypted**. It won't show up by
  opening the exe in a text editor, but a technically determined person can
  extract it with free tools. More importantly, anyone with the exe can spend
  your ElevenLabs credits through the app itself.
- Therefore: give the exe only to people you'd trust with the key, and if a
  copy escapes, rotate the API key at ElevenLabs and rebuild.
- Windows Defender / SmartScreen sometimes flags unsigned PyInstaller exes
  ("Windows protected your PC"). Teammates can click "More info → Run anyway".
  If your org's antivirus quarantines it, the hosted option (DEPLOY.md) avoids
  this entirely.

## Rebuilding after changes

Any edit to `app.py` or `index.html` requires rerunning `python build_exe.py`
and redistributing the exe. If you rotate the API key, update `secrets.json`
and rebuild.
