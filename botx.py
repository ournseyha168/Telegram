
import asyncio
import html
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yt_dlp
from telegram import InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ខ្ញុំបានដាក់ Token របស់អ្នកជា Default នៅទីនេះ
BOT_TOKEN = os.getenv("BOT_TOKEN", "8749240113:AAHHnSClHpbMNpIT7pY-BGw-vYg8WpRfqeM").strip()

USE_LOCAL_BOT_API = os.getenv("USE_LOCAL_BOT_API", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
LOCAL_BOT_API_BASE = os.getenv("LOCAL_BOT_API_BASE", "http://127.0.0.1:8081/bot").strip()
LOCAL_BOT_API_FILE_BASE = os.getenv("LOCAL_BOT_API_FILE_BASE", "http://127.0.0.1:8081/file/bot").strip()

MAX_FILE_SIZE_MB = 1024 if USE_LOCAL_BOT_API else 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

URL_RE = re.compile(r"https?://\S+")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""

def first_existing_path(*candidates: str) -> str:
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ""

COOKIE_FB = env_first("COOKIE_FB", "cookie_fb")
COOKIE_X = env_first("COOKIE_X", "cookie_x")
COOKIE_TT = env_first("COOKIE_TT", "cookie_tt")
COOKIE_YT = env_first("COOKIE_YT", "cookie_yt")

def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None

def is_x_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(x in host for x in ("x.com", "twitter.com"))

def valid_cookie_path(path: str) -> str | None:
    if not path:
        return None
    p = Path(path)
    return str(p) if p.is_file() else None

def get_cookie_file(url: str) -> str | None:
    host = urlparse(url).netloc.lower()

    if any(x in host for x in ("facebook.com", "fb.watch", "m.facebook.com")):
        return valid_cookie_path(COOKIE_FB) or valid_cookie_path(
            first_existing_path("cookies_fb.txt", "cookies_fb")
        )

    if any(x in host for x in ("x.com", "twitter.com", "www.x.com")):
        return valid_cookie_path(COOKIE_X) or valid_cookie_path(
            first_existing_path("cookies_x.txt", "cookies_x")
        )

    if any(x in host for x in ("tiktok.com", "www.tiktok.com", "vm.tiktok.com")):
        return valid_cookie_path(COOKIE_TT) or valid_cookie_path(
            first_existing_path("cookies_tt.txt", "cookies_tt", "cookies_tiktok.txt")
        )

    if any(x in host for x in ("youtube.com", "youtu.be", "m.youtube.com")):
        return valid_cookie_path(COOKIE_YT) or valid_cookie_path(
            first_existing_path("cookies_yt.txt", "cookies_yt", "cookies_youtube.txt")
        )

    return None

def build_browser_headers(url: str) -> dict:
    host = urlparse(url).netloc.lower()
    referer = "https://www.google.com/"

    if "facebook.com" in host or "fb.watch" in host:
        referer = "https://www.facebook.com/"
    elif "x.com" in host or "twitter.com" in host:
        referer = "https://x.com/"
    elif "tiktok.com" in host:
        referer = "https://www.tiktok.com/"
    elif "youtube.com" in host or "youtu.be" in host:
        referer = "https://www.youtube.com/"

    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }

def read_netscape_cookies(cookie_file: str | None) -> list[dict]:
    if not cookie_file or not Path(cookie_file).is_file():
        return []

    cookies: list[dict] = []

    for raw_line in Path(cookie_file).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain, _flag, path, secure, _expires, name, value = parts[:7]
        cookies.append(
            {
                "domain": domain,
                "path": path or "/",
                "secure": secure.upper() == "TRUE",
                "name": name,
                "value": value,
            }
        )

    return cookies

def session_with_cookies(url: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(build_browser_headers(url))

    cookie_file = get_cookie_file(url)
    if cookie_file:
        for cookie in read_netscape_cookies(cookie_file):
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
                secure=cookie["secure"],
            )

    return session

def resolve_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if not any(x in host for x in ("facebook.com", "fb.watch", "x.com", "twitter.com")):
        return url

    try:
        session = session_with_cookies(url)
        response = session.get(url, allow_redirects=True, timeout=20)
        if response.url:
            return response.url
    except Exception:
        pass

    return url

def build_ydl_opts(output_dir: str, url: str, format_string: str) -> dict:
    opts = {
        "outtmpl": str(Path(output_dir) / "%(title).100s-%(id)s-%(autonumber)03d.%(ext)s"),
        "noplaylist": not is_x_url(url),
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "merge_output_format": "mp4",
        "format": format_string,
        "http_headers": build_browser_headers(url),
    }

    cookie_file = get_cookie_file(url)
    if cookie_file:
        opts["cookiefile"] = cookie_file

    return opts

