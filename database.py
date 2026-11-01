"""
Database Manager Module — Cloud Synced with GitHub API.
Manages users, quotas, channels, and donation configurations.
No premium tier; standard limits apply to all registered members.
"""

import os
import json
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from github_client import GitHubClient
from utils import human_readable_size

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, ".db_cache.json")

class DatabaseManager:
    """Central database managing users, quotas, channels, donations, and cloud synchronization."""

    def __init__(self, cache_path: str = CACHE_FILE):
        self.cache_path = cache_path
        self.lock = threading.RLock()
        self.data = self._load_cache()
        self._github_client: Optional[GitHubClient] = None
        self._init_github()

    def _get_default_structure(self) -> dict:
        return {
            "settings": {
                "bot_mode": "admin_only",  # Default locked to admin_only until DB is connected
                "git_token": "",
                "database_url": "",
                "bot_channel": "",
                "channel_invite": "",
                "results_channel": "",
                "error_channel": "",
                "daily_limit_regular": 5,
                "max_size_gb_regular": 40.0,
                "buzzheavier_account_id": "",
                "gofile_api_token": "",
                # Donation configurations
                "donation_paypal": "",
                "donation_binance_id": "",
                "donation_trc20": "",
                "donation_bep20": "",
                "donation_solana": "",
                "donation_ton": "",
                "donation_erc20": "",
                "donation_arbitrum": "",
                "donation_opbnb": "",
                "donation_note": ""
            },
            "users": {}
        }

    def _load_cache(self) -> dict:
        """Load database from local cache if available."""
        defaults = self._get_default_structure()
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "settings" not in data:
                        data["settings"] = defaults["settings"]
                    if "users" not in data:
                        data["users"] = {}
                    for k, v in defaults["settings"].items():
                        data["settings"].setdefault(k, v)
                    return data
            except Exception as e:
                print(f"[!] Warning reading cache: {e}")
        return defaults

    def _save_cache(self):
        """Persist current state to local cache."""
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Warning saving cache: {e}")

    def _init_github(self):
        token = self.data["settings"].get("git_token", "").strip()
        if token:
            self._github_client = GitHubClient(token)
            threading.Thread(target=self.sync_from_github, daemon=True).start()

    def _today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ════════════════ GitHub Cloud Synchronization ════════════════

    def is_github_connected(self) -> bool:
        """Check if a valid GitHub token is configured."""
        token = self.data["settings"].get("git_token", "").strip()
        if not token:
            return False
        if not self._github_client:
            self._github_client = GitHubClient(token)
        return self._github_client.test_token()

    def sync_from_github(self) -> bool:
        """Fetch remote database and merge with in-memory data."""
        with self.lock:
            url = self.data["settings"].get("database_url", "").strip()
            token = self.data["settings"].get("git_token", "").strip()
            if not token or not url:
                return False

            client = GitHubClient(token)
            remote = client.read_file(url, as_json=True)
            if remote and isinstance(remote, dict):
                remote_settings = remote.get("settings", {})
                remote_users = remote.get("users", {})

                # Merge settings
                for k, v in remote_settings.items():
                    if k != "git_token" or v:
                        self.data["settings"][k] = v

                # Merge users
                for uid, uinfo in remote_users.items():
                    if uid not in self.data["users"]:
                        self.data["users"][uid] = uinfo
                    else:
                        local_u = self.data["users"][uid]
                        if local_u.get("today_date") == uinfo.get("today_date"):
                            local_u["downloads_today"] = max(local_u.get("downloads_today", 0), uinfo.get("downloads_today", 0))
                        local_u["is_banned"] = uinfo.get("is_banned", local_u.get("is_banned", False))

                self._save_cache()
                return True
            return False

    def sync_to_github(self, async_call: bool = True):
        """Push current memory database to GitHub cloud."""
        self._save_cache()
        url = self.data["settings"].get("database_url", "").strip()
        token = self.data["settings"].get("git_token", "").strip()
        if not token or not url:
            return

        def _worker():
            try:
                client = GitHubClient(token)
                with self.lock:
                    payload = dict(self.data)
                client.write_file(url, payload, commit_msg=f"Database update - {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
            except Exception as e:
                print(f"[!] GitHub sync error: {e}")

        if async_call:
            threading.Thread(target=_worker, daemon=True).start()
        else:
            _worker()

    def set_github_token(self, token: str) -> Tuple[bool, str]:
        """Validate and configure GitHub token, automatically provisioning database repo."""
        token = token.strip()
        client = GitHubClient(token)
        if not client.test_token():
            return False, "Invalid GitHub token. Please verify token permissions."

        with self.lock:
            self.data["settings"]["git_token"] = token
            self._github_client = client

            current_url = self.data["settings"].get("database_url", "").strip()
            if not current_url:
                ok, res_url = client.auto_setup_repo_and_file(
                    repo_name="torrent-stream-db",
                    file_path="database.json",
                    initial_content=self.data
                )
                if ok:
                    self.data["settings"]["database_url"] = res_url
                else:
                    info = client.get_token_info()
                    uname = info.get("username", "user")
                    self.data["settings"]["database_url"] = f"https://raw.githubusercontent.com/{uname}/torrent-stream-db/main/database.json"

            self.sync_to_github(async_call=False)
            return True, "Token configured and database synced successfully!"

    def remove_github_token(self) -> bool:
        """Remove GitHub token and revert to admin_only mode."""
        with self.lock:
            self.data["settings"]["git_token"] = ""
            self.data["settings"]["bot_mode"] = "admin_only"
            self._github_client = None
            self._save_cache()
            return True

    def set_database_url(self, url: str) -> Tuple[bool, str]:
        """Update remote database URL."""
        url = url.strip()
        with self.lock:
            self.data["settings"]["database_url"] = url
            self._save_cache()
            if self.is_github_connected():
                self.sync_from_github()
                self.sync_to_github(async_call=True)
            return True, f"Database URL updated to: {url}"

    # ════════════════ Bot Mode Management (Gatekeeper Constraint) ════════════════

    def set_bot_mode(self, mode: str) -> Tuple[bool, str]:
        """
        Switch bot mode between 'admin_only' and 'public'.
        STRICT CONSTRAINT: 'public' mode REQUIRES a valid connected GitHub token!
        """
        mode = mode.lower()
        if mode not in ("admin_only", "public"):
            return False, "Invalid mode. Must be 'admin_only' or 'public'."

        with self.lock:
            if mode == "public":
                if not self.is_github_connected():
                    return False, (
                        "❌ <b>ACTION BLOCKED:</b>\n\n"
                        "You <b>cannot</b> switch to <b>Public</b> mode without configuring a valid GitHub Database Token!\n\n"
                        "Please go to <b>Database (GitHub)</b> in Settings and enter your GitHub token first."
                    )

            self.data["settings"]["bot_mode"] = mode
            self.sync_to_github(async_call=True)
            return True, f"Bot mode switched to <b>{mode.upper()}</b>."

    # ════════════════ User Queries & Quotas ════════════════

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
                    "is_banned": False,
                    "today_date": self._today_str(),
                    "downloads_today": 0,
                    "total_downloads": 0
                }
                self.sync_to_github(async_call=True)
                return True
            return False

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
            self.sync_to_github(async_call=True)
            return True

    def reset_user_daily_limit(self, user_id: int) -> bool:
        with self.lock:
            uid = str(user_id)
            u = self.data["users"].get(uid)
            if u:
                u["downloads_today"] = 0
                u["today_date"] = self._today_str()
                self.sync_to_github(async_call=True)
                return True
            return False

    def get_user_status_label(self, user_id: int, admin_id: int) -> str:
        if user_id == admin_id:
            return "👑 Admin / Owner"
        if self.is_banned(user_id):
            return "🚫 Banned"
        if self.is_registered(user_id):
            return "🟢 Active"
        return "❌ Not Registered"

    def get_user_info(self, user_id: int) -> dict:
        with self.lock:
            return self.data["users"].get(str(user_id), {})

    def get_all_user_ids(self) -> List[int]:
        with self.lock:
            return [int(uid) for uid in self.data["users"].keys()]

    def search_users(self, query: str, filter_type: str = "all") -> List[dict]:
        """Search users by ID or username matching filter."""
        query = query.strip().lower().lstrip("@")
        results = []
        with self.lock:
            for uid_str, u in self.data["users"].items():
                if filter_type == "banned" and not u.get("is_banned"):
                    continue

                uname = (u.get("username") or "").lower()
                if not query or query in uid_str or (uname and query in uname):
                    results.append(u)
        return results

    def get_users_list(self, filter_type: str = "all") -> List[dict]:
        """Get list of users sorted by registration date."""
        with self.lock:
            users = list(self.data["users"].values())
            if filter_type == "banned":
                users = [u for u in users if u.get("is_banned")]
            return sorted(users, key=lambda x: x.get("registered_at", ""), reverse=True)

    # ════════════════ Access & Limits Checking ════════════════

    def check_access(self, user_id: int, admin_id: int) -> Tuple[bool, str]:
        if user_id == admin_id:
            return True, ""

        if self.is_banned(user_id):
            return False, "🚫 <b>ACCESS DENIED:</b> Your account has been banned."

        bot_mode = self.get_setting("bot_mode", "admin_only")
        if bot_mode == "admin_only":
            return False, "🔒 <b>MAINTENANCE:</b> The bot is currently in Admin-Only mode. Please check back later."

        if not self.is_registered(user_id):
            return False, "REGISTRATION_REQUIRED"

        return True, ""

    def check_download_limit(self, user_id: int, admin_id: int, total_size_bytes: int) -> Tuple[bool, str]:
        if user_id == admin_id:
            return True, ""

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
                    f"The maximum allowed size is <b>{max_size_gb} GB</b> per torrent."
                )

            daily_limit = int(self.data["settings"].get("daily_limit_regular", 5))
            downloads_today = user.get("downloads_today", 0)
            if downloads_today >= daily_limit:
                return False, (
                    f"⚠️ <b>DAILY LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{daily_limit}/{daily_limit}</b> downloads for today.\n"
                    f"Your quota resets tomorrow at 00:00 UTC."
                )

            return True, ""

    def record_download_success(self, user_id: int, admin_id: int):
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
                self.sync_to_github(async_call=True)

    # ════════════════ Settings Management ════════════════

    def get_setting(self, key: str, default=None):
        with self.lock:
            return self.data["settings"].get(key, default)

    def set_setting(self, key: str, value) -> bool:
        with self.lock:
            self.data["settings"][key] = value
            self.sync_to_github(async_call=True)
            return True

    def reset_database(self) -> bool:
        """Reset user database while preserving settings."""
        with self.lock:
            self.data["users"] = {}
            self.sync_to_github(async_call=True)
            return True

    def get_stats(self) -> dict:
        with self.lock:
            total_users = len(self.data["users"])
            banned_users = sum(1 for u in self.data["users"].values() if u.get("is_banned"))
            today = self._today_str()
            downloads_today = sum(
                u.get("downloads_today", 0)
                for u in self.data["users"].values()
                if u.get("today_date") == today
            )
            return {
                "total_users": total_users,
                "banned_users": banned_users,
                "downloads_today": downloads_today,
                "bot_mode": self.data["settings"].get("bot_mode", "admin_only"),
                "daily_limit": self.data["settings"].get("daily_limit_regular", 5),
                "max_size_gb": self.data["settings"].get("max_size_gb_regular", 40.0),
                "bot_channel": self.data["settings"].get("bot_channel", ""),
                "channel_invite": self.data["settings"].get("channel_invite", ""),
                "results_channel": self.data["settings"].get("results_channel", ""),
                "error_channel": self.data["settings"].get("error_channel", ""),
                "git_token": self.data["settings"].get("git_token", ""),
                "database_url": self.data["settings"].get("database_url", ""),
                # Donations
                "donation_paypal": self.data["settings"].get("donation_paypal", ""),
                "donation_binance_id": self.data["settings"].get("donation_binance_id", ""),
                "donation_trc20": self.data["settings"].get("donation_trc20", ""),
                "donation_bep20": self.data["settings"].get("donation_bep20", ""),
                "donation_solana": self.data["settings"].get("donation_solana", ""),
                "donation_ton": self.data["settings"].get("donation_ton", ""),
                "donation_erc20": self.data["settings"].get("donation_erc20", ""),
                "donation_arbitrum": self.data["settings"].get("donation_arbitrum", ""),
                "donation_opbnb": self.data["settings"].get("donation_opbnb", ""),
                "donation_note": self.data["settings"].get("donation_note", ""),
            }

db = DatabaseManager()
