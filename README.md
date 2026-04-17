# YTD Clone — Tester Build

Thanks for helping test this! It's a simple YouTube downloader.

## How to use it

1. **Double-click `YTDClone.exe`.** First launch takes ~5 seconds (Windows unpacks it).
2. Paste a YouTube URL into the box.
3. Pick a quality. **Best Available** is usually what you want.
4. Click **DOWNLOAD**.
5. Your file lands in `Downloads` by default. Change the folder with the 📁 icon if you want.

That's it. Downloads show up in the **Activity** tab.

## The exe won't open / Windows says "unrecognized"

This happens with any unsigned `.exe` from a small developer. It's not a virus — it's Microsoft being careful.

- A blue "Windows protected your PC" screen will appear.
- Click **More info** → **Run anyway**.
- You only have to do this once.

## I get an error when I download a video

Try these in order:

- **Pick a different quality.** Some videos don't have 4K or 1440p available.
- **Try a different video.** Some videos are age-restricted, region-locked, or members-only.
- **Restart the app.** Takes two seconds and fixes most weird states.
- **Check the title bar.** It should say `✓ ffmpeg`. If it says `✗ no ffmpeg`, tell me — that means the bundled ffmpeg didn't unpack correctly.

## How do I report a bug?

Take a screenshot of the error and send it to me along with:

- The YouTube URL you were trying to download.
- The quality you picked.
- Whether you'd downloaded anything before in the same session.

## What gets saved where?

- Downloads: wherever you chose (default is `Downloads` in your user folder).
- App settings: `%APPDATA%\YTDClone\settings.json` — remembers your save folder and quality between runs.
- Nothing else. No telemetry, no account, no network calls except to YouTube itself and a once-per-day check to GitHub to see if yt-dlp has a newer version.

## Known limitations in this build

- No pause/cancel mid-download. If you need to stop, close the app.
- No playlist support. Paste one video URL at a time.
- Premium/Upgrade buttons in the UI are decorative — there's nothing behind them.

---

## Build from source

If you want to rebuild the `.exe` yourself (for example, to ship an updated yt-dlp when YouTube changes things), you'll need:

- **Windows 10 or 11**
- **Python 3.10 – 3.13** installed and on `PATH` (check with `python --version`). Python 3.14 works too, but some wheels lag behind and 3.12 is the safest choice.
- **Internet access** — the build script downloads the latest yt-dlp and ffmpeg automatically.

Then from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The first build takes 2–5 minutes (downloads ffmpeg, installs PyInstaller). Subsequent builds are faster because ffmpeg is cached. Output lands at `dist\YTDClone.exe` — that's the single file you can ship to testers.

**To run from source without building**, just install the two Python dependencies and launch the script directly:

```powershell
pip install -U yt-dlp
winget install Gyan.FFmpeg   # or install ffmpeg any other way you like
python ytd_clone.py
```

## Credits

This app is a thin GUI on top of two excellent open-source projects that do all the real work:

- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — the video downloader backend. Unlicense / public domain.
- **[FFmpeg](https://ffmpeg.org/)** — handles stream merging and format conversion. LGPL / GPL.
- **Gyan Doshi's FFmpeg Windows builds** at [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) — what the build script downloads and bundles.

The bundled `YTDClone.exe` includes FFmpeg binaries. FFmpeg is licensed under the LGPL, which permits redistribution in this form; see the FFmpeg site for full license text.