def collect_downloaded_media_files(output_dir: str) -> list[str]:
    files = [
        p for p in Path(output_dir).glob("*")
        if p.is_file() and p.suffix.lower() in (VIDEO_EXTS | IMAGE_EXTS)
    ]
    if not files:
        raise RuntimeError("No file downloaded")

    files = sorted(files, key=lambda p: (p.name, p.stat().st_size))
    return [str(p) for p in files]

def try_ytdlp_download(url: str, output_dir: str) -> tuple[list[str], dict]:
    final_url = resolve_url(url)
    format_attempts = [
        "bestvideo*+bestaudio/best",
        "best",
        "bv*+ba/b",
        "mp4/best",
    ]

    last_error: Exception | None = None

    for fmt in format_attempts:
        try:
            opts = build_ydl_opts(output_dir, final_url, fmt)

            print(f"[yt-dlp] url={final_url}")
            print(f"[yt-dlp] format={fmt}")
            print(f"[yt-dlp] cookie={get_cookie_file(final_url)}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(final_url, download=True)
                file_paths = collect_downloaded_media_files(output_dir)
                return file_paths, info

        except Exception as exc:
            last_error = exc

    if last_error is None:
        raise RuntimeError("Download failed")

    raise last_error

def normalize_escaped_url(value: str) -> str:
    value = html.unescape(value)
    value = bytes(value, "utf-8").decode("unicode_escape")
    value = value.replace("\\/", "/")
    return value

def collect_facebook_video_candidates(html_text: str) -> list[str]:
    patterns = [
        r'"browser_native_hd_url":"(https:[^"]+)"',
        r'"browser_native_sd_url":"(https:[^"]+)"',
        r'"playable_url_quality_hd":"(https:[^"]+)"',
        r'"playable_url":"(https:[^"]+)"',
        r'"playable_url_dash":"(https:[^"]+)"',
        r'"videoDeliveryResponseFragment":"([^"]+https:[^"]+\.mp4[^"]*)"',
        r'"src":"(https:[^"]+\.mp4[^"]*)"',
    ]

    found: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, html_text):
            raw = match.group(1)
            url = normalize_escaped_url(raw)

            if ".mp4" not in url and "video" not in url and "playable" not in raw:
                continue

            if "lookaside.fbsbx.com" in url:
                continue

            if url not in seen:
                seen.add(url)
                found.append(url)

    return found

def collect_image_candidates(html_text: str, page_url: str) -> list[str]:
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'"image"\s*:\s*\{[^\}]*"uri"\s*:\s*"([^"]+)"',
        r'"image"\s*:\s*"([^"]+)"',
        r'"display_url"\s*:\s*"([^"]+)"',
    ]

    found: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
            raw = match.group(1)
            url = normalize_escaped_url(raw)
            url = urljoin(page_url, url)
            lower = url.lower()

            if not lower.startswith(("http://", "https://")):
                continue

            if not any(ext in lower for ext in IMAGE_EXTS) and not any(
                key in lower for key in ("image", "photo", "scontent", "twimg", "cdn")
            ):
                continue

            if any(
                bad in lower
                for bad in (
                    "profile",
                    "avatar",
                    "emoji",
                    "sprite",
                    "icon",
                    "logo",
                    "1x1",
                    "pixel",
                )
            ):
                continue

            if url not in seen:
                seen.add(url)
                found.append(url)

    return found

def score_facebook_video_url(url: str) -> tuple[int, int, int, int]:
    lower = url.lower()
    hd_score = 1 if "hd" in lower or "quality_hd" in lower else 0
    mp4_score = 1 if ".mp4" in lower else 0
    fb_video_score = 1 if "video" in lower or "fbcdn" in lower else 0
    len_score = len(url)
    return (hd_score, mp4_score, fb_video_score, len_score)

def score_image_url(url: str) -> tuple[int, int, int, int, int]:
    lower = url.lower()
    ext_score = 1 if any(lower.endswith(ext) or f"{ext}?" in lower for ext in IMAGE_EXTS) else 0
    cdn_score = 1 if any(x in lower for x in ("fbcdn", "cdn", "twimg", "scontent")) else 0
    content_score = 1 if any(x in lower for x in ("photo", "image", "media")) else 0
    bad_score = -1 if any(x in lower for x in ("profile", "avatar", "icon", "logo", "sprite")) else 0
    len_score = len(url)
    return (ext_score, cdn_score, content_score, bad_score, len_score)

