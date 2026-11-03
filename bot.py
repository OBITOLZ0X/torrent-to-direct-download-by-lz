import os
import sys
import re
import time
import uuid
import html
import json
import requests
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
    AUTH_DATA,
    AUTH_FILE,
    DOWNLOADS_DIR,
    METADATA_DIR,
    PROGRESS_UPDATE_INTERVAL,
    FILES_PER_PAGE,
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
    safe_remove,
)
from stream_manager import StreamManager, StreamController, parse_torrent
from database import db
from github_client import GitHubClient

SEP = "━━━━━━━━━━━━━━━━━━━━━"

# Custom animated emojis (Telegram Sticker Set: SoLo_HaMiD) with fallback standard emojis
EMOJI_STREAM_UP   = '<tg-emoji emoji-id="5116395218882528029">⏫</tg-emoji>'
EMOJI_STREAM_DOWN = '<tg-emoji emoji-id="5116204921766544244">⏬</tg-emoji>'
EMOJI_SPEED       = '<tg-emoji emoji-id="5116093437300442328">⚡️</tg-emoji>'
EMOJI_CLOCK       = '<tg-emoji emoji-id="5123230779593196220">⏰</tg-emoji>'
EMOJI_PEERS       = '<tg-emoji emoji-id="5121007227779416740">📶</tg-emoji>'
EMOJI_GLOBE       = '<tg-emoji emoji-id="4906943755644306322">🌐</tg-emoji>'
EMOJI_PROGRESS    = '<tg-emoji emoji-id="4904626998745237074">🔋</tg-emoji>'
EMOJI_CHECK       = '<tg-emoji emoji-id="5118861066981344121">✅</tg-emoji>'
EMOJI_FIRE        = '<tg-emoji emoji-id="5116414868357907335">🔥</tg-emoji>'
EMOJI_PIN         = '<tg-emoji emoji-id="4917985158784615388">📌</tg-emoji>'
EMOJI_USER        = '<tg-emoji emoji-id="5116582462276764538">👤</tg-emoji>'
EMOJI_LINK        = '<tg-emoji emoji-id="4916086774649848789">🔗</tg-emoji>'
EMOJI_GEAR        = '<tg-emoji emoji-id="4904936030232117798">⚙️</tg-emoji>'
EMOJI_ALERT       = '<tg-emoji emoji-id="5120722716260828125">🚨</tg-emoji>'
EMOJI_HEART       = '<tg-emoji emoji-id="5116368680279606270">♥️</tg-emoji>'
EMOJI_WAVE        = '<tg-emoji emoji-id="4918354603281482671">👋</tg-emoji>'
EMOJI_SPARK       = '<tg-emoji emoji-id="5104960787579929462">✨</tg-emoji>'
EMOJI_LOCK        = '<tg-emoji emoji-id="4904500559203009298">🔒</tg-emoji>'
EMOJI_PAUSE       = '⏸️'

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

# Initialize Telegram bot and managers with crash protection & resilient network adapters
try:
    import logging
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from telebot import apihelper

    # Suppress noisy telebot disconnect tracebacks in terminal
    telebot.logger.setLevel(logging.CRITICAL)

    # Configure resilient HTTP session with automatic retries for Telegram API
    retry_strategy = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=25, pool_maxsize=25)
    telegram_session = requests.Session()
    telegram_session.mount("https://", adapter)
    telegram_session.mount("http://", adapter)
    apihelper.session = telegram_session
    apihelper.SESSION_TIME_TO_LIVE = 60  # Recycle idle sockets before WinError 10054 drops them
    apihelper.RETRY_ON_ERROR = True

    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

    # Resilient wrapper for answer_callback_query against WinError 10054
    _orig_answer_callback = bot.answer_callback_query
    def _safe_answer_callback_wrapper(*args, **kwargs):
        for _att in range(2):
            try:
                return _orig_answer_callback(*args, **kwargs)
            except ApiTelegramException as _api_err:
                if "query is too old" in str(_api_err).lower() or "query_id_invalid" in str(_api_err).lower():
                    return False
                break
            except Exception:
                if _att == 0:
                    time.sleep(0.3)
                else:
                    return False
    bot.answer_callback_query = _safe_answer_callback_wrapper

    # Resilient wrapper for reply_to against WinError 10054
    _orig_reply_to = bot.reply_to
    def _safe_reply_to_wrapper(*args, **kwargs):
        for _att in range(3):
            try:
                return _orig_reply_to(*args, **kwargs)
            except ApiTelegramException as _api_err:
                if "flood" in str(_api_err).lower():
                    time.sleep(2)
                else:
                    raise
            except Exception:
                if _att < 2:
                    time.sleep(0.5)
                else:
                    raise
    bot.reply_to = _safe_reply_to_wrapper

    # Resilient wrapper for send_message against WinError 10054
    _orig_send_message = bot.send_message
    def _safe_send_message_wrapper(*args, **kwargs):
        for _att in range(3):
            try:
                return _orig_send_message(*args, **kwargs)
            except ApiTelegramException as _api_err:
                if "flood" in str(_api_err).lower():
                    time.sleep(2)
                else:
                    raise
            except Exception:
                if _att < 2:
                    time.sleep(0.5)
                else:
                    raise
    bot.send_message = _safe_send_message_wrapper

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

def is_user_transferring(user_id: int) -> bool:
    """Check if the user already has an active live transfer in progress."""
    with sessions_lock:
        for s in sessions.values():
            if s.get("user_id") == user_id and s.get("status") == "running":
                cancel_ev = s.get("cancel_event")
                if not (cancel_ev and cancel_ev.is_set()):
                    return True
    return False

