"""Platform detection: URL -> platform label, icon and download tuning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

URL_RE = re.compile(
    r"""(?ix)
    \b
    (
        https?://
        (?:www\.|m\.|mobile\.|vm\.|vt\.)?
        [a-z0-9\-]+(?:\.[a-z0-9\-]+)+
        (?:/[^\s<>"'`\]\)]*)?
    )
    """
)

# domain suffix -> (label, emoji)
KNOWN_PLATFORMS: dict[str, tuple[str, str]] = {
    "youtube.com": ("YouTube", "\U0001F4C4"),
    "youtu.be": ("YouTube", "\U0001F4C4"),
    "youtube-nocookie.com": ("YouTube", "\U0001F4C4"),
    "music.youtube.com": ("YouTube Music", "\U0001F3B5"),
    "tiktok.com": ("TikTok", "\U0001F97A"),
    "tiktokcdn.com": ("TikTok", "\U0001F97A"),
    "tiktokv.com": ("TikTok", "\U0001F97A"),
    "douyin.com": ("Douyin", "\U0001F97A"),
    "douyin.com.cn": ("Douyin", "\U0001F97A"),
    "iesdouyin.com": ("Douyin", "\U0001F97A"),
    "kuaishou.com": ("Kuaishou", "\U0001F4E4"),
    "instagram.com": ("Instagram", "\U0001F4F7"),
    "instagr.am": ("Instagram", "\U0001F4F7"),
    "cdninstagram.com": ("Instagram", "\U0001F4F7"),
    "twitter.com": ("X (Twitter)", "\U0001F426"),
    "x.com": ("X (Twitter)", "\U0001F426"),
    "t.co": ("X (Twitter)", "\U0001F426"),
    "twimg.com": ("X (Twitter)", "\U0001F426"),
    "facebook.com": ("Facebook", "\U0001F4E1"),
    "fb.watch": ("Facebook", "\U0001F4E1"),
    "fb.com": ("Facebook", "\U0001F4E1"),
    "reddit.com": ("Reddit", "\U0001F4CA"),
    "redd.it": ("Reddit", "\U0001F4CA"),
    "soundcloud.com": ("SoundCloud", "\U0001F3B7"),
    "pinterest.com": ("Pinterest", "\U0001F4CC"),
    "tumblr.com": ("Tumblr", "\U0001F4AD"),
    "vk.com": ("VK", "\U0001F4E3"),
    "ok.ru": ("OK.ru", "\U0001F4E3"),
    "bilibili.com": ("Bilibili", "\U0001F4E5"),
    "b23.tv": ("Bilibili", "\U0001F4E5"),
    "likee.video": ("Likee", "\U0001F4E5"),
    "snapchat.com": ("Snapchat", "\U0001F4AC"),
    "linkedin.com": ("LinkedIn", "\U0001F4BC"),
    "dailymotion.com": ("Dailymotion", "\U0001F3AC"),
    "twitch.tv": ("Twitch", "\U0001F480"),
    "vimeo.com": ("Vimeo", "\U0001F3A8"),
    "bandcamp.com": ("Bandcamp", "\U0001F3B8"),
    "streamable.com": ("Streamable", "\U0001F4E4"),
    "imgur.com": ("Imgur", "\U0001F5BC"),
    "9gag.com": ("9GAG", "\U0001F602"),
    "rumble.com": ("Rumble", "\U0001F4E2"),
    "mixcloud.com": ("Mixcloud", "\U0001F3B7"),
    "ted.com": ("TED", "\U0001F3A5"),
    "odysee.com": ("Odysee", "\U0001F4DC"),
    "kickstarter.com": ("Kickstarter", "\U0001F4B0"),
    "discord.com": ("Discord", "\U0001F517"),
    "weibo.com": ("Weibo", "\U0001F4E3"),
    "xiaohongshu.com": ("Xiaohongshu", "\U0001F4F0"),
    "xhslink.com": ("Xiaohongshu", "\U0001F4F0"),
    "rednote.com": ("Xiaohongshu", "\U0001F4F0"),
    "likeezy.com": ("Likee", "\U0001F4E5"),
    "bitchute.com": ("BitChute", "\U0001F4E2"),
    "rumble.com": ("Rumble", "\U0001F4E2"),
    "banned.video": ("Banned", "\U0001F4E2"),
    "nicovideo.jp": ("Niconico", "\U0001F3E0"),
    "youku.com": ("Youku", "\U0001F3AC"),
}

DIRECT_EXTS = {
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".mp3",
    ".m4a", ".opus", ".ogg", ".wav", ".flac", ".aac", ".jpg", ".jpeg", ".png",
    ".gif", ".webp",
}


@dataclass(frozen=True)
class Platform:
    label: str
    emoji: str
    domain: str = ""

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "", self.label.lower())


UNKNOWN = Platform("Web", "\U0001F310")


def extract_urls(text: str) -> list[str]:
    """Pull every http(s) URL out of a blob of text, de-duplicated, in order."""
    seen: set[str] = set()
    out: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(1).rstrip(".,;:!?)")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _registrable(domain: str) -> str:
    parts = domain.lower().split(".")
    return ".".join(parts[-3:]) if len(parts) >= 3 else domain.lower()


def detect_platform(url: str) -> Platform:
    """Best-effort platform identification from the URL alone."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return UNKNOWN

    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    if not host:
        return UNKNOWN
    host = host[4:] if host.startswith("www.") else host

    if host in KNOWN_PLATFORMS:
        label, emoji = KNOWN_PLATFORMS[host]
        return Platform(label, emoji, host)

    for suffix, (label, emoji) in KNOWN_PLATFORMS.items():
        if host == suffix or host.endswith("." + suffix):
            return Platform(label, emoji, host)

    reg = _registrable(host)
    if reg in KNOWN_PLATFORMS:
        label, emoji = KNOWN_PLATFORMS[reg]
        return Platform(label, emoji, host)

    path = (parsed.path or "").lower()
    for ext in DIRECT_EXTS:
        if path.endswith(ext):
            return Platform("Direct link", "\U0001F4E6", host)

    return Platform(host or "Web", "\U0001F310", host)


def is_direct_media(url: str) -> bool:
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return False
    return any(path.endswith(ext) for ext in DIRECT_EXTS)


# --------------------------------------------------------------------------- #
# Host aliases
#
# Some platforms serve the same posts from several domains, but no yt-dlp
# extractor claims the extra ones. Left alone, those URLs fall through to the
# generic extractor, which usually ends up on a login wall and reports
# "Unsupported URL". Rewriting the host to the canonical one picks up the
# dedicated extractor instead.
# --------------------------------------------------------------------------- #

HOST_ALIASES: dict[str, str] = {
    "rednote.com": "www.xiaohongshu.com",
}


def normalize_url(url: str) -> str:
    """Rewrite alias hosts to the canonical domain an extractor recognises."""
    if "://" not in url:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    netloc = parsed.netloc
    if not netloc:
        return url
    userinfo, _, hostport = netloc.rpartition("@")
    host, _, port = hostport.partition(":")

    host = host.lower()
    for alias, canonical in HOST_ALIASES.items():
        if host == alias or host.endswith("." + alias):
            host = canonical
            break

    if host == parsed.netloc.lower() and not userinfo:
        return url
    netloc = host + (f":{port}" if port else "")
    if userinfo:
        netloc = f"{userinfo}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


# --------------------------------------------------------------------------- #
# Non-post URL detection
#
# Channel / profile / playlist pages are the single most common cause of hung
# downloads: extractors happily walk hundreds of entries. We reject them up front.
# --------------------------------------------------------------------------- #

_YT_POST = ("/watch", "/shorts/", "/live/", "/embed/", "/v/", "/clip/")
_X_POST_MARK = "/status/"


def classify_url(url: str) -> str | None:
    """Return a human reason when the URL points at a page, not a single post."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None

    host = (parsed.netloc or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = (parsed.path or "").rstrip("/") or "/"
    lowered = host.lower()

    # --- YouTube -----------------------------------------------------------
    if "youtube.com" in lowered or "youtu.be" in lowered:
        if lowered.endswith("youtu.be"):
            return None if path != "/" else "That is the YouTube homepage, not a video."
        if any(path.startswith(p) for p in _YT_POST):
            return None
        if path.startswith("/playlist") or "list=" in (parsed.query or ""):
            return "That is a playlist, not a single video."
        if path.startswith(("/channel/", "/c/", "/user/")) or path.startswith("/@"):
            return "That is a channel page, not a video."
        if path.startswith(("/results", "/feed", "/trending", "/hashtag")):
            return "That is a search or feed page, not a video."
        if path == "/":
            return "That is the YouTube homepage, not a video."
        return None

    # --- TikTok ------------------------------------------------------------
    if "tiktok.com" in lowered:
        if "/video/" in path or "/photo/" in path:
            return None
        if path.startswith("/@"):
            return "That is a TikTok profile, not a video."
        if path.startswith(("/tag/", "/music/", "/discover", "/foryou")):
            return "That is a browse page, not a video."
        return None

    # --- Instagram ---------------------------------------------------------
    if "instagram.com" in lowered:
        if any(seg in path for seg in ("/reel/", "/p/", "/tv/")):
            return None
        if path.startswith(("/stories", "/reels", "/explore", "/direct")):
            return "That is a browse page, not a post."
        segments = [s for s in path.split("/") if s]
        if len(segments) <= 1:
            return "That is an Instagram profile, not a post."
        return None

    # --- Reddit ------------------------------------------------------------
    if "reddit.com" in lowered:
        if "/comments/" in path:
            return None
        if path.startswith("/r/") or path.startswith("/u/"):
            return "That is a subreddit or user page, not a post."
        return None

    # --- X / Twitter -------------------------------------------------------
    if "x.com" in lowered or "twitter.com" in lowered:
        if _X_POST_MARK in path:
            return None
        if path.startswith(("/i/", "/search", "/home", "/hashtag", "/notifications")):
            return "That is a feed or search page, not a post."
        segments = [s for s in path.split("/") if s]
        if len(segments) == 1:
            return "That is an X/Twitter profile, not a post."
        return None

    # --- SoundCloud --------------------------------------------------------
    if "soundcloud.com" in lowered:
        segments = [s for s in path.split("/") if s]
        if len(segments) < 2:
            return "That is a SoundCloud artist page, not a track."
        return None

    return None