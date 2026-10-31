import os
import json
import threading
from datetime import datetime, timezone
from config import DATABASE_FILE
from utils import human_readable_size

class DatabaseManager:
    """Manages users, registrations, daily limits, premium tiers, and bot settings."""
    def __init__(self, db_path: str = DATABASE_FILE):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.data = self._load()

    def _get_default_structure(self) -> dict:
        return {
            "settings": {
                "bot_mode": "public",  # "public", "premium", "admin_only"
                "daily_limit_regular": 5,
                "max_size_gb_regular": 40.0,
                "required_channel": "",
                "required_channel_url": ""
            },
            "users": {}
        }

    def _load(self) -> dict:
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    defaults = self._get_default_structure()
                    if "settings" not in data:
                        data["settings"] = defaults["settings"]
                    if "users" not in data:
                        data["users"] = {}
                    for k, v in defaults["settings"].items():
                        data["settings"].setdefault(k, v)
                    return data
            except Exception as e:
                print(f"[Database] Error reading {self.db_path}: {e}")
        return self._get_default_structure()

    def _save(self):
        try:
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"[Database] Error saving {self.db_path}: {e}")

    def _today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ════════════════ User Queries ════════════════

    def is_registered(self, user_id: int) -> bool:
        with self.lock:
            return str(user_id) in self.data["users"]

    def register_user(self, user_id: int, username: str = "") -> bool:
        with self.lock:
            uid = str(user_id)
            if uid not in self.data["users"]:
                self.data["users"][uid] = {
                    "user_id": user_id,
                    "username": username or "",
                    "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "is_premium": False,
                    "is_banned": False,
                    "today_date": self._today_str(),
                    "downloads_today": 0,
                    "total_downloads": 0
                }
                self._save()
                return True
            return False

    def is_premium(self, user_id: int) -> bool:
        with self.lock:
            u = self.data["users"].get(str(user_id))
            return bool(u and u.get("is_premium", False))

    def set_premium(self, user_id: int, status: bool) -> bool:
        with self.lock:
            uid = str(user_id)
            if uid not in self.data["users"]:
                self.register_user(user_id)
            self.data["users"][uid]["is_premium"] = status
            self._save()
            return True

    def is_banned(self, user_id: int) -> bool:
        with self.lock:
            u = self.data["users"].get(str(user_id))
            return bool(u and u.get("is_banned", False))

    def set_banned(self, user_id: int, status: bool) -> bool:
        with self.lock:
            uid = str(user_id)
            if uid not in self.data["users"]:
                self.register_user(user_id)
            self.data["users"][uid]["is_banned"] = status
            self._save()
            return True

    def get_user_status_label(self, user_id: int, admin_id: int) -> str:
        if user_id == admin_id:
            return "👑 Admin / Owner"
        if self.is_banned(user_id):
            return "🚫 Banned"
        if self.is_premium(user_id):
            return "⭐ Premium Member"
        if self.is_registered(user_id):
            return "👤 Regular User"
        return "❌ Not Registered"

    def get_user_info(self, user_id: int) -> dict:
        with self.lock:
            return self.data["users"].get(str(user_id), {})

    def get_all_user_ids(self) -> list[int]:
        with self.lock:
            return [int(uid) for uid in self.data["users"].keys()]

    # ════════════════ Access & Limits Checking ════════════════

    def check_access(self, user_id: int, admin_id: int) -> tuple[bool, str]:
        """Check if user is allowed to interact with the bot based on bot_mode and ban status."""
        if user_id == admin_id:
            return True, ""

        if self.is_banned(user_id):
            return False, "🚫 <b>ACCESS DENIED:</b> Your account has been banned."

        bot_mode = self.get_setting("bot_mode", "public")
        if bot_mode == "admin_only":
            return False, "🔒 <b>MAINTENANCE:</b> The bot is currently in Admin-Only mode. Please check back later."

        if bot_mode == "premium" and not self.is_premium(user_id):
            return False, "⭐ <b>PREMIUM ONLY:</b> The bot is currently reserved for Premium members. Contact admin to upgrade!"

        if not self.is_registered(user_id):
            return False, "REGISTRATION_REQUIRED"

        return True, ""

    def check_download_limit(self, user_id: int, admin_id: int, total_size_bytes: int) -> tuple[bool, str]:
        """Validate daily download count and max file size limits for regular users."""
        if user_id == admin_id or self.is_premium(user_id):
            return True, ""  # Premium and Admin have UNLIMITED downloads & size!

        with self.lock:
            uid = str(user_id)
            user = self.data["users"].get(uid)
            if not user:
                return False, "User record not found."

            today = self._today_str()
            if user.get("today_date") != today:
                user["today_date"] = today
                user["downloads_today"] = 0

            max_size_gb = float(self.data["settings"].get("max_size_gb_regular", 40.0))
            max_size_bytes = max_size_gb * (1024 ** 3)
            if total_size_bytes > max_size_bytes:
                sz_str = human_readable_size(total_size_bytes)
                return False, (
                    f"⚠️ <b>FILE SIZE LIMIT EXCEEDED</b>\n\n"
                    f"Your requested torrent size is <b>{sz_str}</b>.\n"
                    f"Regular users are limited to <b>{max_size_gb} GB</b> per torrent.\n\n"
                    f"⭐ <i>Upgrade to Premium for unlimited file sizes!</i>"
                )

            daily_limit = int(self.data["settings"].get("daily_limit_regular", 5))
            downloads_today = user.get("downloads_today", 0)
            if downloads_today >= daily_limit:
                return False, (
                    f"⚠️ <b>DAILY LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{daily_limit}/{daily_limit}</b> downloads for today.\n"
                    f"Your quota resets tomorrow at 00:00 UTC.\n\n"
                    f"⭐ <i>Upgrade to Premium for unlimited daily downloads!</i>"
                )

            return True, ""

    def record_download_success(self, user_id: int, admin_id: int):
        """Increment daily and total download counters."""
        with self.lock:
            uid = str(user_id)
            user = self.data["users"].get(uid)
            if user:
                today = self._today_str()
                if user.get("today_date") != today:
                    user["today_date"] = today
                    user["downloads_today"] = 0
                user["downloads_today"] = user.get("downloads_today", 0) + 1
                user["total_downloads"] = user.get("total_downloads", 0) + 1
                self._save()

    # ════════════════ Settings Management ════════════════

    def get_setting(self, key: str, default=None):
        with self.lock:
            return self.data["settings"].get(key, default)

    def set_setting(self, key: str, value) -> bool:
        with self.lock:
            self.data["settings"][key] = value
            self._save()
            return True

    def get_stats(self) -> dict:
        with self.lock:
            total_users = len(self.data["users"])
            premium_users = sum(1 for u in self.data["users"].values() if u.get("is_premium"))
            banned_users = sum(1 for u in self.data["users"].values() if u.get("is_banned"))
            today = self._today_str()
            downloads_today = sum(
                u.get("downloads_today", 0)
                for u in self.data["users"].values()
                if u.get("today_date") == today
            )
            return {
                "total_users": total_users,
                "premium_users": premium_users,
                "banned_users": banned_users,
                "downloads_today": downloads_today,
                "bot_mode": self.data["settings"].get("bot_mode", "public"),
                "daily_limit": self.data["settings"].get("daily_limit_regular", 5),
                "max_size_gb": self.data["settings"].get("max_size_gb_regular", 40.0),
                "required_channel": self.data["settings"].get("required_channel", ""),
                "required_channel_url": self.data["settings"].get("required_channel_url", ""),
            }

db = DatabaseManager()
