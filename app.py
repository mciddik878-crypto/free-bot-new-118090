import asyncio
import base64
import hashlib
import hmac
import html as html_lib
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
import psutil

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
OWNER_ID_STR = (os.getenv("OWNER_ID") or "").strip()
ADMIN_ID_STR = (os.getenv("ADMIN_ID") or "").strip()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()

if not TOKEN:
    logger.error("BOT_TOKEN missing! Set BOT_TOKEN in .env or Render Environment.")
    raise ValueError("BOT_TOKEN is required.")

if not OWNER_ID_STR and not ADMIN_ID_STR:
    logger.error("OWNER_ID or ADMIN_ID missing!")
    raise ValueError("OWNER_ID or ADMIN_ID is required.")

try:
    OWNER_ID = int(OWNER_ID_STR) if OWNER_ID_STR else int(ADMIN_ID_STR)
    ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR else OWNER_ID
except ValueError:
    logger.error("OWNER_ID and ADMIN_ID must be valid integer IDs.")
    raise

env_user = (os.getenv("YOUR_USERNAME") or "@SHIHAB_TILINIYAR_IN_2035").strip()
YOUR_USERNAME = env_user if env_user.startswith("@") else f"@{env_user}"
UPDATE_CHANNEL = (os.getenv("UPDATE_CHANNEL") or "https://t.me/ONLINE_INCOME_FREE_2027").strip()

BASE_DIR = Path(__file__).parent.absolute()
DISK_ROOT = (os.getenv("RENDER_DISK_PATH") or "/var/data").strip()
PERSISTENT_ROOT = Path(DISK_ROOT) if (DISK_ROOT and Path(DISK_ROOT).exists()) else BASE_DIR

UPLOAD_BOTS_DIR = PERSISTENT_ROOT / "upload_bots"
IROTECH_DIR = PERSISTENT_ROOT / "inf"
STAGING_FILES_DIR = PERSISTENT_ROOT / "staged_project_files"
DATABASE_PATH = IROTECH_DIR / "bot_data.db"
JSON_BACKUP_PATH = IROTECH_DIR / "persistent_backup.json"

UPLOAD_BOTS_DIR.mkdir(parents=True, exist_ok=True)
IROTECH_DIR.mkdir(parents=True, exist_ok=True)
STAGING_FILES_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_SIX_FILES = [
    ".env.example",
    ".gitignore",
    "app.py",
    "Dockerfile",
    "render.yaml",
    "requirements.txt",
]

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
BOT_START_TIME = datetime.now()
_BOT_USERNAME = None
_WEB_RUNNER: web.AppRunner | None = None

# Runtime state
bot_scripts = {}
user_subscriptions = {}
user_files = {}
user_favorites = {}
banned_users = {}
active_users = set()
user_profiles = {}
user_points = {}
user_free_trial = {}
referred_by = {}
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
bot_stats = {"total_uploads": 0, "total_downloads": 0, "total_runs": 0}
bot_settings = {
    "refer_bonus": 10,
    "file_point": 5,
    "limit_minutes": 10,
    "limit_points": 5,
    "channels": {},
    "redeem_codes": {},
    "all_files_list": list(PROJECT_SIX_FILES),
}
staged_deletions = set()
staged_additions = set()
user_states = {}


def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def is_admin_user(user_id: int) -> bool:
    return int(user_id) in admin_ids or int(user_id) == OWNER_ID or int(user_id) == ADMIN_ID


def get_admin_recipients() -> list[int]:
    rec = set(admin_ids)
    rec.add(OWNER_ID)
    rec.add(ADMIN_ID)
    return [int(x) for x in rec]


def format_user_mention_or_name(uid: int, fallback_name: str = "") -> str:
    prof = user_profiles.get(uid, {})
    uname = (prof.get("username") or "").strip()
    if uname:
        return f"@{uname.lstrip('@')}"
    return fallback_name or prof.get("name") or f"User {uid}"


def format_clickable_user_link(uid: int, fallback_name: str = "") -> str:
    prof = user_profiles.get(uid, {})
    display_name = fallback_name or prof.get("name") or (
        f"@{prof['username'].lstrip('@')}" if (prof.get("username") or "").strip() else f"User {uid}"
    )
    return f'<a href="tg://user?id={uid}">{html_lib.escape(display_name)}</a>'


async def get_user_profile_photo_bytes(uid: int) -> bytes | None:
    try:
        photos = await bot.get_user_profile_photos(user_id=uid, limit=1)
        if photos.total_count > 0 and photos.photos and photos.photos[0]:
            file_id = photos.photos[0][-1].file_id
            tg_file = await bot.get_file(file_id)
            buf = io.BytesIO()
            await bot.download_file(tg_file.file_path, destination=buf)
            data = buf.getvalue()
            if data:
                return data
    except Exception as e:
        logger.debug(f"Profile photo fetch failed for {uid}: {e}")
    return None


def db_connect():
    return sqlite3.connect(DATABASE_PATH, timeout=30)


def save_json_backup():
    try:
        payload = {
            "saved_at": datetime.now().isoformat(),
            "active_users": list(active_users),
            "user_profiles": {str(k): v for k, v in user_profiles.items()},
            "user_points": {str(k): int(v) for k, v in user_points.items()},
            "user_files": {str(k): [list(x) for x in v] for k, v in user_files.items()},
            "user_favorites": {str(k): list(v) for k, v in user_favorites.items()},
            "user_free_trial": {str(k): v.isoformat() for k, v in user_free_trial.items()},
            "referred_by": {str(k): int(v) for k, v in referred_by.items()},
            "admin_ids": list(admin_ids),
            "banned_users": {
                str(k): {
                    "until": v["until"].isoformat() if isinstance(v.get("until"), datetime) else str(v.get("until", "")),
                    "reason": v.get("reason", "Banned"),
                }
                for k, v in banned_users.items()
            },
            "bot_stats": dict(bot_stats),
            "bot_settings": {
                "refer_bonus": int(bot_settings.get("refer_bonus", 10)),
                "file_point": int(bot_settings.get("file_point", 5)),
                "limit_minutes": int(bot_settings.get("limit_minutes", 10)),
                "limit_points": int(bot_settings.get("limit_points", 5)),
                "channels": {str(k): v for k, v in (bot_settings.get("channels") or {}).items()},
                "redeem_codes": {
                    code: {
                        "points": int(info.get("points", 0)),
                        "date": str(info.get("date", get_today_str())),
                        "used_by": list(info.get("used_by") or []),
                    }
                    for code, info in (bot_settings.get("redeem_codes") or {}).items()
                },
                "all_files_list": list(bot_settings.get("all_files_list") or PROJECT_SIX_FILES),
            },
        }
        tmp_path = JSON_BACKUP_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(JSON_BACKUP_PATH)
    except Exception as e:
        logger.error(f"JSON backup save error: {e}")


