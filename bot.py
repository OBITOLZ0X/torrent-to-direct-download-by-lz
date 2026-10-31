import os
import sys
import re
import time
import uuid
import html
import threading
from typing import Dict, Optional, Any

# Ensure UTF-8 output on Windows consoles
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from config import (
    BOT_TOKEN,
    ADMIN_ID,
    METADATA_DIR,
    PROGRESS_UPDATE_INTERVAL,
    FILES_PER_PAGE,
    ARIA2_PORT,
    CYAN,
    GREEN,
    YELLOW,
    RED,
    MAGENTA,
    WHITE,
    BOLD,
    DIM,
    RESET,
)
from utils import (
    human_readable_size,
    format_time,
    progress_bar,
    truncate_text,
)
from torrent_manager import Aria2Manager
from stream_manager import StreamManager
from database import db
from github_client import GitHubClient

SEP = "━━━━━━━━━━━━━━━━━━━━━"

GITHUB_HELP_TEXT = (
    f"🔑 <b>HOW TO GET A GITHUB TOKEN</b>\n{SEP}\n"
    f"<blockquote>1️⃣ Go to <b>github.com/settings/tokens</b>\n"
    f"2️⃣ Click <b>'Generate new token (classic)'</b>\n"
    f"3️⃣ Set Note (e.g. <code>TorrentBot</code>)\n"
    f"4️⃣ Select <b>repo</b> scope (Full control of repositories)\n"
    f"5️⃣ Click <b>Generate token</b> and copy it\n"
    f"6️⃣ Paste the token here!</blockquote>\n{SEP}\n"
    f"💡 <i>The bot will automatically create a private repository and keep database.json synced for free!</i>"
)

# Initialize Telegram bot and managers with crash protection
try:
    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
    aria2 = Aria2Manager()
    stream_mgr = StreamManager()
except Exception as init_err:
    print(f"\n{RED}[!] CRITICAL INITIALIZATION ERROR: {init_err}{RESET}")
    import traceback
    traceback.print_exc()
    print(f"\n{YELLOW}[!] The console window will remain OPEN so you can see this error.{RESET}")
    while True:
        try:
            input("\nPress Enter to exit...")
            break
        except Exception:
            time.sleep(5)
    sys.exit(1)

# In-memory storage for active sessions
sessions: Dict[str, dict] = {}
sessions_lock = threading.Lock()

# Admin pending state for reply-based input prompts: admin_id -> {"action": "...", "mid": int, "cid": int, "return_menu": "..."}
admin_pending: Dict[int, dict] = {}

def safe_edit_message(chat_id: int, message_id: int, text: str, reply_markup=None):
    """Safely edit message text handling rate limits and unmodified text."""
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            pass
        elif "flood" in str(e).lower() or "too many requests" in str(e).lower():
            time.sleep(2)
        else:
            print(f"[safe_edit_message] Telegram API error: {e}")
    except Exception as e:
        print(f"[safe_edit_message] Error: {e}")

def safe_edit_markup(chat_id: int, message_id: int, reply_markup):
    """Safely edit reply markup."""
    try:
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            print(f"[safe_edit_markup] Telegram API error: {e}")
    except Exception as e:
        print(f"[safe_edit_markup] Error: {e}")

def cleanup_torrent_file(filepath: Optional[str]):
    """Clean up downloaded .torrent metadata files immediately."""
    if not filepath:
        return
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
        aria_control = filepath + ".aria2"
        if os.path.exists(aria_control):
            os.remove(aria_control)
    except Exception as e:
        print(f"[Cleanup] Error removing {filepath}: {e}")

# ================= Registration & Verification Flow =================

def check_channel_membership(user_id: int) -> bool:
    """Verify if user is a member of the required channel if configured."""
    bot_channel = db.get_setting("bot_channel", "").strip()
    if not bot_channel:
        return True
    try:
        member = bot.get_chat_member(bot_channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        print(f"[ChannelVerify] Could not check membership: {e}")
        return True

def send_registration_prompt(chat_id: int, user_id: int, reply_to_id: int = None):
    """Send blockquote registration banner."""
    bot_channel = db.get_setting("bot_channel", "").strip()
    invite_link = db.get_setting("channel_invite", "").strip() or (f"https://t.me/{bot_channel.lstrip('@')}" if bot_channel else "")

    kb = types.InlineKeyboardMarkup(row_width=1)
    if invite_link:
        kb.add(types.InlineKeyboardButton("📢 Join Channel", url=invite_link))
    kb.add(types.InlineKeyboardButton("📝 Register Now", callback_data=f"reg:{user_id}"))

    text = (
        "🔐 <b>REGISTRATION REQUIRED</b>\n"
        f"{SEP}\n"
        "<blockquote>👋 Welcome! You must register before using this bot.\n\n"
        "1️⃣ Join our official channel (if required)\n"
        "2️⃣ Tap <b>Register Now</b> below</blockquote>\n"
        f"{SEP}"
    )
    if reply_to_id:
        bot.send_message(chat_id, text, reply_markup=kb, reply_to_message_id=reply_to_id)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

def check_user_access(message: types.Message) -> bool:
    """Validate user registration, ban status, and bot mode."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    allowed, reason = db.check_access(user_id, ADMIN_ID)
    if allowed:
        return True

    if reason == "REGISTRATION_REQUIRED":
        send_registration_prompt(chat_id, user_id, message.id)
        return False

    bot.reply_to(message, reason)
    return False

# ================= Selection Keyboard & Display =================

def build_file_selection_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Construct inline keyboard displaying torrent files with toggles."""
    session = sessions.get(session_id)
    if not session:
        return types.InlineKeyboardMarkup()

    files = session["files"]
    selected = session["selected"]
    page = session["page"]

    total_files = len(files)
    total_pages = (total_files + FILES_PER_PAGE - 1) // FILES_PER_PAGE
    start_idx = page * FILES_PER_PAGE
    end_idx = min(start_idx + FILES_PER_PAGE, total_files)

    kb = types.InlineKeyboardMarkup(row_width=1)

    for f in files[start_idx:end_idx]:
        idx = f["index"]
        is_sel = idx in selected
        icon = "✅" if is_sel else "❌"
        fname = truncate_text(f["name"], 26)
        sz = human_readable_size(f["length"])
        btn_text = f"{icon} {fname} ({sz})"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"tgl:{session_id}:{idx}"))

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️ Prev", callback_data=f"pg:{session_id}:{page - 1}"))
    nav_buttons.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data=f"noop:{session_id}"))
    if page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("Next ▶️", callback_data=f"pg:{session_id}:{page + 1}"))

    kb.row(*nav_buttons)

    # Bulk Selection buttons
    kb.row(
        types.InlineKeyboardButton("Select All", callback_data=f"all:{session_id}"),
        types.InlineKeyboardButton("Deselect All", callback_data=f"none:{session_id}")
    )

    # Start button
    kb.add(types.InlineKeyboardButton("🚀 Start Downloading", callback_data=f"prompt_dest:{session_id}"))
    return kb

def build_destination_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Construct keyboard allowing user to pick Buzzheavier or Gofile."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🐝 Buzzheavier", callback_data=f"start:{session_id}:buzzheavier"),
        types.InlineKeyboardButton("📁 Gofile.io", callback_data=f"start:{session_id}:gofile")
    )
    kb.add(types.InlineKeyboardButton("← Back to Selection", callback_data=f"back_select:{session_id}"))
    return kb

def build_stop_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Keyboard containing the Stop button during live streaming."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop:{session_id}"))
    return kb

