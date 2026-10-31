import os
import sys
import re
import time
import uuid
import html
import threading

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
sessions: dict[str, dict] = {}
sessions_lock = threading.Lock()

# Admin pending state for input prompts: admin_id -> {"action": "...", "mid": int, ...}
admin_pending: dict[int, dict] = {}

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
            time.sleep(3)
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

# ================= Registration & Verification Flow (Anya Bot Theme) =================

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

def check_channel_membership(user_id: int) -> bool:
    """Verify if user is a member of the required channel if configured."""
    req_channel = db.get_setting("required_channel", "").strip()
    if not req_channel:
        return True
    try:
        member = bot.get_chat_member(req_channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        print(f"[ChannelVerify] Could not check membership: {e}")
        return True

def send_registration_prompt(chat_id: int, user_id: int, reply_to_id: int = None):
    """Send blockquote registration banner."""
    req_channel = db.get_setting("required_channel", "").strip()
    channel_url = db.get_setting("required_channel_url", "").strip() or (f"https://t.me/{req_channel.lstrip('@')}" if req_channel else "")

    kb = types.InlineKeyboardMarkup(row_width=1)
    if channel_url:
        kb.add(types.InlineKeyboardButton("📢 Join Channel", url=channel_url))
    kb.add(types.InlineKeyboardButton("📝 Register Now", callback_data=f"reg:{user_id}"))

    text = (
        "🔐 <b>REGISTRATION REQUIRED</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<blockquote>👋 <b>Welcome to:</b> Torrent Direct Stream Bot</blockquote>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📋 <b>STEPS TO USE BOT</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
    )
    if channel_url:
        text += "<blockquote>1️⃣ Join our official channel</blockquote>\n"
        text += "<blockquote>2️⃣ Click 'Register Now' below</blockquote>\n"
    else:
        text += "<blockquote>1️⃣ Click 'Register Now' to start using the bot</blockquote>\n"
    text += "━━━━━━━━━━━━━━━━━\n🚀 Click the button below to get started!"

    bot.send_message(chat_id, text, reply_markup=kb, reply_to_message_id=reply_to_id)

# ================= Inline Keyboard Generator =================

def build_file_selection_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Build the inline keyboard with file toggles, pagination, and start button."""
    session = sessions.get(session_id)
    if not session:
        return types.InlineKeyboardMarkup()

    files = session["files"]
    selected = session["selected"]
    page = session.get("page", 0)
    total_files = len(files)
    total_pages = max(1, (total_files + FILES_PER_PAGE - 1) // FILES_PER_PAGE)

    page = max(0, min(page, total_pages - 1))
    session["page"] = page

    markup = types.InlineKeyboardMarkup(row_width=1)

    # Slice files for current page
    start_idx = page * FILES_PER_PAGE
    end_idx = min(start_idx + FILES_PER_PAGE, total_files)
    page_files = files[start_idx:end_idx]

    # File buttons
    for f in page_files:
        idx = f["index"]
        is_selected = idx in selected
        icon = "✅" if is_selected else "❌"
        display_name = truncate_text(f["name"], 26)
        size_str = human_readable_size(f["length"])
        btn_text = f"{icon} {idx}. {display_name} ({size_str})"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"tgl:{session_id}:{idx}"))

    # Pagination row
    if total_pages > 1:
        prev_btn = types.InlineKeyboardButton(
            text="◀️ Prev" if page > 0 else "⏹️",
            callback_data=f"pg:{session_id}:{page-1}" if page > 0 else f"noop:{session_id}"
        )
        page_btn = types.InlineKeyboardButton(
            text=f"📄 {page + 1}/{total_pages}",
            callback_data=f"noop:{session_id}"
        )
        next_btn = types.InlineKeyboardButton(
            text="Next ▶️" if page < total_pages - 1 else "⏹️",
            callback_data=f"pg:{session_id}:{page+1}" if page < total_pages - 1 else f"noop:{session_id}"
        )
        markup.row(prev_btn, page_btn, next_btn)

    # Quick selection controls
    select_all_btn = types.InlineKeyboardButton(text="✅ Select All", callback_data=f"all:{session_id}")
    deselect_all_btn = types.InlineKeyboardButton(text="❌ Deselect All", callback_data=f"none:{session_id}")
    markup.row(select_all_btn, deselect_all_btn)

    # Start Download Button
    count_selected = len(selected)
    start_btn = types.InlineKeyboardButton(
        text=f"🚀 Start Downloading ({count_selected} files)",
        callback_data=f"prompt_dest:{session_id}"
    )
    markup.add(start_btn)

    return markup

def build_destination_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Build the destination choice menu (Buzzheavier vs Gofile)."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(text="⚡ Buzzheavier.com (Direct Stream)", callback_data=f"start:{session_id}:buzzheavier"),
        types.InlineKeyboardButton(text="🚀 Gofile.io (Direct Stream)", callback_data=f"start:{session_id}:gofile"),
        types.InlineKeyboardButton(text="◀️ Back to File Selection", callback_data=f"back_select:{session_id}")
    )
    return markup

def build_stop_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Build Cancel/Stop button."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text="🛑 Stop", callback_data=f"stop:{session_id}"))
    return markup

def render_selection_message(session_id: str) -> str:
    """Render the file selection message text."""
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
            safe_edit_message(
                chat_id,
                message_id,
                f"❌ <b>Stream Error for:</b> <code>{html.escape(fname)}</code>\n"
                f"<b>Reason:</b> {html.escape(str(result))}",
                reply_markup=None,
            )
            return

        uploaded_results.append((fname, result, fsize_str))

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
        for i, (fn, url, sz) in enumerate(uploaded_results, 1):
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

    with sessions_lock:
        sessions.pop(session_id, None)

# ================= Telegram Command Handlers =================

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
        "━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Status:</b> {status_label}\n"
        f"📅 <b>Registered:</b> <code>{info.get('registered_at', 'N/A')}</code>\n"
        f"📊 <b>Today's Usage:</b> <code>{quota_str}</code>\n"
        f"📦 <b>Max File Size:</b> <code>{max_sz_str}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{info.get('total_downloads', 0)}</code></blockquote>"
    )
    bot.reply_to(message, text)

# ================= Admin Control Panel (Anya Bot Style) =================

def render_admin_panel() -> tuple[str, types.InlineKeyboardMarkup]:
    """Build the settings control panel text and markup."""
    stats = db.get_stats()
    bot_mode = stats["bot_mode"]

    text = (
        "⚙️ <b>ADMIN CONTROL PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin ID:</b> <code>{ADMIN_ID}</code>\n"
        f"🌐 <b>Bot Mode:</b> <code>{bot_mode.upper()}</code>\n"
        f"👥 <b>Total Users:</b> <code>{stats['total_users']}</code> | ⭐ <b>Premium:</b> <code>{stats['premium_users']}</code>\n"
        f"🚫 <b>Banned Users:</b> <code>{stats['banned_users']}</code> | 📥 <b>Today:</b> <code>{stats['downloads_today']}</code>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🔢 <b>Daily Quota (Regular):</b> <code>{stats['daily_limit']} links/day</code>\n"
        f"📦 <b>Max Size (Regular):</b> <code>{stats['max_size_gb']} GB</code>\n"
        f"📢 <b>Required Channel:</b> <code>{stats['required_channel'] or 'None'}</code>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Tap buttons below to change settings or manage users:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)

    # Mode toggle button
    mode_text = f"🌐 Mode: {bot_mode.title()}"
    kb.add(types.InlineKeyboardButton(mode_text, callback_data="adm_toggle_mode"))

    # Limits
    kb.row(
        types.InlineKeyboardButton(f"🔢 Daily Limit: {stats['daily_limit']}", callback_data="adm_set_limit"),
        types.InlineKeyboardButton(f"📦 Max Size: {stats['max_size_gb']}G", callback_data="adm_set_size")
    )

    # Channel requirement
    kb.row(
        types.InlineKeyboardButton("📢 Set Channel", callback_data="adm_set_channel"),
        types.InlineKeyboardButton("🔗 Channel URL", callback_data="adm_set_url")
    )

    # User management
    kb.row(
        types.InlineKeyboardButton("⭐ Add Premium", callback_data="adm_add_prem"),
        types.InlineKeyboardButton("❌ Del Premium", callback_data="adm_del_prem")
    )
    kb.row(
        types.InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban_user"),
        types.InlineKeyboardButton("🟢 Unban User", callback_data="adm_unban_user")
    )

    # Broadcast & Refresh
    kb.row(
        types.InlineKeyboardButton("📢 Broadcast Message", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh")
    )

    return text, kb

@bot.message_handler(commands=["settings", "panel"])
def handle_settings(message: types.Message):
    """Admin settings command."""
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 <b>Access Denied:</b> This command is restricted to the bot Administrator.")
        return

    text, kb = render_admin_panel()
    bot.send_message(message.chat.id, text, reply_markup=kb)

# ================= Process Torrent & Magnet =================

def process_torrent_source(message: types.Message, magnet_link: str = None, torrent_bytes: bytes = None, filename: str = None):
    """Extract metadata and present the file selector."""
    chat_id = message.chat.id
    user_id = message.from_user.id
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
            return

        with sessions_lock:
            sessions[session_id] = {
                "chat_id": chat_id,
                "user_id": user_id,
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
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"❌ <b>Error processing torrent:</b> {html.escape(str(e))}"
        )

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

# ================= Inline Callback Query Handler =================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: types.CallbackQuery):
    """Handle all inline buttons (registration, files, destination, admin panel, stop)."""
    data = call.data
    user_id = call.from_user.id

    # 1. Registration Callback
    if data.startswith("reg:"):
        target_uid = int(data.split(":")[1])
        if user_id != target_uid:
            bot.answer_callback_query(call.id, "This registration button is not for you.", show_alert=True)
            return

        if not check_channel_membership(user_id):
            channel_url = db.get_setting("required_channel_url", "")
            bot.answer_callback_query(
                call.id,
                "⚠️ Please join our official channel first before registering!",
                show_alert=True
            )
            return

        db.register_user(user_id, call.from_user.username or "")
        bot.answer_callback_query(call.id, "✅ Registration Complete!")
        safe_edit_message(
            call.message.chat.id,
            call.message.message_id,
            "✅ <b>REGISTRATION COMPLETE!</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "🎉 Your account has been registered successfully!\n"
            "You can now send torrent magnet links or <code>.torrent</code> files to stream directly."
        )
        return

    # 2. Admin Panel Callbacks
    if data.startswith("adm_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Unauthorized.", show_alert=True)
            return

        handle_admin_callbacks(call)
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

    chat_id = session["chat_id"]
    message_id = session["message_id"]

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
        safe_edit_message(chat_id, message_id, text, reply_markup=markup)

    elif action == "pg":
        target_page = int(parts[2])
        session["page"] = target_page
        markup = build_file_selection_keyboard(session_id)
        safe_edit_markup(chat_id, message_id, markup)
        bot.answer_callback_query(call.id)

    elif action == "all":
        session["selected"] = set(f["index"] for f in session["files"])
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(chat_id, message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id, "All files selected")

    elif action == "none":
        session["selected"].clear()
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(chat_id, message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id, "All files deselected")

    elif action == "prompt_dest":
        if len(session["selected"]) == 0:
            bot.answer_callback_query(call.id, "⚠️ Please select at least one file to download!", show_alert=True)
            return

        # Calculate total selected size
        selected_files = [f for f in session["files"] if f["index"] in session["selected"]]
        total_selected_size = sum(f["length"] for f in selected_files)

        # Check quota & limits
        ok, reason = db.check_download_limit(user_id, ADMIN_ID, total_selected_size)
        if not ok:
            bot.answer_callback_query(call.id, "Limit reached!", show_alert=False)
            safe_edit_message(chat_id, message_id, reason, reply_markup=None)
            return

        # Prompt user to select destination
        bot.answer_callback_query(call.id)
        dest_kb = build_destination_keyboard(session_id)
        dest_text = (
            "🌐 <b>CHOOSE UPLOAD DESTINATION</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"📁 <b>Torrent:</b> <code>{html.escape(session['torrent_name'])}</code>\n"
            f"📦 <b>Files:</b> {len(selected_files)} selected ({human_readable_size(total_selected_size)})\n\n"
            "<i>Where would you like to stream your files?</i>"
        )
        safe_edit_message(chat_id, message_id, dest_text, reply_markup=dest_kb)

    elif action == "back_select":
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(chat_id, message_id, text, reply_markup=markup)
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

# ================= Admin Panel Callback Handler =================

def handle_admin_callbacks(call: types.CallbackQuery):
    """Admin settings buttons routing."""
    data = call.data
    cid = call.message.chat.id
    mid = call.message.message_id
    admin_id = call.from_user.id

    if data == "adm_refresh":
        text, kb = render_admin_panel()
        safe_edit_message(cid, mid, text, reply_markup=kb)
        bot.answer_callback_query(call.id, "Refreshed!")

    elif data == "adm_toggle_mode":
        current = db.get_setting("bot_mode", "public")
        modes = ["public", "premium", "admin_only"]
        new_mode = modes[(modes.index(current) + 1) % len(modes)]
        db.set_setting("bot_mode", new_mode)
        text, kb = render_admin_panel()
        safe_edit_message(cid, mid, text, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Mode switched to {new_mode.upper()}")

    elif data == "adm_set_limit":
        admin_pending[admin_id] = {"action": "set_limit", "mid": mid}
        bot.send_message(cid, "✏️ <b>Enter new daily limit for regular users</b> (e.g. <code>10</code>):")
        bot.answer_callback_query(call.id)

    elif data == "adm_set_size":
        admin_pending[admin_id] = {"action": "set_size", "mid": mid}
        bot.send_message(cid, "✏️ <b>Enter max torrent size in GB for regular users</b> (e.g. <code>50</code>):")
        bot.answer_callback_query(call.id)

    elif data == "adm_set_channel":
        admin_pending[admin_id] = {"action": "set_channel", "mid": mid}
        bot.send_message(cid, "✏️ <b>Enter required channel username or ID</b> (e.g. <code>@MyChannel</code> or <code>-100123456789</code>, or send <code>none</code> to disable):")
        bot.answer_callback_query(call.id)

    elif data == "adm_set_url":
        admin_pending[admin_id] = {"action": "set_url", "mid": mid}
        bot.send_message(cid, "✏️ <b>Enter invite link for the channel</b> (e.g. <code>https://t.me/MyChannel</code>):")
        bot.answer_callback_query(call.id)

    elif data == "adm_add_prem":
        admin_pending[admin_id] = {"action": "add_prem", "mid": mid}
        bot.send_message(cid, "⭐ <b>Send the User ID to grant Premium access:</b>")
        bot.answer_callback_query(call.id)

    elif data == "adm_del_prem":
        admin_pending[admin_id] = {"action": "del_prem", "mid": mid}
        bot.send_message(cid, "❌ <b>Send the User ID to revoke Premium access:</b>")
        bot.answer_callback_query(call.id)

    elif data == "adm_ban_user":
        admin_pending[admin_id] = {"action": "ban_user", "mid": mid}
        bot.send_message(cid, "🚫 <b>Send the User ID to Ban:</b>")
        bot.answer_callback_query(call.id)

    elif data == "adm_unban_user":
        admin_pending[admin_id] = {"action": "unban_user", "mid": mid}
        bot.send_message(cid, "🟢 <b>Send the User ID to Unban:</b>")
        bot.answer_callback_query(call.id)

    elif data == "adm_broadcast":
        admin_pending[admin_id] = {"action": "broadcast", "mid": mid}
        bot.send_message(cid, "📢 <b>Send the message text you want to broadcast to ALL registered users:</b>")
        bot.answer_callback_query(call.id)

# ================= Admin Input Handler =================

@bot.message_handler(func=lambda msg: msg.from_user.id == ADMIN_ID and msg.from_user.id in admin_pending)
def handle_admin_text_inputs(message: types.Message):
    """Handle text input sent by admin for settings changes."""
    admin_id = message.from_user.id
    pending = admin_pending.pop(admin_id, {})
    action = pending.get("action")
    val = (message.text or "").strip()

    if action == "set_limit":
        if val.isdigit():
            db.set_setting("daily_limit_regular", int(val))
            bot.reply_to(message, f"✅ Daily limit updated to: <b>{val}</b> links/day")
        else:
            bot.reply_to(message, "❌ Invalid number.")

    elif action == "set_size":
        try:
            sz = float(val)
            db.set_setting("max_size_gb_regular", sz)
            bot.reply_to(message, f"✅ Max file size updated to: <b>{sz}</b> GB")
        except ValueError:
            bot.reply_to(message, "❌ Invalid number.")

    elif action == "set_channel":
        if val.lower() == "none" or val == "0" or val == "":
            db.set_setting("required_channel", "")
            bot.reply_to(message, "✅ Channel requirement disabled.")
        else:
            db.set_setting("required_channel", val)
            bot.reply_to(message, f"✅ Required channel set to: <code>{val}</code>")

    elif action == "set_url":
        db.set_setting("required_channel_url", val)
        bot.reply_to(message, f"✅ Channel invite URL set to: <code>{val}</code>")

    elif action == "add_prem":
        if val.isdigit():
            uid = int(val)
            db.set_premium(uid, True)
            bot.reply_to(message, f"⭐ User <code>{uid}</code> has been upgraded to <b>Premium</b>!")
            try:
                bot.send_message(uid, "🎉 <b>Congratulations!</b> You have been upgraded to <b>Premium</b>! You now have unlimited downloads and file sizes.")
            except Exception:
                pass
        else:
            bot.reply_to(message, "❌ Invalid User ID.")

    elif action == "del_prem":
        if val.isdigit():
            uid = int(val)
            db.set_premium(uid, False)
            bot.reply_to(message, f"❌ Premium revoked for user <code>{uid}</code>.")
        else:
            bot.reply_to(message, "❌ Invalid User ID.")

    elif action == "ban_user":
        if val.isdigit():
            uid = int(val)
            db.set_banned(uid, True)
            bot.reply_to(message, f"🚫 User <code>{uid}</code> has been <b>Banned</b>.")
        else:
            bot.reply_to(message, "❌ Invalid User ID.")

    elif action == "unban_user":
        if val.isdigit():
            uid = int(val)
            db.set_banned(uid, False)
            bot.reply_to(message, f"🟢 User <code>{uid}</code> has been <b>Unbanned</b>.")
        else:
            bot.reply_to(message, "❌ Invalid User ID.")

    elif action == "broadcast":
        all_users = db.get_all_user_ids()
        bot.reply_to(message, f"📢 Broadcasting message to <b>{len(all_users)}</b> users...")
        sent_count = 0
        for uid in all_users:
            try:
                bot.send_message(uid, f"📢 <b>ANNOUNCEMENT:</b>\n\n{val}")
                sent_count += 1
                time.sleep(0.05)  # rate limit safety
            except Exception:
                pass
        bot.send_message(message.chat.id, f"✅ Broadcast complete! Delivered to <b>{sent_count}/{len(all_users)}</b> users.")

# ================= Entry Point (Anya Bot Theme & Crash Protection) =================

def print_anya_banner(bot_info):
    """Render a clean, colorful console status dashboard inspired by Anya Bot."""
    width = 68
    bot_name = bot_info.first_name if bot_info else "Torrent Stream Bot"
    bot_username = f"@{bot_info.username}" if (bot_info and bot_info.username) else "N/A"
    bot_id = str(bot_info.id) if bot_info else "N/A"
    mode = db.get_setting("bot_mode", "public").upper()
    users_count = len(db.get_all_user_ids())

    print()
    print(f"{CYAN}{'=' * width}{RESET}")
    print(f"{BOLD}{WHITE}             [+]  T O R R E N T   S T R E A M   B O T  [+]{RESET}")
    print(f"{MAGENTA}                   Pure RAM Streaming Engine v2.0{RESET}")
    print(f"{DIM}{WHITE}                      Buzzheavier & Gofile.io{RESET}")
    print(f"{CYAN}{'=' * width}{RESET}")
    print(f"\n{CYAN}{'-' * width}{RESET}")
    print(f"  {BOLD}{WHITE}SYSTEM & BOT STATUS{RESET}")
    print(f"{CYAN}{'-' * width}{RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Bot Name:{RESET}       {GREEN}{bot_name}{RESET} ({CYAN}{bot_username}{RESET})")
    print(f"  {CYAN}[•]{RESET} {WHITE}Bot ID:{RESET}         {YELLOW}{bot_id}{RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Admin ID:{RESET}       {YELLOW}{ADMIN_ID}{RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Bot Mode:{RESET}       {MAGENTA}{mode}{RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}RAM Streamer:{RESET}   {WHITE}Node.js WebTorrent (In-RAM Buffer){RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Metadata:{RESET}       {WHITE}Aria2c RPC Daemon (Port {ARIA2_PORT}){RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Cloud Hosts:{RESET}    {CYAN}Buzzheavier.com{RESET} & {CYAN}Gofile.io{RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Disk Footprint:{RESET} {GREEN}0 BYTES (Pure RAM Relay){RESET}")
    print(f"  {CYAN}[•]{RESET} {WHITE}Registered:{RESET}     {WHITE}{users_count} Users (database.json){RESET}")
    print(f"{CYAN}{'-' * width}{RESET}")
    print(f"  {GREEN}[✓]{RESET} {BOLD}{GREEN}Bot is ONLINE and listening for incoming messages...{RESET}")
    print(f"  {DIM}(Press Ctrl+C to stop){RESET}")
    print(f"{CYAN}{'-' * width}{RESET}\n")

if __name__ == "__main__":
    if os.name == "nt":
        os.system("title Torrent Stream Bot v2.0 - [ONLINE]")

    try:
        # Fetch bot credentials from Telegram
        bot_info = None
        try:
            bot_info = bot.get_me()
        except Exception as e:
            print(f"{YELLOW}[!] Notice: Could not query get_me() immediately: {e}{RESET}")

        print_anya_banner(bot_info)

        # Polling loop with resilient auto-reconnect
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
            except KeyboardInterrupt:
                print(f"\n{YELLOW}[*] Bot stopped by user (Ctrl+C).{RESET}")
                time.sleep(1)
                break
            except Exception as poll_err:
                print(f"\n{RED}[!] Polling glitch: {poll_err}{RESET}")
                print(f"{YELLOW}[*] Auto-reconnecting in 5 seconds...{RESET}")
                time.sleep(5)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}[*] Bot stopped by user.{RESET}")
    except Exception as fatal_err:
        print("\n" + RED + "=" * 70 + RESET)
        print(f"{BOLD}{RED} [!] CRITICAL ERROR OCCURRED:{RESET}")
        print(RED + "=" * 70 + RESET)
        import traceback
        traceback.print_exc()
        print(RED + "=" * 70 + RESET)
        print(f"{YELLOW} [!] The console window will remain OPEN so you can see this error.{RESET}")
        print(RED + "=" * 70 + RESET)
        while True:
            try:
                input("\nPress Enter to exit...")
                break
            except Exception:
                time.sleep(5)
