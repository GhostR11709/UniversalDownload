# GhostR Universal Downloader

A Chrome MV3 extension backed by a quiet Windows Python server. Paste a link, inspect the media, choose quality, and save the result through Chrome. The server uses `yt-dlp` for extraction and FFmpeg for merging and conversion.

## Features

- Polished popup with current-tab capture, paste, metadata preview, quality choices, queue progress, cancel, save, and reveal-folder actions.
- Right-click any link, page, video, or audio and choose **Download with GhostR**.
- Local-only API on `127.0.0.1:8756`; the extension does not send links through a hosted middleman.
- Background launch with a packaged `UD Server.exe` process and a system-tray icon; no command window during normal use.
- Cookies, browser impersonation, size limits, quality limits, concurrent jobs, and retry settings through `.env`.
- `update.bat` pulls the latest GitHub version while preserving `.venv`, `.env`, FFmpeg, logs, and downloaded files.

## One-click Windows setup

1. Install Python 3.10+ and Git if you want Git-based updates.
2. Double-click `setup.bat`.
3. When setup finishes, open `chrome://extensions`, enable **Developer mode**, and choose **Load unpacked**.
4. Select the repository's `extension` folder.

Chrome does not allow a normal downloaded folder to silently install an unpacked extension. That one manual load is a browser security rule, not a bug in GhostR.

The setup script creates `.venv`, builds `UD Server.exe`, installs Python dependencies, downloads a local FFmpeg build if FFmpeg is not already on `PATH`, registers Windows startup, and starts the server. It does not open the local dashboard automatically; the extension is the normal interface.

## Updating from GitHub

This repository is designed to be downloaded or cloned from:

`https://github.com/GhostR11709/UniversalDownload`

Run `update.bat` whenever you want to pull the latest version. For Git clones it uses `git pull --ff-only`; for ZIP downloads it fetches the latest `main` archive and replaces project files while preserving local runtime data. Reload the unpacked extension on `chrome://extensions` after updating so Chrome picks up new popup code.

GitHub itself cannot remotely update an unpacked Chrome extension. If you later publish the extension in the Chrome Web Store, Chrome's normal store update channel can be used; until then `update.bat` plus one extension reload is the reliable path.

## Optional `.env`

Copy `.env.example` to `.env` for server settings. To use a browser session for sites that require login, prefer an exported `cookies.txt` file or configure `COOKIES_FROM_BROWSER=chrome`. Never commit cookies or tokens.

## Developer checks

```powershell
py -3 -m compileall -q core server
py -3 -m server --no-tray --open
```

The API is intentionally small: `/api/health`, `/api/detect`, `/api/downloads`, `/api/jobs`, `/api/jobs/{id}/cancel`, `/files/{id}`, `/api/reveal`, `/api/settings`, and `/api/update`.

## Responsible use

Only download media you have the right to save. Respect platform terms, copyright, privacy, access controls, and DRM. DRM-protected media is intentionally unsupported.

## License

MIT. See [LICENSE](LICENSE).