def cancel_all_non_admin_transfers() -> int:
    """
    Cancel and terminate all active transfers for non-admin users when Admin Mode is activated.
    Updates in-progress messages with a sleek suspension card and sends a direct notice.
    """
    with sessions_lock:
        active_sessions = list(sessions.items())

    stopped_count = 0
    for sess_id, s in active_sessions:
        u_id = s.get("user_id")
        if u_id and u_id != ADMIN_ID:
            s["status"] = "admin_stopped"

            # 1. Trigger cancellation & stop streamer
            cancel_ev = s.get("cancel_event")
            if cancel_ev:
                cancel_ev.set()

            ctrl = s.get("controller")
            if ctrl:
                try:
                    ctrl.stop()
                except Exception:
                    pass

            # 2. Update progress card message
            chat_id = s.get("chat_id")
            message_id = s.get("message_id")
            torrent_name = s.get("torrent_name", "Torrent")

            stopped_card = (
                f"🛡️ <b>TRANSFER HALTED (ADMIN MODE)</b>\n"
                f"{SEP}\n"
                f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                f"🔒 <b>Status:</b> <b>Transfer stopped by system</b>\n"
                f"⚠️ <b>Reason:</b> Bot entered <b>Admin Maintenance Mode</b>.</blockquote>\n"
                f"{SEP}\n"
                f"<i>⚙️ All public conversions and transfers have been halted temporarily for administrative maintenance. You may restart your transfer once public service resumes.</i>"
            )

            if chat_id and message_id:
                safe_edit_message(chat_id, message_id, stopped_card, reply_markup=None)
                try:
                    bot.send_message(
                        chat_id,
                        f"🛡️ <b>ADMIN MODE NOTIFICATION</b>\n{SEP}\n"
                        f"<blockquote>Your active transfer for <code>{html.escape(torrent_name)}</code> was terminated because the bot was switched to <b>Admin Mode</b>.\n\n"
                        f"<i>Please try again once regular public service resumes.</i></blockquote>\n{SEP}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            stopped_count += 1

    return stopped_count

# Admin pending state for reply-based input prompts: admin_id -> {"action": "...", "mid": int, "cid": int, "return_menu": "..."}
admin_pending: Dict[int, dict] = {}

# Cached admin contact string
_admin_contact_cache: Optional[str] = None
_admin_username_cache: Optional[str] = None

def get_admin_contact() -> str:
    """Dynamically retrieve admin username or mention from Telegram API using ADMIN_ID."""
    global _admin_contact_cache
    if _admin_contact_cache:
        return _admin_contact_cache
    try:
        chat = bot.get_chat(ADMIN_ID)
        if chat.username:
            _admin_contact_cache = f"@{chat.username}"
            return _admin_contact_cache
        elif chat.first_name:
            _admin_contact_cache = f'<a href="tg://user?id={ADMIN_ID}">{html.escape(chat.first_name)}</a>'
            return _admin_contact_cache
    except Exception:
        pass
    return f'<a href="tg://user?id={ADMIN_ID}">the Owner</a>'

def get_admin_username() -> str:
    """Retrieve admin username (e.g. @OBITOLZ) for console and status displays."""
    global _admin_username_cache
    if _admin_username_cache:
        return _admin_username_cache

    saved = AUTH_DATA.get("admin_username")
    if saved:
        _admin_username_cache = saved
        return _admin_username_cache

    try:
        chat = bot.get_chat(ADMIN_ID)
        if chat and chat.username:
            _admin_username_cache = f"@{chat.username}"
            try:
                if os.path.exists(AUTH_FILE):
                    with open(AUTH_FILE, "r", encoding="utf-8") as f:
                        cur_auth = json.load(f)
                    cur_auth["admin_username"] = _admin_username_cache
                    with open(AUTH_FILE, "w", encoding="utf-8") as f:
                        json.dump(cur_auth, f, indent=2)
            except Exception:
                pass
            return _admin_username_cache
        elif chat and chat.first_name:
            _admin_username_cache = chat.first_name
            return _admin_username_cache
    except Exception:
        pass

    _admin_username_cache = str(ADMIN_ID)
    return _admin_username_cache

def safe_edit_message(chat_id: int, message_id: int, text: str, reply_markup=None):
    """Safely edit message text handling rate limits, unmodified text, and network glitches."""
    for attempt in range(3):
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return
        except ApiTelegramException as e:
            if "message is not modified" in str(e).lower():
                return
            elif "flood" in str(e).lower() or "too many requests" in str(e).lower():
                time.sleep(2)
            else:
                return
        except Exception as e:
            if attempt < 2:
                time.sleep(0.4)
            else:
                pass

def safe_edit_markup(chat_id: int, message_id: int, reply_markup):
    """Safely edit reply markup handling transient connection glitches."""
    for attempt in range(2):
        try:
            bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except ApiTelegramException as e:
            if "message is not modified" in str(e).lower():
                return
            elif "flood" in str(e).lower() or "too many requests" in str(e).lower():
                time.sleep(2)
            else:
                return
        except Exception:
            if attempt == 0:
                time.sleep(0.3)

def cleanup_torrent_file(filepath: Optional[str]):
    """Clean up downloaded .torrent metadata files immediately."""
    if not filepath:
        return
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
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
    except ApiTelegramException as e:
        err = str(e).lower()
        if "user not found" in err or "chat not found" in err:
            return False
        print(f"[ChannelVerify] Telegram API error: {e}")
        return False
    except Exception as e:
        print(f"[ChannelVerify] Could not check membership: {e}")
        return False

def build_registration_view(user_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """
    Construct dynamic registration view and inline keyboard based on user's current progress:
    - If channel needed and not joined: show join button and verify button.
    - If channel joined (or not needed) and not registered: show only Register Now button.
    - If neither: show both Register Now and channel buttons.
    """
    bot_channel = db.get_setting("bot_channel", "").strip()
    invite_link = db.get_setting("channel_invite", "").strip() or (f"https://t.me/{bot_channel.lstrip('@')}" if bot_channel else "")
    channel_required = bool(bot_channel)
    is_channel_member = check_channel_membership(user_id) if channel_required else True
    is_reg = db.is_registered(user_id)

    kb = types.InlineKeyboardMarkup(row_width=1)

    # 1. User registered, but channel not joined yet
    if is_reg and channel_required and not is_channel_member:
        text = (
            f"{EMOJI_LOCK} <b>CHANNEL MEMBERSHIP REQUIRED</b>\n"
            f"{SEP}\n"
            f"<blockquote>{EMOJI_CHECK} <b>Registration:</b> Complete\n"
            f"{EMOJI_ALERT} <b>Channel:</b> Not a member yet</blockquote>\n"
            f"{SEP}\n"
            f"<blockquote>1️⃣ Join our official channel (link below)\n"
            f"2️⃣ Click <b>Verify Channel Membership</b> to confirm</blockquote>\n"
            f"{SEP}\n"
            f"🚀 <i>Join the channel and tap verify to start!</i>"
        )
        if invite_link:
            kb.add(types.InlineKeyboardButton("📢 Join Channel", url=invite_link))
        kb.add(types.InlineKeyboardButton("✅ Verify Channel Membership", callback_data=f"verify_channel:{user_id}"))
        return text, kb

    # 2. User joined channel (or no channel required), but not registered yet
    if is_channel_member and not is_reg:
        chan_line = f"<blockquote>{EMOJI_CHECK} <b>Channel Status:</b> Verified Member</blockquote>\n" if channel_required else ""
        text = (
            f"{EMOJI_LOCK} <b>REGISTRATION REQUIRED</b>\n"
            f"{SEP}\n"
            f"<blockquote>{EMOJI_WAVE} Welcome to <b>tDownUp</b>!</blockquote>\n"
            f"{chan_line}"
            f"<blockquote>📝 <b>Final Step:</b> Register your account below</blockquote>\n"
            f"{SEP}\n"
            f"🚀 <i>Tap 'Register Now' to complete setup!</i>"
        )
        kb.add(types.InlineKeyboardButton("📝 Register Now", callback_data=f"reg:{user_id}"))
        return text, kb

    # 3. Neither step completed (not registered and not in channel)
    text = (
        f"{EMOJI_LOCK} <b>REGISTRATION REQUIRED</b>\n"
        f"{SEP}\n"
        f"<blockquote>{EMOJI_WAVE} Welcome to <b>tDownUp</b>!</blockquote>\n"
        f"{SEP}\n"
        f"📋 <b>STEPS TO USE BOT</b>\n"
        f"{SEP}\n"
        f"<blockquote>1️⃣ Register your account\n"
        f"2️⃣ Join our official channel</blockquote>\n"
        f"{SEP}\n"
        f"🚀 <i>Click the buttons below to get started!</i>"
    )
    kb.add(types.InlineKeyboardButton("📝 Register Now", callback_data=f"reg:{user_id}"))
    if invite_link:
        kb.add(types.InlineKeyboardButton("📢 Join Channel", url=invite_link))
    kb.add(types.InlineKeyboardButton("✅ Verify Channel Membership", callback_data=f"verify_channel:{user_id}"))
    return text, kb

def send_registration_prompt(chat_id: int, user_id: int, reply_to_id: int = None):
    """Send dynamic blockquote registration banner."""
    text, kb = build_registration_view(user_id)
    if reply_to_id:
        bot.send_message(chat_id, text, reply_markup=kb, reply_to_message_id=reply_to_id, disable_web_page_preview=True)
    else:
        bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)

def check_user_access(message: types.Message) -> bool:
    """Validate user registration, channel membership, ban status, and bot mode."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id == ADMIN_ID:
        return True

    allowed, reason = db.check_access(user_id, ADMIN_ID)
    if not allowed:
        if reason == "REGISTRATION_REQUIRED":
            send_registration_prompt(chat_id, user_id, message.id)
            return False
        bot.reply_to(message, reason)
        return False

    # Also verify channel membership if channel is configured
    if not check_channel_membership(user_id):
        send_registration_prompt(chat_id, user_id, message.id)
        return False

    return True

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
    """Construct keyboard allowing user to pick Buzzheavier, Gofile, or VikingFile."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🐝 Buzzheavier.com", callback_data=f"start:{session_id}:buzzheavier"),
        types.InlineKeyboardButton("📁 Gofile.io", callback_data=f"start:{session_id}:gofile"),
        types.InlineKeyboardButton("⚔️ VikingFile.com", callback_data=f"start:{session_id}:vikingfile"),
        types.InlineKeyboardButton("← Back to Selection", callback_data=f"back_select:{session_id}")
    )
    return kb

def build_streaming_keyboard(session_id: str, is_paused: bool = False, destination: str = "") -> types.InlineKeyboardMarkup:
    """Construct keyboard with Pause / Continue and Stop buttons during live transfer."""
    session = sessions.get(session_id)
    dest = destination or (session.get("destination", "") if session else "")
    kb = types.InlineKeyboardMarkup(row_width=2)

    if dest == "vikingfile":
        if is_paused:
            kb.add(
                types.InlineKeyboardButton("▶️ Continue", callback_data=f"resume:{session_id}"),
                types.InlineKeyboardButton("🛑 Stop Transfer", callback_data=f"confirm_stop:{session_id}")
            )
        else:
            kb.add(
                types.InlineKeyboardButton("⏸️ Pause", callback_data=f"pause:{session_id}"),
                types.InlineKeyboardButton("🛑 Stop Transfer", callback_data=f"confirm_stop:{session_id}")
            )
    else:
        # Hosts that do not support resume (Buzzheavier, Gofile) only show Stop
        kb.add(types.InlineKeyboardButton("🛑 Stop Transfer", callback_data=f"confirm_stop:{session_id}"))
    return kb

def build_stop_confirm_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Construct confirmation keyboard when user clicks Stop."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Yes, Stop", callback_data=f"do_stop:{session_id}"),
        types.InlineKeyboardButton("❌ No, Keep Transferring", callback_data=f"cancel_stop:{session_id}")
    )
    return kb

def build_stop_keyboard(session_id: str) -> types.InlineKeyboardMarkup:
    """Backward-compatible helper."""
    return build_streaming_keyboard(session_id)

def render_selection_message(session_id: str) -> str:
    """Render the files selection overview message with animated emojis and clean cards."""
    session = sessions.get(session_id)
    if not session:
        return "Session expired."
    name = session["torrent_name"]
    total_size = human_readable_size(session["total_size"])
    file_count = len(session["files"])
    selected_count = len(session["selected"])

    return (
        f"{EMOJI_STREAM_DOWN} <b>TORRENT FILE SELECTION</b>\n"
        f"{SEP}\n"
        f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(name)}</code>\n"
        f"📦 <b>Total Size:</b> <code>{total_size}</code>\n"
        f"📑 <b>Total Files:</b> <code>{file_count}</code>\n"
        f"{EMOJI_CHECK} <b>Selected:</b> <code>{selected_count} files</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Select the files you want to transfer below using toggles.\n"
        f"When ready, tap <b>Start Downloading</b>.</i>"
    )

# ================= Public Donation Card =================

def render_donation_card() -> str:
    """Build the public donation card with configured payment methods."""
    paypal = db.get_setting("donation_paypal", "").strip()
    binance = db.get_setting("donation_binance_id", "").strip()
    trc20 = db.get_setting("donation_trc20", "").strip()
    bep20 = db.get_setting("donation_bep20", "").strip()
    sol = db.get_setting("donation_solana", "").strip()
    ton = db.get_setting("donation_ton", "").strip()
    erc20 = db.get_setting("donation_erc20", "").strip()
    arb = db.get_setting("donation_arbitrum", "").strip()
    opbnb = db.get_setting("donation_opbnb", "").strip()
    note = db.get_setting("donation_note", "").strip()

    sections = []
    if paypal:
        sections.append(f"🅿️ <b>PayPal:</b>\n<code>{html.escape(paypal)}</code>")
    if binance:
        sections.append(f"🟡 <b>Binance Pay ID:</b>\n<code>{html.escape(binance)}</code>")
    if trc20:
        sections.append(f"🔴 <b>USDT (TRC20):</b>\n<code>{html.escape(trc20)}</code>")
    if bep20:
        sections.append(f"🟡 <b>BNB Smart Chain (BEP20):</b>\n<code>{html.escape(bep20)}</code>")
    if sol:
        sections.append(f"🟣 <b>Solana (SOL):</b>\n<code>{html.escape(sol)}</code>")
    if ton:
        sections.append(f"💎 <b>The Open Network (TON):</b>\n<code>{html.escape(ton)}</code>")
    if erc20:
        sections.append(f"🔷 <b>Ethereum (ERC20):</b>\n<code>{html.escape(erc20)}</code>")
    if arb:
        sections.append(f"🔵 <b>Arbitrum One:</b>\n<code>{html.escape(arb)}</code>")
    if opbnb:
        sections.append(f"🟡 <b>opBNB:</b>\n<code>{html.escape(opbnb)}</code>")

    if not sections:
        admin_contact = get_admin_contact()
        return (
            f"{EMOJI_HEART} <b>SUPPORT & DONATE</b>\n{SEP}\n"
            f"<blockquote>Thank you for your generosity! Donation methods are currently being updated.\n\n"
            f"Please reach out directly to {admin_contact} to support the project.</blockquote>\n{SEP}"
        )

    text = (
        f"{EMOJI_HEART} <b>SUPPORT & DONATE</b>\n{SEP}\n"
        f"<blockquote>If you enjoy using this bot, please consider supporting the project to keep the servers, high-speed bandwidth, and development alive!</blockquote>\n\n"
        f"<b>💳 AVAILABLE PAYMENT METHODS:</b>\n{SEP}\n" +
        "\n\n".join(sections) + "\n" +
        f"{SEP}\n"
    )
    if note:
        text += f"<blockquote>ℹ️ <b>Note:</b> {html.escape(note)}</blockquote>\n{SEP}\n"

    text += "<i>💡 Tap any address or ID to copy it directly. Thank you for your support! 🙏</i>"
    return text

