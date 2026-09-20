# Deriv Monitor — Android App

A real Kivy-based Android app version of the monitor: on-screen status,
Settings and Plan screens (no more editing JSON by hand), native Android
notifications for alerts, running as a background thread while the app
is open.

## What's different from the Termux script

- Settings (token, symbols, daily limit, email) are entered in a **Settings
  screen** in the app and saved automatically — no `nano`, no JSON editing.
- Trade plan (rules + supply zones) has its own **Plan screen**.
- Alerts fire as **native Android notifications**, not just email.
- A **Status screen** shows today's P&L, your limit bar, lock state, and a
  live event log, with a Start/Stop button.

## Important limitation — same as before, just worth repeating

This still only monitors while the **app is open in the foreground** (or
briefly backgrounded — Android will eventually suspend it like any app,
just as it would suspend Termux). For true always-on background operation
you'd want to promote the bot into a proper Android **foreground service**
— possible, but a further step beyond what's built here. Realistically:
keep your phone on, charging, and the app open/unlocked while trading.

## I can't compile the APK myself — here's how you actually get one

This needs the Android SDK/NDK build toolchain, which isn't available in
the environment I run in. You have two realistic paths:

### Option A — Build it for free with GitHub Actions (recommended, no PC needed)

1. Create a free account at **github.com** if you don't have one.
2. Create a new **public** repository (e.g. `deriv-monitor-app`).
3. Upload every file from this project into it — easiest way on
   mobile/web: use GitHub's **"Add file → Upload files"** button and drag
   in all the files, keeping the `.github/workflows/build-apk.yml` path
   exactly as-is (create the `.github/workflows/` folders when uploading —
   GitHub lets you type the full path in the file name box).
4. Go to the repo's **Actions** tab. A workflow called **"Build APK"**
   should appear — click **Run workflow**.
5. Wait (a first build typically takes **20–40 minutes** — Android
   toolchains are slow to set up). You can close the tab and come back.
6. When it finishes, open the completed run and download the
   **`deriv-monitor-apk`** artifact — that's a zip containing your `.apk`.
7. Transfer that `.apk` to your phone (download it directly if you're
   checking GitHub from your phone's browser) and tap it to install.
   You'll need to allow **"Install unknown apps"** for your browser in
   Android Settings the first time.

### Option B — Build it yourself on a Linux PC or WSL

```
pip install buildozer cython==0.29.36
buildozer android debug
```
Buildozer will download the Android SDK/NDK on first run (large, needs a
good internet connection and ~30-60 min). The APK appears in `bin/`.

## Files

- `main.py` — the Kivy app (UI + screens)
- `bot_core.py` — background thread wrapping the monitoring logic
- `deriv_client.py`, `risk_manager.py`, `strategy.py`, `news_monitor.py` —
  same logic as the desktop/Termux version, unchanged
- `buildozer.spec` — Android build configuration
- `.github/workflows/build-apk.yml` — the cloud build pipeline

## First run

1. Install the APK
2. Open the app, go to **Settings**
3. Enter your Deriv API token, account type (`demo` first!), symbols, and
   daily loss limit
4. Go to **Plan**, add your rules and supply zones
5. Go to **Status**, tap **Start monitoring**

Start on a **demo** account token, same advice as before, before pointing
this at real money.
