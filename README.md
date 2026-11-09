# Shutter Light Measure

This project contains:

- a Windows desktop app for testing shutter timing
- a Pico firmware project for the shutter tester device
- the serial protocol documentation used by the app and device

## Project layout

- `desktop_app/shutter_app.py` — desktop GUI app
- `PROTOCOL.md` — serial protocol between PC and device
- `shutter_tester_pico/` — Pico firmware source and build files
- `requirements.txt` — Python dependencies
- `dist/ShutterTester.exe` — built single-file Windows executable

## Python app setup

1. Open a terminal in the project root.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Run the app directly:

```bash
python desktop_app\shutter_app.py
```

## Build a single EXE

The project already includes a generated PyInstaller spec file, so the simplest rebuild command is:

```bash
python -m PyInstaller ShutterTester.spec
```

This creates the single-file Windows executable in:

```text
dist\ShutterTester.exe
```

This build is a single-file Windows executable with no custom icon.

If you need to regenerate the spec file from scratch, use:

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name ShutterTester --hidden-import serial --hidden-import serial.tools.list_ports --hidden-import customtkinter .\desktop_app\shutter_app.py
```

## Notes

- The app communicates with the Pico over serial at 115200 baud.
- The protocol details are documented in [PROTOCOL.md](PROTOCOL.md).
- The Pico firmware is built with CMake and the Pico SDK.