def build_links_presentation(uploaded_results: list, max_inline: int = 10) -> str:
    """
    Format direct download links inside a single clean expandable blockquote.
    Telegram collapses this automatically so messages remain sleek even with many episodes.
    """
    total_count = len(uploaded_results)
    inline_items = uploaded_results[:max_inline]

    lines = []
    for i, (fn, url, sz, _) in enumerate(inline_items, 1):
        lines.append(f"<b>{i}.</b> <a href=\"{url}\">{html.escape(fn)}</a> (<code>{sz}</code>)")

    if total_count > max_inline:
        lines.append(f"<i>... and {total_count - max_inline} more file(s) (see attached document below).</i>")

    return "<blockquote expandable>\n" + "\n".join(lines) + "\n</blockquote>"

def build_links_text_file(torrent_name: str, host: str, uploaded_results: list) -> bytes:
    """Generate a clean .txt file with all uploaded file links."""
    total_sz = human_readable_size(sum(sz_b for (_, _, _, sz_b) in uploaded_results))
    content = [
        "============================================================",
        f"  tDownUp - UPLOADED FILE LINKS",
        f"  Torrent: {torrent_name}",
        f"  Host:    {host}",
        f"  Files:   {len(uploaded_results)} files ({total_sz})",
        f"  Date:    {time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "============================================================\n",
        "--- FILES LIST ---"
    ]
    for i, (fn, url, sz, _) in enumerate(uploaded_results, 1):
        content.append(f"{i}. {fn} ({sz})")
        content.append(f"   Link: {url}\n")

    content.append("============================================================")
    content.append("--- ALL LINKS ---")
    content.append("============================================================")
    for _, url, _, _ in uploaded_results:
        content.append(url)

    return "\n".join(content).encode("utf-8")

def send_unified_completion_message(chat_id: Any, torrent_name: str, dest_display: str, user_tag: str, uploaded_results: list, is_channel: bool = False) -> bool:
    """
    Sends a single merged Rich Message containing:
    1. The batch download text document (_Links.txt) embedded at the top of the bubble.
    2. The full completion status card.
    3. The native bordered & striped table (Name | Size | Download) inside an expandable details block.
    Telegram natively handles smooth vertical & horizontal scrolling for this table without bot pagination buttons.
    """
    try:
        total_uploaded_bytes = sum(sz_b for (_, _, _, sz_b) in uploaded_results)
        total_sz_str = human_readable_size(total_uploaded_bytes)

        # Prepare table preview (Telegram limits Rich Messages to 32,768 chars and 500 blocks)
        # Cap table preview to 100 rows so large batches (e.g. 500-1000 files) never fail or exceed limits.
        # All 1000 files are always fully preserved in the embedded _Links.txt file at the top!
        max_table_rows = 100
        table_items = uploaded_results[:max_table_rows]

        table_cells = [
            [
                {"text": "Name", "is_header": True, "align": "center"},
                {"text": "Size", "is_header": True, "align": "center"},
                {"text": "Download", "is_header": True, "align": "center"}
            ]
        ]
        for fname, url, sz_str, _ in table_items:
            table_cells.append([
                {"text": fname, "align": "left"},
                {"text": sz_str, "align": "center"},
                {"text": {"type": "url", "text": "Download 📥", "url": url}, "align": "center"}
            ])

        if len(uploaded_results) > max_table_rows:
            remaining = len(uploaded_results) - max_table_rows
            table_cells.append([
                {"text": f"... +{remaining} more files", "align": "left"},
                {"text": "Full Batch", "align": "center"},
                {"text": "In File ⬆️", "align": "center"}
            ])

        summary_text = (
            f"📁 Download Links ({len(uploaded_results)} Files)"
            if len(uploaded_results) <= max_table_rows
            else f"📁 Download Links ({len(uploaded_results)} Files - Previewing {max_table_rows})"
        )

        blocks = []
        files = None
        has_batch_doc = len(uploaded_results) > 1

        # Only attach the _Links.txt document file if there are 2 or more files in the batch
        if has_batch_doc:
            clean_doc_name = re.sub(r'[\\/*?:"<>|]', "_", torrent_name)[:45].strip() + "_Links.txt"
            links_doc = build_links_text_file(torrent_name, dest_display, uploaded_results)
            blocks.append({
                "type": "document",
                "document": {
                    "type": "document",
                    "media": "attach://doc"
                }
            })
            files = {
                'doc': (clean_doc_name, links_doc, 'text/plain')
            }

        blocks.append({
            "type": "paragraph",
            "text": [
                "🎉 ", {"type": "bold", "text": "TORRENT TRANSFER COMPLETED\n"},
                f"{SEP}\n",
                "📁 ", {"type": "bold", "text": "Torrent: "}, torrent_name + "\n",
                "📦 ", {"type": "bold", "text": "Total Size: "}, f"{total_sz_str} ({len(uploaded_results)} files)\n",
                "🌐 ", {"type": "bold", "text": "Host: "}, dest_display + "\n",
                "👤 ", {"type": "bold", "text": "Uploaded By: "}, user_tag + "\n",
                f"{SEP}"
            ]
        })

        blocks.append({
            "type": "details",
            "summary": summary_text,
            "blocks": [
                {
                    "type": "table",
                    "cells": table_cells,
                    "is_bordered": True,
                    "is_striped": True
                }
            ]
        })

        rich_msg = {
            "blocks": blocks
        }

        data = {
            'chat_id': str(chat_id),
            'rich_message': json.dumps(rich_msg, ensure_ascii=True)
        }
        if not is_channel:
            data['reply_markup'] = json.dumps({
                "inline_keyboard": [
                    [{"text": "☕ Support & Donate", "callback_data": "pub_show_donation"}]
                ]
            }, ensure_ascii=True)

        token = getattr(bot, "token", BOT_TOKEN)
        url = f"https://api.telegram.org/bot{token}/sendRichMessage"
        req_kwargs = {"data": data, "timeout": 30}
        if files:
            req_kwargs["files"] = files

        try:
            resp = requests.post(url, **req_kwargs)
        except requests.exceptions.SSLError:
            # Handles proxy, VPN, or antivirus SSL interception that causes self-signed certificate errors
            resp = requests.post(url, **req_kwargs, verify=False)

        res_data = resp.json()
        if res_data.get("ok"):
            return True
        else:
            print(f"[send_unified_completion_message] Telegram API error: {res_data}")
    except Exception as e:
        print(f"[send_unified_completion_message] Exception: {e}")
    return False

# ================= Direct Live Transfer Pipeline =================

