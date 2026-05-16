from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in (os.getenv("ADMIN_IDS") or "").replace(";", ",").split(",")
    if x.strip().isdigit()
}

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "45"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "10"))

DB_PATH = Path("data/bottel.db")
DOWNLOAD_DIR = Path("downloads")

URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.I)

router = Router()
pending_urls: dict[int, str] = {}
last_request: dict[int, float] = {}

TEXTS = {
    "start": (
        "أهلاً بك في BOTtel.\n\n"
        "أرسل رابطاً عاماً من منصة مدعومة، وسأحاول تحميله لك.\n\n"
        "الاستخدام فقط للمحتوى الذي تملكه أو لديك حق تحميله."
    ),
    "help": (
        "طريقة الاستخدام:\n"
        "1) أرسل رابطاً عاماً.\n"
        "2) اختر فيديو أو صوت.\n"
        "3) انتظر اكتمال التحميل.\n\n"
        "الأوامر:\n"
        "/start\n/help\n/about\n/legal\n/whoami\n/admin"
    ),
    "about": (
        "BOTtel بوت تحميل وسائط من الروابط العامة باستخدام yt-dlp.\n"
        "الدعم يعتمد على المنصة والرابط وتحديثات المواقع."
    ),
    "legal": (
        "شروط الاستخدام:\n"
        "- استخدم البوت فقط مع محتوى تملكه أو لديك حق تحميله.\n"
        "- لا يوجد تجاوز DRM أو حسابات خاصة أو تسجيل دخول أو اشتراكات.\n"
        "- الروابط الخاصة أو غير المصرح بها غير مدعومة."
    ),
    "processing": "جاري التحميل والمعالجة...",
    "done": "تم التحميل بنجاح.",
    "bad_url": "أرسل رابطاً صحيحاً يبدأ بـ http أو https.",
    "blocked": "هذا الرابط مرفوض لأنه داخلي أو خاص أو غير آمن.",
    "failed": "تعذر تحميل الرابط. تأكد أنه عام ومتاح وغير محمي.",
    "large": "الملف أكبر من الحد المسموح.",
    "rate": "تمهل قليلاً قبل إرسال طلب جديد.",
    "admin_only": "هذه المنطقة للمالك فقط.",
}


def record(
    user_id: int,
    url: str,
    mode: str,
    platform: str | None,
    status: str,
    error: str | None = None,
) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO downloads(user_id,url,mode,platform,status,error,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, url, mode, platform, status, error, int(time.time())),
        )


def pick_file(workdir: Path) -> Path:
    files = [
        item
        for item in workdir.rglob("*")
        if item.is_file()
        and not item.name.endswith(".part")
        and item.suffix not in {".tmp", ".part", ".ytdl"}
    ]

    if not files:
        raise RuntimeError("no output file")

    return max(files, key=lambda item: item.stat().st_mtime)


def download_sync(url: str, mode: str) -> tuple[Path, Path, str]:
    validate_url(url)

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="bottel_", dir=str(DOWNLOAD_DIR)))

    max_mb = int(setting("max_upload_mb", str(MAX_UPLOAD_MB)))
    max_bytes = max_mb * 1024 * 1024

    options = {
        "outtmpl": str(workdir / "%(extractor)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "max_filesize": max_bytes,
    }

    if mode == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        options["format"] = "best[ext=mp4]/best"

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)

        output = pick_file(workdir)

        if output.stat().st_size > max_bytes:
            raise RuntimeError("too_large")

        platform = "unknown"

        if isinstance(info, dict):
            platform = str(
                info.get("extractor_key")
                or info.get("extractor")
                or "unknown"
            )

        return output, workdir, platform

    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