def fetch_page_html(url: str) -> tuple[str, str]:
    final_url = resolve_url(url)
    session = session_with_cookies(final_url)
    response = session.get(final_url, timeout=20)
    response.raise_for_status()
    return response.text, final_url

def extract_direct_video_from_facebook(url: str) -> str | None:
    html_text, final_url = fetch_page_html(url)
    candidates = collect_facebook_video_candidates(html_text)
    if not candidates:
        return None

    candidates.sort(key=score_facebook_video_url, reverse=True)
    return candidates[0]

def extract_direct_images_from_page(url: str, limit: int = 3) -> list[str]:
    html_text, final_url = fetch_page_html(url)
    candidates = collect_image_candidates(html_text, final_url)
    if not candidates:
        return []

    candidates.sort(key=score_image_url, reverse=True)
    return candidates[:limit]

def guess_extension_from_url(url: str, content_type: str = "") -> str:
    path = urlparse(url).path.lower()

    for ext in VIDEO_EXTS | IMAGE_EXTS:
        if path.endswith(ext):
            return ext

    content_type = (content_type or "").lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"

    return ".mp4"

def download_direct_file(media_url: str, output_dir: str, source_url: str, base_name: str = "media") -> tuple[str, dict]:
    session = session_with_cookies(source_url)

    with session.get(media_url, stream=True, timeout=60) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        ext = guess_extension_from_url(media_url, content_type)
        file_path = Path(output_dir) / f"{base_name}{ext}"

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    title = "Downloaded image" if ext in IMAGE_EXTS else "Downloaded video"
    info = {"title": title}
    return str(file_path), info

def download_direct_images(image_urls: list[str], output_dir: str, source_url: str) -> tuple[list[str], dict]:
    files: list[str] = []

    for index, image_url in enumerate(image_urls, start=1):
        file_path, _info = download_direct_file(image_url, output_dir, source_url, base_name=f"image_{index}")
        files.append(file_path)

    return files, {"title": "Downloaded image"}

def facebook_video_only_fallback(url: str, output_dir: str) -> tuple[list[str], dict]:
    media_url = extract_direct_video_from_facebook(url)
    if not media_url:
        raise RuntimeError("Facebook fallback video URL not found")
    file_path, info = download_direct_file(media_url, output_dir, url, base_name="facebook_video_fallback")
    return [file_path], info

def page_image_fallback(url: str, output_dir: str) -> tuple[list[str], dict]:
    image_urls = extract_direct_images_from_page(url, limit=3)
    if not image_urls:
        raise RuntimeError("Image fallback URL not found")
    return download_direct_images(image_urls, output_dir, url)

def direct_image_url_fallback(url: str, output_dir: str) -> tuple[list[str], dict]:
    file_path, info = download_direct_file(url, output_dir, url, base_name="direct_image")
    return [file_path], info

def download_media(url: str, output_dir: str) -> tuple[list[str], dict]:
    lower_url = url.lower()
    if any(lower_url.endswith(ext) for ext in IMAGE_EXTS):
        return direct_image_url_fallback(url, output_dir)

    try:
        file_paths, info = try_ytdlp_download(url, output_dir)
        return file_paths, info
    except Exception as exc:
        err = str(exc).lower()
        host = urlparse(url).netloc.lower()

        if "facebook.com" in host or "fb.watch" in host:
            if (
                "cannot parse data" in err
                or "no video formats found" in err
                or "unsupported url" in err
                or "no formats" in err
                or "no video could be found" in err
            ):
                try:
                    return facebook_video_only_fallback(url, output_dir)
                except Exception:
                    return page_image_fallback(url, output_dir)

        if any(x in host for x in ("x.com", "twitter.com", "tiktok.com")):
            if (
                "no video formats found" in err
                or "no formats" in err
                or "unsupported url" in err
                or "no video could be found" in err
                or "unable to extract" in err
            ):
                return page_image_fallback(url, output_dir)

        raise exc

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ផ្ញើ link មកបាន\n"
        "Support: Facebook, X/Twitter, TikTok, YouTube\n"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "របៀបប្រើ៖\n"
        "1) កំណត់ BOT_TOKEN\n"
        "2) អាចកំណត់ COOKIE_FB / COOKIE_X / COOKIE_TT / COOKIE_YT ជា path ទៅ cookie txt file\n"
        "3) bot សាក yt-dlp មុន\n"
        "4) បើ video មិនចេញ វានឹងសាក fallback រក image/video ដោយប្រើ cookie"
    )