def render_selection_message(session_id: str) -> str:
    """Render the files selection overview message."""
    session = sessions.get(session_id)
    if not session:
        return "Session expired."
    name = session["torrent_name"]
    total_size = human_readable_size(session["total_size"])
    file_count = len(session["files"])
    selected_count = len(session["selected"])

    return (
        f"🎬 <b>Torrent:</b> <code>{html.escape(name)}</code>\n"
        f"📦 <b>Total Size:</b> {total_size} | 📁 <b>Files:</b> {file_count}\n"
        f"🎯 <b>Selected:</b> {selected_count} files\n\n"
        f"<i>Select the files you want to download below.\n"
        f"Clicking any file toggles it on (✅) or off (❌).\n"
        f"When ready, click 'Start Downloading'.</i>"
    )

# ================= Direct Live Stream Pipeline =================

def run_stream_pipeline(session_id: str, destination: str):
    """
    Direct Stream Relay:
    Downloads torrent data in RAM and concurrently pipes it to Buzzheavier or Gofile via HTTP stream.
    Zero bytes are written to the RDP hard disk!
    """
    session = sessions.get(session_id)
    if not session:
        return

    chat_id = session["chat_id"]
    user_id = session.get("user_id", chat_id)
    user_name = session.get("user_name", "user")
    message_id = session["message_id"]
    torrent_path = session["torrent_path"]
    torrent_name = session["torrent_name"]
    selected_indices = sorted(list(session["selected"]))
    cancel_event = session["cancel_event"]

    dest_display = "Buzzheavier.com" if destination == "buzzheavier" else "Gofile.io"

    files_to_stream = [f for f in session["files"] if f["index"] in selected_indices]
    total_files = len(files_to_stream)
    uploaded_results = []

    last_ui_update = [0.0]
    stream_start_time = time.time()

    try:
        for step_num, file_info in enumerate(files_to_stream, 1):
            if cancel_event.is_set():
                break

            idx = file_info["index"]
            zero_idx = idx - 1  # 0-based index for webtorrent streamer
            fname = file_info["name"]
            fsize = file_info["length"]
            fsize_str = human_readable_size(fsize)

            def on_stream_progress(ev: dict):
                now = time.time()
                if now - last_ui_update[0] < PROGRESS_UPDATE_INTERVAL:
                    return
                last_ui_update[0] = now

                transferred = ev.get("transferred", 0)
                total = ev.get("total", fsize)
                pct = (transferred / total * 100.0) if total > 0 else 0.0
                speed = ev.get("speed", 0.0)
                eta = ev.get("eta", 0.0)
                peers = ev.get("peers", 0)

                text = (
                    f"📥 <b>Streaming to {dest_display}...</b>\n"
                    f"📁 <code>{html.escape(torrent_name)}</code>\n"
                    f"📄 <b>File ({step_num}/{total_files}):</b> <code>{html.escape(fname)}</code> ({fsize_str})\n\n"
                    f"<code>{progress_bar(pct)}</code>\n\n"
                    f"📊 <b>Progress:</b> {human_readable_size(transferred)} / {human_readable_size(total)}\n"
                    f"⚡ <b>Speed:</b> {human_readable_size(speed)}/s\n"
                    f"⏳ <b>ETA:</b> {format_time(eta)}\n"
                    f"👥 <b>Peers:</b> {peers}\n"
                    f"💾 <b>Storage:</b> 0 B (Direct RAM Relay)\n"
                    f"📦 <b>File:</b> {step_num} of {total_files}"
                )
                safe_edit_message(chat_id, message_id, text, reply_markup=build_stop_keyboard(session_id))

            # Initial message
            safe_edit_message(
                chat_id,
                message_id,
                f"📥 <b>Connecting to Swarm...</b>\n"
                f"📄 <b>File ({step_num}/{total_files}):</b> <code>{html.escape(fname)}</code> ({fsize_str})\n"
                f"⏳ <i>Streaming directly to {dest_display}...</i>",
                reply_markup=build_stop_keyboard(session_id),
            )

            ok, result = stream_mgr.stream_file(
                torrent_source=torrent_path,
                file_zero_index=zero_idx,
                destination=destination,
                progress_callback=on_stream_progress,
                cancel_event=cancel_event,
            )

            if cancel_event.is_set():
                break

            if not ok:
                error_msg = str(result)
                safe_edit_message(
                    chat_id,
                    message_id,
                    f"❌ <b>Stream Error for:</b> <code>{html.escape(fname)}</code>\n"
                    f"<b>Reason:</b> {html.escape(error_msg)}",
                    reply_markup=None,
                )
                # Dispatch notification to error channel if configured
                err_channel = db.get_setting("error_channel", "").strip()
                if err_channel:
                    try:
                        err_card = (
                            f"❌ <b>STREAM ERROR LOG</b>\n{SEP}\n"
                            f"<blockquote>📁 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                            f"👤 <b>User:</b> @{user_name} (<code>{user_id}</code>)\n"
                            f"⚠️ <b>Error:</b> <code>{html.escape(error_msg[:200])}</code>\n"
                            f"⏱️ <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC')}</code></blockquote>\n"
                            f"{SEP}"
                        )
                        bot.send_message(err_channel, err_card)
                    except Exception as ce:
                        print(f"[ErrorChannel] Failed to send: {ce}")
                return

            uploaded_results.append((fname, result, fsize_str, fsize))

            # Dispatch notification to results channel for each uploaded file
            res_channel = db.get_setting("results_channel", "").strip()
            if res_channel:
                try:
                    res_card = (
                        f"🚀 <b>TORRENT STREAM COMPLETED</b>\n{SEP}\n"
                        f"<blockquote>📁 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                        f"📦 <b>Size:</b> <code>{fsize_str}</code>\n"
                        f"🌐 <b>Host:</b> {dest_display}\n"
                        f"🔗 <b>Link:</b> <a href=\"{result}\">Download File</a>\n"
                        f"👤 <b>User:</b> @{user_name} (<code>{user_id}</code>)\n"
                        f"💾 <b>Relay:</b> Direct RAM Stream (0 B RDP Disk)</blockquote>\n"
                        f"{SEP}"
                    )
                    bot.send_message(res_channel, res_card, disable_web_page_preview=False)
                except Exception as ce:
                    print(f"[ResultsChannel] Failed to send: {ce}")

        # Record download usage in database
        if not cancel_event.is_set() and uploaded_results:
            db.record_download_success(user_id, ADMIN_ID)

        # Final rendering
        if cancel_event.is_set():
            cancel_text = (
                "🛑 <b>Download Stopped</b>\n\n"
                "The download and stream process was cancelled by user request."
            )
            safe_edit_message(chat_id, message_id, cancel_text, reply_markup=None)
        else:
            links_section = ""
            for i, (fn, url, sz, _) in enumerate(uploaded_results, 1):
                links_section += f"{i}. <a href=\"{url}\">{html.escape(fn)}</a> ({sz})\n"

            done_text = (
                f"🎉 <b>Download & Transfer Completed!</b>\n"
                f"📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                f"🌐 <b>Host:</b> {dest_display}\n\n"
                f"🔗 <b>Download Links:</b>\n"
                f"{links_section}\n"
                f"💾 <i>0 Bytes retained on RDP.</i>"
            )
            safe_edit_message(chat_id, message_id, done_text, reply_markup=None)

    finally:
        # Strictly clean up temporary torrent metadata file
        cleanup_torrent_file(torrent_path)
        with sessions_lock:
            sessions.pop(session_id, None)

# ================= Process Torrent & Magnet =================