def restore_from_json_backup_if_needed():
    if not JSON_BACKUP_PATH.exists():
        return
    try:
        data = json.loads(JSON_BACKUP_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return

        for uid in data.get("active_users") or []:
            active_users.add(int(uid))

        for k, v in (data.get("user_profiles") or {}).items():
            uid = int(k)
            if uid not in user_profiles and isinstance(v, dict):
                user_profiles[uid] = {
                    "name": v.get("name") or f"User {uid}",
                    "username": v.get("username") or "",
                }

        for k, v in (data.get("user_points") or {}).items():
            uid = int(k)
            if uid not in user_points:
                user_points[uid] = int(v or 0)

        for k, flist in (data.get("user_files") or {}).items():
            uid = int(k)
            user_files.setdefault(uid, [])
            for item in flist or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    tup = (str(item[0]), str(item[1]))
                    if tup not in user_files[uid]:
                        user_files[uid].append(tup)

        for k, favs in (data.get("user_favorites") or {}).items():
            uid = int(k)
            user_favorites.setdefault(uid, [])
            for fn in favs or []:
                if str(fn) not in user_favorites[uid]:
                    user_favorites[uid].append(str(fn))

        for k, ts in (data.get("user_free_trial") or {}).items():
            uid = int(k)
            if uid not in user_free_trial:
                try:
                    user_free_trial[uid] = datetime.fromisoformat(str(ts))
                except Exception:
                    pass

        for k, r_id in (data.get("referred_by") or {}).items():
            ref_uid = int(k)
            if ref_uid not in referred_by:
                referred_by[ref_uid] = int(r_id)

        for aid in data.get("admin_ids") or []:
            admin_ids.add(int(aid))

        for k, binfo in (data.get("banned_users") or {}).items():
            uid = int(k)
            if uid not in banned_users and isinstance(binfo, dict):
                try:
                    until_dt = datetime.fromisoformat(str(binfo.get("until", "")))
                except Exception:
                    until_dt = datetime.now() + timedelta(hours=24)
                banned_users[uid] = {"until": until_dt, "reason": binfo.get("reason") or "Banned"}

        st = data.get("bot_stats") or {}
        for stat_k in ["total_uploads", "total_downloads", "total_runs"]:
            if int(st.get(stat_k, 0)) > int(bot_stats.get(stat_k, 0)):
                bot_stats[stat_k] = int(st.get(stat_k, 0))

        cfg = data.get("bot_settings") or {}
        if "refer_bonus" in cfg:
            bot_settings["refer_bonus"] = int(cfg["refer_bonus"])
        if "file_point" in cfg:
            bot_settings["file_point"] = int(cfg["file_point"])
        if "limit_minutes" in cfg:
            bot_settings["limit_minutes"] = max(1, int(cfg["limit_minutes"]))
        if "limit_points" in cfg:
            bot_settings["limit_points"] = max(0, int(cfg["limit_points"]))
        if not bot_settings.get("channels") and isinstance(cfg.get("channels"), dict):
            for sk, sv in cfg["channels"].items():
                bot_settings["channels"][int(sk)] = sv
        if isinstance(cfg.get("redeem_codes"), dict):
            for code, info in cfg["redeem_codes"].items():
                if str(code) not in bot_settings["redeem_codes"] and isinstance(info, dict):
                    bot_settings["redeem_codes"][str(code)] = {
                        "points": int(info.get("points", 0)),
                        "date": str(info.get("date", get_today_str())),
                        "used_by": set(int(x) for x in (info.get("used_by") or [])),
                    }
        if isinstance(cfg.get("all_files_list"), list) and cfg["all_files_list"]:
            bot_settings["all_files_list"] = [str(x) for x in cfg["all_files_list"]]
    except Exception as e:
        logger.warning(f"JSON backup restore warning: {e}")


def migrate_db():
    try:
        conn = db_connect()
        c = conn.cursor()

        c.execute("PRAGMA table_info(user_files)")
        cols = [r[1] for r in c.fetchall()]
        if cols and "upload_date" not in cols:
            c.execute("ALTER TABLE user_files ADD COLUMN upload_date TEXT")

        c.execute("PRAGMA table_info(active_users)")
        cols = [r[1] for r in c.fetchall()]
        if cols:
            if "join_date" not in cols:
                c.execute("ALTER TABLE active_users ADD COLUMN join_date TEXT")
            if "last_active" not in cols:
                c.execute("ALTER TABLE active_users ADD COLUMN last_active TEXT")
            if "full_name" not in cols:
                c.execute("ALTER TABLE active_users ADD COLUMN full_name TEXT")
            if "username" not in cols:
                c.execute("ALTER TABLE active_users ADD COLUMN username TEXT")

        c.execute("PRAGMA table_info(banned_users)")
        cols = [r[1] for r in c.fetchall()]
        if cols and "ban_until" not in cols:
            c.execute("ALTER TABLE banned_users ADD COLUMN ban_until TEXT")

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB migration error: {e}")


def init_db():
    try:
        conn = db_connect()
        c = conn.cursor()
        c.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions
               (user_id INTEGER PRIMARY KEY, expiry TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS user_files
               (user_id INTEGER, file_name TEXT, file_type TEXT, upload_date TEXT,
                PRIMARY KEY (user_id, file_name))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS active_users
               (user_id INTEGER PRIMARY KEY, join_date TEXT, last_active TEXT,
                full_name TEXT, username TEXT)"""
        )
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS banned_users
               (user_id INTEGER PRIMARY KEY, banned_date TEXT, reason TEXT, ban_until TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS favorites
               (user_id INTEGER, file_name TEXT, PRIMARY KEY (user_id, file_name))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS bot_stats
               (stat_name TEXT PRIMARY KEY, stat_value INTEGER)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS user_points
               (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS free_trials
               (user_id INTEGER PRIMARY KEY, first_upload_at TEXT)"""
        )
        c.execute("CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS referrals
               (referred_id INTEGER PRIMARY KEY, referrer_id INTEGER, created_at TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS referral_rewards
               (referred_id INTEGER PRIMARY KEY, referrer_id INTEGER, points INTEGER, rewarded_at TEXT)"""
        )

        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (ADMIN_ID,))

        for stat in ["total_uploads", "total_downloads", "total_runs"]:
            c.execute(
                "INSERT OR IGNORE INTO bot_stats (stat_name, stat_value) VALUES (?, 0)",
                (stat,),
            )

        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('refer_bonus', '10')")
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('file_point', '5')")
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('limit_minutes', '10')")
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('limit_points', '5')")
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('channels', '{}')")
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('redeem_codes', '{}')")
        c.execute(
            "INSERT OR IGNORE INTO bot_config (key, value) VALUES ('all_files_list', ?)",
            (json.dumps(PROJECT_SIX_FILES),),
        )

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB init error: {e}")


def save_config_key(key: str, value: str):
    try:
        conn = db_connect()
        conn.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Save config error ({key}): {e}")
    save_json_backup()


def set_user_points_db(user_id: int, points: int):
    pts = max(0, int(points))
    user_points[user_id] = pts
    try:
        conn = db_connect()
        conn.execute(
            "INSERT OR REPLACE INTO user_points (user_id, points) VALUES (?, ?)",
            (user_id, pts),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Save points error ({user_id}): {e}")
    save_json_backup()


def add_user_points(user_id: int, delta: int) -> int:
    current = user_points.get(user_id, 0)
    new_pts = max(0, current + int(delta))
    set_user_points_db(user_id, new_pts)
    return new_pts


def load_data():
    try:
        conn = db_connect()
        c = conn.cursor()

        c.execute("SELECT user_id, expiry FROM subscriptions")
        for uid, exp in c.fetchall():
            try:
                user_subscriptions[int(uid)] = {"expiry": datetime.fromisoformat(exp)}
            except Exception:
                pass

        c.execute("SELECT user_id, file_name, file_type FROM user_files")
        for uid, fname, ftype in c.fetchall():
            uid = int(uid)
            user_files.setdefault(uid, [])
            if (fname, ftype) not in user_files[uid]:
                user_files[uid].append((fname, ftype))

        c.execute("SELECT user_id, full_name, username FROM active_users")
        for uid, fname, uname in c.fetchall():
            uid = int(uid)
            active_users.add(uid)
            user_profiles[uid] = {"name": fname or f"User {uid}", "username": uname or ""}

        c.execute("SELECT user_id FROM admins")
        for (uid,) in c.fetchall():
            admin_ids.add(int(uid))

        c.execute("SELECT user_id, banned_date, reason, ban_until FROM banned_users")
        for uid, bdate, reason, b_until in c.fetchall():
            uid = int(uid)
            if b_until:
                try:
                    until_dt = datetime.fromisoformat(b_until)
                except Exception:
                    until_dt = datetime.now() + timedelta(hours=24)
            else:
                try:
                    until_dt = datetime.fromisoformat(bdate) + timedelta(hours=24)
                except Exception:
                    until_dt = datetime.now() + timedelta(hours=24)
            banned_users[uid] = {"until": until_dt, "reason": reason or "Banned"}

        c.execute("SELECT user_id, file_name FROM favorites")
        for uid, fname in c.fetchall():
            uid = int(uid)
            user_favorites.setdefault(uid, [])
            if fname not in user_favorites[uid]:
                user_favorites[uid].append(fname)

        c.execute("SELECT stat_name, stat_value FROM bot_stats")
        for sname, sval in c.fetchall():
            bot_stats[sname] = sval or 0

        c.execute("SELECT user_id, points FROM user_points")
        for uid, pts in c.fetchall():
            user_points[int(uid)] = int(pts or 0)

        c.execute("SELECT user_id, first_upload_at FROM free_trials")
        for uid, ts in c.fetchall():
            try:
                user_free_trial[int(uid)] = datetime.fromisoformat(ts)
            except Exception:
                pass

        c.execute("SELECT referred_id, referrer_id FROM referrals")
        for ref_id, r_id in c.fetchall():
            referred_by[int(ref_id)] = int(r_id)

        c.execute("SELECT key, value FROM bot_config")
        for k, v in c.fetchall():
            if k == "refer_bonus":
                try:
                    bot_settings["refer_bonus"] = int(v)
                except Exception:
                    pass
            elif k == "file_point":
                try:
                    bot_settings["file_point"] = int(v)
                except Exception:
                    pass
            elif k == "limit_minutes":
                try:
                    bot_settings["limit_minutes"] = max(1, int(v))
                except Exception:
                    pass
            elif k == "limit_points":
                try:
                    bot_settings["limit_points"] = max(0, int(v))
                except Exception:
                    pass
            elif k == "channels":
                try:
                    raw = json.loads(v or "{}")
                    bot_settings["channels"] = {int(sk): sv for sk, sv in raw.items()}
                except Exception:
                    pass
            elif k == "redeem_codes":
                try:
                    raw = json.loads(v or "{}")
                    parsed = {}
                    for code_k, info in raw.items():
                        parsed[str(code_k)] = {
                            "points": int(info.get("points", 0)),
                            "date": str(info.get("date", get_today_str())),
                            "used_by": set(int(x) for x in info.get("used_by", [])),
                        }
                    bot_settings["redeem_codes"] = parsed
                except Exception:
                    pass
            elif k == "all_files_list":
                try:
                    arr = json.loads(v or "[]")
                    if isinstance(arr, list) and arr:
                        bot_settings["all_files_list"] = [str(x) for x in arr]
                except Exception:
                    pass

        conn.close()
    except Exception as e:
        logger.error(f"Load data error: {e}")

    restore_from_json_backup_if_needed()


def init_staged_files():
    for fname in bot_settings.get("all_files_list") or PROJECT_SIX_FILES:
        staged_path = STAGING_FILES_DIR / fname
        if not staged_path.exists():
            live_path = BASE_DIR / fname
            if live_path.exists() and live_path.is_file():
                try:
                    shutil.copy2(live_path, staged_path)
                except Exception:
                    staged_path.write_text("", encoding="utf-8")
            else:
                staged_path.write_text("", encoding="utf-8")


init_db()
migrate_db()
load_data()
init_staged_files()
save_json_backup()


def save_channels_to_db():
    save_config_key(
        "channels",
        json.dumps({str(k): v for k, v in bot_settings["channels"].items()}),
    )


def save_redeem_codes_to_db():
    save_config_key(
        "redeem_codes",
        json.dumps(
            {
                code: {
                    "points": info["points"],
                    "date": info["date"],
                    "used_by": list(info["used_by"]),
                }
                for code, info in bot_settings["redeem_codes"].items()
            }
        ),
    )


def save_all_files_list_to_db():
    save_config_key("all_files_list", json.dumps(list(bot_settings.get("all_files_list") or [])))


# ── SAFE MESSAGE / CALLBACK HELPERS ──────────────────
async def safe_answer_callback(callback: types.CallbackQuery, text: str = "", show_alert: bool = False):
    try:
        await callback.answer(text=text or None, show_alert=show_alert)
    except Exception:
        pass


async def safe_edit_message(
    message: types.Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
):
    try:
        return await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            return await message.answer(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"safe_edit_message failed: {e}")
            return None


# ── REFERRAL REWARD PROCESSOR ────────────────────────
async def grant_referral_reward_if_eligible(user: types.User):
    uid = int(user.id)
    ref_id = referred_by.get(uid)
    if not ref_id or ref_id == uid:
        return

    try:
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT 1 FROM referral_rewards WHERE referred_id = ?", (uid,))
        already_rewarded = c.fetchone() is not None
        if already_rewarded:
            conn.close()
            return

        bonus = int(bot_settings.get("refer_bonus", 10))
        new_ref_pts = add_user_points(ref_id, bonus)
        c.execute(
            "INSERT INTO referral_rewards (referred_id, referrer_id, points, rewarded_at) VALUES (?, ?, ?, ?)",
            (uid, ref_id, bonus, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        save_json_backup()

        clickable_new_user = format_clickable_user_link(uid, user.full_name)
        try:
            await bot.send_message(
                ref_id,
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                f"   🎉 <b>রেফারেল বোনাস সফল!</b> 🎉\n"
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
                f"👤 <b>নতুন মেম্বার:</b> {clickable_new_user}\n"
                f"📢 <b>চ্যানেল যাচাই:</b> <code>সম্পন্ন হয়েছে ✓</code>\n"
                f"💎 <b>বোনাস রিওয়ার্ড:</b> <b>+{bonus} পয়েন্ট</b>\n"
                f"💰 <b>আপনার মোট পয়েন্ট:</b> <b>{new_ref_pts} পয়েন্ট</b>\n\n"
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                f"<i>নতুন মেম্বারের নামের ওপর চাপ দিয়ে তার অ্যাকাউন্ট দেখুন!</i>",
                parse_mode="HTML",
            )
        except Exception as notify_err:
            logger.warning(f"Failed to send referral message to {ref_id}: {notify_err}")
    except Exception as e:
        logger.error(f"Referral reward error ({uid} -> {ref_id}): {e}")


# ── FREE 12-HOUR TRIAL FOR NEW USERS ──────────────────
def get_or_start_free_trial(user_id: int) -> datetime:
    if user_id in user_free_trial:
        return user_free_trial[user_id]
    now = datetime.now()
    user_free_trial[user_id] = now
    try:
        conn = db_connect()
        conn.execute(
            "INSERT OR IGNORE INTO free_trials (user_id, first_upload_at) VALUES (?, ?)",
            (user_id, now.isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Free trial save error ({user_id}): {e}")
    save_json_backup()
    return now


def is_user_in_12h_free_trial(user_id: int) -> tuple[bool, timedelta]:
    if is_admin_user(user_id):
        return False, timedelta(0)
    start_dt = user_free_trial.get(user_id)
    if not start_dt:
        return True, timedelta(hours=12)
    elapsed = datetime.now() - start_dt
    trial_len = timedelta(hours=12)
    if elapsed < trial_len:
        return True, trial_len - elapsed
    return False, timedelta(0)


# ── HOURLY +10 USER DISPLAY COUNTER ──────────────────
def get_displayed_total_users() -> int:
    hours_passed = max(0, int((datetime.now() - BOT_START_TIME).total_seconds() // 3600))
    return len(active_users) + (hours_passed * 10)


# ── AUTO DEPENDENCY INSTALLER ────────────────────────
IMPORT_TO_PIP = {
    "telebot": "pyTelegramBotAPI",
    "telegram": "python-telegram-bot",
    "pyrogram": "pyrogram",
    "tgcrypto": "TgCrypto",
    "PIL": "Pillow",
    "cv2": "opencv-python-headless",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodomex",
    "jwt": "PyJWT",
    "dateutil": "python-dateutil",
    "discord": "discord.py",
    "yt_dlp": "yt-dlp",
    "youtube_dl": "youtube-dl",
    "sklearn": "scikit-learn",
    "websocket": "websocket-client",
    "serial": "pyserial",
}

STDLIB_MODULES = (
    set(sys.stdlib_module_names)
    if hasattr(sys, "stdlib_module_names")
    else {
        "os",
        "sys",
        "time",
        "json",
        "re",
        "math",
        "random",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "subprocess",
        "threading",
        "asyncio",
        "socket",
        "io",
        "sqlite3",
        "hashlib",
        "base64",
        "logging",
        "pathlib",
        "shutil",
        "zipfile",
        "urllib",
        "http",
        "email",
        "csv",
        "string",
        "copy",
        "typing",
        "traceback",
        "signal",
        "struct",
        "queue",
        "multiprocessing",
        "abc",
        "enum",
        "uuid",
        "glob",
    }
)


def extract_imports(file_path):
    modules = set()
    try:
        content = Path(file_path).read_text(errors="ignore")
        for match in re.finditer(
            r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)",
            content,
            re.MULTILINE,
        ):
            top_mod = match.group(1).split(".")[0].strip()
            if top_mod:
                modules.add(top_mod)
    except Exception as e:
        logger.error(f"Import extract error: {e}")
    return modules


def install_missing_dependencies(file_path, timeout=120):
    modules = extract_imports(file_path)
    installed, failed = [], []
    for mod in modules:
        if not mod or mod in STDLIB_MODULES or mod in ("__future__",):
            continue
        if importlib.util.find_spec(mod) is not None:
            continue

        pip_name = IMPORT_TO_PIP.get(mod, mod)
        cmds = [
            [sys.executable, "-m", "pip", "install", pip_name, "--break-system-packages", "--quiet"],
            [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
        ]
        ok = False
        for cmd in cmds:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                if res.returncode == 0:
                    installed.append(pip_name)
                    ok = True
                    break
            except Exception as e:
                logger.warning(f"pip attempt failed for {pip_name}: {e}")
        if not ok:
            failed.append(pip_name)
    return installed, failed


def find_user_by_identifier(ident: str):
    cleaned = (ident or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("@"):
        uname = cleaned[1:].strip().lower()
        for uid, prof in user_profiles.items():
            if (prof.get("username") or "").lstrip("@").strip().lower() == uname:
                return int(uid)
    for uid, prof in user_profiles.items():
        if (prof.get("name") or "").strip().lower() == cleaned.lower():
            return int(uid)
    try:
        return int(cleaned)
    except ValueError:
        return None


# ── BAN & CHANNEL HELPERS ────────────────────────────
async def check_and_refresh_ban(user_id: int):
    if is_admin_user(user_id):
        banned_users.pop(user_id, None)
        return False, None

    info = banned_users.get(user_id)
    if not info:
        return False, None

    now = datetime.now()
    until_dt = info.get("until")
    if until_dt and now >= until_dt:
        banned_users.pop(user_id, None)
        try:
            conn = db_connect()
            conn.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        save_json_backup()
        try:
            await bot.send_message(
                user_id,
                "✅ <b>অ্যাকাউন্ট আনব্যান হয়েছে!</b>\n\n🎉 আপনার ২৪ ঘণ্টার ব্যান শেষ হয়েছে। এখন আপনি আবার বট ব্যবহার করতে পারবেন!",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False, None

    rem = (until_dt - now) if until_dt else timedelta(hours=24)
    return True, rem


def format_timedelta(td: timedelta) -> str:
    total_sec = max(0, int(td.total_seconds()))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hrs}h {mins}m {secs}s"


def extract_chat_target(url_or_id: str):
    s = url_or_id.strip()
    if s.startswith("https://t.me/") or s.startswith("http://t.me/"):
        slug = s.split("t.me/", 1)[1].split("?")[0].strip("/")
        if slug.startswith("+") or slug.startswith("joinchat/"):
            return None
        return "@" + slug.lstrip("@")
    if s.startswith("@"):
        return s
    try:
        return int(s)
    except ValueError:
        return s


def get_configured_channels_sorted():
    ch_map = bot_settings.get("channels") or {}
    items = []
    for k, v in ch_map.items():
        if isinstance(v, dict):
            url = (v.get("url") or "").strip()
            target = v.get("target")
        else:
            url = str(v).strip()
            target = extract_chat_target(url)
        if url:
            items.append((int(k), url, target))
    items.sort(key=lambda x: x[0])
    return items


async def is_user_joined_all_channels(user_id: int) -> tuple[bool, list[int]]:
    if is_admin_user(user_id):
        return True, []

    channels = get_configured_channels_sorted()
    if not channels:
        return True, []

    missing = []
    for slot, url, target in channels:
        resolved = target or extract_chat_target(url)
        if not resolved:
            missing.append(slot)
            continue
        try:
            member = await bot.get_chat_member(chat_id=resolved, user_id=user_id)
            status = getattr(member, "status", "")
            if status in ("left", "kicked") or status not in ("creator", "administrator", "member", "restricted"):
                missing.append(slot)
            elif status == "restricted" and getattr(member, "is_member", True) is False:
                missing.append(slot)
        except Exception as e:
            logger.warning(f"Channel check error ({slot}, {resolved}): {e}")
            missing.append(slot)

    return (len(missing) == 0), missing


def build_force_join_keyboard(include_delete_for_admin: bool = False):
    rows = []
    for slot, url, _ in get_configured_channels_sorted():
        rows.append([InlineKeyboardButton(text=f"📢 চ্যানেল #{slot} জয়েন করুন", url=url)])
    rows.append([InlineKeyboardButton(text="✅ জয়েন যাচাই করুন", callback_data="verify_channels")])
    if include_delete_for_admin:
        rows.append([InlineKeyboardButton(text="🗑️ চ্যানেল ডিলিট", callback_data="admin_channel_delete_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_force_join_text(full_name: str) -> str:
    channels = get_configured_channels_sorted()
    ch_lines = "\n".join([f"✦ চ্যানেল <b>#{slot}</b> — নিচের বাটনে জয়েন করুন" for slot, _, _ in channels])
    return (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   ✨ <b>স্বাগতম, {html_lib.escape(full_name)}!</b> ✨\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"🚀 এই বটে <b>২৪/৭ ফ্রি বট হোস্টিং</b> উপভোগ করুন!\n"
        f"⚡ আপনার <code>.py</code>, <code>.js</code> ও <code>.zip</code> স্ক্রিপ্ট সহজে চালান।\n\n"
        f"📢 <b>বট ব্যবহার করতে নিচের চ্যানেলগুলোতে জয়েন করুন:</b>\n\n"
        f"{ch_lines}\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"👉 সব চ্যানেলে জয়েন করে <b>✅ জয়েন যাচাই করুন</b> বাটনে চাপ দিন!"
    )


# ── KEYBOARDS ────────────────────────────────────────
def get_main_keyboard(user_id: int):
    rows = [
        [InlineKeyboardButton(text="📢 আপডেট চ্যানেল", url=UPDATE_CHANNEL)],
        [
            InlineKeyboardButton(text="📤 ফাইল আপলোড", callback_data="upload_file"),
            InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
        ],
        [
            InlineKeyboardButton(text="👤 অ্যাকাউন্ট", callback_data="user_account"),
            InlineKeyboardButton(text="🎁 বোনাস", callback_data="claim_bonus"),
        ],
        [
            InlineKeyboardButton(text="🔗 রেফার", callback_data="user_refer"),
            InlineKeyboardButton(text="⚡ বট স্পিড", callback_data="bot_speed"),
        ],
        [
            InlineKeyboardButton(text="🔍 ফাইল খুঁজুন", callback_data="search_files"),
            InlineKeyboardButton(text="📊 মাই স্ট্যাটস", callback_data="statistics"),
        ],
    ]
    if is_admin_user(user_id):
        rows.append([InlineKeyboardButton(text="👑 অ্যাডমিন প্যানেল", callback_data="admin_panel")])
    rows.append(
        [
            InlineKeyboardButton(
                text="💬 যোগাযোগ",
                url=f"https://t.me/{YOUR_USERNAME.lstrip('@')}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💎 পয়েন্ট যোগ", callback_data="adm_point_add"),
                InlineKeyboardButton(text="⏳ লিমিট", callback_data="adm_limit_set"),
            ],
            [
                InlineKeyboardButton(text="🔗 চ্যানেল লিংক সেট", callback_data="adm_channel_set"),
                InlineKeyboardButton(text="🚫 ব্যান ইউজার", callback_data="adm_ban_user"),
            ],
            [
                InlineKeyboardButton(text="🔓 আনব্যান", callback_data="adm_unban_user"),
                InlineKeyboardButton(text="📢 মেম্বার মেসেজ", callback_data="adm_msg_member"),
            ],
            [
                InlineKeyboardButton(text="🎟️ রিডিম কোড", callback_data="adm_redeem_code"),
                InlineKeyboardButton(text="🤝 রেফার বোনাস", callback_data="adm_refer_bonus"),
            ],
            [
                InlineKeyboardButton(text="💰 ফাইল পয়েন্ট সেট", callback_data="adm_file_point"),
                InlineKeyboardButton(text="📂 অল ফাইল", callback_data="adm_all_files"),
            ],
            [
                InlineKeyboardButton(text="📁 ফাইন চেক", callback_data="adm_fine_check"),
                InlineKeyboardButton(text="🗑️ চ্যানেল ডিলিট", callback_data="admin_channel_delete_menu"),
            ],
            [
                InlineKeyboardButton(text="⏱️ বট আপটাইম", callback_data="admin_bot_uptime"),
                InlineKeyboardButton(text="👥 মোট ইউজার", callback_data="admin_total_users_simple"),
            ],
            [
                InlineKeyboardButton(text="📝 লগস", callback_data="admin_view_logs"),
                InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
            ],
        ]
    )


def cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ বাতিল করুন", callback_data="cancel_action")]]
    )


def build_welcome_text(user_id: int, full_name: str) -> str:
    pts_display = "আনলিমিটেড ♾️" if is_admin_user(user_id) else f"{user_points.get(user_id, 0)} পয়েন্ট"
    in_trial, rem_trial = is_user_in_12h_free_trial(user_id)
    trial_line = (
        f"🆓 <b>ফ্রি আপলোড সময়:</b> <b>{format_timedelta(rem_trial)} বাকি</b>\n"
        if in_trial
        else ""
    )
    return (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   🚀 <b>GENIUS HOSTBOT</b> 🚀\n"
        f"   ⚡ <i>২৪/৭ ফ্রি বট হোস্টিং প্ল্যাটফর্ম</i>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"✨ <b>স্বাগতম, {html_lib.escape(full_name)}!</b>\n"
        f"🎉 এখানে সহজেই <b>২৪/৭ বট হোস্টিং</b> ব্যবহার করুন!\n\n"
        f"🆔 <b>চ্যাট আইডি:</b> <code>{user_id}</code>\n"
        f"💎 <b>পয়েন্ট ব্যালেন্স:</b> <b>{pts_display}</b>\n"
        f"{trial_line}\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"🔥 <b>মূল সুবিধাসমূহ:</b>\n"
        f"📤 ফাইল আপলোড ও অটো-রান (<code>.py</code>, <code>.js</code>, <code>.zip</code>)\n"
        f"👤 অ্যাকাউন্ট তথ্য • 🎁 ডেইলি রিডিম বোনাস\n"
        f"🔗 রেফার রিওয়ার্ড • ⚡ স্পিড ও স্ট্যাটস\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"📢 <b>আপডেট চ্যানেল:</b> {UPDATE_CHANNEL}\n"
        f"💬 <b>সাপোর্ট / যোগাযোগ:</b> {YOUR_USERNAME}"
    )


async def cleanup_finished_scripts():
    for key in list(bot_scripts.keys()):
        info = bot_scripts.get(key)
        if not info:
            continue
        proc = info.get("process")
        if proc and proc.poll() is not None:
            mt = info.get("monitor_task")
            if mt and not mt.done():
                mt.cancel()
            out_s = info.get("out_stream")
            if out_s and not out_s.closed:
                try:
                    out_s.close()
                except Exception:
                    pass
            bot_scripts.pop(key, None)


async def stop_running_script_by_key(script_key: str) -> bool:
    info = bot_scripts.get(script_key)
    if not info:
        return False
    try:
        mt = info.get("monitor_task")
        if mt and not mt.done():
            mt.cancel()
        parent = psutil.Process(info["process"].pid)
        for ch in parent.children(recursive=True):
            try:
                ch.terminate()
            except Exception:
                pass
        try:
            parent.terminate()
        except Exception:
            pass
    except Exception:
        pass
    out_s = info.get("out_stream")
    if out_s and not out_s.closed:
        try:
            out_s.close()
        except Exception:
            pass
    bot_scripts.pop(script_key, None)
    return True


async def monitor_script_point_limit(script_key: str, user_id: int, file_name: str):
    while True:
        interval_mins = max(1, int(bot_settings.get("limit_minutes", 10)))
        await asyncio.sleep(interval_mins * 60)

        info = bot_scripts.get(script_key)
        if not info:
            break
        proc = info.get("process")
        if not proc or proc.poll() is not None:
            await cleanup_finished_scripts()
            break

        if is_admin_user(user_id):
            continue

        in_trial, _ = is_user_in_12h_free_trial(user_id)
        if in_trial:
            continue

        pts_cost = max(0, int(bot_settings.get("limit_points", 5)))
        if pts_cost <= 0:
            continue

        curr_pts = user_points.get(user_id, 0)
        if curr_pts < pts_cost:
            await stop_running_script_by_key(script_key)
            try:
                await bot.send_message(
                    user_id,
                    f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                    f"   🛑 <b>স্ক্রিপ্ট স্বয়ক্রিয়ভাবে বন্ধ হয়েছে!</b>\n"
                    f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
                    f"📄 <b>ফাইল:</b> <code>{html_lib.escape(file_name)}</code>\n"
                    f"💎 <b>প্রয়োজনীয় পয়েন্ট:</b> <b>{pts_cost} পয়েন্ট</b>\n"
                    f"💰 <b>আপনার বর্তমান পয়েন্ট:</b> <b>{curr_pts} পয়েন্ট</b>\n\n"
                    f"পয়েন্ট শেষ হয়ে যাওয়ায় স্ক্রিপ্টটি বন্ধ করা হয়েছে।",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            break

        new_pts = add_user_points(user_id, -pts_cost)
        try:
            await bot.send_message(
                user_id,
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                f"   ⏳ <b>লিমিট পয়েন্ট কাটা হয়েছে!</b>\n"
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
                f"📄 <b>ফাইল:</b> <code>{html_lib.escape(file_name)}</code>\n"
                f"⏱️ <b>সময় পর পর:</b> প্রতি <b>{interval_mins} মিনিট</b>\n"
                f"💎 <b>কাটা পয়েন্ট:</b> <b>-{pts_cost} পয়েন্ট</b>\n"
                f"💰 <b>বাকি পয়েন্ট:</b> <b>{new_pts} পয়েন্ট</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def ensure_access(event: types.Message | types.CallbackQuery) -> bool:
    user = event.from_user
    if not user:
        return False
    uid = user.id
    is_cb = isinstance(event, types.CallbackQuery)
    msg = event.message if is_cb else event

    banned, rem = await check_and_refresh_ban(uid)
    if banned:
        rem_str = format_timedelta(rem or timedelta(hours=24))
        ban_msg = (
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   🚫 <b>অ্যাকাউন্ট ব্যান করা হয়েছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"⏳ <b>বাকি সময়:</b> <code>{rem_str}</code>\n"
            f"আপনার চলমান স্ক্রিপ্ট বন্ধ করা হয়েছে এবং ২৪ ঘণ্টার জন্য আপলোড বন্ধ রাখা হয়েছে।"
        )
        if is_cb:
            await safe_answer_callback(event, f"🚫 ব্যান! বাকি সময়: {rem_str}", show_alert=True)
        else:
            await msg.answer(ban_msg, parse_mode="HTML")
        return False

    if get_configured_channels_sorted():
        joined, _ = await is_user_joined_all_channels(uid)
        if not joined:
            text = build_force_join_text(user.full_name)
            kb = build_force_join_keyboard(include_delete_for_admin=is_admin_user(uid))
            if is_cb:
                await safe_edit_message(msg, text, reply_markup=kb, parse_mode="HTML")
                await safe_answer_callback(event, "⚠️ আগে সব চ্যানেলে জয়েন করুন!", show_alert=True)
            else:
                await msg.answer(text, reply_markup=kb, parse_mode="HTML")
            return False

    await grant_referral_reward_if_eligible(user)
    return True


async def record_active_user(user: types.User) -> bool:
    uid = int(user.id)
    is_brand_new = uid not in active_users
    active_users.add(uid)
    user_profiles[uid] = {
        "name": user.full_name or f"User {uid}",
        "username": user.username or "",
    }
    user_points.setdefault(uid, 0)
    try:
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT 1 FROM active_users WHERE user_id = ?", (uid,))
        if c.fetchone() is not None:
            is_brand_new = False
        now = datetime.now().isoformat()
        c.execute(
            "INSERT OR IGNORE INTO active_users (user_id, join_date, last_active, full_name, username) VALUES (?, ?, ?, ?, ?)",
            (uid, now, now, user.full_name or "", user.username or ""),
        )
        c.execute(
            "UPDATE active_users SET last_active = ?, full_name = ?, username = ? WHERE user_id = ?",
            (now, user.full_name or "", user.username or "", uid),
        )
        c.execute(
            "INSERT OR IGNORE INTO user_points (user_id, points) VALUES (?, ?)",
            (uid, user_points[uid]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Record user error ({uid}): {e}")
    save_json_backup()
    return is_brand_new


# ── START & VERIFY ───────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    uid = user.id
    is_brand_new = await record_active_user(user)
    await cleanup_finished_scripts()

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try:
            ref_id = int(parts[1].replace("ref_", "", 1).strip())
            if ref_id != uid and uid not in referred_by and is_brand_new:
                referred_by[uid] = ref_id
                conn = db_connect()
                conn.execute(
                    "INSERT OR IGNORE INTO referrals (referred_id, referrer_id, created_at) VALUES (?, ?, ?)",
                    (uid, ref_id, datetime.now().isoformat()),
                )
                conn.commit()
                conn.close()
                save_json_backup()
        except Exception as e:
            logger.error(f"Referral save error: {e}")

    banned, rem = await check_and_refresh_ban(uid)
    if banned:
        rem_str = format_timedelta(rem or timedelta(hours=24))
        await message.answer(
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   🚫 <b>আপনার অ্যাকাউন্ট ব্যান করা আছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"⏳ <b>বাকি সময়:</b> <code>{rem_str}</code>\n"
            f"💬 <b>সহায়তা:</b> {YOUR_USERNAME}",
            parse_mode="HTML",
        )
        return

    if get_configured_channels_sorted():
        joined, _ = await is_user_joined_all_channels(uid)
        if not joined:
            await message.answer(
                build_force_join_text(user.full_name),
                reply_markup=build_force_join_keyboard(include_delete_for_admin=is_admin_user(uid)),
                parse_mode="HTML",
            )
            return

    await grant_referral_reward_if_eligible(user)

    await message.answer(
        build_welcome_text(uid, user.full_name),
        reply_markup=get_main_keyboard(uid),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "verify_channels")
async def callback_verify_channels(callback: types.CallbackQuery):
    user = callback.from_user
    uid = user.id
    await record_active_user(user)

    banned, rem = await check_and_refresh_ban(uid)
    if banned:
        await safe_answer_callback(callback, f"🚫 ব্যান! বাকি সময়: {format_timedelta(rem or timedelta(hours=24))}", show_alert=True)
        return

    joined, _ = await is_user_joined_all_channels(uid)
    if not joined:
        await safe_answer_callback(callback, "❌ আপনি এখনো সব চ্যানেলে জয়েন করেননি!", show_alert=True)
        return

    await grant_referral_reward_if_eligible(user)

    await safe_answer_callback(callback, "✅ সফলভাবে ভেরিফাই হয়েছে!", show_alert=True)
    await safe_edit_message(
        callback.message,
        build_welcome_text(uid, user.full_name),
        reply_markup=get_main_keyboard(uid),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "cancel_action")
async def callback_cancel_action(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_states.pop(uid, None)
    await safe_answer_callback(callback, "❌ বাতিল করা হয়েছে", show_alert=False)
    await callback_back_to_main(callback)


@dp.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: types.CallbackQuery):
    uid = callback.from_user.id
    user_states.pop(uid, None)
    await cleanup_finished_scripts()
    if not await ensure_access(callback):
        return

    pts_str = "আনলিমিটেড ♾️" if is_admin_user(uid) else f"{user_points.get(uid, 0)} পয়েন্ট"
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   🏠 <b>মেইন মেনু</b> 🏠\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"🙋 <b>নাম:</b> <b>{html_lib.escape(callback.from_user.full_name)}</b>\n"
        f"🆔 <b>চ্যাট আইডি:</b> <code>{uid}</code>\n"
        f"💎 <b>পয়েন্ট ব্যালেন্স:</b> <b>{pts_str}</b>\n"
        f"📁 <b>হোস্ট করা ফাইল:</b> <b>{len(user_files.get(uid, []))} টি</b>\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"👇 <i>নিচের যেকোনো বাটন বেছে নিন</i>"
    )
    await safe_edit_message(callback.message, text, reply_markup=get_main_keyboard(uid), parse_mode="HTML")
    await safe_answer_callback(callback)


# ── ACCOUNT / BONUS / REFER (BENGALI DESIGNED) ───────
@dp.callback_query(F.data == "user_account")
async def callback_user_account(callback: types.CallbackQuery):
    uid = callback.from_user.id
    await record_active_user(callback.from_user)
    await cleanup_finished_scripts()
    if not await ensure_access(callback):
        return

    hosted_count = len(user_files.get(uid, []))
    pts_display = "আনলিমিটেড ♾️" if is_admin_user(uid) else f"{user_points.get(uid, 0)} পয়েন্ট"
    role_badge = "👑 অ্যাডমিন" if is_admin_user(uid) else "👤 মেম্বার"
    in_trial, rem_trial = is_user_in_12h_free_trial(uid)
    trial_txt = f"🆓 <b>ফ্রি ট্রায়াল বাকি:</b> <b>{format_timedelta(rem_trial)}</b>\n" if in_trial else ""
    running_cnt = sum(1 for k in bot_scripts if k.startswith(f"{uid}_"))

    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   👤 <b>অ্যাকাউন্ট প্রোফাইল</b> 👤\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"🏷️ <b>ধরন:</b> {role_badge}\n"
        f"🙋 <b>নাম:</b> <b>{html_lib.escape(callback.from_user.full_name)}</b>\n"
        f"🆔 <b>চ্যাট আইডি:</b> <code>{uid}</code>\n"
        f"💎 <b>মোট পয়েন্ট:</b> <b>{pts_display}</b>\n"
        f"{trial_txt}"
        f"📂 <b>হোস্ট করা ফাইল:</b> <b>{hosted_count} টি</b>\n"
        f"▶️ <b>চলমান স্ক্রিপ্ট:</b> <b>{running_cnt} টি</b>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 বোনাস", callback_data="claim_bonus"),
                InlineKeyboardButton(text="🔗 রেফার", callback_data="user_refer"),
            ],
            [InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")],
        ]
    )

    photo_bytes = await get_user_profile_photo_bytes(uid)
    if photo_bytes:
        try:
            await callback.message.answer_photo(
                photo=BufferedInputFile(photo_bytes, filename=f"avatar_{uid}.jpg"),
                caption=text,
                reply_markup=kb,
                parse_mode="HTML",
            )
            await safe_answer_callback(callback)
            return
        except Exception as e:
            logger.warning(f"Send profile photo failed for {uid}: {e}")

    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "claim_bonus")
async def callback_claim_bonus(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    user_states[uid] = {"step": "await_redeem_input", "data": {}}
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   🎁 <b>ডেইলি বোনাস রিডিম সেন্টার</b> 🎁\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"✨ আপনার ফ্রি বোনাস পয়েন্ট পেতে নিচে <b>৬ ডিজিটের রিডিম কোড</b> লিখুন!\n\n"
        f"🔹 কোডটি অবশ্যই <b>৬ সংখ্যার (0-9)</b> হতে হবে\n"
        f"🔹 রাত <b>১২:০০টার আগে</b> কোডটি ব্যবহার করতে হবে\n"
        f"🔹 প্রতিটি অ্যাকাউন্টে দিনে <b>একবার</b> বোনাস নেওয়া যাবে\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"⌨️ <i>এখন আপনার ৬ ডিজিটের কোডটি লিখে পাঠান...</i>"
    )
    await safe_edit_message(callback.message, text, reply_markup=cancel_keyboard(), parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "user_refer")
async def callback_user_refer(callback: types.CallbackQuery):
    global _BOT_USERNAME
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    if not _BOT_USERNAME:
        try:
            _BOT_USERNAME = (await bot.get_me()).username or ""
        except Exception:
            _BOT_USERNAME = ""

    ref_link = (
        f"https://t.me/{_BOT_USERNAME}?start=ref_{uid}"
        if _BOT_USERNAME
        else f"/start ref_{uid}"
    )
    ref_bonus = int(bot_settings.get("refer_bonus", 10))
    ref_count = sum(1 for r_id in referred_by.values() if r_id == uid)

    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   🔗 <b>রেফার করুন ও পয়েন্ট জিতুন</b> 🔗\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"💎 <b>প্রতি সফল রেফারে বোনাস:</b> <b>+{ref_bonus} পয়েন্ট</b>\n"
        f"👥 <b>আপনার মোট রেফার:</b> <b>{ref_count} জন</b>\n\n"
        f"📎 <b>আপনার ব্যক্তিগত রেফার লিংক:</b>\n"
        f"<code>{html_lib.escape(ref_link)}</code>\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"🌟 <b>নিয়মাবলি:</b>\n"
        f"১️⃣ উপরের লিংকটি বন্ধুদের সাথে শেয়ার করুন।\n"
        f"২️⃣ নতুন ইউজার লিংক থেকে জয়েন করে চ্যানেল ভেরিফাই করলেই\n"
        f"৩️⃣ আপনার অ্যাকাউন্টে সাথে সাথে <b>+{ref_bonus} পয়েন্ট</b> যোগ হবে!"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 অ্যাকাউন্ট", callback_data="user_account"),
                InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
            ]
        ]
    )
    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


# ── UPLOAD FILE & AUTO-RUN / AUTO-FORWARD ──────────
async def launch_user_script_internal(user_id: int, file_name: str):
    await cleanup_finished_scripts()
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    file_path = user_folder / file_name
    if not file_path.exists():
        return False, "❌ ফাইলটি খুঁজে পাওয়া যায়নি!"

    in_trial, rem_trial = is_user_in_12h_free_trial(user_id)
    limit_mins = max(1, int(bot_settings.get("limit_minutes", 10)))
    limit_pts = max(0, int(bot_settings.get("limit_points", 5)))

    if not is_admin_user(user_id) and not in_trial and limit_pts > 0:
        current_pts = user_points.get(user_id, 0)
        if current_pts < limit_pts:
            return (
                False,
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                f"   ❌ <b>পর্যাপ্ত পয়েন্ট নেই!</b>\n"
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
                f"⏱️ <b>লিমিট:</b> প্রতি <b>{limit_mins} মিনিটে {limit_pts} পয়েন্ট</b>\n"
                f"💰 <b>আপনার পয়েন্ট:</b> <b>{current_pts} পয়েন্ট</b>",
            )

    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts and bot_scripts[script_key]["process"].poll() is None:
        return True, f"⚠️ স্ক্রিপ্টটি ইতিমধ্যে চলছে! (PID: <code>{bot_scripts[script_key]['process'].pid}</code>)"

    file_ext = file_path.suffix.lower()
    log_file_path = user_folder / f"{file_path.stem}.log"
    out_stream = None

    try:
        with open(log_file_path, "w", encoding="utf-8", errors="replace") as log_file:
            if file_ext == ".py":
                installed, failed = install_missing_dependencies(file_path)
                if installed:
                    log_file.write(f"[AUTO-INSTALLER] Installed: {', '.join(installed)}\n")
                if failed:
                    log_file.write(f"[AUTO-INSTALLER] Failed: {', '.join(failed)}\n")
                log_file.flush()

        out_stream = open(log_file_path, "a", encoding="utf-8", errors="replace")
        if file_ext == ".py":
            process = subprocess.Popen(
                [sys.executable, str(file_path)],
                cwd=str(user_folder),
                stdout=out_stream,
                stderr=out_stream,
            )
        elif file_ext == ".js":
            process = subprocess.Popen(
                ["node", str(file_path)],
                cwd=str(user_folder),
                stdout=out_stream,
                stderr=out_stream,
            )
        else:
            out_stream.close()
            return False, "❌ এই ধরনের ফাইল চালানো যায় না!"

        monitor_task = asyncio.create_task(
            monitor_script_point_limit(script_key, user_id, file_name)
        )

        bot_scripts[script_key] = {
            "process": process,
            "file_name": file_name,
            "script_owner_id": user_id,
            "start_time": datetime.now(),
            "user_folder": str(user_folder),
            "type": file_ext[1:],
            "out_stream": out_stream,
            "monitor_task": monitor_task,
        }

        try:
            conn = db_connect()
            conn.execute(
                "UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = ?",
                ("total_runs",),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        bot_stats["total_runs"] = bot_stats.get("total_runs", 0) + 1
        save_json_backup()

        if is_admin_user(user_id):
            billing_info = "\n💎 <b>বিলিং:</b> অ্যাডমিন (কোনো পয়েন্ট কাটবে না)"
        elif in_trial:
            billing_info = f"\n🆓 <b>ফ্রি ট্রায়াল:</b> ফ্রি চলছে ({format_timedelta(rem_trial)} বাকি)"
        else:
            billing_info = f"\n⏳ <b>পয়েন্ট কাটার নিয়ম:</b> প্রতি <b>{limit_mins} মিনিট</b> পর পর <b>{limit_pts} পয়েন্ট</b> কাটবে"

        return (
            True,
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   ✅ <b>স্ক্রিপ্ট সফলভাবে চালু হয়েছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"📄 <b>ফাইল:</b> <code>{html_lib.escape(file_name)}</code>\n"
            f"🆔 <b>PID:</b> <code>{process.pid}</code>{billing_info}",
        )
    except Exception as e:
        if out_stream and not out_stream.closed:
            try:
                out_stream.close()
            except Exception:
                pass
        logger.error(f"Launch error ({file_name}): {e}")
        return False, f"❌ স্ক্রিপ্ট চালু করতে সমস্যা হয়েছে: <code>{html_lib.escape(str(e))}</code>"


@dp.callback_query(F.data == "upload_file")
async def callback_upload_file(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    if bot_locked and not is_admin_user(uid):
        await safe_answer_callback(callback, "🔒 মেইনটেন্যান্সের জন্য বট লক করা আছে!", show_alert=True)
        return

    in_trial, rem_trial = is_user_in_12h_free_trial(uid)
    limit_mins = max(1, int(bot_settings.get("limit_minutes", 10)))
    limit_pts = max(0, int(bot_settings.get("limit_points", 5)))
    file_pt = int(bot_settings.get("file_point", 5))
    curr_pts = user_points.get(uid, 0)

    if not is_admin_user(uid) and not in_trial and limit_pts > 0 and curr_pts < limit_pts:
        text = (
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   ⚠️ <b>পয়েন্ট ব্যালেন্স কম আছে</b> ⚠️\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"❌ <b>আপনার ফ্রি ১২ ঘণ্টার সময় শেষ এবং প্রয়োজনীয় পয়েন্ট নেই!</b>\n\n"
            f"⏳ <b>লিমিট:</b> প্রতি <b>{limit_mins} মিনিটে {limit_pts} পয়েন্ট</b>\n"
            f"💰 <b>ফাইল পয়েন্ট রেট:</b> <b>{file_pt} পয়েন্ট</b>\n"
            f"💰 <b>আপনার পয়েন্ট:</b> <b>{curr_pts} পয়েন্ট</b>\n\n"
            f"🎁 <b>বোনাস</b> থেকে কোড নিন অথবা <b>রেফার</b> করে পয়েন্ট জমান!"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎁 বোনাস", callback_data="claim_bonus"),
                    InlineKeyboardButton(text="🔗 রেফার", callback_data="user_refer"),
                ],
                [InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")],
            ]
        )
        await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")
        await safe_answer_callback(callback, "⚠️ পয়েন্ট কম!", show_alert=True)
        return

    current_files = len(user_files.get(uid, []))
    if is_admin_user(uid):
        access_line = "💎 <b>অ্যাক্সেস:</b> অ্যাডমিন (আনলিমিটেড ♾️)"
    elif in_trial:
        access_line = f"🆓 <b>ফ্রি আপলোড:</b> সক্রিয় ({format_timedelta(rem_trial)} বাকি)"
    else:
        access_line = f"⏳ <b>লিমিট:</b> প্রতি <b>{limit_mins} মিনিটে {limit_pts} পয়েন্ট</b> (আপনার আছে: <b>{curr_pts}</b>)"

    upload_text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📤 <b>ফাইল আপলোড সেন্টার</b> 📤\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"📁 <b>আপনার ফাইল:</b> <b>{current_files} টি</b>\n"
        f"💰 <b>ফাইল পয়েন্ট:</b> <b>{file_pt} পয়েন্ট</b>\n"
        f"{access_line}\n\n"
        f"📝 <b>সাপোর্টেড ফাইল:</b> <code>.py</code>, <code>.js</code>, <code>.zip</code>\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"⚡ <i>এখন আপনার ফাইলটি পাঠান — প্যাকেজ অটো-ইনস্টল ও স্ক্রিপ্ট অটো-রান হবে!</i>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")]]
    )
    await safe_edit_message(callback.message, upload_text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.message(F.document)
async def handle_document(message: types.Message):
    user = message.from_user
    uid = user.id
    await record_active_user(user)

    st = user_states.get(uid)
    if st and is_admin_user(uid) and st.get("step") == "adm_all_files_add":
        user_states.pop(uid, None)
        doc = message.document
        add_name = os.path.basename(doc.file_name or "new_file.txt")
        dst_path = STAGING_FILES_DIR / add_name
        await bot.download(doc, destination=dst_path)
        staged_deletions.discard(add_name)
        staged_additions.add(add_name)
        if add_name not in bot_settings["all_files_list"]:
            bot_settings["all_files_list"].append(add_name)
            save_all_files_list_to_db()
        await message.answer(
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   ✅ <b>নতুন ফাইল প্রস্তুত হয়েছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"📄 <b>ফাইল:</b> <code>{html_lib.escape(add_name)}</code>\n"
            f"ℹ️ নিচে <b>▶️ স্টার্ট</b> বাটনে চাপ দিলে এটি মূল ফাইলে যুক্ত হবে।",
            reply_markup=build_all_files_keyboard(),
            parse_mode="HTML",
        )
        return

    if st and is_admin_user(uid) and st.get("step") == "adm_all_files_edit":
        target_fname = st.get("data", {}).get("target_file")
        user_states.pop(uid, None)
        if target_fname:
            doc = message.document
            dst_path = STAGING_FILES_DIR / target_fname
            await bot.download(doc, destination=dst_path)
            staged_deletions.discard(target_fname)
            await message.answer(
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
                f"   ✅ <b>সাক্সেস! ফাইল আপডেট হয়েছে।</b>\n"
                f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
                f"📄 <b>ফাইল:</b> <code>{html_lib.escape(target_fname)}</code>\n"
                f"ℹ️ <b>▶️ স্টার্ট</b> বাটনে চাপলে পরিবর্তনটি মূল ফাইলে কার্যকর হবে।",
                reply_markup=build_all_files_keyboard(),
                parse_mode="HTML",
            )
        return

    if not await ensure_access(message):
        return

    if bot_locked and not is_admin_user(uid):
        await message.answer("🔒 বটটি বর্তমানে মেইনটেন্যান্সের জন্য লক করা আছে!")
        return

    in_trial, _ = is_user_in_12h_free_trial(uid)
    limit_pts = max(0, int(bot_settings.get("limit_points", 5)))
    curr_pts = user_points.get(uid, 0)

    if not is_admin_user(uid) and not in_trial and limit_pts > 0 and curr_pts < limit_pts:
        await message.answer(
            f"❌ <b>পর্যাপ্ত পয়েন্ট নেই!</b>\n\n"
            f"💎 <b>প্রয়োজনীয় পয়েন্ট:</b> <b>{limit_pts}</b>\n"
            f"💰 <b>আপনার পয়েন্ট:</b> <b>{curr_pts}</b>",
            parse_mode="HTML",
        )
        return

    document = message.document
    file_name = os.path.basename(document.file_name or "script.py")
    file_ext = os.path.splitext(file_name)[1].lower()

    if file_ext not in [".py", ".js", ".zip"]:
        await message.answer(
            "❌ শুধুমাত্র <code>.py</code>, <code>.js</code> এবং <code>.zip</code> ফাইল সাপোর্ট করে!",
            parse_mode="HTML",
        )
        return

    if not is_admin_user(uid):
        get_or_start_free_trial(uid)

    user_folder = UPLOAD_BOTS_DIR / str(uid)
    user_folder.mkdir(parents=True, exist_ok=True)
    file_path = user_folder / file_name

    status_msg = await message.answer(
        f"📥 <b><code>{html_lib.escape(file_name)}</code> ডাউনলোড হচ্ছে...</b>",
        parse_mode="HTML",
    )

    try:
        await bot.download(document, destination=file_path)

        user_files.setdefault(uid, [])
        ftype = file_ext[1:]
        if (file_name, ftype) not in user_files[uid]:
            user_files[uid].append((file_name, ftype))

        conn = db_connect()
        c = conn.cursor()
        now_iso = datetime.now().isoformat()
        c.execute(
            "INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, upload_date) VALUES (?, ?, ?, ?)",
            (uid, file_name, ftype, now_iso),
        )
        c.execute(
            "UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = ?",
            ("total_uploads",),
        )
        conn.commit()
        conn.close()
        bot_stats["total_uploads"] = bot_stats.get("total_uploads", 0) + 1
        save_json_backup()

        sender_handle = format_user_mention_or_name(uid, user.full_name)
        for adm_id in get_admin_recipients():
            if adm_id == uid:
                continue
            try:
                await bot.send_document(
                    adm_id,
                    FSInputFile(file_path),
                    caption=(
                        f"📥 <b>নতুন ফাইল আপলোড হয়েছে</b>\n\n"
                        f"👤 <b>ইউজারনেম:</b> <code>{html_lib.escape(sender_handle)}</code> (<code>{uid}</code>)\n"
                        f"📄 <b>ফাইল:</b> <code>{html_lib.escape(file_name)}</code>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as fwd_err:
                logger.warning(f"Forward to admin {adm_id} failed: {fwd_err}")

        if file_ext in [".py", ".js"]:
            await safe_edit_message(
                status_msg,
                f"⚙️ <b>ডিপেন্ডেন্সি ইনস্টল ও <code>{html_lib.escape(file_name)}</code> চালু হচ্ছে...</b>",
                parse_mode="HTML",
            )
            ok, run_info = await launch_user_script_internal(uid, file_name)
            script_key = f"{uid}_{file_name}"
            rows = []
            if ok and script_key in bot_scripts:
                rows.append(
                    [InlineKeyboardButton(text="🛑 স্ক্রিপ্ট বন্ধ করুন", callback_data=f"stop_script:{script_key}")]
                )
                rows.append([InlineKeyboardButton(text="📋 আউটপুট দেখুন", callback_data=f"view_log:{file_name}")])
            rows.append(
                [
                    InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
                    InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
                ]
            )
            await safe_edit_message(
                status_msg,
                run_info,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
        else:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="📦 ZIP আনজিপ করুন", callback_data=f"extract_zip:{file_name}"),
                        InlineKeyboardButton(text="🗑️ ডিলিট", callback_data=f"delete_file:{file_name}"),
                    ],
                    [
                        InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
                        InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
                    ],
                ]
            )
            await safe_edit_message(
                status_msg,
                f"✅ <b>ZIP ফাইল আপলোড হয়েছে!</b>\n\n📄 <b>ফাইল:</b> <code>{html_lib.escape(file_name)}</code>",
                reply_markup=kb,
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Document handler error: {e}", exc_info=True)
        await safe_edit_message(status_msg, f"❌ আপলোড সমস্যা: <code>{html_lib.escape(str(e))}</code>", parse_mode="HTML")


# ── FILE MANAGEMENT, RUN, STOP, LOG, ZIP ─────────────
def build_user_files_view(uid: int):
    files = user_files.get(uid, [])
    if not files:
        text = (
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   📁 <b>আমার ফাইলসমূহ</b> 📁\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"📭 এখনো কোনো ফাইল আপলোড করা হয়নি!"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 ফাইল আপলোড", callback_data="upload_file")],
                [InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")],
            ]
        )
        return text, kb

    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📁 <b>আমার ফাইল ({len(files)} টি)</b> 📁\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
    )
    buttons = []
    for i, (fname, ftype) in enumerate(files, 1):
        icon = "🐍" if ftype == "py" else "🟨" if ftype == "js" else "📦"
        running_flag = " 🟢" if f"{uid}_{fname}" in bot_scripts else ""
        text += f"{i}. {icon} <code>{html_lib.escape(fname)}</code>{running_flag}\n"
        buttons.append(
            [
                InlineKeyboardButton(text=f"▶️ রান {fname[:12]}", callback_data=f"run_script:{fname}"),
                InlineKeyboardButton(text="📋 লগ", callback_data=f"view_log:{fname}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"delete_file:{fname}"),
            ]
        )
    buttons.append([InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data == "check_files")
async def callback_check_files(callback: types.CallbackQuery):
    uid = callback.from_user.id
    await cleanup_finished_scripts()
    if not await ensure_access(callback):
        return

    text, kb = build_user_files_view(uid)
    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data.startswith("run_script:"))
async def callback_run_script(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    fname = callback.data.split(":", 1)[1]
    ok, info_text = await launch_user_script_internal(uid, fname)
    if not ok:
        await callback.message.answer(info_text, parse_mode="HTML")
        await safe_answer_callback(callback, "⚠️ স্ক্রিপ্ট চালু করা যায়নি", show_alert=True)
        return

    script_key = f"{uid}_{fname}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛑 স্ক্রিপ্ট বন্ধ করুন", callback_data=f"stop_script:{script_key}")],
            [InlineKeyboardButton(text="📋 আউটপুট দেখুন", callback_data=f"view_log:{fname}")],
            [
                InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
                InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
            ],
        ]
    )
    await callback.message.answer(info_text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback, "✅ চালু হয়েছে!", show_alert=False)


@dp.callback_query(F.data.startswith("view_log:"))
async def callback_view_log(callback: types.CallbackQuery):
    uid = callback.from_user.id
    await cleanup_finished_scripts()
    if not await ensure_access(callback):
        return

    fname = callback.data.split(":", 1)[1]
    log_path = UPLOAD_BOTS_DIR / str(uid) / f"{Path(fname).stem}.log"
    if not log_path.exists():
        await safe_answer_callback(callback, "❌ এখনো কোনো লগ পাওয়া যায়নি।", show_alert=True)
        return

    content = log_path.read_text(errors="replace").strip() or "(কোনো আউটপুট নেই)"
    if len(content) > 3500:
        content = "...(truncated)...\n" + content[-3500:]

    running = "🟢 চলছে" if f"{uid}_{fname}" in bot_scripts else "🔴 বন্ধ"
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📋 <b>স্ক্রিপ্ট আউটপুট লগ</b> 📋\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"📄 <b>ফাইল:</b> <code>{html_lib.escape(fname)}</code> ({running})\n"
        f"<pre>{html_lib.escape(content)}</pre>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 রিফ্রেশ", callback_data=f"view_log:{fname}")],
            [InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files")],
        ]
    )
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data.startswith("stop_script:"))
async def callback_stop_script(callback: types.CallbackQuery):
    script_key = callback.data.split(":", 1)[1]
    if not await stop_running_script_by_key(script_key):
        await safe_answer_callback(callback, "❌ স্ক্রিপ্টটি আগেই বন্ধ হয়ে গেছে!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
                InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
            ]
        ]
    )
    await safe_answer_callback(callback, "✅ স্ক্রিপ্ট বন্ধ করা হয়েছে!", show_alert=True)
    await safe_edit_message(callback.message, "🛑 <b>স্ক্রিপ্ট বন্ধ করা হয়েছে!</b>", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("extract_zip:"))
async def callback_extract_zip(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    fname = callback.data.split(":", 1)[1]
    user_folder = UPLOAD_BOTS_DIR / str(uid)
    zip_path = user_folder / fname
    if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        await safe_answer_callback(callback, "❌ ZIP ফাইলটি সঠিক নয় বা নেই!", show_alert=True)
        return

    try:
        base_resolved = user_folder.resolve()
        names = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                target_p = (user_folder / member.filename).resolve()
                if not str(target_p).startswith(str(base_resolved) + os.sep) and target_p != base_resolved:
                    continue
                zf.extract(member, path=user_folder)
                names.append(member.filename)

        registered = []
        conn = db_connect()
        c = conn.cursor()
        now_iso = datetime.now().isoformat()
        for rel in names:
            if rel.endswith("/"):
                continue
            p = Path(rel)
            ext = p.suffix.lower()
            if ext in [".py", ".js"]:
                just_name = p.name
                src = user_folder / rel
                dst = user_folder / just_name
                if src.exists() and src != dst:
                    if dst.exists():
                        idx = 1
                        while (user_folder / f"{dst.stem}_{idx}{dst.suffix}").exists():
                            idx += 1
                        dst = user_folder / f"{dst.stem}_{idx}{dst.suffix}"
                        just_name = dst.name
                    src.rename(dst)

                user_files.setdefault(uid, [])
                if (just_name, ext[1:]) not in user_files[uid]:
                    user_files[uid].append((just_name, ext[1:]))
                c.execute(
                    "INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, upload_date) VALUES (?, ?, ?, ?)",
                    (uid, just_name, ext[1:], now_iso),
                )
                registered.append(just_name)

        user_files[uid] = [f for f in user_files.get(uid, []) if f[0] != fname]
        c.execute("DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (uid, fname))
        conn.commit()
        conn.close()
        zip_path.unlink(missing_ok=True)
        save_json_backup()

        reg_txt = (
            "\n".join([f"• <code>{html_lib.escape(x)}</code>" for x in registered[:15]])
            or "<i>কোনো .py/.js ফাইল নেই</i>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files"),
                    InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
                ]
            ]
        )
        await safe_edit_message(
            callback.message,
            f"✅ <b>ZIP আনজিপ সম্পন্ন!</b>\n\n📄 <b>ফাইল:</b> <code>{html_lib.escape(fname)}</code>\n"
            f"✅ <b>যুক্ত ফাইল ({len(registered)} টি):</b>\n{reg_txt}",
            reply_markup=kb,
            parse_mode="HTML",
        )
        await safe_answer_callback(callback, "✅ আনজিপ হয়েছে!", show_alert=True)
    except Exception as e:
        await safe_answer_callback(callback, f"❌ ZIP সমস্যা: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("delete_file:"))
async def callback_delete_file(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return

    fname = callback.data.split(":", 1)[1]
    await stop_running_script_by_key(f"{uid}_{fname}")
    (UPLOAD_BOTS_DIR / str(uid) / fname).unlink(missing_ok=True)

    if uid in user_files:
        user_files[uid] = [f for f in user_files.get(uid, []) if f[0] != fname]
    if uid in user_favorites and fname in user_favorites[uid]:
        user_favorites[uid].remove(fname)

    try:
        conn = db_connect()
        conn.execute("DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (uid, fname))
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND file_name = ?", (uid, fname))
        conn.commit()
        conn.close()
    except Exception:
        pass
    save_json_backup()

    await safe_answer_callback(callback, "🗑️ ডিলিট হয়েছে!", show_alert=True)
    text, kb = build_user_files_view(uid)
    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")


# ── OTHER USER BUTTONS (SPEED, SEARCH, STATS) ────────
@dp.callback_query(F.data == "bot_speed")
async def callback_bot_speed(callback: types.CallbackQuery):
    if not await ensure_access(callback):
        return
    t0 = datetime.now()
    await safe_answer_callback(callback, "⚡ স্পিড টেস্ট হচ্ছে...")
    ms = (datetime.now() - t0).total_seconds() * 1000
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   ⚡ <b>বট স্পিড ও সার্ভার স্ট্যাটাস</b> ⚡\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"🚀 <b>রেসপন্স পিং:</b> <b>{ms:.2f} ms</b>\n"
        f"💻 <b>CPU ব্যবহার:</b> <b>{psutil.cpu_percent()}%</b>\n"
        f"🧠 <b>RAM ব্যবহার:</b> <b>{psutil.virtual_memory().percent}%</b>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 আবার চেক করুন", callback_data="bot_speed"),
                InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main"),
            ]
        ]
    )
    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "search_files")
async def callback_search_files(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await ensure_access(callback):
        return
    files = user_files.get(uid, [])
    user_states[uid] = {"step": "user_search_file_query", "data": {}}
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   🔍 <b>ফাইল সার্চ সেন্টার</b> 🔍\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"📊 <b>আপনার মোট ফাইল:</b> <b>{len(files)} টি</b>\n"
        f"⌨️ <b>যে ফাইলটি খুঁজছেন তার নাম নিচে লিখে পাঠান</b> অথবা <code>/search ফাইলের_নাম</code> ব্যবহার করুন।\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦"
    )
    await safe_edit_message(callback.message, text, reply_markup=cancel_keyboard(), parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "statistics")
async def callback_statistics(callback: types.CallbackQuery):
    uid = callback.from_user.id
    await cleanup_finished_scripts()
    if not await ensure_access(callback):
        return
    pts_str = "আনলিমিটেড ♾️" if is_admin_user(uid) else f"{user_points.get(uid, 0)} পয়েন্ট"
    shown_total_users = get_displayed_total_users()
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📊 <b>মাই স্ট্যাটস ও পরিসংখ্যান</b> 📊\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"👥 <b>টোটাল ইউজার:</b> <b>{shown_total_users}</b>\n"
        f"🆔 <b>চ্যাট আইডি:</b> <code>{uid}</code>\n"
        f"💎 <b>পয়েন্ট:</b> <b>{pts_str}</b>\n"
        f"💰 <b>ফাইল পয়েন্ট রেট:</b> <b>{int(bot_settings.get('file_point', 5))} পয়েন্ট</b>\n"
        f"📁 <b>আপনার ফাইল:</b> <b>{len(user_files.get(uid, []))} টি</b>\n"
        f"▶️ <b>চলমান স্ক্রিপ্ট:</b> <b>{sum(1 for k in bot_scripts if k.startswith(f'{uid}_'))} টি</b>\n"
        f"📤 <b>মোট আপলোড:</b> <b>{bot_stats.get('total_uploads', 0)} টি</b>\n"
        f"▶️ <b>মোট রান:</b> <b>{bot_stats.get('total_runs', 0)} বার</b>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")]]
    )
    await safe_edit_message(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await safe_answer_callback(callback)


# ── ADMIN FINE CHECK (ফাইন চেক) ───────────────────────
def collect_all_uploaded_user_files() -> list[tuple[int, str, str]]:
    entries = []
    seen = set()
    for uid, flist in user_files.items():
        for fname, ftype in flist:
            key = (int(uid), str(fname))
            if key not in seen:
                seen.add(key)
                entries.append((int(uid), str(fname), str(ftype)))
    return entries


async def render_fine_check_view(callback: types.CallbackQuery, page: int = 0):
    entries = collect_all_uploaded_user_files()
    if not entries:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")]]
        )
        await safe_edit_message(
            callback.message,
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   📁 <b>ফাইন চেক</b> 📁\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"📭 বটে এখনো কোনো ইউজার ফাইল আপলোড করেনি!",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    per_page = 5
    max_page = max(0, (len(entries) - 1) // per_page)
    page = max(0, min(page, max_page))
    chunk = entries[page * per_page : (page + 1) * per_page]

    lines = [
        "✦━━━━━━━━━━━━━━━━━━━━━━━━✦",
        "   📁 <b>ফাইন চেক (ইউজার ফাইল)</b> 📁",
        "✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n",
    ]
    rows = []
    for pos_in_all, (target_uid, fname, ftype) in enumerate(chunk, start=page * per_page + 1):
        handle = format_user_mention_or_name(target_uid)
        lines.append(
            f"{pos_in_all}. 👤 <b>ইউজার:</b> <code>{html_lib.escape(handle)}</code> (<code>{target_uid}</code>)\n"
            f"    📄 <b>ফাইল:</b> <code>{html_lib.escape(fname)}</code> (<code>.{html_lib.escape(ftype)}</code>)"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑️ ডিলিট ({fname[:14]})",
                    callback_data=f"fc_del:{pos_in_all - 1}:{page}",
                )
            ]
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ আগের পাতা", callback_data=f"fc_page:{page - 1}"))
    if page < max_page:
        nav_row.append(InlineKeyboardButton(text="পরের পাতা ➡️", callback_data=f"fc_page:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")])

    text = "\n".join(lines)
    first_uid = chunk[0][0]
    photo_bytes = await get_user_profile_photo_bytes(first_uid)
    if photo_bytes and page == 0:
        try:
            await callback.message.answer_photo(
                photo=BufferedInputFile(photo_bytes, filename=f"user_{first_uid}.jpg"),
                caption=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await safe_edit_message(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "adm_fine_check")
async def callback_adm_fine_check(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    await cleanup_finished_scripts()
    await safe_answer_callback(callback)
    await render_fine_check_view(callback, page=0)


@dp.callback_query(F.data.startswith("fc_page:"))
async def callback_fc_page(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    try:
        page = int(callback.data.split(":", 1)[1])
    except Exception:
        page = 0
    await safe_answer_callback(callback)
    await render_fine_check_view(callback, page=page)


@dp.callback_query(F.data.startswith("fc_del:"))
async def callback_fc_del(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return

    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await safe_answer_callback(callback, "❌ ভুল ডেটা!", show_alert=True)
        return

    try:
        entry_idx = int(parts[1])
        page = int(parts[2])
    except Exception:
        await safe_answer_callback(callback, "❌ তথ্য পার্স করা যায়নি!", show_alert=True)
        return

    entries = collect_all_uploaded_user_files()
    if entry_idx < 0 or entry_idx >= len(entries):
        await safe_answer_callback(callback, "❌ ফাইলটি আগেই ডিলিট হয়েছে!", show_alert=True)
        await render_fine_check_view(callback, page=page)
        return

    target_uid, fname, _ = entries[entry_idx]

    await stop_running_script_by_key(f"{target_uid}_{fname}")
    (UPLOAD_BOTS_DIR / str(target_uid) / fname).unlink(missing_ok=True)

    if target_uid in user_files:
        user_files[target_uid] = [f for f in user_files.get(target_uid, []) if f[0] != fname]
    if target_uid in user_favorites and fname in user_favorites[target_uid]:
        user_favorites[target_uid].remove(fname)

    try:
        conn = db_connect()
        conn.execute("DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (target_uid, fname))
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND file_name = ?", (target_uid, fname))
        conn.commit()
        conn.close()
    except Exception:
        pass
    save_json_backup()

    await safe_answer_callback(callback, f"🗑️ '{fname}' ডিলিট হয়ে গেছে!", show_alert=True)
    await render_fine_check_view(callback, page=page)


# ── ADMIN PANEL & ADMIN BUTTONS (BENGALI) ────────────
@dp.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states.pop(uid, None)
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   👑 <b>অ্যাডমিন কন্ট্রোল প্যানেল</b> 👑\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"💎 <b>অ্যাডমিন সুবিধা:</b> আনলিমিটেড ♾️\n"
        f"👥 <b>মোট ইউজার:</b> <b>{get_displayed_total_users()}</b>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"👇 <i>নিচের যেকোনো বাটন চাপুন</i>"
    )
    await safe_edit_message(callback.message, text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_point_add")
async def callback_adm_point_add(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_point_amount", "data": {}}
    await safe_edit_message(
        callback.message,
        "💎 <b>পয়েন্ট যোগ করুন</b>\n\nআপনি <b>কত পয়েন্ট</b> যোগ করতে চান তা নিচে লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_limit_set")
async def callback_adm_limit_set(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_limit_minutes", "data": {}}
    await safe_edit_message(
        callback.message,
        "আপনি কতো মিনিট সেট করতে চান তলে লিখুন",
        reply_markup=cancel_keyboard(),
        parse_mode=None,
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_channel_set")
async def callback_adm_channel_set(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_channel_slot", "data": {}}
    await safe_edit_message(
        callback.message,
        "🔗 <b>চ্যানেল লিংক সেট</b>\n\nচ্যানেল স্লট নম্বর লিখুন (যেমন: <code>1</code>, <code>2</code>):",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_ban_user")
async def callback_adm_ban_user(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_ban_target", "data": {}}
    await safe_edit_message(
        callback.message,
        "🚫 <b>ব্যান ইউজার (২৪ ঘণ্টা)</b>\n\nযে ইউজারকে ব্যান করতে চান তার <b>ইউজারনেম (@user)</b> বা <b>চ্যাট আইডি</b> লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_unban_user")
async def callback_adm_unban_user(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_unban_target", "data": {}}
    await safe_edit_message(
        callback.message,
        "আপনি জাকে বট থেকে আনব্যান করতে চান তার ইউজার নেম লিখুন",
        reply_markup=cancel_keyboard(),
        parse_mode=None,
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_msg_member")
async def callback_adm_msg_member(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_broadcast_text", "data": {}}
    await safe_edit_message(
        callback.message,
        "📢 <b>মেম্বার মেসেজ</b>\n\nসব ইউজারকে পাঠানোর জন্য <b>মেসেজটি</b> নিচে লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_redeem_code")
async def callback_adm_redeem_code(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_redeem_points", "data": {}}
    await safe_edit_message(
        callback.message,
        "🎟️ <b>রিডিম কোড তৈরি</b>\n\nএই ৬ ডিজিটের কোডে <b>কত পয়েন্ট</b> দেবেন তা নিচে লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_refer_bonus")
async def callback_adm_refer_bonus(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_refer_bonus_val", "data": {}}
    await safe_edit_message(
        callback.message,
        f"🤝 <b>রেফার বোনাস সেট</b>\n\nবর্তমান রেফার বোনাস: <b>{bot_settings.get('refer_bonus', 10)} পয়েন্ট</b>\n\n"
        f"প্রতি রেফারে <b>কত পয়েন্ট</b> দেবেন তা নিচে লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "adm_file_point")
async def callback_adm_file_point(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_file_point_val", "data": {}}
    await safe_edit_message(
        callback.message,
        f"💰 <b>ফাইল পয়েন্ট সেট</b>\n\nবর্তমান ফাইল পয়েন্ট: <b>{bot_settings.get('file_point', 5)} পয়েন্ট</b>\n\n"
        f"ফাইলের জন্য <b>কত পয়েন্ট</b> সেট করতে চান তা নিচে লিখুন:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


# ── ALL FILES STAGED MANAGER (অল ফাইল) ───────────────
def build_all_files_keyboard():
    files_list = list(bot_settings.get("all_files_list") or PROJECT_SIX_FILES)
    rows = []
    for idx, fname in enumerate(files_list):
        tag = " (ডিলিটেড)" if fname in staged_deletions else (" (নতুন)" if fname in staged_additions else "")
        rows.append(
            [
                InlineKeyboardButton(text=f"📄 {fname[:16]}{tag}", callback_data=f"af_info:{idx}"),
                InlineKeyboardButton(text="✏️ এডিট", callback_data=f"af_edit:{idx}"),
                InlineKeyboardButton(text="🗑️ ডিলিট", callback_data=f"af_del:{idx}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ অ্যাড ফাইল", callback_data="af_add")])
    rows.append([InlineKeyboardButton(text="▶️ স্টার্ট", callback_data="af_start_apply")])
    rows.append([InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_all_files_text() -> str:
    files_list = list(bot_settings.get("all_files_list") or PROJECT_SIX_FILES)
    lines = []
    for i, fname in enumerate(files_list, 1):
        status = ""
        if fname in staged_deletions:
            status = " — <i>ডিলিট হবে (স্টার্ট চাপলে)</i>"
        elif fname in staged_additions:
            status = " — <i>নতুন যুক্ত হবে (স্টার্ট চাপলে)</i>"
        elif (STAGING_FILES_DIR / fname).exists():
            status = " — <i>প্রস্তুত</i>"
        lines.append(f"{i}. <code>{html_lib.escape(fname)}</code>{status}")

    body = "\n".join(lines) if lines else "<i>কোনো ফাইল নেই</i>"
    return (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📂 <b>অল ফাইল ম্যানেজার</b> 📂\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"{body}\n\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"ℹ️ <b>▶️ স্টার্ট</b> বাটনে না চাপা পর্যন্ত কোনো পরিবর্তন লাইভে বদলাবে না।\n"
        f"<b>▶️ স্টার্ট</b> চাপলেই এডিট/ডিলিট/অ্যাড কার্যকর হবে।"
    )


@dp.callback_query(F.data == "adm_all_files")
async def callback_adm_all_files(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states.pop(uid, None)
    init_staged_files()
    await safe_edit_message(
        callback.message,
        build_all_files_text(),
        reply_markup=build_all_files_keyboard(),
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data.startswith("af_info:"))
async def callback_af_info(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ অ্যাডমিন প্রয়োজন!", show_alert=True)
        return
    try:
        idx = int(callback.data.split(":", 1)[1])
        fname = (bot_settings.get("all_files_list") or [])[idx]
    except Exception:
        await safe_answer_callback(callback, "❌ ফাইল পাওয়া যায়নি!", show_alert=True)
        return
    await safe_answer_callback(callback, f"📄 ফাইল: {fname}", show_alert=True)


@dp.callback_query(F.data.startswith("af_edit:"))
async def callback_af_edit(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    try:
        idx = int(callback.data.split(":", 1)[1])
        fname = (bot_settings.get("all_files_list") or [])[idx]
    except Exception:
        await safe_answer_callback(callback, "❌ ফাইল পাওয়া যায়নি!", show_alert=True)
        return

    staged_p = STAGING_FILES_DIR / fname
    live_p = BASE_DIR / fname
    if not staged_p.exists() and live_p.exists() and live_p.is_file():
        try:
            shutil.copy2(live_p, staged_p)
        except Exception:
            pass

    user_states[uid] = {"step": "adm_all_files_edit", "data": {"target_file": fname}}
    await safe_edit_message(
        callback.message,
        "ফাইল আপলোড করুন",
        reply_markup=cancel_keyboard(),
        parse_mode=None,
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data.startswith("af_del:"))
async def callback_af_del(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    try:
        idx = int(callback.data.split(":", 1)[1])
        fname = (bot_settings.get("all_files_list") or [])[idx]
    except Exception:
        await safe_answer_callback(callback, "❌ ফাইল পাওয়া যায়নি!", show_alert=True)
        return

    staged_deletions.add(fname)
    staged_additions.discard(fname)
    await safe_answer_callback(callback, f"🗑️ '{fname}' ডিলিটের তালিকায় রাখা হয়েছে (স্টার্ট চাপলে কার্যকর হবে)", show_alert=True)
    await safe_edit_message(
        callback.message,
        build_all_files_text(),
        reply_markup=build_all_files_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "af_add")
async def callback_af_add(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    user_states[uid] = {"step": "adm_all_files_add", "data": {}}
    await safe_edit_message(
        callback.message,
        "আপনি যে ফাইল এড করতে চাত তা তলে সেই ফাইল আপলোড করুন",
        reply_markup=cancel_keyboard(),
        parse_mode=None,
    )
    await safe_answer_callback(callback)


async def _clean_shutdown_before_restart():
    for key in list(bot_scripts.keys()):
        try:
            await stop_running_script_by_key(key)
        except Exception:
            pass
    global _WEB_RUNNER
    if _WEB_RUNNER is not None:
        try:
            await _WEB_RUNNER.cleanup()
        except Exception:
            pass
        _WEB_RUNNER = None
    try:
        await bot.session.close()
    except Exception:
        pass


async def _restart_self_process_delayed():
    await asyncio.sleep(1.2)
    logger.info("♻️ Restarting app.py after apply...")
    try:
        await _clean_shutdown_before_restart()
    except Exception:
        pass
    try:
        os.chdir(str(BASE_DIR))
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, str(BASE_DIR / "app.py")])


@dp.callback_query(F.data == "af_start_apply")
async def callback_af_start_apply(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return

    save_json_backup()
    applied_count = 0
    app_updated = False
    current_list = list(bot_settings.get("all_files_list") or [])

    for fname in list(staged_deletions):
        if fname in current_list:
            current_list.remove(fname)
        (STAGING_FILES_DIR / fname).unlink(missing_ok=True)
        live_target = BASE_DIR / fname
        if live_target.exists() and live_target.is_file() and fname != "app.py":
            try:
                live_target.unlink()
            except Exception:
                pass
        applied_count += 1

    for fname in current_list:
        staged_p = STAGING_FILES_DIR / fname
        if staged_p.exists() and staged_p.is_file():
            try:
                shutil.copy2(staged_p, BASE_DIR / fname)
                applied_count += 1
                if fname == "app.py":
                    app_updated = True
            except Exception as e:
                logger.warning(f"Apply staged file error ({fname}): {e}")

    bot_settings["all_files_list"] = current_list
    save_all_files_list_to_db()
    staged_deletions.clear()
    staged_additions.clear()

    extra_note = "\n♻️ <b>app.py আপডেট হয়েছে — বট এখন নতুন কোডে রিস্টার্ট নিচ্ছে...</b>" if app_updated else ""
    await safe_answer_callback(callback, "✅ সাক্সেস! সব পরিবর্তন সেট হয়েছে।", show_alert=True)
    await safe_edit_message(
        callback.message,
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   ✅ <b>সাক্সেস! পরিবর্তন কার্যকর হয়েছে</b>\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"⚙️ <b>কার্যকর ফাইল:</b> <b>{applied_count} টি</b>\n"
        f"📁 <b>বর্তমান ফাইল সংখ্যা:</b> <b>{len(current_list)} টি</b>{extra_note}",
        reply_markup=build_all_files_keyboard(),
        parse_mode="HTML",
    )

    if app_updated:
        asyncio.create_task(_restart_self_process_delayed())


@dp.callback_query(F.data == "admin_channel_delete_menu")
async def callback_admin_channel_delete_menu(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ সব ডিলিট করুন", callback_data="admin_channel_delete_all")],
            [InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")],
        ]
    )
    await safe_edit_message(
        callback.message,
        "🗑️ <b>চ্যানেল ডিলিট</b>\n\nসব সেট করা চ্যানেল মুছে ফেলতে <b>সব ডিলিট করুন</b> বাটনে চাপুন।",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "admin_channel_delete_all")
async def callback_admin_channel_delete_all(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin_user(uid):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    bot_settings["channels"] = {}
    save_channels_to_db()
    await safe_answer_callback(callback, "✅ সব চ্যানেল ডিলিট হয়েছে!", show_alert=True)
    await safe_edit_message(
        callback.message,
        "✅ <b>সব চ্যানেল ডিলিট করা হয়েছে!</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")]]
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_bot_uptime")
async def callback_admin_bot_uptime(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    delta = datetime.now() - BOT_START_TIME
    d = delta.days
    h, rem = divmod(delta.seconds, 3600)
    m, s = divmod(rem, 60)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")]])
    await safe_edit_message(
        callback.message,
        f"⏱️ <b>বট আপটাইম</b>\n\n⏳ <code>{d}d {h}h {m}m {s}s</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "admin_total_users_simple")
async def callback_admin_total_users_simple(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    await cleanup_finished_scripts()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")]])
    await safe_edit_message(
        callback.message,
        f"👥 <b>মোট ইউজার</b>\n\n👤 ইউজার: <b>{get_displayed_total_users()}</b>\n▶️ চলছে: <b>{len(bot_scripts)}</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


@dp.callback_query(F.data == "admin_view_logs")
async def callback_admin_view_logs(callback: types.CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await safe_answer_callback(callback, "❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)
        return
    await cleanup_finished_scripts()
    running_list = [
        f"• <code>{html_lib.escape(v.get('file_name', k))}</code> (PID: <code>{v['process'].pid}</code>)"
        for k, v in bot_scripts.items()
        if v.get("process") and v["process"].poll() is None
    ]
    running_txt = "\n".join(running_list) if running_list else "<i>বর্তমানে কোনো ইউজার স্ক্রিপ্ট চলছে না</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 অ্যাডমিন প্যানেল", callback_data="admin_panel")]])
    await safe_edit_message(
        callback.message,
        f"📝 <b>সিস্টেম লগস</b>\n\n"
        f"✅ বট ও ওয়েব সার্ভার সচল রয়েছে।\n"
        f"👥 প্রকৃত ইউজার: <b>{len(active_users)}</b> | ব্যানড: <b>{len(banned_users)}</b>\n"
        f"▶️ চলমান স্ক্রিপ্ট ({len(running_list)}):\n{running_txt}",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await safe_answer_callback(callback)


# ── COMMANDS ─────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await record_active_user(message.from_user)
    if not await ensure_access(message):
        return
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   ℹ️ <b>হেল্প ও কমান্ড গাইড</b> ℹ️\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"• /start — বট চালু করুন\n"
        f"• /help — সাহায্য দেখুন\n"
        f"• /stats — আপনার স্ট্যাটস\n"
        f"• /search &lt;নাম&gt; — ফাইল খুঁজুন\n\n"
        f"<b>অ্যাডমিন কমান্ড:</b>\n"
        f"• /addadmin &lt;ID&gt; • /removeadmin &lt;ID&gt;\n"
        f"• /ban &lt;ID&gt; [কারণ] • /unban &lt;ID&gt;\n"
        f"• /broadcast &lt;মেসেজ&gt;"
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    uid = message.from_user.id
    await record_active_user(message.from_user)
    await cleanup_finished_scripts()
    if not await ensure_access(message):
        return
    pts_str = "আনলিমিটেড ♾️" if is_admin_user(uid) else f"{user_points.get(uid, 0)} পয়েন্ট"
    text = (
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
        f"   📊 <b>মাই স্ট্যাটস</b> 📊\n"
        f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
        f"👥 <b>টোটাল ইউজার:</b> <b>{get_displayed_total_users()}</b>\n"
        f"🆔 <b>চ্যাট আইডি:</b> <code>{uid}</code>\n"
        f"💎 <b>পয়েন্ট:</b> <b>{pts_str}</b>\n"
        f"💰 <b>ফাইল পয়েন্ট রেট:</b> <b>{int(bot_settings.get('file_point', 5))} পয়েন্ট</b>\n"
        f"📁 <b>ফাইল:</b> <b>{len(user_files.get(uid, []))} টি</b>\n"
        f"▶️ <b>চলছে:</b> <b>{sum(1 for k in bot_scripts if k.startswith(f'{uid}_'))} টি</b>"
    )
    await message.answer(text, reply_markup=get_main_keyboard(uid), parse_mode="HTML")


@dp.message(Command("search"))
async def cmd_search_files(message: types.Message):
    uid = message.from_user.id
    if not await ensure_access(message):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("ব্যবহার: <code>/search ফাইলের_নাম</code>", parse_mode="HTML")
        return
    q = args[1].lower()
    matches = [f for f in user_files.get(uid, []) if q in f[0].lower()]
    if not matches:
        await message.answer(f"🔍 '<code>{html_lib.escape(q)}</code>' নামের কোনো ফাইল পাওয়া যায়নি", parse_mode="HTML")
        return
    txt = "🔍 <b>অনুসন্ধানের ফলাফল:</b>\n\n" + "\n".join([f"• <code>{html_lib.escape(x[0])}</code>" for x in matches])
    await message.answer(txt, parse_mode="HTML")


@dp.message(Command("addadmin"))
async def cmd_add_admin(message: types.Message):
    if not is_admin_user(message.from_user.id):
        await message.answer("❌ অনুমতি নেই!")
        return
    args = (message.text or "").split()
    if len(args) != 2 or not args[1].lstrip("-").isdigit():
        await message.answer("ব্যবহার: <code>/addadmin USER_ID</code>", parse_mode="HTML")
        return
    new_id = int(args[1])
    admin_ids.add(new_id)
    conn = db_connect()
    conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_id,))
    conn.commit()
    conn.close()
    save_json_backup()
    await message.answer(f"✅ অ্যাডমিন যুক্ত করা হয়েছে: <code>{new_id}</code>", parse_mode="HTML")


@dp.message(Command("removeadmin"))
async def cmd_remove_admin(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ শুধুমাত্র ওনার অ্যাডমিন সরাতে পারবেন!")
        return
    args = (message.text or "").split()
    if len(args) != 2 or not args[1].lstrip("-").isdigit():
        await message.answer("ব্যবহার: <code>/removeadmin USER_ID</code>", parse_mode="HTML")
        return
    rem_id = int(args[1])
    if rem_id == OWNER_ID:
        await message.answer("❌ ওনারকে সরানো যাবে না!")
        return
    admin_ids.discard(rem_id)
    conn = db_connect()
    conn.execute("DELETE FROM admins WHERE user_id = ?", (rem_id,))
    conn.commit()
    conn.close()
    save_json_backup()
    await message.answer(f"✅ অ্যাডমিন সরানো হয়েছে: <code>{rem_id}</code>", parse_mode="HTML")


@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if not is_admin_user(message.from_user.id):
        await message.answer("❌ অনুমতি নেই!")
        return
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 2:
        await message.answer("ব্যবহার: <code>/ban USER_ID [কারণ]</code>", parse_mode="HTML")
        return
    target_uid = find_user_by_identifier(args[1])
    if target_uid is None or is_admin_user(target_uid):
        await message.answer("❌ ভুল ইউজার বা অ্যাডমিনকে ব্যান করা যাবে না!")
        return
    reason = args[2] if len(args) > 2 else "Banned by admin (24h)"
    now = datetime.now()
    until_dt = now + timedelta(hours=24)
    banned_users[target_uid] = {"until": until_dt, "reason": reason}
    for k in list(bot_scripts.keys()):
        if k.startswith(f"{target_uid}_"):
            await stop_running_script_by_key(k)
    conn = db_connect()
    conn.execute(
        "INSERT OR REPLACE INTO banned_users (user_id, banned_date, reason, ban_until) VALUES (?, ?, ?, ?)",
        (target_uid, now.isoformat(), reason, until_dt.isoformat()),
    )
    conn.commit()
    conn.close()
    save_json_backup()
    await message.answer(f"🚫 <code>{target_uid}</code> ২৪ ঘণ্টার জন্য ব্যান করা হয়েছে।", parse_mode="HTML")


@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if not is_admin_user(message.from_user.id):
        await message.answer("❌ অনুমতি নেই!")
        return
    args = (message.text or "").split()
    if len(args) != 2:
        await message.answer("ব্যবহার: <code>/unban USER_ID</code>", parse_mode="HTML")
        return
    target_uid = find_user_by_identifier(args[1])
    if target_uid is None:
        await message.answer("❌ ভুল ইউজার!")
        return
    banned_users.pop(target_uid, None)
    conn = db_connect()
    conn.execute("DELETE FROM banned_users WHERE user_id = ?", (target_uid,))
    conn.commit()
    conn.close()
    save_json_backup()
    await message.answer(f"✅ সাক্সেস! <code>{target_uid}</code> আনব্যান করা হয়েছে।", parse_mode="HTML")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not is_admin_user(message.from_user.id):
        await message.answer("❌ অনুমতি নেই!")
        return
    txt = (message.text or "").replace("/broadcast", "", 1).strip()
    if not txt:
        await message.answer("ব্যবহার: <code>/broadcast মেসেজ</code>", parse_mode="HTML")
        return
    sent, failed = 0, 0
    for uid in list(active_users):
        if uid in banned_users:
            continue
        try:
            await bot.send_message(uid, f"📢 <b>ঘোষণা</b>\n\n{html_lib.escape(txt)}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await message.answer(f"✅ ব্রডকাস্ট সম্পন্ন! সফল: <b>{sent}</b>, ব্যর্থ: <b>{failed}</b>", parse_mode="HTML")


# ── STATEFUL TEXT HANDLER ───────────────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_states(message: types.Message):
    user = message.from_user
    uid = user.id
    await record_active_user(user)

    state = user_states.get(uid)
    if not state:
        return

    step = state.get("step")
    data = state.get("data", {})
    txt = (message.text or "").strip()

    if step == "user_search_file_query":
        user_states.pop(uid, None)
        if not txt:
            await message.answer("❌ সার্চ করার জন্য ফাইলের নাম লিখুন।")
            return
        q = txt.lower()
        matches = [f for f in user_files.get(uid, []) if q in f[0].lower()]
        if not matches:
            await message.answer(
                f"🔍 '<code>{html_lib.escape(txt)}</code>' নামের কোনো ফাইল পাওয়া যায়নি।",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🏠 মেইন মেনু", callback_data="back_to_main")]]
                ),
                parse_mode="HTML",
            )
            return
        res_lines = "\n".join([f"• <code>{html_lib.escape(fn)}</code> (<code>.{html_lib.escape(ft)}</code>)" for fn, ft in matches])
        await message.answer(
            f"🔍 <b>অনুসন্ধানের ফলাফল ({len(matches)} টি):</b>\n\n{res_lines}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="📁 আমার ফাইল", callback_data="check_files")]]
            ),
            parse_mode="HTML",
        )
        return

    if step == "await_redeem_input":
        user_states.pop(uid, None)
        if not (txt.isdigit() and len(txt) == 6):
            await message.answer(
                "❌ <b>ভুল কোড!</b>\n\nঅনুগ্রহ করে সঠিক <b>৬ ডিজিটের</b> রিডিম কোড লিখুন।",
                parse_mode="HTML",
            )
            return

        loading_msg = await message.answer("⏳ <b>রিডিম কোড যাচাই করা হচ্ছে...</b>", parse_mode="HTML")
        await asyncio.sleep(0.2)

        today = get_today_str()
        rc_info = bot_settings.get("redeem_codes", {}).get(txt)
        if not rc_info or rc_info.get("date") != today:
            await safe_edit_message(
                loading_msg,
                "❌ <b>কোডটি ভুল অথবা মেয়াদ শেষ হয়ে গেছে!</b>\n\nরাত <b>১২:০০টার পর</b> কোড বাতিল হয়ে যায়।",
                parse_mode="HTML",
            )
            return

        used_set = rc_info.get("used_by", set())
        if uid in used_set:
            await safe_edit_message(
                loading_msg,
                "⚠️ <b>ইতিমধ্যে ব্যবহৃত!</b>\n\nআপনি আজ এই রিডিম কোডটি আগেই ব্যবহার করেছেন।",
                parse_mode="HTML",
            )
            return

        pts = int(rc_info.get("points", 0))
        used_set.add(uid)
        save_redeem_codes_to_db()
        new_total = add_user_points(uid, pts)

        await safe_edit_message(
            loading_msg,
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   🎉 <b>বোনাস সফলভাবে যুক্ত হয়েছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"💎 <b>প্রাপ্ত পয়েন্ট:</b> <b>+{pts} পয়েন্ট</b>\n"
            f"💰 <b>মোট পয়েন্ট:</b> <b>{new_total} পয়েন্ট</b>",
            parse_mode="HTML",
        )
        return

    if not is_admin_user(uid):
        user_states.pop(uid, None)
        return

    if step == "adm_limit_minutes":
        try:
            mins = int(txt)
            if mins <= 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ অনুগ্রহ করে সঠিক মিনিট (১ বা তার বেশি সংখ্যা) লিখুন!", reply_markup=cancel_keyboard())
            return
        user_states[uid] = {"step": "adm_limit_points", "data": {"minutes": mins}}
        await message.answer(
            "আপনি এই কই মিনিট এর জন্য কতো পয়েন্ট সেট করতে চান তলে লিখুন",
            reply_markup=cancel_keyboard(),
        )
        return

    if step == "adm_limit_points":
        mins = int(data.get("minutes", 10))
        user_states.pop(uid, None)
        try:
            pts = int(txt)
            if pts < 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ অনুগ্রহ করে সঠিক পয়েন্ট সংখ্যা লিখুন!")
            return
        bot_settings["limit_minutes"] = mins
        bot_settings["limit_points"] = pts
        save_config_key("limit_minutes", str(mins))
        save_config_key("limit_points", str(pts))
        await message.answer(
            f"✅ <b>সাক্সেস!</b>\n\n"
            f"⏱️ <b>সময়:</b> প্রতি <b>{mins} মিনিট</b>\n"
            f"💎 <b>পয়েন্ট:</b> <b>{pts} পয়েন্ট</b>",
            parse_mode="HTML",
        )
        return

    if step == "adm_unban_target":
        user_states.pop(uid, None)
        target_uid = find_user_by_identifier(txt)
        if target_uid is None:
            await message.answer("❌ এই ইউজারনেম বা আইডি দিয়ে কোনো ইউজার পাওয়া যায়নি!")
            return
        banned_users.pop(target_uid, None)
        try:
            conn = db_connect()
            conn.execute("DELETE FROM banned_users WHERE user_id = ?", (target_uid,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        save_json_backup()
        await message.answer(
            f"✅ <b>সাক্সেস!</b>\n\n🔓 ইউজার <code>{html_lib.escape(txt)}</code> (<code>{target_uid}</code>) বট থেকে আনব্যান হয়েছে।",
            parse_mode="HTML",
        )
        try:
            await bot.send_message(
                target_uid,
                "✅ <b>সাক্সেস!</b>\n\n🎉 আপনাকে বট থেকে আনব্যান করা হয়েছে। এখন আপনি বট ব্যবহার করতে পারবেন!",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if step == "adm_point_amount":
        try:
            amt = int(txt)
            if amt == 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ সঠিক সংখ্যা লিখুন!", reply_markup=cancel_keyboard())
            return
        user_states[uid] = {"step": "adm_point_target", "data": {"amount": amt}}
        await message.answer(
            "👤 ইউজারের <b>ইউজারনেম (@user)</b> বা <b>চ্যাট আইডি</b> লিখুন:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    if step == "adm_point_target":
        amt = int(data.get("amount", 0))
        user_states.pop(uid, None)
        target_uid = find_user_by_identifier(txt)
        if target_uid is None:
            await message.answer("❌ ইউজার পাওয়া যায়নি! সঠিক চ্যাট আইডি বা @username দিন।")
            return
        new_pts = add_user_points(target_uid, amt)
        await message.answer(
            f"✅ <b>সাক্সেস!</b>\n\n"
            f"👤 <b>ইউজার:</b> <code>{target_uid}</code>\n"
            f"💎 <b>যোগ হয়েছে:</b> <b>{amt} পয়েন্ট</b>\n"
            f"💰 <b>নতুন ব্যালেন্স:</b> <b>{new_pts} পয়েন্ট</b>",
            parse_mode="HTML",
        )
        try:
            await bot.send_message(
                target_uid,
                f"💎 <b>পয়েন্ট পেয়েছেন!</b>\n\n"
                f"✨ অ্যাডমিন আপনার অ্যাকাউন্টে <b>+{amt} পয়েন্ট</b> যোগ করেছেন!\n"
                f"💰 <b>বর্তমান ব্যালেন্স:</b> <b>{new_pts} পয়েন্ট</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if step == "adm_channel_slot":
        try:
            slot = int(txt)
            if slot <= 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ সঠিক স্লট নম্বর লিখুন (যেমন: <code>1</code>)!", reply_markup=cancel_keyboard(), parse_mode="HTML")
            return
        user_states[uid] = {"step": "adm_channel_url", "data": {"slot": slot}}
        await message.answer(
            f"🔗 চ্যানেল <b>#{slot}</b> এর <b>লিংক</b> পাঠান:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    if step == "adm_channel_url":
        slot = int(data.get("slot", 1))
        user_states.pop(uid, None)
        if not (txt.startswith("http://") or txt.startswith("https://") or txt.startswith("@")):
            await message.answer("❌ সঠিক চ্যানেল লিংক দিন (যেমন: <code>https://t.me/channel</code>)", parse_mode="HTML")
            return
        full_url = txt if txt.startswith("http") else f"https://t.me/{txt.lstrip('@')}"
        bot_settings["channels"][slot] = {
            "url": full_url,
            "target": extract_chat_target(full_url),
        }
        save_channels_to_db()
        await message.answer(
            f"✅ <b>সাক্সেস!</b>\n\n🔹 <b>চ্যানেল #{slot}</b> সেট হয়েছে:\n{html_lib.escape(full_url)}",
            parse_mode="HTML",
        )
        return

    if step == "adm_ban_target":
        user_states.pop(uid, None)
        target_uid = find_user_by_identifier(txt)
        if target_uid is None:
            await message.answer("❌ ইউজার পাওয়া যায়নি! সঠিক চ্যাট আইডি বা @username দিন।")
            return
        if is_admin_user(target_uid):
            await message.answer("❌ অ্যাডমিনকে ব্যান করা যাবে না!")
            return
        now = datetime.now()
        until_dt = now + timedelta(hours=24)
        banned_users[target_uid] = {"until": until_dt, "reason": "Banned by admin (24h)"}
        stopped_cnt = 0
        for key in list(bot_scripts.keys()):
            if key.startswith(f"{target_uid}_"):
                if await stop_running_script_by_key(key):
                    stopped_cnt += 1
        try:
            conn = db_connect()
            conn.execute(
                "INSERT OR REPLACE INTO banned_users (user_id, banned_date, reason, ban_until) VALUES (?, ?, ?, ?)",
                (target_uid, now.isoformat(), "Banned by admin (24h)", until_dt.isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ban save error: {e}")
        save_json_backup()

        await message.answer(
            f"✅ <b>সাক্সেস!</b>\n\n"
            f"🚫 <b>ব্যান করা ইউজার:</b> <code>{target_uid}</code>\n"
            f"🛑 <b>বন্ধ হওয়া স্ক্রিপ্ট:</b> {stopped_cnt} টি\n"
            f"⏳ <b>মেয়াদ:</b> ২৪ ঘণ্টা",
            parse_mode="HTML",
        )
        try:
            await bot.send_message(
                target_uid,
                f"🚫 <b>অ্যাকাউন্ট ব্যান করা হয়েছে!</b>\n\n"
                f"আপনার চলমান স্ক্রিপ্ট বন্ধ করা হয়েছে এবং ২৪ ঘণ্টার জন্য আপলোড বন্ধ রাখা হয়েছে।\n"
                f"⏳ <b>আনব্যান সময়:</b> {until_dt.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if step == "adm_broadcast_text":
        user_states.pop(uid, None)
        if not txt:
            await message.answer("❌ মেসেজ খালি রাখা যাবে না!")
            return
        status = await message.answer(f"📢 {len(active_users)} জন ইউজারকে মেসেজ পাঠানো হচ্ছে...")
        sent, failed = 0, 0
        for rec_id in list(active_users):
            if rec_id in banned_users:
                continue
            try:
                await bot.send_message(rec_id, f"📢 <b>অ্যাডমিন মেসেজ</b>\n\n{html_lib.escape(txt)}", parse_mode="HTML")
                sent += 1
                await asyncio.sleep(0.03)
            except Exception:
                failed += 1
        await safe_edit_message(
            status,
            f"✅ <b>সাক্সেস!</b>\n\n📤 পাঠানো হয়েছে: <b>{sent}</b> | ❌ ব্যর্থ: <b>{failed}</b>",
            parse_mode="HTML",
        )
        return

    if step == "adm_redeem_points":
        user_states.pop(uid, None)
        try:
            pts = int(txt)
            if pts <= 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ সঠিক পয়েন্ট সংখ্যা লিখুন!")
            return
        existing = set(bot_settings.get("redeem_codes", {}).keys())
        code = None
        for idx in range(100):
            cand = hashlib.sha256(f"{uid}:{datetime.now().timestamp()}:{idx}:{len(existing)}".encode()).hexdigest()
            digits = "".join(ch for ch in cand if ch.isdigit())
            if len(digits) >= 6:
                c6 = digits[:6]
                if c6 != "000000" and c6 not in existing:
                    code = c6
                    break
        if not code:
            code = f"{(abs(hash(str(datetime.now()))) % 899999) + 100000:06d}"

        bot_settings["redeem_codes"][code] = {
            "points": pts,
            "date": get_today_str(),
            "used_by": set(),
        }
        save_redeem_codes_to_db()
        await message.answer(
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
            f"   🎟️ <b>নতুন রিডিম কোড তৈরি হয়েছে!</b>\n"
            f"✦━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
            f"🔑 <b>৬ ডিজিটের কোড:</b> <code>{code}</code>\n"
            f"💎 <b>বোনাস পয়েন্ট:</b> <b>{pts} পয়েন্ট</b>\n"
            f"⏰ <b>মেয়াদ শেষ:</b> আজ রাত <b>১২:০০টায়</b>",
            parse_mode="HTML",
        )
        return

    if step == "adm_refer_bonus_val":
        user_states.pop(uid, None)
        try:
            val = int(txt)
            if val < 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ সঠিক সংখ্যা লিখুন!")
            return
        bot_settings["refer_bonus"] = val
        save_config_key("refer_bonus", str(val))
        await message.answer(f"✅ <b>সাক্সেস!</b>\n\n🤝 <b>রেফার বোনাস</b> সেট হয়েছে <b>{val} পয়েন্ট</b>।", parse_mode="HTML")
        return

    if step == "adm_file_point_val":
        user_states.pop(uid, None)
        try:
            val = int(txt)
            if val < 0:
                raise ValueError()
        except ValueError:
            await message.answer("❌ সঠিক সংখ্যা লিখুন!")
            return
        bot_settings["file_point"] = val
        save_config_key("file_point", str(val))
        await message.answer(f"✅ <b>সাক্সেস!</b>\n\n💰 <b>ফাইল পয়েন্ট</b> সেট হয়েছে <b>{val} পয়েন্ট</b>।", parse_mode="HTML")
        return


# ── WEB SERVER & ADMIN DASHBOARD ─────────────────────
def _auth_ok(request):
    if not ADMIN_PASSWORD:
        return True
    h = request.headers.get("Authorization", "")
    if not h.startswith("Basic "):
        return False
    try:
        pwd = base64.b64decode(h[6:]).decode("utf-8").partition(":")[2]
    except Exception:
        return False
    return hmac.compare_digest(pwd.encode(), ADMIN_PASSWORD.encode())


def _deny():
    if not ADMIN_PASSWORD:
        return web.Response(text="Panel disabled. Set ADMIN_PASSWORD.", status=503)
    return web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="Admin"'},
        text="Login required",
    )


async def landing_page(request):
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        try:
            _BOT_USERNAME = (await bot.get_me()).username or ""
        except Exception:
            _BOT_USERNAME = ""
    bot_url = f"https://t.me/{_BOT_USERNAME}" if _BOT_USERNAME else UPDATE_CHANNEL
    html_page = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Genius HostBot</title><style>body{{font-family:system-ui,sans-serif;background:#070b12;color:#e6edf3;text-align:center;padding:60px 20px}}"
        f".btn{{display:inline-block;padding:12px 22px;margin:8px;border-radius:10px;text-decoration:none;font-weight:700;background:#1f6feb;color:#fff}}</style></head>"
        f"<body><h1>🚀 Genius HostBot</h1><p>24/7 Free Bot Hosting on Telegram</p>"
        f"<a class='btn' href='{html_lib.escape(bot_url)}'>Start Bot</a>"
        f"<a class='btn' href='{html_lib.escape(UPDATE_CHANNEL)}'>Updates Channel</a></body></html>"
    )
    return web.Response(text=html_page, content_type="text/html")


async def admin_page(request):
    if not _auth_ok(request):
        return _deny()
    return web.Response(
        text="<h2>Genius HostBot Admin</h2><p>Active users: " + str(get_displayed_total_users()) + "</p>",
        content_type="text/html",
    )


async def keep_alive():
    url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if not url:
        return
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url.rstrip("/") + "/health",
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    logger.info(f"Keep-alive ping: {r.status}")
        except Exception as e:
            logger.warning(f"Keep-alive failed: {e}")
        await asyncio.sleep(600)


async def web_server():
    global _WEB_RUNNER
    app = web.Application()

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", landing_page)
    app.router.add_get("/health", health)
    app.router.add_get("/admin", admin_page)

    runner = web.AppRunner(app)
    await runner.setup()
    _WEB_RUNNER = runner
    port = int(os.getenv("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Web server started on port {port}")


async def main():
    logger.info("🚀 Starting Genius HostBot...")
    asyncio.create_task(web_server())
    asyncio.create_task(keep_alive())
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