async def send_media(update: Update, file_paths: list[str], info: dict) -> None:
    if not file_paths:
        raise RuntimeError("No media file to send")

    caption = (info.get("title") or "Downloaded media")[:1024]
    album_paths = [
        p for p in file_paths[:10]
        if Path(p).suffix.lower() in (IMAGE_EXTS | VIDEO_EXTS)
    ]

    if len(album_paths) > 1:
        media = []
        handles = []
        try:
            for i, path in enumerate(album_paths):
                f = open(path, "rb")
                handles.append(f)
                ext = Path(path).suffix.lower()
                item_caption = caption if i == 0 else None

                if ext in IMAGE_EXTS:
                    media.append(InputMediaPhoto(media=f, caption=item_caption))
                elif ext in VIDEO_EXTS:
                    media.append(InputMediaVideo(media=f, caption=item_caption))

            if len(media) > 1:
                await update.message.reply_media_group(media=media)
                return
        finally:
            for f in handles:
                try:
                    f.close()
                except Exception:
                    pass

    file_path = file_paths[0]
    ext = Path(file_path).suffix.lower()
    width = info.get("width")
    height = info.get("height")
    duration = info.get("duration")

    with open(file_path, "rb") as f:
        if ext in IMAGE_EXTS:
            await update.message.reply_photo(photo=f, caption=caption)
            return

        if ext in VIDEO_EXTS:
            await update.message.reply_video(
                video=f,
                caption=caption,
                supports_streaming=True,
                width=width if isinstance(width, int) else None,
                height=height if isinstance(height, int) else None,
                duration=duration if isinstance(duration, int) else None,
                filename=Path(file_path).name,
            )
            return

        await update.message.reply_document(
            document=f,
            caption=caption,
            filename=Path(file_path).name,
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    url = extract_url(update.message.text)
    if not url:
        await update.message.reply_text("សូមផ្ញើ URL ត្រឹមត្រូវ។")
        return

    status = await update.message.reply_text("កំពុងដោនឡូត...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_paths, info = await asyncio.to_thread(download_media, url, tmpdir)

            if not file_paths:
                await status.edit_text("រក file មិនឃើញក្រោយ download។")
                return

            total_size = sum(os.path.getsize(path) for path in file_paths if os.path.exists(path))
            if total_size > MAX_FILE_SIZE_BYTES:
                await status.edit_text(
                    f"File ធំពេក ({total_size / 1024 / 1024:.1f} MB)\n"
                    f"Limit បច្ចុប្បន្ន = {MAX_FILE_SIZE_MB} MB"
                )
                return

            await status.edit_text("កំពុងផ្ញើទៅ Telegram...")
            await send_media(update, file_paths, info)
            await status.delete()

    except Exception as e:
        err = str(e).lower()

        if "failed to decrypt with dpapi" in err:
            await status.edit_text("cookie មិនអាចប្រើបាន។ សូមប្រើ Netscape cookies txt file ត្រឹមត្រូវ។")
            return
        if "registered users" in err or "authentication" in err or "login" in err or "private" in err or "sign in" in err:
            await status.edit_text("Link នេះត្រូវការ cookie ត្រឹមត្រូវ ឬអាចជា private content។")
            return
        if "image fallback url not found" in err:
            await status.edit_text("រក image មិនឃើញពី page នេះទេ។")
            return
        if "facebook fallback video url not found" in err:
            await status.edit_text("Facebook post នេះរក media ពិតមិនឃើញទេ។")
            return
        if "cannot parse data" in err and "facebook" in err:
            await status.edit_text("Facebook parse មិនចេញ។ សាកប្តូរ cookie ថ្មី។")
            return
        if "no video could be found" in err:
            await status.edit_text("Post នេះអាចមិនមានវីដេអូ ហើយ bot ក៏រក image មិនឃើញដែរ។")
            return

        await status.edit_text(f"Error: {e}")

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("សូមកំណត់ BOT_TOKEN ជា environment variable")

    builder = Application.builder().token(BOT_TOKEN)

    if USE_LOCAL_BOT_API:
        builder = builder.base_url(LOCAL_BOT_API_BASE).base_file_url(LOCAL_BOT_API_FILE_BASE)

    app = builder.build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # កន្លែងនេះបានកំណត់ Webhook របស់អ្នករួចជាស្រេច
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegram-iyns.onrender.com").strip()
    PORT = int(os.getenv("PORT", "8443")) # Render នឹងផ្តល់ PORT នេះដោយស្វ័យប្រវត្តិ

    if WEBHOOK_URL:
        print(f"Bot is running with Webhook on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL
        )
    else:
        print("Bot is running with Polling...")
        app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