def process_torrent_source(message: types.Message, magnet_link: str = None, torrent_bytes: bytes = None, filename: str = None):
    """Extract metadata and present the file selector."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.username or message.from_user.first_name or "user"
    status_msg = bot.reply_to(message, "🔍 <b>Fetching torrent metadata...</b> Please wait.")

    session_id = uuid.uuid4().hex[:8]
    torrent_filepath = None

    try:
        if torrent_bytes:
            fname = filename or f"{session_id}.torrent"
            torrent_filepath = os.path.join(METADATA_DIR, f"{session_id}_{fname}")
            with open(torrent_filepath, "wb") as f:
                f.write(torrent_bytes)
        elif magnet_link:
            ok, res = aria2.fetch_magnet_metadata(magnet_link, timeout=45)
            if not ok:
                safe_edit_message(
                    chat_id,
                    status_msg.message_id,
                    f"❌ <b>Failed to fetch torrent metadata:</b>\n{html.escape(res)}\n\n"
                    f"<i>Make sure the torrent has active seeders.</i>"
                )
                return
            torrent_filepath = res
        else:
            safe_edit_message(chat_id, status_msg.message_id, "❌ No torrent data provided.")
            return

        t_name, t_size, files = aria2.parse_torrent(torrent_filepath)
        if not files:
            safe_edit_message(chat_id, status_msg.message_id, "❌ No files found inside this torrent.")
            cleanup_torrent_file(torrent_filepath)
            return

        with sessions_lock:
            sessions[session_id] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "user_name": user_name,
                "message_id": status_msg.message_id,
                "torrent_name": t_name,
                "torrent_path": torrent_filepath,
                "total_size": t_size,
                "files": files,
                "selected": set(f["index"] for f in files),
                "page": 0,
                "cancel_event": threading.Event(),
                "status": "selecting",
            }

        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(chat_id, status_msg.message_id, text, reply_markup=markup)

    except Exception as e:
        cleanup_torrent_file(torrent_filepath)
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"❌ <b>Error processing torrent:</b> {html.escape(str(e))}"
        )

# ================= Admin Control Panel (Anya Bot Architecture) =================

def render_settings_main() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render main /settings control panel."""
    stats = db.get_stats()
    bot_mode = stats["bot_mode"]

    # Cloud DB status indicator
    if stats["git_token"]:
        gh = GitHubClient(stats["git_token"])
        db_icon = "🟢 Connected" if gh.test_token() else "🔴 Invalid Token"
    else:
        db_icon = "🔴 Not Set (Locked to Admin Only)"

    text = (
        f"⚙️ <b>ADMIN CONTROL PANEL</b>\n{SEP}\n"
        f"<blockquote>👑 <b>Admin ID:</b> <code>{ADMIN_ID}</code>\n"
        f"🤖 <b>Bot Mode:</b> <code>{bot_mode.upper()}</code>\n"
        f"🐙 <b>Cloud DB:</b> <code>{db_icon}</code>\n"
        f"👥 <b>Users:</b> <code>{stats['total_users']}</code> (⭐ <code>{stats['premium_users']}</code> | 🚫 <code>{stats['banned_users']}</code>)\n"
        f"📢 <b>Bot Channel:</b> <code>{stats['bot_channel'] or 'Not Set'}</code>\n"
        f"✅ <b>Results Channel:</b> <code>{stats['results_channel'] or 'Not Set'}</code>\n"
        f"❌ <b>Error Channel:</b> <code>{stats['error_channel'] or 'Not Set'}</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Select a category below to configure:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🐙 Database (GitHub)", callback_data="cfg_github"),
        types.InlineKeyboardButton("📢 Channels", callback_data="cfg_channels")
    )
    kb.add(
        types.InlineKeyboardButton("👥 Members", callback_data="cfg_members"),
        types.InlineKeyboardButton("🔐 Access Modes", callback_data="cfg_modes")
    )
    kb.add(
        types.InlineKeyboardButton("⏱️ Quotas & Limits", callback_data="cfg_limits"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="cfg_broadcast")
    )
    kb.add(types.InlineKeyboardButton("❌ Close Panel", callback_data="cfg_close"))
    return text, kb

def render_github_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render GitHub Database Submenu."""
    stats = db.get_stats()
    token = stats["git_token"]
    db_url = stats["database_url"] or "Not Set"

    tok_status = "🔴 Not Set"
    account_info = ""
    if token:
        gh = GitHubClient(token)
        info = gh.get_token_info()
        if info.get("valid"):
            tok_status = "🟢 Valid"
            account_info = f"\n<blockquote>👤 <b>Account:</b> {info['username']}</blockquote>"
        else:
            tok_status = "🔴 Invalid"

    db_status = "🟢 Connected & Syncing" if (token and tok_status == "🟢 Valid") else "🔴 Offline / Not Configured"

    text = (
        f"🐙 <b>GITHUB DATABASE CONFIGURATION</b>\n{SEP}\n"
        f"<b>1️⃣ AUTHENTICATION</b>\n"
        f"<blockquote>🔑 <b>Token:</b> {tok_status}</blockquote>{account_info}\n\n"
        f"<b>2️⃣ 💾 CLOUD DATABASE (database.json)</b>\n"
        f"<blockquote>📊 <b>Status:</b> {db_status}</blockquote>\n"
        f"<blockquote>🔗 <b>Raw URL:</b>\n<code>{db_url}</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Manage cloud storage and credentials:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    if token:
        kb.add(
            types.InlineKeyboardButton("🔄 Update Token", callback_data="cfg_gh_update_token"),
            types.InlineKeyboardButton("🧪 Test Token", callback_data="cfg_gh_test_token")
        )
        kb.add(types.InlineKeyboardButton("🗑️ Remove Token", callback_data="cfg_gh_remove_token"))
    else:
        kb.add(types.InlineKeyboardButton("➕ Set GitHub Token", callback_data="cfg_gh_add_token"))

    kb.add(
        types.InlineKeyboardButton("✏️ Edit Database URL", callback_data="cfg_gh_edit_url"),
        types.InlineKeyboardButton("🔄 Refresh / Sync DB", callback_data="cfg_gh_refresh")
    )
    kb.add(types.InlineKeyboardButton("🧪 Test Database Connection", callback_data="cfg_gh_test_db"))
    kb.add(types.InlineKeyboardButton("🗑️ Reset Remote DB", callback_data="cfg_gh_reset_db"))
    kb.add(types.InlineKeyboardButton("❓ Help & Token Guide", callback_data="cfg_gh_help"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_channels_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Channels Configuration Submenu."""
    stats = db.get_stats()
    bot_ch = stats["bot_channel"] or "Not Set"
    inv_link = stats["channel_invite"] or "Not Set"
    res_ch = stats["results_channel"] or "Not Set"
    err_ch = stats["error_channel"] or "Not Set"

    text = (
        f"📢 <b>CHANNELS CONFIGURATION</b>\n{SEP}\n"
        f"🤖 <b>BOT CHANNEL (For Registration Verification)</b>\n"
        f"<blockquote><code>{bot_ch}</code></blockquote>\n"
        f"🔗 <b>INVITE LINK</b>\n"
        f"<blockquote><code>{inv_link}</code></blockquote>\n"
        f"✅ <b>RESULTS CHANNEL (Successful Stream Broadcasts)</b>\n"
        f"<blockquote><code>{res_ch}</code></blockquote>\n"
        f"❌ <b>ERROR CHANNEL (Stream Error Reports)</b>\n"
        f"<blockquote><code>{err_ch}</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Configure or test channel integrations:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📢 Set Bot Channel", callback_data="cfg_ch_bot"),
        types.InlineKeyboardButton("🔗 Set Invite Link", callback_data="cfg_ch_invite")
    )
    kb.add(
        types.InlineKeyboardButton("✅ Set Results Channel", callback_data="cfg_ch_results"),
        types.InlineKeyboardButton("❌ Set Error Channel", callback_data="cfg_ch_error")
    )
    kb.add(
        types.InlineKeyboardButton("🧪 Test Bot Channel", callback_data="cfg_ch_test_bot"),
        types.InlineKeyboardButton("🧪 Test Results Channel", callback_data="cfg_ch_test_results")
    )
    kb.add(types.InlineKeyboardButton("🧪 Test Error Channel", callback_data="cfg_ch_test_error"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_members_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Members Management Submenu."""
    stats = db.get_stats()
    text = (
        f"👥 <b>MEMBERS MANAGEMENT</b>\n{SEP}\n"
        f"📊 <b>DATABASE OVERVIEW</b>\n"
        f"<blockquote>👤 <b>Total Users:</b> {stats['total_users']}</blockquote>\n"
        f"<blockquote>⭐ <b>Premium Members:</b> {stats['premium_users']}</blockquote>\n"
        f"<blockquote>🚫 <b>Banned Users:</b> {stats['banned_users']}</blockquote>\n"
        f"<blockquote>📅 <b>Active Today:</b> {stats['downloads_today']}</blockquote>\n"
        f"{SEP}\n"
        f"<i>Choose an action below:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👤 All Members", callback_data="cfg_mem_all_0"),
        types.InlineKeyboardButton("⭐ Premium Users", callback_data="cfg_mem_prem_0")
    )
    kb.add(
        types.InlineKeyboardButton("🚫 Banned Users", callback_data="cfg_mem_ban_0"),
        types.InlineKeyboardButton("🔍 Search User", callback_data="cfg_mem_search")
    )
    kb.add(
        types.InlineKeyboardButton("⭐ Add Premium", callback_data="cfg_mem_grant_prem"),
        types.InlineKeyboardButton("❌ Del Premium", callback_data="cfg_mem_revoke_prem")
    )
    kb.add(
        types.InlineKeyboardButton("🚫 Ban User", callback_data="cfg_mem_ban"),
        types.InlineKeyboardButton("🟢 Unban User", callback_data="cfg_mem_unban")
    )
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_members_list(filter_type: str = "all", page: int = 0) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render paginated user list with quick profile buttons."""
    users = db.get_users_list(filter_type=filter_type)
    total_users = len(users)
    per_page = 6
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    title_map = {"all": "ALL MEMBERS", "premium": "PREMIUM USERS", "banned": "BANNED USERS"}
    list_title = title_map.get(filter_type, "MEMBERS")

    start = page * per_page
    end = min(start + per_page, total_users)
    page_users = users[start:end]

    text = (
        f"👥 <b>{list_title}</b> (Page {page + 1}/{total_pages})\n{SEP}\n"
        f"<blockquote>📊 Total: <b>{total_users}</b> user(s)</blockquote>\n"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    if not page_users:
        text += "<blockquote><i>No users found in this category.</i></blockquote>\n"
    else:
        for u in page_users:
            uid = u["user_id"]
            uname = f"@{u['username']}" if u.get("username") else f"ID: {uid}"
            prem_badge = "⭐ " if u.get("is_premium") else ""
            ban_badge = "🚫 " if u.get("is_banned") else ""
            label = f"{ban_badge}{prem_badge}{uname} ({u.get('downloads_today', 0)} today)"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"cfg_mem_prof_{uid}"))

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton("◀️ Prev", callback_data=f"cfg_mem_{filter_type[:3]}_{page - 1}"))
    nav_row.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="cfg_noop"))
    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton("Next ▶️", callback_data=f"cfg_mem_{filter_type[:3]}_{page + 1}"))
    if nav_row:
        kb.row(*nav_row)

    kb.add(types.InlineKeyboardButton("← Back to Members", callback_data="cfg_members"))
    return text + f"{SEP}", kb

