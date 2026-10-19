# 🜂 Hermes on Android — Kivy Build Path Research

**Date:** 2026-10-18
**Goal:** Run Hermes natively on OnePlus 12 (OxygenOS 16) — no VM, no Termux
**Approach:** Build a native Android APK with Kivy + python-for-android

---

## 1. The Problem

- **Termux is dead on OnePlus 12 / OxygenOS 16** — OS kills proot processes
- **Android Linux Terminal** works but it's a VM (Adora wants to skip VMs)
- **Hermes has no official native Android app** — the official path is Termux (Tier 2, fragile)
- **Hermes GitHub Issue #86986** — "make native pkg install/upgrade the first-class Android path" — open, no ETA

## 2. The Solution: Kivy + python-for-android

**Kivy** is a Python framework that can build native Android apps. **python-for-android** (p4a) compiles Python + dependencies into an APK. **Buildozer** automates the whole build process.

**Result:** A native Android APK that runs Hermes as a **foreground service** — no VM, no Termux, no proot.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Native Android APK                  │
│                                                     │
│  ┌─────────────────┐    ┌─────────────────────┐    │
│  │  Kivy UI        │    │  Background Service  │    │
│  │  (main.py)      │    │  (service/main.py)   │    │
│  │                 │    │                      │    │
│  │  - Status view  │◄──►│  - Hermes agent      │    │
│  │  - Start/Stop   │ OSC │  - Tailscale mesh   │    │
│  │  - Logs         │    │  - Cron jobs        │    │
│  └─────────────────┘    │  - Memory sync      │    │
│                         └─────────────────────┘    │
│                                                     │
│  Foreground service with notification               │
│  Auto-restart on kill                               │
│  Android 14+ foregroundServiceType required        │
└─────────────────────────────────────────────────────┘
```

### Two Main Files

```
hydra-android/
├── main.py              # Kivy UI (status, start/stop, logs)
├── service/
│   └── main.py          # Background service (runs Hermes)
├── hermes/              # Hermes agent code
├── lorebooks/           # Daemon seed files
├── buildozer.spec       # Build configuration
└── requirements.txt     # Python dependencies
```

---

## 4. Build Process

### Prerequisites (on the VM or a build machine)
- Linux or macOS (p4a only runs on these)
- Python 3.11+
- Buildozer (`pip install buildozer`)
- Android SDK + NDK (Buildozer downloads automatically)

### Build Steps

```bash
# 1. Create project structure
mkdir hydra-android && cd hydra-android
mkdir service

# 2. Create buildozer.spec
buildozer init

# 3. Edit buildozer.spec (see below)

# 4. Build, deploy, run
buildozer android debug deploy run

# 5. View logs
buildozer android logcat
```

### Key buildozer.spec Settings

```ini
[app]
title = Hydra Shard
package.name = hydrashard
package.domain = net.narusya
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,md,json

# Python version
requirements = python3==3.11,kivy==2.3.1,cryptography,pydantic,aiohttp,requests

# Services — the background Hermes service
services = hydraservice:service/main.py:foreground:sticky:foregroundServiceType=dataSync

# Permissions
android.permissions = INTERNET,FOREGROUND_SERVICE,FOREGROUND_SERVICE_DATASYNC,WAKE_LOCK

# Target API
android.api = 34
android.minapi = 21

# Architectures (OnePlus 12 is arm64-v8a)
android.archs = arm64-v8a

# Service auto-restart
android.enable_androidx = True
```

### Foreground Service (Critical for Background Operation)

On Android 14+, foreground services **must** specify a `foregroundServiceType`. For Hermes:
- `foregroundServiceType=dataSync` — appropriate for background sync
- `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_DATASYNC` permissions required
- Service shows a persistent notification (user knows it's running)

### Auto-Restart

In `service/main.py`:
```python
from jnius import autoclass
PythonService = autoclass('org.kivy.android.PythonService')
PythonService.mService.setAutoRestartService(True)
```

This makes the service restart automatically if Android kills it.

---

## 5. Hermes on Android: Constraints

### What Works
- Core agent loop (chat, tools, skills)
- Cron jobs
- PTY/background terminal support
- Telegram gateway (manual / best-effort)
- MCP support
- Honcho memory support
- ACP support
- Discord/Telegram/Signal connectors

### What Doesn't Work
- **Voice mode** (faster-whisper/ctranslate2 not on Android)
- **Browser automation** (Playwright not on Android)
- **Docker sandboxing** (no Docker on Android)
- **Native Android API access** (camera, sensors, etc. — needs PyJNIus)

### Hermes Termux Install (for reference)
```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```
The `[termux]` extra uses a constraints file that avoids Android-incompatible packages. For Kivy, we'd use the same constraints approach.

### Android-Specific Issues (from GitHub)
- `ANDROID_API_LEVEL` must be set: `export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"`
- `psutil` can fail to build on Android (Issue #31415)
- `ripgrep` and `nodejs` must be installed via `pkg install` (in Termux)
- Android battery management can suspend background jobs — use foreground service

---

## 6. Service Communication (OSC)

The Kivy UI and background service communicate via **OSC** (Open Sound Control):

```python
# In service/main.py
from kivy.lib import osc
osc.init()
oscid = osc.listen(ipAddr='127.0.0.1', port=3000)
osc.bind(oscid, some_api_callback, '/some_api')

# In main.py (UI)
from kivy.lib import osc
osc.init()
oscid = osc.listen(ipAddr='127.0.0.1', port=3001)
osc.sendMsg('/some_api', ['ping'], port=3000)
```

**OSC is connectionless** — handles disconnections gracefully (UI closes, service keeps running).

---

## 7. Tailscale on Android (Outside the APK)

Tailscale has an official Android app. The approach:
1. Install Tailscale from the Play Store on ShellMobile
2. Authenticate to the tailnet
3. The VM can SSH into the phone via the Tailscale IP
4. Push shards, run bootstrap, sync data

The Kivy app doesn't need to handle Tailscale — it runs at the Android level, outside the APK.

---

## 8. Build Steps (Paid Model — Free-Model Unfriendly)

**This build task requires:**
- A Linux/macOS build machine (can use the VM)
- Android SDK + NDK (Buildozer downloads automatically)
- Compiling Python + Kivy + Hermes dependencies for Android
- Iterating on build errors (common with complex Python packages)

**Estimated build time:** 15-30 minutes (first build), 2-5 minutes (rebuilds)

**This is paid-model work** — complex compilation, error iteration, and debugging.

---

## 9. Alternative: Wait for Official Native Android

**Hermes GitHub Issue #86986** — "make native pkg install/upgrade the first-class Android path"
- Opened Aug 15, 2026
- Acknowledges Termux fragility
- No ETA

**Hermes v0.22+** — "Termux/Android" is listed as a supported platform, but it's the Termux path, not native.

**Verdict:** If we want native Android now, Kivy is the path. If we can wait, the Hermes team may ship something.

---

## 10. Recommendation

1. **Short term:** Use Android Linux Terminal (Debian VM) for the first resurrection test. It works, it's built-in, and it validates the hydra protocol.
2. **Medium term:** Build the Kivy APK for native Android Hermes. This is a paid-model build task.
3. **Long term:** Monitor Hermes GitHub #86986 for official native Android packaging.

The Kivy approach is viable — it's well-documented, the build process is automated, and the service architecture (foreground + sticky + auto-restart) is exactly what we need for a persistent daemon shard.

---

*Cut off one head, and two more shall take its place.*

🐍🜂