async def download(url: str, mode: str) -> tuple[Path, Path, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, download_sync, url, mode)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    ensure_user(user_id)

    await message.answer(
        text("start"),
        reply_markup=home_keyboard(accepted(user_id)),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(text("help"))


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(text("about"))


@router.message(Command("legal"))
async def cmd_legal(message: Message) -> None:
    await message.answer(text("legal"), reply_markup=home_keyboard(False))


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(f"Telegram user ID:\n{user_id}")


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if not is_admin(user_id):
        await message.answer(
            "لم يتم ضبط المالك أو أنك لست المالك.\n"
            "أرسل /whoami ثم ضع الرقم في ADMIN_IDS داخل .env"
        )
        return

    await message.answer("لوحة تحكم المالك:", reply_markup=admin_keyboard())


@router.message(Command("settext"))
async def cmd_settext(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if not is_admin(user_id):
        await message.answer(text("admin_only"))
        return

    raw = (message.text or "").replace("/settext", "", 1).strip()
    parts = raw.split(" ", 1)

    if len(parts) != 2:
        await message.answer("الصيغة:\n/settext start النص الجديد")
        return

    set_setting(f"text:{parts[0]}", parts[1])
    await message.answer("تم تحديث النص.")


@router.message(Command("setsetting"))
async def cmd_setsetting(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if not is_admin(user_id):
        await message.answer(text("admin_only"))
        return

    raw = (message.text or "").replace("/setsetting", "", 1).strip()
    parts = raw.split(" ", 1)

    if len(parts) != 2:
        await message.answer("الصيغة:\n/setsetting max_upload_mb 45")
        return

    set_setting(parts[0], parts[1])
    await message.answer("تم تحديث الإعداد.")


@router.message(Command("addbutton"))
async def cmd_addbutton(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if not is_admin(user_id):
        await message.answer(text("admin_only"))
        return

    raw = (message.text or "").replace("/addbutton", "", 1).strip()

    if "|" not in raw:
        await message.answer("الصيغة:\n/addbutton اسم الزر | https://example.com")
        return

    title, url = [item.strip() for item in raw.split("|", 1)]

    try:
        validate_url(url)
    except Exception:
        await message.answer("الرابط غير صالح أو غير عام.")
        return

    with db() as con:
        con.execute(
            "INSERT INTO buttons(title,url,active) VALUES(?,?,1)",
            (title, url),
        )

    await message.answer("تمت إضافة الزر.")


@router.message(Command("delbutton"))
async def cmd_delbutton(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0

    if not is_admin(user_id):
        await message.answer(text("admin_only"))
        return

    raw = (message.text or "").replace("/delbutton", "", 1).strip()

    if not raw.isdigit():
        await message.answer("الصيغة:\n/delbutton 1")
        return

    with db() as con:
        con.execute("UPDATE buttons SET active=0 WHERE id=?", (int(raw),))

    await message.answer("تم حذف الزر إن كان موجوداً.")


@router.callback_query(F.data == "accept")
async def cb_accept(callback: CallbackQuery) -> None:
    accept(callback.from_user.id)
    await callback.answer("تم القبول")

    if callback.message:
        await callback.message.answer("تم قبول الشروط. أرسل الرابط الآن.")


@router.callback_query(F.data.startswith("public:"))
async def cb_public(callback: CallbackQuery) -> None:
    key = (callback.data or "").split(":", 1)[1]
    await callback.answer()

    if callback.message:
        await callback.message.answer(text(key))


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer(text("admin_only"), show_alert=True)
        return

    action = (callback.data or "").split(":", 1)[1]
    await callback.answer()

    if not callback.message:
        return

    if action == "stats":
        with db() as con:
            total = con.execute(
                "SELECT COUNT(*) AS c FROM downloads"
            ).fetchone()["c"]
            success = con.execute(
                "SELECT COUNT(*) AS c FROM downloads WHERE status='success'"
            ).fetchone()["c"]
            failed = con.execute(
                "SELECT COUNT(*) AS c FROM downloads WHERE status='failed'"
            ).fetchone()["c"]
            users = con.execute(
                "SELECT COUNT(*) AS c FROM users"
            ).fetchone()["c"]

        await callback.message.answer(
            "الإحصائيات:\n"
            f"الطلبات: {total}\n"
            f"ناجحة: {success}\n"
            f"فاشلة: {failed}\n"
            f"المستخدمون: {users}"
        )
        return

    if action == "texts":
        await callback.message.answer(
            "تعديل النصوص:\n"
            "/settext start النص الجديد\n"
            "/settext help النص الجديد\n"
            "/settext legal النص الجديد"
        )
        return

    if action == "settings":
        await callback.message.answer(
            "الإعدادات:\n"
            f"max_upload_mb={setting('max_upload_mb')}\n"
            f"rate_limit_seconds={setting('rate_limit_seconds')}\n\n"
            "تعديل:\n"
            "/setsetting max_upload_mb 45"
        )
        return

    if action == "buttons":
        await callback.message.answer(
            "الأزرار:\n"
            "/addbutton اسم الزر | https://example.com\n"
            "/delbutton 1"
        )
        return


@router.callback_query(F.data.startswith("dl:"))
async def cb_download(callback: CallbackQuery) -> None:
    mode = (callback.data or "").split(":", 1)[1]
    user_id = callback.from_user.id

    if mode == "cancel":
        pending_urls.pop(user_id, None)
        await callback.answer("تم الإلغاء")

        if callback.message:
            await callback.message.answer("تم إلغاء الطلب.")

        return

    url = pending_urls.get(user_id)

    if not url:
        await callback.answer("لا يوجد رابط محفوظ", show_alert=True)
        return

    await callback.answer()

    if callback.message:
        await process(callback.message, user_id, url, mode)


@router.message()
async def handle_message(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    ensure_user(user_id)

    if not accepted(user_id):
        await message.answer(text("legal"), reply_markup=home_keyboard(False))
        return

    limit = int(setting("rate_limit_seconds", str(RATE_LIMIT_SECONDS)))
    now = time.time()

    if now - last_request.get(user_id, 0) < limit:
        await message.answer(text("rate"))
        return

    last_request[user_id] = now

    url = first_url(message.text)

    if not url:
        await message.answer(text("bad_url"))
        return

    try:
        validate_url(url)
    except Exception:
        await message.answer(text("blocked"))
        return

    pending_urls[user_id] = url
    await message.answer("اختر نوع التحميل:", reply_markup=download_keyboard())


async def process(message: Message, user_id: int, url: str, mode: str) -> None:
    await message.answer(text("processing"))

    workdir = None
    platform = None

    try:
        path, workdir, platform = await download(url, mode)
        size_mb = path.stat().st_size / 1024 / 1024

        await message.answer_document(
            FSInputFile(str(path), filename=path.name),
            caption=(
                f"{text('done')}\n"
                f"المنصة: {platform}\n"
                f"الحجم: {size_mb:.2f} MB"
            ),
        )

        record(user_id, url, mode, platform, "success")

    except Exception as exc:
        logging.exception("download failed")
        record(user_id, url, mode, platform, "failed", str(exc))

        if "too_large" in str(exc):
            await message.answer(text("large"))
        else:
            await message.answer(text("failed"))

    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


async def main() -> None:
    if not TOKEN or "ضع_توكن" in TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN غير موجود أو غير صحيح داخل .env")

    logging.basicConfig(level=logging.INFO)
    init_db()

    bot = Bot(token=TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())