def render_user_profile(user_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render single user profile with action toggles."""
    u = db.get_user_info(user_id)
    if not u:
        text = f"❌ <b>User <code>{user_id}</code> not found in database.</b>"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("← Back to Members", callback_data="cfg_members"))
        return text, kb

    uname = f"@{u['username']}" if u.get("username") else "None"
    status_label = db.get_user_status_label(user_id, ADMIN_ID)
    is_prem = u.get("is_premium", False)
    is_ban = u.get("is_banned", False)

    text = (
        f"👤 <b>USER PROFILE</b>\n{SEP}\n"
        f"<blockquote>🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Username:</b> {uname}\n"
        f"🏷️ <b>Status:</b> {status_label}\n"
        f"📅 <b>Registered:</b> <code>{u.get('registered_at', 'N/A')}</code>\n"
        f"📥 <b>Today's Usage:</b> <code>{u.get('downloads_today', 0)} links</code>\n"
        f"📦 <b>Total Downloads:</b> <code>{u.get('total_downloads', 0)}</code></blockquote>\n"
        f"{SEP}"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    prem_btn = "❌ Revoke Premium" if is_prem else "⭐ Grant Premium"
    ban_btn = "🟢 Unban User" if is_ban else "🚫 Ban User"

    kb.add(
        types.InlineKeyboardButton(prem_btn, callback_data=f"cfg_tog_prem_{user_id}"),
        types.InlineKeyboardButton(ban_btn, callback_data=f"cfg_tog_ban_{user_id}")
    )
    kb.add(types.InlineKeyboardButton("🔄 Reset Daily Downloads", callback_data=f"cfg_reset_dl_{user_id}"))
    kb.add(types.InlineKeyboardButton("← Back to Members", callback_data="cfg_members"))
    return text, kb

def render_modes_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Access Modes Submenu."""
    stats = db.get_stats()
    current_mode = stats["bot_mode"]
    db_connected = db.is_github_connected()

    text = (
        f"🔐 <b>ACCESS MODES CONFIGURATION</b>\n{SEP}\n"
        f"<blockquote>🤖 <b>Current Mode:</b> <code>{current_mode.upper()}</code>\n"
        f"🐙 <b>Cloud DB:</b> {'🟢 Connected' if db_connected else '🔴 Offline / Not Configured'}</blockquote>\n"
        f"{SEP}\n"
        f"<b>Modes Description:</b>\n"
        f"• <b>Admin Only:</b> Only you can use the bot (default safe mode)\n"
        f"• <b>Premium Only:</b> Only users with Premium tier can use the bot\n"
        f"• <b>Public:</b> Anyone registered can use the bot within daily quotas\n"
        f"{SEP}\n"
        f"⚠️ <i>Public and Premium modes require a connected GitHub Cloud Database!</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    admin_mark = " (Active)" if current_mode == "admin_only" else ""
    prem_mark = " (Active)" if current_mode == "premium" else ""
    pub_mark = " (Active)" if current_mode == "public" else ""

    kb.add(types.InlineKeyboardButton(f"🔐 Admin Only Mode{admin_mark}", callback_data="cfg_set_mode_admin"))
    kb.add(types.InlineKeyboardButton(f"⭐ Premium Only Mode{prem_mark}", callback_data="cfg_set_mode_premium"))
    kb.add(types.InlineKeyboardButton(f"🌐 Public Mode{pub_mark}", callback_data="cfg_set_mode_public"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_limits_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Quotas & Limits Submenu."""
    stats = db.get_stats()
    text = (
        f"⏱️ <b>QUOTAS & LIMITS CONFIGURATION</b>\n{SEP}\n"
        f"<blockquote>🔢 <b>Regular Daily Limit:</b> <code>{stats['daily_limit']} links/day</code>\n"
        f"📦 <b>Regular Max Size:</b> <code>{stats['max_size_gb']} GB/torrent</code>\n"
        f"⭐ <b>Premium Users:</b> <code>UNLIMITED ♾️</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Configure limits for regular registered members:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✏️ Set Daily Limit", callback_data="cfg_set_daily_limit"),
        types.InlineKeyboardButton("📦 Set Max Size", callback_data="cfg_set_max_size")
    )
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

# ================= Prompt Input Helper =================

def prompt_admin_input(chat_id: int, message_id: int, action: str, title: str, instructions: str, return_menu: str, extra: dict = None):
    """
    Edits the current settings message to an input prompt with a [Cancel] button.
    Stores the pending state so the next reply from the admin is intercepted.
    """
    admin_pending[ADMIN_ID] = {
        "action": action,
        "mid": message_id,
        "cid": chat_id,
        "return_menu": return_menu,
        "extra": extra or {}
    }

    text = (
        f"✏️ <b>ENTER {title}</b>\n{SEP}\n"
        f"<blockquote>{instructions}</blockquote>\n{SEP}\n"
        f"<i>Reply directly to this message with your value.</i>"
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("← Cancel", callback_data="cfg_cancel_input"))
    safe_edit_message(chat_id, message_id, text, reply_markup=kb)

def return_to_submenu(chat_id: int, message_id: int, return_menu: str):
    """Return to designated submenu after an action or cancellation."""
    menu_dispatch = {
        "main": render_settings_main,
        "github": render_github_menu,
        "channels": render_channels_menu,
        "members": render_members_menu,
        "modes": render_modes_menu,
        "limits": render_limits_menu
    }
    renderer = menu_dispatch.get(return_menu, render_settings_main)
    text, kb = renderer()
    safe_edit_message(chat_id, message_id, text, reply_markup=kb)

# ================= Admin Commands & Routing =================

@bot.message_handler(commands=["settings", "panel"])
def handle_settings(message: types.Message):
    """Open Admin Control Panel."""
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 <b>Access Denied:</b> This command is restricted to the bot Administrator.")
        return

    text, kb = render_settings_main()
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(commands=["start", "help"])
def handle_start(message: types.Message):
    """Welcome and usage instructions."""
    if not check_user_access(message):
        return

    user_id = message.from_user.id
    status_label = db.get_user_status_label(user_id, ADMIN_ID)

    text = (
        "👋 <b>Welcome to the Torrent-to-Direct Stream Bot!</b>\n\n"
        f"👤 <b>Your Status:</b> {status_label}\n"
        "⚡ <b>Direct Stream Relay:</b>\n"
        "Downloads data from BitTorrent and streams it directly to "
        "<b>Buzzheavier.com</b> or <b>Gofile.io</b> in real-time "
        "<b>with 0 bytes saved on RDP!</b>\n\n"
        "📥 <b>How to Use:</b>\n"
        "• Send <code>/dw &lt;magnet_link&gt;</code>\n"
        "• Or reply to a <code>.torrent</code> file with <code>/dw</code>\n"
        "• Or directly send a <code>.torrent</code> file\n"
        "• Or simply paste any magnet link!\n\n"
        "📌 <b>Commands:</b>\n"
        "• <code>/account</code> - View your account quota & limits\n"
    )
    if user_id == ADMIN_ID:
        text += "• <code>/settings</code> - Open Admin Control Panel 👑\n"

    bot.reply_to(message, text, disable_web_page_preview=True)

@bot.message_handler(commands=["account"])
def handle_account(message: types.Message):
    """View user quota, limits, and tier."""
    if not check_user_access(message):
        return

    user_id = message.from_user.id
    status_label = db.get_user_status_label(user_id, ADMIN_ID)
    info = db.get_user_info(user_id)
    is_prem = db.is_premium(user_id) or user_id == ADMIN_ID

    daily_limit = db.get_setting("daily_limit_regular", 5)
    max_size_gb = db.get_setting("max_size_gb_regular", 40.0)
    used_today = info.get("downloads_today", 0)

    quota_str = "Unlimited ♾️" if is_prem else f"{used_today} / {daily_limit} links"
    max_sz_str = "Unlimited ♾️" if is_prem else f"{max_size_gb} GB"

    text = (
        "💳 <b>ACCOUNT INFORMATION</b>\n"
        f"{SEP}\n"
        f"<blockquote>🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Status:</b> {status_label}\n"
        f"📅 <b>Registered:</b> <code>{info.get('registered_at', 'N/A')}</code>\n"
        f"📊 <b>Today's Usage:</b> <code>{quota_str}</code>\n"
        f"📦 <b>Max File Size:</b> <code>{max_sz_str}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{info.get('total_downloads', 0)}</code></blockquote>\n"
        f"{SEP}"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=["dw"])
def handle_dw_command(message: types.Message):
    """Handle /dw command."""
    if not check_user_access(message):
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) > 1:
        arg = parts[1].strip()
        if arg.startswith("magnet:?"):
            process_torrent_source(message, magnet_link=arg)
            return

    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.document and replied.document.file_name and replied.document.file_name.endswith(".torrent"):
            file_info = bot.get_file(replied.document.file_id)
            torrent_bytes = bot.download_file(file_info.file_path)
            process_torrent_source(message, torrent_bytes=torrent_bytes, filename=replied.document.file_name)
            return

        replied_text = replied.text or replied.caption or ""
        match = re.search(r"(magnet:\?xt=urn:btih:[^\s\"'>]+)", replied_text, re.IGNORECASE)
        if match:
            process_torrent_source(message, magnet_link=match.group(1))
            return

    bot.reply_to(
        message,
        "⚠️ <b>Usage:</b>\n"
        "• <code>/dw &lt;magnet_link&gt;</code>\n"
        "• Or reply to a <code>.torrent</code> file with <code>/dw</code>\n\n"
        "Example:\n<code>/dw magnet:?xt=urn:btih:...</code>"
    )

@bot.message_handler(content_types=["document"])
def handle_document_upload(message: types.Message):
    """Handle direct .torrent document upload."""
    if not check_user_access(message):
        return

    if message.document and message.document.file_name and message.document.file_name.endswith(".torrent"):
        file_info = bot.get_file(message.document.file_id)
        torrent_bytes = bot.download_file(file_info.file_path)
        process_torrent_source(message, torrent_bytes=torrent_bytes, filename=message.document.file_name)

@bot.message_handler(func=lambda msg: msg.text and msg.text.strip().startswith("magnet:?"))
def handle_direct_magnet(message: types.Message):
    """Handle raw magnet links sent directly."""
    if not check_user_access(message):
        return

    magnet_link = message.text.strip()
    process_torrent_source(message, magnet_link=magnet_link)

# ================= Admin Reply-Input Engine =================

@bot.message_handler(func=lambda msg: msg.from_user.id == ADMIN_ID and ADMIN_ID in admin_pending)
def handle_admin_reply_inputs(message: types.Message):
    """Intercept admin reply to an edit prompt, process input, and return smoothly to menu."""
    pending = admin_pending.pop(ADMIN_ID, {})
    if not pending:
        return

    action = pending.get("action")
    mid = pending.get("mid")
    cid = pending.get("cid", message.chat.id)
    return_menu = pending.get("return_menu", "main")
    val = (message.text or "").strip()

    # Try to delete admin's reply message to keep the chat clean
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    # 1. GitHub Token
    if action in ("add_github_token", "update_github_token"):
        safe_edit_message(cid, mid, f"⏳ <b>Validating GitHub Token...</b>\n{SEP}\n<blockquote>Connecting to GitHub API...</blockquote>")
        ok, res_msg = db.set_github_token(val)
        if ok:
            status_text = (
                f"✅ <b>GitHub Setup Successful!</b>\n{SEP}\n"
                f"<blockquote>{res_msg}</blockquote>\n"
                f"<blockquote>🔄 Remote cloud database loaded!</blockquote>\n{SEP}\n"
                f"<i>Returning to menu...</i>"
            )
        else:
            status_text = (
                f"❌ <b>GitHub Token Failed!</b>\n{SEP}\n"
                f"<blockquote>{res_msg}</blockquote>\n{SEP}\n"
                f"<i>Returning to menu...</i>"
            )
        safe_edit_message(cid, mid, status_text)
        time.sleep(2.0)
        return_to_submenu(cid, mid, "github")
        return

    # 2. GitHub Database URL
    elif action == "edit_db_url":
        ok, res_msg = db.set_database_url(val)
        safe_edit_message(cid, mid, f"✅ <b>Database URL Updated!</b>\n{SEP}\n<blockquote>{res_msg}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "github")
        return

    # 3. Channels
    elif action == "set_bot_channel":
        if val.lower() in ("none", "0", "disable"):
            db.set_setting("bot_channel", "")
            res = "Bot channel disabled."
        else:
            db.set_setting("bot_channel", val)
            res = f"Bot channel set to: <code>{val}</code>"
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "channels")
        return

    elif action == "set_invite_link":
        db.set_setting("channel_invite", val)
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>Invite URL set to: <code>{val}</code></blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "channels")
        return

    elif action == "set_results_channel":
        if val.lower() in ("none", "0", "disable"):
            db.set_setting("results_channel", "")
            res = "Results channel disabled."
        else:
            db.set_setting("results_channel", val)
            res = f"Results channel set to: <code>{val}</code>"
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "channels")
        return

    elif action == "set_error_channel":
        if val.lower() in ("none", "0", "disable"):
            db.set_setting("error_channel", "")
            res = "Error channel disabled."
        else:
            db.set_setting("error_channel", val)
            res = f"Error channel set to: <code>{val}</code>"
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "channels")
        return

    # 4. Limits
    elif action == "set_daily_limit":
        if val.isdigit() and int(val) > 0:
            db.set_setting("daily_limit_regular", int(val))
            res = f"Daily limit set to: <b>{val} links/day</b>"
        else:
            res = "❌ Invalid number. Limit unchanged."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "limits")
        return

    elif action == "set_max_size":
        try:
            sz = float(val)
            if sz > 0:
                db.set_setting("max_size_gb_regular", sz)
                res = f"Max torrent size set to: <b>{sz} GB</b>"
            else:
                res = "❌ Value must be greater than 0."
        except ValueError:
            res = "❌ Invalid number."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "limits")
        return

    # 5. Members Actions
    elif action == "search_user":
        found = db.search_users(val)
        if not found:
            safe_edit_message(cid, mid, f"❌ <b>No users found matching:</b> <code>{val}</code>\n{SEP}\n<i>Returning...</i>")
            time.sleep(2.0)
            return_to_submenu(cid, mid, "members")
            return
        elif len(found) == 1:
            # Open user profile directly
            text, kb = render_user_profile(found[0]["user_id"])
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return
        else:
            # Render match list
            text = f"🔍 <b>SEARCH RESULTS FOR:</b> <code>{val}</code>\n{SEP}\n"
            kb = types.InlineKeyboardMarkup(row_width=1)
            for u in found[:10]:
                uid = u["user_id"]
                uname = f"@{u['username']}" if u.get("username") else f"ID: {uid}"
                kb.add(types.InlineKeyboardButton(f"👤 {uname}", callback_data=f"cfg_mem_prof_{uid}"))
            kb.add(types.InlineKeyboardButton("← Back to Members", callback_data="cfg_members"))
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

    elif action == "grant_premium":
        if val.isdigit():
            target_uid = int(val)
            db.set_premium(target_uid, True)
            res = f"User <code>{target_uid}</code> granted <b>Premium</b> ⭐!"
            try:
                bot.send_message(target_uid, "🎉 <b>Congratulations!</b> You have been upgraded to <b>Premium</b>! You now enjoy unlimited file sizes and daily links.")
            except Exception:
                pass
        else:
            res = "❌ Invalid numerical User ID."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "members")
        return

    elif action == "revoke_premium":
        if val.isdigit():
            target_uid = int(val)
            db.set_premium(target_uid, False)
            res = f"Premium revoked for user <code>{target_uid}</code>."
        else:
            res = "❌ Invalid numerical User ID."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "members")
        return

    elif action == "ban_user":
        if val.isdigit():
            target_uid = int(val)
            db.set_banned(target_uid, True)
            res = f"User <code>{target_uid}</code> has been <b>Banned</b> 🚫."
        else:
            res = "❌ Invalid numerical User ID."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "members")
        return

    elif action == "unban_user":
        if val.isdigit():
            target_uid = int(val)
            db.set_banned(target_uid, False)
            res = f"User <code>{target_uid}</code> has been <b>Unbanned</b> 🟢."
        else:
            res = "❌ Invalid numerical User ID."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "members")
        return

    # 6. Broadcast
    elif action == "broadcast":
        all_users = db.get_all_user_ids()
        safe_edit_message(cid, mid, f"📢 <b>Broadcasting message to {len(all_users)} users...</b>\n{SEP}\n<blockquote>⏳ Sending...</blockquote>")
        sent = 0
        for uid in all_users:
            try:
                bot.send_message(uid, f"📢 <b>ANNOUNCEMENT:</b>\n{SEP}\n{val}\n{SEP}")
                sent += 1
                time.sleep(0.05)
            except Exception:
                pass
        safe_edit_message(cid, mid, f"✅ <b>Broadcast Completed!</b>\n{SEP}\n<blockquote>Delivered to: <b>{sent}/{len(all_users)}</b> members.</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(2.0)
        return_to_submenu(cid, mid, "main")
        return

# ================= Inline Callbacks Router =================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: types.CallbackQuery):
    """Handle all inline buttons (registration, settings, file selectors, stream controls)."""
    data = call.data
    user_id = call.from_user.id
    cid = call.message.chat.id
    mid = call.message.message_id

    # 1. Registration Callback
    if data.startswith("reg:"):
        target_uid = int(data.split(":")[1])
        if user_id != target_uid:
            bot.answer_callback_query(call.id, "This registration button is not for you.", show_alert=True)
            return

        if not check_channel_membership(user_id):
            bot.answer_callback_query(
                call.id,
                "⚠️ Please join our official channel first before registering!",
                show_alert=True
            )
            return

        db.register_user(user_id, call.from_user.username or "")
        bot.answer_callback_query(call.id, "✅ Registration Complete!")
        safe_edit_message(
            cid,
            mid,
            f"✅ <b>REGISTRATION COMPLETE!</b>\n{SEP}\n"
            f"🎉 Your account has been registered successfully!\n"
            f"You can now send torrent magnet links or <code>.torrent</code> files to stream directly."
        )
        return

    # 2. Admin Settings Callbacks
    if data.startswith("cfg_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Unauthorized.", show_alert=True)
            return

        # Main & Close
        if data == "cfg_main":
            text, kb = render_settings_main()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_close":
            bot.delete_message(cid, mid)
            bot.answer_callback_query(call.id, "Panel closed.")
            return

        elif data == "cfg_cancel_input":
            admin_pending.pop(ADMIN_ID, None)
            text, kb = render_settings_main()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id, "Cancelled.")
            return

        elif data == "cfg_noop":
            bot.answer_callback_query(call.id)
            return

        # Navigation to Submenus
        elif data == "cfg_github":
            text, kb = render_github_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_channels":
            text, kb = render_channels_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_members":
            text, kb = render_members_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_modes":
            text, kb = render_modes_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_limits":
            text, kb = render_limits_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_broadcast":
            prompt_admin_input(
                cid, mid,
                action="broadcast",
                title="BROADCAST MESSAGE",
                instructions="Enter the announcement message to broadcast to all registered members:",
                return_menu="main"
            )
            bot.answer_callback_query(call.id)
            return

        # ── GitHub Submenu Operations ──
        elif data == "cfg_gh_add_token" or data == "cfg_gh_update_token":
            action = "update_github_token" if data == "cfg_gh_update_token" else "add_github_token"
            prompt_admin_input(
                cid, mid,
                action=action,
                title="GITHUB TOKEN",
                instructions="Send your GitHub Personal Access Token (starts with <code>ghp_</code> or <code>github_pat_</code>):",
                return_menu="github"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_gh_test_token":
            tok = db.get_setting("git_token", "")
            gh = GitHubClient(tok)
            info = gh.get_token_info()
            if info.get("valid"):
                bot.answer_callback_query(call.id, f"✅ Valid token for @{info['username']}!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"❌ Token invalid: {info.get('error', 'Unknown')}", show_alert=True)
            return

        elif data == "cfg_gh_remove_token":
            db.remove_github_token()
            bot.answer_callback_query(call.id, "Token removed. Reverted to Admin Only mode.", show_alert=True)
            text, kb = render_github_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_gh_edit_url":
            prompt_admin_input(
                cid, mid,
                action="edit_db_url",
                title="DATABASE RAW URL",
                instructions="Enter the GitHub raw URL pointing to <code>database.json</code>:",
                return_menu="github"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_gh_refresh":
            bot.answer_callback_query(call.id, "🔄 Syncing database from GitHub...")
            ok = db.sync_from_github()
            if ok:
                bot.answer_callback_query(call.id, "✅ Database synced successfully from GitHub!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "⚠️ Sync failed. Verify GitHub token and URL.", show_alert=True)
            text, kb = render_github_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_gh_test_db":
            tok = db.get_setting("git_token", "")
            url = db.get_setting("database_url", "")
            res = GitHubClient(tok).test_database_connection(url)
            if res.get("success"):
                bot.answer_callback_query(call.id, f"✅ DB Reachable! Users loaded: {res.get('users_count', 0)}", show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"❌ DB Connection Error: {res.get('error')}", show_alert=True)
            return

        elif data == "cfg_gh_reset_db":
            db.reset_database()
            bot.answer_callback_query(call.id, "🗑️ Remote database reset complete.", show_alert=True)
            text, kb = render_github_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_gh_help":
            safe_edit_message(cid, mid, GITHUB_HELP_TEXT, reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("← Back to Database Settings", callback_data="cfg_github")
            ))
            bot.answer_callback_query(call.id)
            return

        # ── Channels Submenu Operations ──
        elif data == "cfg_ch_bot":
            prompt_admin_input(
                cid, mid,
                action="set_bot_channel",
                title="BOT CHANNEL",
                instructions="Enter required channel ID or username (e.g. <code>@MyChannel</code> or <code>-100123456789</code>) or <code>none</code>:",
                return_menu="channels"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_ch_invite":
            prompt_admin_input(
                cid, mid,
                action="set_invite_link",
                title="CHANNEL INVITE LINK",
                instructions="Enter the invite link for your channel (e.g. <code>https://t.me/MyChannel</code>):",
                return_menu="channels"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_ch_results":
            prompt_admin_input(
                cid, mid,
                action="set_results_channel",
                title="RESULTS CHANNEL",
                instructions="Enter results channel ID (e.g. <code>-100123456789</code>) or <code>none</code>:",
                return_menu="channels"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_ch_error":
            prompt_admin_input(
                cid, mid,
                action="set_error_channel",
                title="ERROR CHANNEL",
                instructions="Enter error channel ID (e.g. <code>-100123456789</code>) or <code>none</code>:",
                return_menu="channels"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_ch_test_bot":
            ch = db.get_setting("bot_channel", "")
            if not ch:
                bot.answer_callback_query(call.id, "No Bot Channel configured.", show_alert=True)
                return
            try:
                bot.send_message(ch, f"📢 <b>Test Message:</b> Bot Channel connection is working properly!")
                bot.answer_callback_query(call.id, "✅ Test message sent to Bot Channel!", show_alert=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Test failed: {e}", show_alert=True)
            return

        elif data == "cfg_ch_test_results":
            ch = db.get_setting("results_channel", "")
            if not ch:
                bot.answer_callback_query(call.id, "No Results Channel configured.", show_alert=True)
                return
            try:
                bot.send_message(ch, f"🚀 <b>Test Broadcast:</b> Results Channel integration is active!")
                bot.answer_callback_query(call.id, "✅ Test message sent to Results Channel!", show_alert=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Test failed: {e}", show_alert=True)
            return

        elif data == "cfg_ch_test_error":
            ch = db.get_setting("error_channel", "")
            if not ch:
                bot.answer_callback_query(call.id, "No Error Channel configured.", show_alert=True)
                return
            try:
                bot.send_message(ch, f"❌ <b>Test Broadcast:</b> Error Channel integration is active!")
                bot.answer_callback_query(call.id, "✅ Test message sent to Error Channel!", show_alert=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Test failed: {e}", show_alert=True)
            return

        # ── Access Modes Operations (Strict Gatekeeper Constraint!) ──
        elif data == "cfg_set_mode_admin":
            ok, res = db.set_bot_mode("admin_only")
            bot.answer_callback_query(call.id, "Switched to Admin Only mode.")
            text, kb = render_modes_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_set_mode_premium":
            ok, res = db.set_bot_mode("premium")
            if not ok:
                bot.answer_callback_query(call.id, "❌ Connect GitHub Database Token first!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "Switched to Premium Only mode.")
            text, kb = render_modes_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_set_mode_public":
            ok, res = db.set_bot_mode("public")
            if not ok:
                bot.answer_callback_query(call.id, "❌ Connect GitHub Database Token first!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "Switched to Public mode.")
            text, kb = render_modes_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        # ── Limits Operations ──
        elif data == "cfg_set_daily_limit":
            prompt_admin_input(
                cid, mid,
                action="set_daily_limit",
                title="DAILY LIMIT",
                instructions="Enter daily download link limit for regular users (e.g. <code>5</code>):",
                return_menu="limits"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_set_max_size":
            prompt_admin_input(
                cid, mid,
                action="set_max_size",
                title="MAX TORRENT SIZE (GB)",
                instructions="Enter maximum torrent file size in GB for regular users (e.g. <code>40.0</code>):",
                return_menu="limits"
            )
            bot.answer_callback_query(call.id)
            return

        # ── Members Submenu & Paginated Lists ──
        elif data.startswith("cfg_mem_all_") or data.startswith("cfg_mem_pre_") or data.startswith("cfg_mem_ban_"):
            parts = data.split("_")
            ftype = "all" if parts[2] == "all" else ("premium" if parts[2] == "pre" else "banned")
            page = int(parts[3])
            text, kb = render_members_list(filter_type=ftype, page=page)
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data.startswith("cfg_mem_prof_"):
            target_uid = int(data.split("_")[3])
            text, kb = render_user_profile(target_uid)
            safe_edit_message(cid, mid, text, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        elif data.startswith("cfg_tog_prem_"):
            target_uid = int(data.split("_")[3])
            current_status = db.is_premium(target_uid)
            db.set_premium(target_uid, not current_status)
            bot.answer_callback_query(call.id, f"Premium {'Revoked' if current_status else 'Granted'}")
            text, kb = render_user_profile(target_uid)
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data.startswith("cfg_tog_ban_"):
            target_uid = int(data.split("_")[3])
            current_status = db.is_banned(target_uid)
            db.set_banned(target_uid, not current_status)
            bot.answer_callback_query(call.id, f"User {'Unbanned' if current_status else 'Banned'}")
            text, kb = render_user_profile(target_uid)
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data.startswith("cfg_reset_dl_"):
            target_uid = int(data.split("_")[3])
            db.reset_user_daily_limit(target_uid)
            bot.answer_callback_query(call.id, "Daily downloads reset to 0.")
            text, kb = render_user_profile(target_uid)
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return

        elif data == "cfg_mem_search":
            prompt_admin_input(
                cid, mid,
                action="search_user",
                title="SEARCH USER",
                instructions="Enter numerical User ID or Telegram @username to search:",
                return_menu="members"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_mem_grant_prem":
            prompt_admin_input(
                cid, mid,
                action="grant_premium",
                title="GRANT PREMIUM",
                instructions="Enter numerical User ID to grant Premium access:",
                return_menu="members"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_mem_revoke_prem":
            prompt_admin_input(
                cid, mid,
                action="revoke_premium",
                title="REVOKE PREMIUM",
                instructions="Enter numerical User ID to revoke Premium access:",
                return_menu="members"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_mem_ban":
            prompt_admin_input(
                cid, mid,
                action="ban_user",
                title="BAN USER",
                instructions="Enter numerical User ID to Ban:",
                return_menu="members"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_mem_unban":
            prompt_admin_input(
                cid, mid,
                action="unban_user",
                title="UNBAN USER",
                instructions="Enter numerical User ID to Unban:",
                return_menu="members"
            )
            bot.answer_callback_query(call.id)
            return

    # 3. Torrent / Stream Session Callbacks
    if ":" not in data:
        bot.answer_callback_query(call.id)
        return

    parts = data.split(":")
    action = parts[0]
    session_id = parts[1]

    session = sessions.get(session_id)
    if not session:
        bot.answer_callback_query(call.id, "Session expired. Please send torrent again.", show_alert=True)
        return

    sess_chat_id = session["chat_id"]
    sess_message_id = session["message_id"]

    if action == "tgl":
        file_idx = int(parts[2])
        if file_idx in session["selected"]:
            session["selected"].remove(file_idx)
            bot.answer_callback_query(call.id, f"Deselected file #{file_idx}")
        else:
            session["selected"].add(file_idx)
            bot.answer_callback_query(call.id, f"Selected file #{file_idx}")

        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(sess_chat_id, sess_message_id, text, reply_markup=markup)

    elif action == "pg":
        target_page = int(parts[2])
        session["page"] = target_page
        markup = build_file_selection_keyboard(session_id)
        safe_edit_markup(sess_chat_id, sess_message_id, markup)
        bot.answer_callback_query(call.id)

    elif action == "all":
        session["selected"] = set(f["index"] for f in session["files"])
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(sess_chat_id, sess_message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id, "All files selected")

    elif action == "none":
        session["selected"].clear()
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(sess_chat_id, sess_message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id, "All files deselected")

    elif action == "prompt_dest":
        if len(session["selected"]) == 0:
            bot.answer_callback_query(call.id, "⚠️ Please select at least one file to download!", show_alert=True)
            return

        selected_files = [f for f in session["files"] if f["index"] in session["selected"]]
        total_selected_size = sum(f["length"] for f in selected_files)

        ok, reason = db.check_download_limit(user_id, ADMIN_ID, total_selected_size)
        if not ok:
            bot.answer_callback_query(call.id, "Limit reached!", show_alert=False)
            safe_edit_message(sess_chat_id, sess_message_id, reason, reply_markup=None)
            return

        bot.answer_callback_query(call.id)
        dest_kb = build_destination_keyboard(session_id)
        dest_text = (
            f"🌐 <b>CHOOSE UPLOAD DESTINATION</b>\n{SEP}\n"
            f"📁 <b>Torrent:</b> <code>{html.escape(session['torrent_name'])}</code>\n"
            f"📦 <b>Files:</b> {len(selected_files)} selected ({human_readable_size(total_selected_size)})\n\n"
            f"<i>Where would you like to stream your files?</i>"
        )
        safe_edit_message(sess_chat_id, sess_message_id, dest_text, reply_markup=dest_kb)

    elif action == "back_select":
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(sess_chat_id, sess_message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif action == "start":
        destination = parts[2] if len(parts) > 2 else "buzzheavier"
        bot.answer_callback_query(call.id, f"🚀 Starting stream to {destination}...")
        session["status"] = "running"
        threading.Thread(target=run_stream_pipeline, args=(session_id, destination), daemon=True).start()

    elif action == "stop":
        bot.answer_callback_query(call.id, "🛑 Stopping download...")
        session["cancel_event"].set()

    elif action == "noop":
        bot.answer_callback_query(call.id)

# ================= Entry Point & Console Dashboard =================

def print_anya_banner(bot_info):
    """Render a clean, colorful console status dashboard inspired by Anya Bot."""
    width = 68
    bot_name = bot_info.first_name if bot_info else "Torrent Stream Bot"
    bot_username = f"@{bot_info.username}" if (bot_info and bot_info.username) else "N/A"
    bot_id = str(bot_info.id) if bot_info else "N/A"
    stats = db.get_stats()
    mode = stats["bot_mode"].upper()
    users_count = stats["total_users"]
    db_conn = "Connected" if db.is_github_connected() else "Offline / Not Set"

    top_border = f"{CYAN}+{'-' * (width - 2)}+{RESET}"
    sep_line   = f"{CYAN}|{'-' * (width - 2)}|{RESET}"
    bot_border = f"{CYAN}+{'-' * (width - 2)}+{RESET}"

    def pad_line(content: str, raw_len: int) -> str:
        pad = width - 4 - raw_len
        return f"{CYAN}|{RESET} {content}{' ' * max(0, pad)} {CYAN}|{RESET}"

    print("\n" + top_border)
    title = f"{BOLD}{WHITE}        TORRENT-TO-DIRECT STREAM BOT (V2.0 CLOUD EDITION){RESET}"
    print(pad_line(title, 57))
    print(sep_line)

    lines = [
        (f"  {CYAN}*{RESET} {BOLD}Bot Name:{RESET}    {GREEN}{bot_name}{RESET}", len(f"  * Bot Name:    {bot_name}")),
        (f"  {CYAN}*{RESET} {BOLD}Username:{RESET}    {GREEN}{bot_username}{RESET}", len(f"  * Username:    {bot_username}")),
        (f"  {CYAN}*{RESET} {BOLD}Bot ID:{RESET}      {WHITE}{bot_id}{RESET}", len(f"  * Bot ID:      {bot_id}")),
        (f"  {CYAN}*{RESET} {BOLD}Admin ID:{RESET}    {YELLOW}{ADMIN_ID}{RESET}", len(f"  * Admin ID:    {ADMIN_ID}")),
        (f"  {CYAN}*{RESET} {BOLD}Bot Mode:{RESET}    {MAGENTA}{mode}{RESET}", len(f"  * Bot Mode:    {mode}")),
        (f"  {CYAN}*{RESET} {BOLD}Cloud DB:{RESET}    {GREEN if 'Connected' in db_conn else RED}{db_conn}{RESET}", len(f"  * Cloud DB:    {db_conn}")),
        (f"  {CYAN}*{RESET} {BOLD}Members:{RESET}     {WHITE}{users_count} registered{RESET}", len(f"  * Members:     {users_count} registered")),
        (f"  {CYAN}*{RESET} {BOLD}Relay Mode:{RESET}  {GREEN}Live RAM Pipe (Buzzheavier & Gofile){RESET}", len("  * Relay Mode:  Live RAM Pipe (Buzzheavier & Gofile)")),
    ]

    for line_str, raw_len in lines:
        print(pad_line(line_str, raw_len))

    print(sep_line)
    footer = f"  {GREEN}[OK]{RESET} {BOLD}Engine ready. Polling Telegram for updates...{RESET}"
    print(pad_line(footer, len("  [OK] Engine ready. Polling Telegram for updates...")))
    print(bot_border + "\n")

def main():
    """Main execution loop with crash protection and auto-reconnect."""
    if os.name == "nt":
        os.system("title Torrent-to-Direct Stream Bot (Active)")

    try:
        bot_info = bot.get_me()
    except Exception as e:
        print(f"\n{RED}[!] ERROR CONNECTING TO TELEGRAM API:{RESET} {e}")
        print(f"{YELLOW}[!] Check your bot_token in .auth file.{RESET}")
        bot_info = None

    print_anya_banner(bot_info)

    # Start Aria2 in background for metadata fetching
    try:
        aria2.start_aria2_daemon()
    except Exception as e:
        print(f"{YELLOW}[!] Notice starting Aria2 daemon: {e}{RESET}")

    # Polling loop with crash immunity
    restart_count = 0
    while True:
        try:
            bot.infinity_polling(timeout=25, long_polling_timeout=20)
        except (KeyboardInterrupt, SystemExit):
            print(f"\n{YELLOW}[!] Bot stopping on user request...{RESET}")
            break
        except Exception as err:
            restart_count += 1
            print(f"\n{RED}[!] POLLING EXCEPTION (#{restart_count}):{RESET} {err}")
            print(f"{YELLOW}[i] Reconnecting in 5 seconds... (CMD window will not close){RESET}")
            time.sleep(5)

    print(f"\n{CYAN}Session ended. Press Enter to exit console.{RESET}")
    try:
        input()
    except Exception:
        pass

if __name__ == "__main__":
    main()