def run_stream_pipeline(session_id: str, destination: str):
    """
    Direct Live Transfer Relay:
    Downloads torrent chunks directly in RAM and concurrently pipes them straight to Buzzheavier or Gofile.
    Zero bytes are written to the local disk!
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

    session["destination"] = destination
    session["is_paused"] = False

    if destination == "buzzheavier":
        dest_display = "Buzzheavier.com"
    elif destination == "gofile":
        dest_display = "Gofile.io"
    elif destination == "vikingfile":
        dest_display = "VikingFile.com"
    else:
        dest_display = destination.title()

    files_to_stream = [f for f in session["files"] if f["index"] in selected_indices]
    total_files = len(files_to_stream)
    uploaded_results = []

    controller = StreamController()
    session["controller"] = controller
    session["current_filename"] = ""
    session["last_rendered_text"] = ""

    last_ui_update = [0.0]

    try:
        for step_num, file_info in enumerate(files_to_stream, 1):
            if cancel_event.is_set():
                break

            idx = file_info["index"]
            zero_idx = idx - 1
            fname = file_info["name"]
            fsize = file_info["length"]
            fsize_str = human_readable_size(fsize)
            session["current_filename"] = fname

            def on_stream_progress(ev: dict):
                now = time.time()
                if now - last_ui_update[0] < PROGRESS_UPDATE_INTERVAL:
                    return
                last_ui_update[0] = now

                phase = ev.get("phase")
                speed = ev.get("speed", 0.0)
                peers = ev.get("peers", 0)

                if phase == "buffering":
                    buf_bytes = ev.get("bufferedBytes", 0)
                    piece_len = ev.get("pieceLength", 1) or 1
                    buf_bytes = min(buf_bytes, piece_len)
                    buf_pct = min(100.0, (buf_bytes / piece_len * 100.0)) if piece_len > 0 else 0.0
                    text = (
                        f"{EMOJI_STREAM_DOWN} <b>BUFFERING INITIAL PIECE IN RAM...</b>\n"
                        f"{SEP}\n"
                        f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                        f"📄 <b>File ({step_num}/{total_files}):</b> <code>{html.escape(fname)}</code> (<code>{fsize_str}</code>)\n"
                        f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b></blockquote>\n"
                        f"{SEP}\n"
                        f"<code>{progress_bar(buf_pct)}</code>\n"
                        f"{SEP}\n"
                        f"<blockquote>{EMOJI_PROGRESS} <b>Initial Piece:</b> <code>{human_readable_size(buf_bytes)} / {human_readable_size(piece_len)}</code>\n"
                        f"{EMOJI_SPEED} <b>Swarm Speed:</b> <code>{human_readable_size(speed)}/s</code>\n"
                        f"{EMOJI_PEERS} <b>Peers:</b> <code>{peers} connected</code>\n"
                        f"📦 <b>Queue:</b> <code>File {step_num} of {total_files}</code></blockquote>\n"
                        f"{SEP}\n"
                        f"<i>⚡ Preparing live RAM buffer before cloud stream starts...</i>"
                    )
                    session["last_rendered_text"] = text
                    safe_edit_message(chat_id, message_id, text, reply_markup=build_streaming_keyboard(session_id, is_paused=False, destination=destination))
                    return

                transferred = ev.get("transferred", 0)
                total = ev.get("total", fsize) or fsize
                pct = (transferred / total * 100.0) if total > 0 else 0.0
                eta = ev.get("eta", 0.0)

                is_p = session.get("is_paused", False) or ev.get("isPaused", False)
                header_title = f"{EMOJI_PAUSE} <b>TRANSFER PAUSED</b>" if is_p else f"{EMOJI_STREAM_UP} <b>TRANSFER IN PROGRESS</b>"
                footer_line = "<i>⏸️ Transfer is paused. Tap Continue to resume (within ~60s to prevent cloud timeout).</i>" if is_p else "<i>⚡ Direct live transfer in progress...</i>"

                text = (
                    f"{header_title}\n"
                    f"{SEP}\n"
                    f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                    f"📄 <b>File ({step_num}/{total_files}):</b> <code>{html.escape(fname)}</code> (<code>{fsize_str}</code>)\n"
                    f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b></blockquote>\n"
                    f"{SEP}\n"
                    f"<code>{progress_bar(pct)}</code>\n"
                    f"{SEP}\n"
                    f"<blockquote>{EMOJI_PROGRESS} <b>Transferred:</b> <code>{human_readable_size(transferred)} / {human_readable_size(total)}</code>\n"
                    f"{EMOJI_SPEED} <b>Speed:</b> <code>{human_readable_size(speed)}/s</code>\n"
                    f"{EMOJI_CLOCK} <b>ETA:</b> <code>{format_time(eta)}</code>\n"
                    f"{EMOJI_PEERS} <b>Peers:</b> <code>{peers} connected</code>\n"
                    f"📦 <b>Queue:</b> <code>File {step_num} of {total_files}</code></blockquote>\n"
                    f"{SEP}\n"
                    f"{footer_line}"
                )
                session["last_rendered_text"] = text
                safe_edit_message(chat_id, message_id, text, reply_markup=build_streaming_keyboard(session_id, is_paused=is_p, destination=destination))

            # Initial message
            init_text = (
                f"{EMOJI_STREAM_DOWN} <b>CONNECTING TO BITTORRENT SWARM...</b>\n"
                f"{SEP}\n"
                f"<blockquote>📄 <b>File ({step_num}/{total_files}):</b> <code>{html.escape(fname)}</code> (<code>{fsize_str}</code>)\n"
                f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b></blockquote>\n"
                f"{SEP}\n"
                f"<i>⚡ Establishing peer connections and starting transfer...</i>"
            )
            session["last_rendered_text"] = init_text
            safe_edit_message(
                chat_id,
                message_id,
                init_text,
                reply_markup=build_streaming_keyboard(session_id),
            )

            # Auto-retry logic (up to 3 retries) if upload connection is dropped by host or timed out
            max_retries = 3
            ok = False
            result = None
            for attempt in range(max_retries + 1):
                if cancel_event.is_set():
                    break

                ok, result = stream_mgr.stream_file(
                    torrent_source=torrent_path,
                    file_zero_index=zero_idx,
                    destination=destination,
                    progress_callback=on_stream_progress,
                    cancel_event=cancel_event,
                    controller=controller,
                )

                if ok or cancel_event.is_set():
                    break

                err_raw = str(result)
                err_detail = err_raw
                try:
                    parsed_err = json.loads(err_raw)
                    if isinstance(parsed_err, dict) and "message" in parsed_err:
                        err_detail = parsed_err["message"]
                except Exception:
                    pass

                if attempt < max_retries and not cancel_event.is_set():
                    # 1. Send separate error alert message directly to user chat
                    retry_user_alert = (
                        f"{EMOJI_ALERT} <b>TRANSFER INTERRUPTION DETECTED</b>\n{SEP}\n"
                        f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                        f"📄 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                        f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b>\n"
                        f"⚠️ <b>Reason:</b> <code>{html.escape(err_detail[:250])}</code>\n"
                        f"🔄 <b>Status:</b> Auto-reconnecting (Attempt {attempt + 2} of {max_retries + 1})...</blockquote>\n{SEP}\n"
                        f"<i>⚡ Retrying automatically. Progress will resume in the card above.</i>"
                    )
                    try:
                        bot.send_message(chat_id, retry_user_alert, disable_web_page_preview=True)
                    except Exception as ue:
                        print(f"[UserNotify] Failed to send retry alert: {ue}")

                    # 2. Send error report to Error Channel (if configured)
                    err_channel = db.get_setting("error_channel", "").strip()
                    if err_channel:
                        try:
                            err_card_retry = (
                                f"{EMOJI_ALERT} <b>TRANSFER ERROR LOG (Auto-Retry)</b>\n{SEP}\n"
                                f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                                f"📄 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                                f"{EMOJI_USER} <b>User:</b> @{user_name} (<code>{user_id}</code>)\n"
                                f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b>\n"
                                f"⚠️ <b>Error:</b> <code>{html.escape(err_detail[:250])}</code>\n"
                                f"🔄 <b>Attempt:</b> <code>{attempt + 2} of {max_retries + 1}</code>\n"
                                f"{EMOJI_CLOCK} <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC')}</code></blockquote>\n{SEP}"
                            )
                            bot.send_message(err_channel, err_card_retry, disable_web_page_preview=True)
                        except Exception as ce:
                            print(f"[ErrorChannel] Failed to send retry log: {ce}")

                    # 3. Update the FIRST message with reconnecting status
                    retry_msg = (
                        f"🔄 <b>CONNECTION INTERRUPTED</b>\n{SEP}\n"
                        f"<blockquote>📄 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                        f"⚠️ <b>Host:</b> {dest_display} connection dropped.\n"
                        f"🔄 <b>Auto-Retrying:</b> Attempt <code>{attempt + 2} of {max_retries + 1}</code>...</blockquote>\n{SEP}\n"
                        f"<i>⚡ Re-establishing transfer pipe and resuming...</i>"
                    )
                    session["last_rendered_text"] = retry_msg
                    safe_edit_message(chat_id, message_id, retry_msg, reply_markup=build_streaming_keyboard(session_id))
                    time.sleep(3)
                    continue
                else:
                    break

            if cancel_event.is_set():
                break

            if not ok:
                error_msg = str(result)
                try:
                    parsed_err = json.loads(error_msg)
                    if isinstance(parsed_err, dict) and "message" in parsed_err:
                        error_msg = parsed_err["message"]
                except Exception:
                    pass

                safe_edit_message(
                    chat_id,
                    message_id,
                    f"{EMOJI_ALERT} <b>TRANSFER ERROR</b>\n"
                    f"{SEP}\n"
                    f"<blockquote>📄 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                    f"⚠️ <b>Reason:</b> <code>{html.escape(error_msg)}</code></blockquote>\n"
                    f"{SEP}\n"
                    f"<i>If the problem persists, please contact the bot owner.</i>",
                    reply_markup=None,
                )
                err_channel = db.get_setting("error_channel", "").strip()
                if err_channel:
                    try:
                        err_card = (
                            f"{EMOJI_ALERT} <b>TRANSFER ERROR LOG</b>\n{SEP}\n"
                            f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                            f"📄 <b>File:</b> <code>{html.escape(fname)}</code>\n"
                            f"{EMOJI_USER} <b>User:</b> @{user_name} (<code>{user_id}</code>)\n"
                            f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b>\n"
                            f"⚠️ <b>Error:</b> <code>{html.escape(error_msg[:250])}</code>\n"
                            f"{EMOJI_CLOCK} <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC')}</code></blockquote>\n{SEP}"
                        )
                        bot.send_message(err_channel, err_card, disable_web_page_preview=True)
                    except Exception as ce:
                        print(f"[ErrorChannel] Failed to send: {ce}")
                return

            uploaded_results.append((fname, result, fsize_str, fsize))

        # Record download usage in database
        if not cancel_event.is_set() and uploaded_results:
            total_transferred_bytes = sum(sz_b for (_, _, _, sz_b) in uploaded_results)
            db.record_download_success(user_id, ADMIN_ID, bytes_transferred=total_transferred_bytes)

        # Final rendering
        if cancel_event.is_set():
            if session.get("status") == "admin_stopped":
                # Message was already updated with professional Admin Mode card by cancel_all_non_admin_transfers
                pass
            else:
                cancel_text = (
                    f"🛑 <b>TRANSFER CANCELLED</b>\n"
                    f"{SEP}\n"
                    f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                    f"⚠️ <b>Status:</b> Transfer cancelled by user request.</blockquote>\n"
                    f"{SEP}"
                )
                safe_edit_message(chat_id, message_id, cancel_text, reply_markup=None)
        else:
            user_tag = f"@{user_name}" if user_name and user_name != "user" else f"User {user_id}"

            # 1. Send Unified Completion Message (Document + Status Card + Scrollable Table) in ONE single bubble
            sent_unified = send_unified_completion_message(
                chat_id=chat_id,
                torrent_name=torrent_name,
                dest_display=dest_display,
                user_tag=user_tag,
                uploaded_results=uploaded_results,
                is_channel=False
            )

            if sent_unified:
                # Delete the previous in-progress progress card so only the single unified message remains in chat
                try:
                    bot.delete_message(chat_id, message_id)
                except Exception:
                    pass
            else:
                # Fallback to standard edit + send_document if Rich Message API is not supported
                links_section = build_links_presentation(uploaded_results, max_inline=10)
                done_text = (
                    f"{EMOJI_CHECK} <b>DOWNLOAD & TRANSFER COMPLETED!</b>\n"
                    f"{SEP}\n"
                    f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                    f"{EMOJI_GLOBE} <b>Host:</b> <b>{dest_display}</b>\n"
                    f"📦 <b>Files Transferred:</b> <code>{len(uploaded_results)} file(s)</code></blockquote>\n"
                    f"{SEP}\n"
                    f"{EMOJI_LINK} <b>DIRECT DOWNLOAD LINKS:</b>\n"
                    f"{links_section}"
                    f"{SEP}\n"
                    f"<i>💡 Tap any link above to start high-speed downloading. Support our servers below:</i>"
                )
                done_kb = types.InlineKeyboardMarkup()
                done_kb.add(types.InlineKeyboardButton("☕ Support & Donate", callback_data="pub_show_donation"))
                safe_edit_message(chat_id, message_id, done_text, reply_markup=done_kb)

                if len(uploaded_results) > 1:
                    try:
                        links_doc = build_links_text_file(torrent_name, dest_display, uploaded_results)
                        clean_doc_name = re.sub(r'[\\/*?:"<>|]', "_", torrent_name)[:45].strip() + "_Links.txt"
                        bot.send_document(
                            chat_id,
                            (clean_doc_name, links_doc),
                            caption=f"📁 <b>Batch Links File:</b> <code>{clean_doc_name}</code>\n<i>Contains all {len(uploaded_results)} file links.</i>",
                            parse_mode="HTML"
                        )
                    except Exception as de:
                        print(f"[SendDoc] Failed to send links txt file: {de}")

            # 2. Broadcast to Results Channel as a single unified message as well
            res_channel = db.get_setting("results_channel", "").strip()
            if res_channel:
                try:
                    sent_res_unified = send_unified_completion_message(
                        chat_id=res_channel,
                        torrent_name=torrent_name,
                        dest_display=dest_display,
                        user_tag=user_tag,
                        uploaded_results=uploaded_results,
                        is_channel=True
                    )
                    if not sent_res_unified:
                        total_uploaded_bytes = sum(sz_b for (_, _, _, sz_b) in uploaded_results)
                        total_sz_str = human_readable_size(total_uploaded_bytes)
                        links_section = build_links_presentation(uploaded_results, max_inline=10)
                        res_card = (
                            f"🎉 <b>TORRENT TRANSFER COMPLETED</b>\n{SEP}\n"
                            f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(torrent_name)}</code>\n"
                            f"📦 <b>Total Size:</b> <code>{total_sz_str}</code> (<code>{len(uploaded_results)} files</code>)\n"
                            f"🌐 <b>Host:</b> <b>{dest_display}</b>\n"
                            f"👤 <b>Uploaded By:</b> {html.escape(user_tag)}</blockquote>\n"
                            f"{SEP}\n"
                            f"🔗 <b>DOWNLOAD LINKS:</b>\n"
                            f"{links_section}"
                            f"{SEP}"
                        )
                        bot.send_message(res_channel, res_card, disable_web_page_preview=True)
                except Exception as ce:
                    print(f"[ResultsChannel] Failed to send combined card: {ce}")

    finally:
        cleanup_torrent_file(torrent_path)
        with sessions_lock:
            sessions.pop(session_id, None)

# ================= Process Torrent & Magnet =================

def process_torrent_source(message: types.Message, magnet_link: str = None, torrent_bytes: bytes = None, filename: str = None):
    """Extract metadata and present the file selector."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.username or message.from_user.first_name or "user"

    # 1. Concurrent transfer restriction: User cannot start another transfer until their current one finishes
    if user_id != ADMIN_ID and is_user_transferring(user_id):
        bot.reply_to(
            message,
            f"⚠️ <b>ACTIVE TRANSFER IN PROGRESS</b>\n{SEP}\n"
            f"<blockquote>You already have a transfer in progress.\n"
            f"Please wait until it finishes or stop it before starting another.</blockquote>\n{SEP}"
        )
        return

    # 2. Early quota check (5 torrents / 200 GB daily limit)
    ok, reason = db.check_user_can_request(user_id, ADMIN_ID)
    if not ok:
        bot.reply_to(message, reason)
        return

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
            target_path = os.path.join(METADATA_DIR, f"{session_id}.torrent")
            def on_meta_status(peers: int, elapsed: int):
                safe_edit_message(
                    chat_id,
                    status_msg.message_id,
                    f"🔍 <b>CONNECTING TO SWARM & FETCHING METADATA...</b>\n{SEP}\n"
                    f"<blockquote>👥 <b>Connected Peers:</b> <code>{peers}</code>\n"
                    f"⏱️ <b>Elapsed:</b> <code>{elapsed}s / 60s</code></blockquote>\n{SEP}\n"
                    f"<i>Searching DHT network and global trackers for torrent headers...</i>"
                )

            ok, res = stream_mgr.fetch_magnet_metadata(magnet_link, output_path=target_path, timeout=60, status_callback=on_meta_status)
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

        t_name, t_size, files = parse_torrent(torrent_filepath)
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

    if stats["git_token"]:
        gh = GitHubClient(stats["git_token"])
        db_icon = "🟢 Connected" if gh.test_token() else "🔴 Invalid Token"
    else:
        db_icon = "🔴 Not Set (Locked to Admin Only)"

    admin_tag = get_admin_username()
    text = (
        f"⚙️ <b>ADMIN CONTROL PANEL</b>\n{SEP}\n"
        f"<blockquote>👑 <b>Admin:</b> <code>{admin_tag}</code>\n"
        f"🤖 <b>Bot Mode:</b> <code>{bot_mode.upper()}</code>\n"
        f"🐙 <b>Cloud DB:</b> <code>{db_icon}</code>\n"
        f"👥 <b>Users:</b> <code>{stats['total_users']}</code> (🚫 <code>{stats['banned_users']}</code>)\n"
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
        types.InlineKeyboardButton("☕ Donations", callback_data="cfg_donations")
    )
    kb.add(
        types.InlineKeyboardButton("🔐 Access Modes", callback_data="cfg_modes"),
        types.InlineKeyboardButton("⏱️ Quotas & Limits", callback_data="cfg_limits")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="cfg_broadcast"),
        types.InlineKeyboardButton("❌ Close Panel", callback_data="cfg_close")
    )
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

    if token and tok_status == "🟢 Valid":
        sync_state = "🟡 Changes Pending (syncing in ≤2m)" if db.has_pending_changes() else "🟢 All Synced"
        last_sync = db.get_last_sync_display()
        db_status = (
            f"🟢 <b>Connected (Local-First Engine)</b>\n"
            f"<blockquote>⏱️ <b>Auto-Sync:</b> Every 2 minutes\n"
            f"⏳ <b>Pending Changes:</b> {sync_state}\n"
            f"🕒 <b>Last Cloud Sync:</b> {last_sync}</blockquote>"
        )
    else:
        db_status = "🔴 Offline / Not Configured"

    text = (
        f"🐙 <b>GITHUB DATABASE CONFIGURATION</b>\n{SEP}\n"
        f"<b>1️⃣ AUTHENTICATION</b>\n"
        f"<blockquote>🔑 <b>Token:</b> {tok_status}</blockquote>{account_info}\n\n"
        f"<b>2️⃣ 💾 CLOUD DATABASE (database.json)</b>\n"
        f"<blockquote>📊 <b>Status:</b> {db_status}</blockquote>\n"
        f"<blockquote>🔗 <b>Raw URL:</b>\n<code>{db_url}</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>⚡ All actions run locally with 0ms delay and sync to GitHub every 2 minutes.</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    if token:
        kb.add(
            types.InlineKeyboardButton("🔄 Sync to GitHub Now", callback_data="cfg_gh_sync_now"),
            types.InlineKeyboardButton("📥 Pull from GitHub", callback_data="cfg_gh_refresh")
        )
        kb.add(
            types.InlineKeyboardButton("🔄 Update Token", callback_data="cfg_gh_update_token"),
            types.InlineKeyboardButton("🧪 Test Token", callback_data="cfg_gh_test_token")
        )
        kb.add(types.InlineKeyboardButton("🗑️ Remove Token", callback_data="cfg_gh_remove_token"))
    else:
        kb.add(types.InlineKeyboardButton("➕ Set GitHub Token", callback_data="cfg_gh_add_token"))

    kb.add(
        types.InlineKeyboardButton("✏️ Edit Database URL", callback_data="cfg_gh_edit_url"),
        types.InlineKeyboardButton("🧪 Test Connection", callback_data="cfg_gh_test_db")
    )
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
        f"✅ <b>RESULTS CHANNEL (Successful Transfer Broadcasts)</b>\n"
        f"<blockquote><code>{res_ch}</code></blockquote>\n"
        f"❌ <b>ERROR CHANNEL (Transfer Error Reports)</b>\n"
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

def render_donations_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Donations Configuration Submenu (PayPal, Binance ID & RedotPay crypto networks)."""
    stats = db.get_stats()
    p = stats.get("donation_paypal") or "Not Set"
    b = stats.get("donation_binance_id") or "Not Set"
    trc = stats.get("donation_trc20") or "Not Set"
    bep = stats.get("donation_bep20") or "Not Set"
    sol = stats.get("donation_solana") or "Not Set"
    ton = stats.get("donation_ton") or "Not Set"
    erc = stats.get("donation_erc20") or "Not Set"
    arb = stats.get("donation_arbitrum") or "Not Set"
    opbnb = stats.get("donation_opbnb") or "Not Set"
    note = stats.get("donation_note") or "None"

    text = (
        f"☕ <b>DONATION SETTINGS & PAYMENT METHODS</b>\n{SEP}\n"
        f"<blockquote>🅿️ <b>PayPal:</b> <code>{p}</code>\n"
        f"🟡 <b>Binance ID:</b> <code>{b}</code>\n"
        f"🔴 <b>USDT (TRC20):</b> <code>{trc}</code>\n"
        f"🟡 <b>BNB (BEP20):</b> <code>{bep}</code>\n"
        f"🟣 <b>Solana:</b> <code>{sol}</code>\n"
        f"💎 <b>TON:</b> <code>{ton}</code>\n"
        f"🔷 <b>ETH (ERC20):</b> <code>{erc}</code>\n"
        f"🔵 <b>Arbitrum One:</b> <code>{arb}</code>\n"
        f"🟡 <b>opBNB:</b> <code>{opbnb}</code>\n"
        f"📝 <b>Note:</b> <code>{note}</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Tap below to add, update, or clear any payment method:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🅿️ Set PayPal", callback_data="cfg_don_paypal"),
        types.InlineKeyboardButton("🟡 Set Binance ID", callback_data="cfg_don_binance")
    )
    kb.add(
        types.InlineKeyboardButton("🔴 Set TRC20", callback_data="cfg_don_trc20"),
        types.InlineKeyboardButton("🟡 Set BEP20", callback_data="cfg_don_bep20")
    )
    kb.add(
        types.InlineKeyboardButton("🟣 Set Solana", callback_data="cfg_don_solana"),
        types.InlineKeyboardButton("💎 Set TON", callback_data="cfg_don_ton")
    )
    kb.add(
        types.InlineKeyboardButton("🔷 Set ERC20", callback_data="cfg_don_erc20"),
        types.InlineKeyboardButton("🔵 Set Arbitrum", callback_data="cfg_don_arbitrum")
    )
    kb.add(
        types.InlineKeyboardButton("🟡 Set opBNB", callback_data="cfg_don_opbnb"),
        types.InlineKeyboardButton("📝 Set Note", callback_data="cfg_don_note")
    )
    kb.add(types.InlineKeyboardButton("🧪 Preview Donation Card", callback_data="cfg_don_preview"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_members_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Members Management Submenu."""
    stats = db.get_stats()
    text = (
        f"👥 <b>MEMBERS MANAGEMENT</b>\n{SEP}\n"
        f"📊 <b>DATABASE OVERVIEW</b>\n"
        f"<blockquote>👤 <b>Total Users:</b> {stats['total_users']}</blockquote>\n"
        f"<blockquote>🚫 <b>Banned Users:</b> {stats['banned_users']}</blockquote>\n"
        f"<blockquote>📅 <b>Active Today:</b> {stats['downloads_today']}</blockquote>\n"
        f"{SEP}\n"
        f"<i>Choose an action below:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👤 All Members", callback_data="cfg_mem_all_0"),
        types.InlineKeyboardButton("🚫 Banned Users", callback_data="cfg_mem_ban_0")
    )
    kb.add(
        types.InlineKeyboardButton("🔍 Search User", callback_data="cfg_mem_search"),
        types.InlineKeyboardButton("🚫 Ban User", callback_data="cfg_mem_ban")
    )
    kb.add(types.InlineKeyboardButton("🟢 Unban User", callback_data="cfg_mem_unban"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_members_list(filter_type: str = "all", page: int = 0) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render paginated user list with quick profile buttons."""
    users = db.get_users_list(filter_type=filter_type)
    total_users = len(users)
    per_page = 6
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    title_map = {"all": "ALL MEMBERS", "banned": "BANNED USERS"}
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
            ban_badge = "🚫 " if u.get("is_banned") else ""
            label = f"{ban_badge}{uname} ({u.get('downloads_today', 0)} today)"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"cfg_mem_prof_{uid}"))

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
    ban_btn = "🟢 Unban User" if is_ban else "🚫 Ban User"

    kb.add(
        types.InlineKeyboardButton(ban_btn, callback_data=f"cfg_tog_ban_{user_id}"),
        types.InlineKeyboardButton("🔄 Reset Daily Downloads", callback_data=f"cfg_reset_dl_{user_id}")
    )
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
        f"• <b>Admin Only:</b> Only you can use the bot (safe maintenance mode)\n"
        f"• <b>Public:</b> Anyone registered can use the bot within daily quotas\n"
        f"{SEP}\n"
        f"⚠️ <i>Public mode requires a connected GitHub Cloud Database!</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    admin_mark = " (Active)" if current_mode == "admin_only" else ""
    pub_mark = " (Active)" if current_mode == "public" else ""

    kb.add(types.InlineKeyboardButton(f"🔐 Admin Only Mode{admin_mark}", callback_data="cfg_set_mode_admin"))
    kb.add(types.InlineKeyboardButton(f"🌐 Public Mode{pub_mark}", callback_data="cfg_set_mode_public"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

def render_limits_menu() -> tuple[str, types.InlineKeyboardMarkup]:
    """Render Quotas & Limits Submenu."""
    stats = db.get_stats()
    text = (
        f"⏱️ <b>QUOTAS & LIMITS CONFIGURATION</b>\n{SEP}\n"
        f"<blockquote>🔢 <b>Daily Torrents Limit:</b> <code>{stats['daily_limit']} torrents/day</code>\n"
        f"📦 <b>Max Single File Size:</b> <code>{stats['max_size_gb']} GB/file</code>\n"
        f"🌐 <b>Daily Total Bandwidth:</b> <code>{stats.get('daily_bandwidth_gb', 100.0)} GB/day</code></blockquote>\n"
        f"{SEP}\n"
        f"<i>Configure limits applied equally to all registered members:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✏️ Set Daily Limit", callback_data="cfg_set_daily_limit"),
        types.InlineKeyboardButton("📦 Set Max File Size", callback_data="cfg_set_max_size")
    )
    kb.add(types.InlineKeyboardButton("🌐 Set Daily Bandwidth", callback_data="cfg_set_daily_bw"))
    kb.add(types.InlineKeyboardButton("← Back to Settings", callback_data="cfg_main"))
    return text, kb

# ================= Prompt Input Helper =================

def prompt_admin_input(chat_id: int, message_id: int, action: str, title: str, instructions: str, return_menu: str, extra: dict = None):
    """Edits current settings message to prompt with [Cancel] button."""
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
        "donations": render_donations_menu,
        "members": render_members_menu,
        "modes": render_modes_menu,
        "limits": render_limits_menu
    }
    renderer = menu_dispatch.get(return_menu, render_settings_main)
    text, kb = renderer()
    safe_edit_message(chat_id, message_id, text, reply_markup=kb)

# ================= Telegram Commands =================

@bot.message_handler(commands=["settings", "panel"])
def handle_settings(message: types.Message):
    """Admin settings command (secretly accessed by admin only)."""
    if message.from_user.id != ADMIN_ID:
        return

    text, kb = render_settings_main()
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(commands=["donate"])
def handle_donate_command(message: types.Message):
    """Direct command to view donation information."""
    bot.reply_to(message, render_donation_card(), disable_web_page_preview=True)

def render_start_text(user_id: int, user_name: str) -> str:
    """Welcome and usage instructions with animated emojis and clean styling."""
    admin_contact = get_admin_contact()
    clean_name = html.escape(user_name or "User")

    return (
        f"{EMOJI_WAVE} <b>Welcome to tDownUp!</b>\n"
        f"{SEP}\n"
        f"<blockquote>{EMOJI_USER} <b>User:</b> {clean_name}\n"
        f"{EMOJI_SPEED} <b>Engine:</b> <code>tDownUp Transfer Core V2</code>\n"
        f"{EMOJI_GLOBE} <b>Supported Hosts:</b> <code>Buzzheavier</code>, <code>Gofile</code> & <code>VikingFile</code></blockquote>\n"
        f"{SEP}\n"
        f"<b>tDownUp</b> allows you to download torrent files and magnet links directly to cloud storage at maximum speeds.\n\n"
        f"📥 <b>HOW TO USE:</b>\n"
        f"<blockquote>• Send <code>/dw &lt;magnet_link&gt;</code>\n"
        f"• Or reply to a <code>.torrent</code> file with <code>/dw</code>\n"
        f"• Or directly send a <code>.torrent</code> file\n"
        f"• Or simply paste any magnet link!</blockquote>\n\n"
        f"{EMOJI_PIN} <b>COMMANDS:</b>\n"
        f"<blockquote>• <code>/account</code> - View daily downloads quota & limits\n"
        f"• <code>/donate</code> - Support project & hosting</blockquote>\n\n"
        f"💬 <b>Support & Assistance:</b>\n"
        f"If you need help or have questions, contact {admin_contact}."
    )

@bot.message_handler(commands=["start", "help"])
def handle_start(message: types.Message):
    """Welcome and usage instructions. Clean layout with owner contact and no settings leaks."""
    if not check_user_access(message):
        return

    user_id = message.from_user.id
    user_name = message.from_user.first_name or message.from_user.username or "User"
    text = render_start_text(user_id, user_name)
    bot.reply_to(message, text, disable_web_page_preview=True)

@bot.message_handler(commands=["account"])
def handle_account(message: types.Message):
    """View user quota and limits."""
    if not check_user_access(message):
        return

    user_id = message.from_user.id
    user_name = message.from_user.first_name or message.from_user.username or "User"
    info = db.get_user_info(user_id)
    is_admin = (user_id == ADMIN_ID)

    daily_limit = int(db.get_setting("daily_limit_regular", 5))
    max_size_gb = float(db.get_setting("max_size_gb_regular", 100.0))
    daily_bw_gb = float(db.get_setting("daily_bandwidth_gb_regular", 200.0))

    today = db._today_str()
    if info.get("today_date") != today:
        used_today = 0
        bytes_today = 0
    else:
        used_today = info.get("downloads_today", 0)
        bytes_today = info.get("bytes_today", 0)

    torrents_str = "Unlimited ♾️ (Admin)" if is_admin else f"<code>{used_today} / {daily_limit} torrents</code>"
    bandwidth_str = "Unlimited ♾️ (Admin)" if is_admin else f"<code>{human_readable_size(bytes_today)} / {int(daily_bw_gb)} GB</code>"
    max_sz_str = "Unlimited ♾️ (Admin)" if is_admin else f"<code>{int(max_size_gb)} GB</code>"
    total_dl = info.get("total_downloads", 0)
    total_bytes = info.get("total_bytes", 0)
    total_str = f"<code>{total_dl} torrents</code> (<code>{human_readable_size(total_bytes)}</code>)"

    text = (
        f"{EMOJI_PROGRESS} <b>ACCOUNT USAGE & LIMITS</b>\n"
        f"{SEP}\n"
        f"<blockquote>{EMOJI_USER} <b>User:</b> {html.escape(user_name)}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📅 <b>Registered:</b> <code>{info.get('registered_at', 'N/A')}</code>\n"
        f"{SEP}\n"
        f"📊 <b>Torrents Today:</b> {torrents_str}\n"
        f"🌐 <b>Bandwidth Today:</b> {bandwidth_str}\n"
        f"📦 <b>Max Single File:</b> {max_sz_str}\n"
        f"📥 <b>Lifetime Transferred:</b> {total_str}</blockquote>\n"
        f"{SEP}\n"
        f"<i>💡 Daily quotas reset every day at 00:00 UTC.</i>"
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
            res = f"Daily torrents limit set to: <b>{val} torrents/day</b>"
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
                res = f"Max single file size set to: <b>{sz} GB</b>"
            else:
                res = "❌ Value must be greater than 0."
        except ValueError:
            res = "❌ Invalid number."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "limits")
        return

    elif action == "set_daily_bw":
        try:
            bw = float(val)
            if bw > 0:
                db.set_setting("daily_bandwidth_gb_regular", bw)
                res = f"Daily total bandwidth set to: <b>{bw} GB/day</b>"
            else:
                res = "❌ Value must be greater than 0."
        except ValueError:
            res = "❌ Invalid number."
        safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
        time.sleep(1.5)
        return_to_submenu(cid, mid, "limits")
        return

    # 5. Donations
    elif action.startswith("don_"):
        key_map = {
            "don_paypal": ("donation_paypal", "PayPal"),
            "don_binance": ("donation_binance_id", "Binance Pay ID"),
            "don_trc20": ("donation_trc20", "USDT (TRC20)"),
            "don_bep20": ("donation_bep20", "BNB Smart Chain (BEP20)"),
            "don_solana": ("donation_solana", "Solana"),
            "don_ton": ("donation_ton", "The Open Network (TON)"),
            "don_erc20": ("donation_erc20", "Ethereum (ERC20)"),
            "don_arbitrum": ("donation_arbitrum", "Arbitrum One"),
            "don_opbnb": ("donation_opbnb", "opBNB"),
            "don_note": ("donation_note", "Donation Note"),
        }
        if action in key_map:
            db_key, label = key_map[action]
            if val.lower() in ("none", "0", "clear"):
                db.set_setting(db_key, "")
                res = f"{label} cleared."
            else:
                db.set_setting(db_key, val)
                res = f"{label} updated to:\n<code>{val}</code>"

            safe_edit_message(cid, mid, f"✅ <b>Updated!</b>\n{SEP}\n<blockquote>{res}</blockquote>\n{SEP}\n<i>Returning...</i>")
            time.sleep(1.5)
            return_to_submenu(cid, mid, "donations")
            return

    # 6. Members Actions
    elif action == "search_user":
        found = db.search_users(val)
        if not found:
            safe_edit_message(cid, mid, f"❌ <b>No users found matching:</b> <code>{val}</code>\n{SEP}\n<i>Returning...</i>")
            time.sleep(2.0)
            return_to_submenu(cid, mid, "members")
            return
        elif len(found) == 1:
            text, kb = render_user_profile(found[0]["user_id"])
            safe_edit_message(cid, mid, text, reply_markup=kb)
            return
        else:
            text = f"🔍 <b>SEARCH RESULTS FOR:</b> <code>{val}</code>\n{SEP}\n"
            kb = types.InlineKeyboardMarkup(row_width=1)
            for u in found[:10]:
                uid = u["user_id"]
                uname = f"@{u['username']}" if u.get("username") else f"ID: {uid}"
                kb.add(types.InlineKeyboardButton(f"👤 {uname}", callback_data=f"cfg_mem_prof_{uid}"))
            kb.add(types.InlineKeyboardButton("← Back to Members", callback_data="cfg_members"))
            safe_edit_message(cid, mid, text, reply_markup=kb)
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

    # 7. Broadcast
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

        # Register user in database
        db.register_user(user_id, call.from_user.username or "")

        bot_channel = db.get_setting("bot_channel", "").strip()
        channel_required = bool(bot_channel)
        is_channel_member = check_channel_membership(user_id) if channel_required else True

        if is_channel_member:
            # Both steps complete! Directly show /start welcome message
            bot.answer_callback_query(call.id, "🎉 Welcome! Registration complete!")
            user_name = call.from_user.first_name or call.from_user.username or "User"
            welcome_text = render_start_text(user_id, user_name)
            safe_edit_message(cid, mid, welcome_text, reply_markup=None)
        else:
            # Registration complete, but channel still needed -> Register button disappears!
            bot.answer_callback_query(call.id, "✅ Registered! Please join our channel.")
            text, kb = build_registration_view(user_id)
            safe_edit_message(cid, mid, text, reply_markup=kb)
        return

    # 1.1 Channel Verification Callback
    if data.startswith("verify_channel:"):
        target_uid = int(data.split(":")[1])
        if user_id != target_uid:
            bot.answer_callback_query(call.id, "This verification button is not for you.", show_alert=True)
            return

        if not check_channel_membership(user_id):
            bot.answer_callback_query(
                call.id,
                "⚠️ You have not joined the channel yet! Please join first.",
                show_alert=True
            )
            return

        # Channel verified! Check if user is registered in DB
        is_reg = db.is_registered(user_id)
        if is_reg:
            # Both steps complete! Directly show /start welcome message
            bot.answer_callback_query(call.id, "🎉 Membership verified! Welcome!")
            user_name = call.from_user.first_name or call.from_user.username or "User"
            welcome_text = render_start_text(user_id, user_name)
            safe_edit_message(cid, mid, welcome_text, reply_markup=None)
        else:
            # Channel verified, but registration still needed -> Channel buttons disappear!
            bot.answer_callback_query(call.id, "✅ Channel verified! Tap Register Now below.")
            text, kb = build_registration_view(user_id)
            safe_edit_message(cid, mid, text, reply_markup=kb)
        return

    # 2. Public Donation Card Trigger (from completion message)
    if data == "pub_show_donation":
        bot.answer_callback_query(call.id, "💖 Thank you for supporting the project!")
        bot.send_message(cid, render_donation_card(), disable_web_page_preview=True)
        return

    # 3. Admin Settings Callbacks
    if data.startswith("cfg_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Unauthorized.", show_alert=True)
            return

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

        # Navigation
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

        elif data == "cfg_donations":
            text, kb = render_donations_menu()
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

        # ── Donation Settings Operations ──
        elif data == "cfg_don_paypal":
            prompt_admin_input(
                cid, mid,
                action="don_paypal",
                title="PAYPAL",
                instructions="Enter your PayPal email address or paypal.me link (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_binance":
            prompt_admin_input(
                cid, mid,
                action="don_binance",
                title="BINANCE PAY ID",
                instructions="Enter your Binance Pay ID / numerical ID (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_trc20":
            prompt_admin_input(
                cid, mid,
                action="don_trc20",
                title="TRON (TRC20) USDT",
                instructions="Enter your USDT (TRC20) deposit address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_bep20":
            prompt_admin_input(
                cid, mid,
                action="don_bep20",
                title="BNB SMART CHAIN (BEP20)",
                instructions="Enter your BNB / USDT (BEP20) deposit address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_solana":
            prompt_admin_input(
                cid, mid,
                action="don_solana",
                title="SOLANA (SOL)",
                instructions="Enter your Solana deposit address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_ton":
            prompt_admin_input(
                cid, mid,
                action="don_ton",
                title="THE OPEN NETWORK (TON)",
                instructions="Enter your TON deposit address / memo (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_erc20":
            prompt_admin_input(
                cid, mid,
                action="don_erc20",
                title="ETHEREUM (ERC20)",
                instructions="Enter your Ethereum (ERC20) address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_arbitrum":
            prompt_admin_input(
                cid, mid,
                action="don_arbitrum",
                title="ARBITRUM ONE",
                instructions="Enter your Arbitrum One address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_opbnb":
            prompt_admin_input(
                cid, mid,
                action="don_opbnb",
                title="OPBNB",
                instructions="Enter your opBNB address (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_note":
            prompt_admin_input(
                cid, mid,
                action="don_note",
                title="DONATION NOTE",
                instructions="Enter a custom note or instructions to show under donation options (or send <code>none</code> to clear):",
                return_menu="donations"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_don_preview":
            preview_card = render_donation_card()
            safe_edit_message(cid, mid, preview_card, reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("← Back to Donation Settings", callback_data="cfg_donations")
            ))
            bot.answer_callback_query(call.id)
            return

        # ── GitHub Submenu Operations ──
        elif data in ("cfg_gh_add_token", "cfg_gh_update_token"):
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
            cancel_all_non_admin_transfers()
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

        elif data == "cfg_gh_sync_now":
            if not db.is_github_connected():
                bot.answer_callback_query(call.id, "❌ GitHub is not connected!", show_alert=True)
                return
            bot.answer_callback_query(call.id, "🔄 Syncing database to GitHub...")
            ok, msg = db.sync_to_github_now(commit_msg="Manual sync via Admin Panel")
            if ok:
                bot.answer_callback_query(call.id, "✅ Database synced to GitHub successfully!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"⚠️ Sync failed: {msg}", show_alert=True)
            text, kb = render_github_menu()
            safe_edit_message(cid, mid, text, reply_markup=kb)
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
                bot.send_message(ch, "📢 <b>Test Message:</b> Bot Channel connection is working properly!")
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
                bot.send_message(ch, "🚀 <b>Test Broadcast:</b> Results Channel integration is active!")
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
                bot.send_message(ch, "❌ <b>Test Broadcast:</b> Error Channel integration is active!")
                bot.answer_callback_query(call.id, "✅ Test message sent to Error Channel!", show_alert=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Test failed: {e}", show_alert=True)
            return

        # ── Access Modes Operations (Strict Gatekeeper Constraint!) ──
        elif data == "cfg_set_mode_admin":
            ok, res = db.set_bot_mode("admin_only")
            stopped = cancel_all_non_admin_transfers()
            msg = f"Switched to Admin Only mode. Stopped {stopped} active user transfer(s)." if stopped > 0 else "Switched to Admin Only mode."
            bot.answer_callback_query(call.id, msg, show_alert=(stopped > 0))
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
                title="DAILY TORRENTS LIMIT",
                instructions="Enter daily torrent limit for regular users (e.g. <code>5</code>):",
                return_menu="limits"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_set_max_size":
            prompt_admin_input(
                cid, mid,
                action="set_max_size",
                title="MAX SINGLE FILE SIZE (GB)",
                instructions="Enter maximum single file size in GB for regular users (e.g. <code>100.0</code>):",
                return_menu="limits"
            )
            bot.answer_callback_query(call.id)
            return

        elif data == "cfg_set_daily_bw":
            prompt_admin_input(
                cid, mid,
                action="set_daily_bw",
                title="DAILY TOTAL BANDWIDTH (GB)",
                instructions="Enter maximum total daily bandwidth in GB for regular users (e.g. <code>200.0</code>):",
                return_menu="limits"
            )
            bot.answer_callback_query(call.id)
            return

        # ── Members Submenu & Paginated Lists ──
        elif data.startswith("cfg_mem_all_") or data.startswith("cfg_mem_ban_"):
            parts = data.split("_")
            ftype = "all" if parts[2] == "all" else "banned"
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

    # 4. Torrent / Stream Session Callbacks
    if ":" not in data:
        bot.answer_callback_query(call.id)
        return

    parts = data.split(":")
    action = parts[0]
    session_id = parts[1]

    session = sessions.get(session_id)
    if not session:
        bot.answer_callback_query(call.id, "Session expired or bot was restarted. Please send torrent again.", show_alert=True)
        try:
            if call.message:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    # Restrict session interactions if bot is currently in Admin Mode
    if user_id != ADMIN_ID:
        allowed, reason = db.check_access(user_id, ADMIN_ID)
        if not allowed:
            bot.answer_callback_query(call.id, "🔒 The bot is currently in Admin Mode.", show_alert=True)
            safe_edit_message(session["chat_id"], session["message_id"], reason, reply_markup=None)
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

        if user_id != ADMIN_ID and is_user_transferring(user_id):
            bot.answer_callback_query(
                call.id,
                "⚠️ You already have an active transfer in progress! Please wait until it completes.",
                show_alert=True
            )
            return

        selected_files = [f for f in session["files"] if f["index"] in session["selected"]]
        total_selected_size = sum(f["length"] for f in selected_files)

        ok, reason = db.check_download_limit(
            user_id,
            ADMIN_ID,
            total_size_bytes=total_selected_size,
            files=selected_files
        )
        if not ok:
            bot.answer_callback_query(call.id, "Limit exceeded!", show_alert=False)
            safe_edit_message(sess_chat_id, sess_message_id, reason, reply_markup=None)
            return

        bot.answer_callback_query(call.id)
        dest_kb = build_destination_keyboard(session_id)
        dest_text = (
            f"{EMOJI_GLOBE} <b>CHOOSE UPLOAD DESTINATION</b>\n{SEP}\n"
            f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(session['torrent_name'])}</code>\n"
            f"📦 <b>Selected:</b> <code>{len(selected_files)} files</code> (<code>{human_readable_size(total_selected_size)}</code>)</blockquote>\n"
            f"{SEP}\n"
            f"<i>Select the cloud storage service to transfer your files to:</i>"
        )
        safe_edit_message(sess_chat_id, sess_message_id, dest_text, reply_markup=dest_kb)

    elif action == "back_select":
        markup = build_file_selection_keyboard(session_id)
        text = render_selection_message(session_id)
        safe_edit_message(sess_chat_id, sess_message_id, text, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif action == "start":
        if user_id != ADMIN_ID and is_user_transferring(user_id):
            bot.answer_callback_query(
                call.id,
                "⚠️ You already have an active transfer in progress! Please wait until it completes.",
                show_alert=True
            )
            return
        destination = parts[2] if len(parts) > 2 else "buzzheavier"
        bot.answer_callback_query(call.id, f"🚀 Starting transfer to {destination}...")
        session["status"] = "running"
        threading.Thread(target=run_stream_pipeline, args=(session_id, destination), daemon=True).start()

    elif action == "pause":
        session_id = parts[1] if len(parts) > 1 else ""
        session = sessions.get(session_id)
        if session:
            ctrl = session.get("controller")
            if ctrl:
                ctrl.pause()
            session["is_paused"] = True
            bot.answer_callback_query(call.id, "⏸️ Transfer paused.")
            last_text = session.get("last_rendered_text", "")
            dest = session.get("destination", "")
            if last_text:
                paused_text = last_text.replace("TRANSFER IN PROGRESS", "TRANSFER PAUSED")
                paused_text = paused_text.replace("⚡ Direct live transfer in progress...", "⏸️ Transfer is paused. Tap Continue to resume.")
                session["last_rendered_text"] = paused_text
                safe_edit_message(
                    session["chat_id"],
                    session["message_id"],
                    paused_text,
                    reply_markup=build_streaming_keyboard(session_id, is_paused=True, destination=dest)
                )
        else:
            bot.answer_callback_query(call.id, "Session expired.")

    elif action == "resume":
        session_id = parts[1] if len(parts) > 1 else ""
        session = sessions.get(session_id)
        if session:
            ctrl = session.get("controller")
            if ctrl:
                ctrl.resume()
            session["is_paused"] = False
            bot.answer_callback_query(call.id, "▶️ Resuming transfer...")
            last_text = session.get("last_rendered_text", "")
            dest = session.get("destination", "")
            if last_text:
                resumed_text = last_text.replace("TRANSFER PAUSED", "TRANSFER IN PROGRESS")
                resumed_text = resumed_text.replace("⏸️ Transfer is paused. Tap Continue to resume.", "⚡ Direct live transfer in progress...")
                session["last_rendered_text"] = resumed_text
                safe_edit_message(
                    session["chat_id"],
                    session["message_id"],
                    resumed_text,
                    reply_markup=build_streaming_keyboard(session_id, is_paused=False, destination=dest)
                )
        else:
            bot.answer_callback_query(call.id, "Session expired.")

    elif action == "confirm_stop":
        session_id = parts[1] if len(parts) > 1 else ""
        session = sessions.get(session_id)
        if session:
            bot.answer_callback_query(call.id)
            t_name = session.get("torrent_name", "Torrent")
            curr_file = session.get("current_filename", "")
            file_line = f"📄 <b>File:</b> <code>{html.escape(curr_file)}</code>\n" if curr_file else ""
            confirm_text = (
                f"🛑 <b>CONFIRM STOP</b>\n{SEP}\n"
                f"<blockquote>📁 <b>Torrent:</b> <code>{html.escape(t_name)}</code>\n"
                f"{file_line}"
                f"⚠️ <b>Are you sure you want to stop the transfer?</b></blockquote>\n{SEP}\n"
                f"<i>Tap <b>Yes</b> to cancel completely, or <b>No</b> to return to the transfer.</i>"
            )
            safe_edit_message(
                session["chat_id"],
                session["message_id"],
                confirm_text,
                reply_markup=build_stop_confirm_keyboard(session_id)
            )
        else:
            bot.answer_callback_query(call.id, "Session expired.")

    elif action == "do_stop" or action == "stop":
        session_id = parts[1] if len(parts) > 1 else ""
        session = sessions.get(session_id)
        if session:
            bot.answer_callback_query(call.id, "🛑 Stopping transfer...")
            session["cancel_event"].set()
            ctrl = session.get("controller")
            if ctrl:
                ctrl.stop()
        else:
            bot.answer_callback_query(call.id, "Session expired.")

    elif action == "cancel_stop":
        session_id = parts[1] if len(parts) > 1 else ""
        session = sessions.get(session_id)
        if session:
            bot.answer_callback_query(call.id, "↩️ Returned to transfer.")
            last_text = session.get("last_rendered_text")
            is_p = session.get("is_paused", False)
            dest = session.get("destination", "")
            if last_text:
                safe_edit_message(
                    session["chat_id"],
                    session["message_id"],
                    last_text,
                    reply_markup=build_streaming_keyboard(session_id, is_paused=is_p, destination=dest)
                )
        else:
            bot.answer_callback_query(call.id, "Session expired.")

    elif action == "noop":
        bot.answer_callback_query(call.id)

# ================= Entry Point & Console Dashboard =================

def print_anya_banner(bot_info):
    """Render a clean, colorful console status dashboard inspired by Anya Bot."""
    width = 68
    bot_name = bot_info.first_name if bot_info else "tDownUp"
    bot_username = f"@{bot_info.username}" if (bot_info and bot_info.username) else "N/A"
    bot_id = str(bot_info.id) if bot_info else "N/A"
    stats = db.get_stats()
    mode = stats.get("bot_mode", "public").upper()
    admin_display = get_admin_username()

    top_border = f"{CYAN}+{'-' * (width - 2)}+{RESET}"
    sep_line   = f"{CYAN}|{'-' * (width - 2)}|{RESET}"
    bot_border = f"{CYAN}+{'-' * (width - 2)}+{RESET}"

    def pad_line(content: str, raw_len: int) -> str:
        pad = width - 4 - raw_len
        return f"{CYAN}|{RESET} {content}{' ' * max(0, pad)} {CYAN}|{RESET}"

    print("\n" + top_border)
    raw_title = "⚡ tDownUp BOT V2 ⚡"
    left_pad = (width - 4 - len(raw_title)) // 2
    title_content = f"{BOLD}{WHITE}{' ' * left_pad}{raw_title}{RESET}"
    print(pad_line(title_content, left_pad + len(raw_title)))
    print(sep_line)

    lines = [
        (f"  {CYAN}*{RESET} {BOLD}Bot Name:{RESET}    {GREEN}{bot_name}{RESET}", len(f"  * Bot Name:    {bot_name}")),
        (f"  {CYAN}*{RESET} {BOLD}Username:{RESET}    {GREEN}{bot_username}{RESET}", len(f"  * Username:    {bot_username}")),
        (f"  {CYAN}*{RESET} {BOLD}Bot ID:{RESET}      {WHITE}{bot_id}{RESET}", len(f"  * Bot ID:      {bot_id}")),
        (f"  {CYAN}*{RESET} {BOLD}Admin:{RESET}       {YELLOW}{admin_display}{RESET}", len(f"  * Admin:       {admin_display}")),
        (f"  {CYAN}*{RESET} {BOLD}Bot Mode:{RESET}    {MAGENTA}{mode}{RESET}", len(f"  * Bot Mode:    {mode}")),
        (f"  {CYAN}*{RESET} {BOLD}Hosts:{RESET}       {CYAN}Buzzheavier, Gofile & VikingFile{RESET}", len("  * Hosts:       Buzzheavier, Gofile & VikingFile")),
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
        os.system("title tDownUp Bot V2 (Active)")
    os.system("cls" if os.name == "nt" else "clear")

    try:
        bot_info = bot.get_me()
    except Exception as e:
        print(f"\n{RED}[!] ERROR CONNECTING TO TELEGRAM API:{RESET} {e}")
        print(f"{YELLOW}[!] Check your bot_token in .auth file.{RESET}")
        bot_info = None

    print_anya_banner(bot_info)

    restart_count = 0
    while True:
        try:
            bot.infinity_polling(
                timeout=25,
                long_polling_timeout=20,
                logger_level=logging.CRITICAL,
                none_stop=True,
                restart_on_change=False,
            )
        except (KeyboardInterrupt, SystemExit):
            print(f"\n{YELLOW}[!] Bot stopping on user request...{RESET}")
            break
        except (ConnectionResetError, ConnectionAbortedError, ConnectionError, requests.exceptions.RequestException) as net_err:
            print(f"  {YELLOW}⚡ Telegram connection reset (WinError 10054). Reconnecting in 3s...{RESET}")
            time.sleep(3)
        except Exception as err:
            err_str = str(err).lower()
            if "10054" in err_str or "connection" in err_str or "aborted" in err_str or "reset" in err_str:
                print(f"  {YELLOW}⚡ Telegram connection reset (WinError 10054). Reconnecting in 3s...{RESET}")
                time.sleep(3)
            else:
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
