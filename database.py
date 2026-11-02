"""
Database Manager Module — Cloud Synced with GitHub API.
Manages users, quotas, channels, and donation configurations.
No premium tier; standard limits apply to all registered members.
"""

import os
import json
import time
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
        self._sync_lock = threading.Lock()
        self.data = self._load_cache()
        self._github_client: Optional[GitHubClient] = None
        self._has_pending_changes: bool = False
        self._sync_interval: int = 120  # Automatic cloud sync every 2 minutes (120s)
        self._last_sync_time: float = 0.0
        self._last_sync_status: str = "Idle"
        self._stop_sync = threading.Event()
        self._sync_thread: Optional[threading.Thread] = None

        self._init_github()
        self._start_sync_thread()

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
                "daily_bandwidth_gb_regular": 100.0,
                "buzzheavier_account_id": "",
                "gofile_api_token": "",
                "vikingfile_user_hash": "",
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
            # Startup background pull: download existing remote DB into local cache
            threading.Thread(target=self._startup_sync_from_github, daemon=True, name="GitHubStartupSync").start()

    def _startup_sync_from_github(self):
        """Startup worker to download remote database without stalling startup."""
        try:
            print("[Database] Initializing connection to GitHub cloud database...")
            if self.sync_from_github():
                print(f"[Database] Successfully loaded database from GitHub ({len(self.data.get('users', {}))} users).")
            else:
                print("[Database] GitHub database could not be loaded or is empty; using local cache.")
        except Exception as e:
            print(f"[Database] Startup sync warning: {e}")

    def _start_sync_thread(self):
        """Background daemon thread that auto-syncs local changes to GitHub every 2 minutes (120 seconds)."""
        if self._sync_thread and self._sync_thread.is_alive():
            return

        def _worker():
            while not self._stop_sync.is_set():
                # Sleep in 1-second ticks so shutdown or interruptions are responsive
                for _ in range(self._sync_interval):
                    if self._stop_sync.is_set():
                        break
                    time.sleep(1)

                if self._stop_sync.is_set():
                    break

                # Every 2 minutes (120s), check if there are pending local changes
                try:
                    if self.is_github_connected() and self._has_pending_changes:
                        print("[Database] 2-minute auto-sync timer: pushing pending local changes to GitHub...")
                        ok, msg = self.sync_to_github_now(
                            commit_msg=f"Auto-sync database - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                        )
                        if ok:
                            print(f"[Database] Auto-sync to GitHub completed successfully ({len(self.data.get('users', {}))} users).")
                        else:
                            print(f"[Database] Auto-sync to GitHub failed: {msg}")
                except Exception as ex:
                    print(f"[Database] Auto-sync worker exception: {ex}")

        self._sync_thread = threading.Thread(target=_worker, daemon=True, name="GitHubAutoSyncThread")
        self._sync_thread.start()

    def _today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ════════════════ GitHub Cloud Synchronization ════════════════

    def is_github_connected(self) -> bool:
        """Check if a valid GitHub token is configured."""
        token = self.data["settings"].get("git_token", "").strip()
        if not token:
            return False
        if not self._github_client or self._github_client.token != token:
            self._github_client = GitHubClient(token)
        return self._github_client.test_token()

    def has_pending_changes(self) -> bool:
        with self.lock:
            return self._has_pending_changes

    def get_last_sync_time(self) -> float:
        with self.lock:
            return self._last_sync_time

    def get_last_sync_display(self) -> str:
        with self.lock:
            if not self._last_sync_time:
                return "Never"
            elapsed = int(time.time() - self._last_sync_time)
            if elapsed < 60:
                return f"{elapsed}s ago"
            mins = elapsed // 60
            return f"{mins}m {elapsed % 60}s ago"

    def sync_from_github(self) -> bool:
        """Fetch remote database and merge with in-memory data."""
        url = self.data["settings"].get("database_url", "").strip()
        token = self.data["settings"].get("git_token", "").strip()
        if not token or not url:
            return False

        try:
            client = GitHubClient(token)
            remote = client.read_file(url, as_json=True)
            if remote and isinstance(remote, dict):
                remote_settings = remote.get("settings", {})
                remote_users = remote.get("users", {})

                with self.lock:
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
                                local_u["bytes_today"] = max(local_u.get("bytes_today", 0), uinfo.get("bytes_today", 0))
                            local_u["is_banned"] = uinfo.get("is_banned", local_u.get("is_banned", False))
                            local_u["total_downloads"] = max(local_u.get("total_downloads", 0), uinfo.get("total_downloads", 0))
                            local_u["total_bytes"] = max(local_u.get("total_bytes", 0), uinfo.get("total_bytes", 0))

                    self._has_pending_changes = False
                    self._last_sync_time = time.time()
                    self._last_sync_status = "Success (pulled from cloud)"
                    self._save_cache()
                return True
        except Exception as e:
            print(f"[!] sync_from_github error: {e}")
        return False

    def sync_to_github_now(self, commit_msg: Optional[str] = None) -> Tuple[bool, str]:
        """
        Immediately push current memory database to GitHub cloud.
        Thread-safe and protected by _sync_lock.
        """
        token = self.data["settings"].get("git_token", "").strip()
        url = self.data["settings"].get("database_url", "").strip()
        if not token:
            return False, "No GitHub token configured."
        if not url:
            return False, "No database URL configured."

        with self._sync_lock:
            with self.lock:
                self._save_cache()
                payload = json.loads(json.dumps(self.data))

            try:
                client = GitHubClient(token)
                msg = commit_msg or f"Update database - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                ok = client.write_file(url, payload, commit_msg=msg)
                if ok:
                    with self.lock:
                        self._has_pending_changes = False
                        self._last_sync_time = time.time()
                        self._last_sync_status = "Success"
                    return True, "Cloud database synchronized successfully."
                else:
                    with self.lock:
                        self._last_sync_status = "Failed to write file"
                    return False, "Failed to write database to GitHub. Please check repository permissions."
            except Exception as e:
                with self.lock:
                    self._last_sync_status = f"Error: {e}"
                return False, f"GitHub sync error: {e}"

    def sync_to_github(self, async_call: bool = True):
        """Backward-compatible helper. Schedules or executes immediate sync."""
        if async_call:
            threading.Thread(target=self.sync_to_github_now, daemon=True).start()
        else:
            self.sync_to_github_now()

    def set_github_token(self, token: str) -> Tuple[bool, str]:
        """
        Validate and configure GitHub token.
        Downloads the existing database from GitHub first (Local-First architecture),
        then starts the 2-minute periodic auto-sync.
        """
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
                    current_url = res_url
                else:
                    info = client.get_token_info()
                    uname = info.get("username", "user")
                    current_url = f"https://raw.githubusercontent.com/{uname}/torrent-stream-db/main/database.json"
                    self.data["settings"]["database_url"] = current_url

            self._save_cache()

        # Step 1: Immediately download/load existing database from GitHub!
        downloaded = self.sync_from_github()
        with self.lock:
            self._has_pending_changes = False
            self._last_sync_time = time.time()
            self._last_sync_status = "Initial sync"
            users_count = len(self.data.get("users", {}))

        # Step 2: Ensure the 2-minute sync thread is running
        self._start_sync_thread()

        if downloaded:
            return True, f"GitHub connected! Downloaded database from GitHub ({users_count} users loaded). Local-first mode active with auto-sync every 2 minutes."
        else:
            self.sync_to_github_now(commit_msg="Initialize database.json")
            return True, "GitHub connected! Initialized remote database. Local-first mode active with auto-sync every 2 minutes."

    def remove_github_token(self) -> bool:
        """Remove GitHub token and revert to admin_only mode."""
        with self.lock:
            self.data["settings"]["git_token"] = ""
            self.data["settings"]["bot_mode"] = "admin_only"
            self._github_client = None
            self._has_pending_changes = False
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
                self._has_pending_changes = False
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
            self._has_pending_changes = True
            self._save_cache()
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
                    "bytes_today": 0,
                    "total_downloads": 0,
                    "total_bytes": 0
                }
                self._has_pending_changes = True
                self._save_cache()
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
            self._has_pending_changes = True
            self._save_cache()
            return True

    def reset_user_daily_limit(self, user_id: int) -> bool:
        with self.lock:
            uid = str(user_id)
            u = self.data["users"].get(uid)
            if u:
                u["downloads_today"] = 0
                u["bytes_today"] = 0
                u["today_date"] = self._today_str()
                self._has_pending_changes = True
                self._save_cache()
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

    def check_user_can_request(self, user_id: int, admin_id: int) -> Tuple[bool, str]:
        """Early quota check before resolving metadata: verifies 5 torrents limit and 100 GB daily limit."""
        if user_id == admin_id:
            return True, ""

        with self.lock:
            uid = str(user_id)
            user = self.data["users"].get(uid)
            if not user:
                return True, ""

            today = self._today_str()
            if user.get("today_date") != today:
                user["today_date"] = today
                user["downloads_today"] = 0
                user["bytes_today"] = 0

            daily_limit = int(self.data["settings"].get("daily_limit_regular", 5))
            downloads_today = user.get("downloads_today", 0)
            if downloads_today >= daily_limit:
                return False, (
                    f"⚠️ <b>DAILY LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{daily_limit}/{daily_limit}</b> torrent transfers for today.\n"
                    f"Your daily quota resets tomorrow at 00:00 UTC."
                )

            daily_bw_gb = float(self.data["settings"].get("daily_bandwidth_gb_regular", 100.0))
            daily_bw_bytes = daily_bw_gb * (1024 ** 3)
            bytes_today = user.get("bytes_today", 0)
            if bytes_today >= daily_bw_bytes:
                return False, (
                    f"⚠️ <b>DAILY BANDWIDTH LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{human_readable_size(bytes_today)} / {int(daily_bw_gb)} GB</b> of your daily bandwidth.\n"
                    f"Your quota resets tomorrow at 00:00 UTC."
                )

            return True, ""

    def check_download_limit(
        self,
        user_id: int,
        admin_id: int,
        total_size_bytes: int = 0,
        files: Optional[List[dict]] = None
    ) -> Tuple[bool, str]:
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
                user["bytes_today"] = 0

            # 1. Daily torrents limit (5 torrents per day)
            daily_limit = int(self.data["settings"].get("daily_limit_regular", 5))
            downloads_today = user.get("downloads_today", 0)
            if downloads_today >= daily_limit:
                return False, (
                    f"⚠️ <b>DAILY LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{daily_limit}/{daily_limit}</b> torrent transfers for today.\n"
                    f"Your quota resets tomorrow at 00:00 UTC."
                )

            # 2. Single file size limit (40 GB max per single file)
            max_file_size_gb = float(self.data["settings"].get("max_size_gb_regular", 40.0))
            max_file_size_bytes = max_file_size_gb * (1024 ** 3)

            if files:
                oversized = [f for f in files if f.get("length", 0) > max_file_size_bytes]
                if oversized:
                    bad_file = oversized[0]
                    bad_name = bad_file.get("name", "Unknown File")
                    bad_size = human_readable_size(bad_file.get("length", 0))
                    return False, (
                        f"⚠️ <b>SINGLE FILE SIZE LIMIT EXCEEDED</b>\n\n"
                        f"File: <code>{bad_name}</code> (<b>{bad_size}</b>)\n"
                        f"The maximum allowed size for a single file is <b>{int(max_file_size_gb)} GB</b>."
                    )
            elif total_size_bytes > max_file_size_bytes:
                sz_str = human_readable_size(total_size_bytes)
                return False, (
                    f"⚠️ <b>FILE SIZE LIMIT EXCEEDED</b>\n\n"
                    f"Requested size: <b>{sz_str}</b>\n"
                    f"The maximum allowed size for a single file is <b>{int(max_file_size_gb)} GB</b>."
                )

            # 3. Daily total bandwidth limit (100 GB across all 5 torrents)
            daily_bw_gb = float(self.data["settings"].get("daily_bandwidth_gb_regular", 100.0))
            daily_bw_bytes = daily_bw_gb * (1024 ** 3)
            bytes_today = user.get("bytes_today", 0)
            requested_bytes = sum(f.get("length", 0) for f in files) if files else total_size_bytes

            if bytes_today >= daily_bw_bytes:
                return False, (
                    f"⚠️ <b>DAILY BANDWIDTH LIMIT REACHED</b>\n\n"
                    f"You have used all <b>{human_readable_size(bytes_today)} / {int(daily_bw_gb)} GB</b> of your daily bandwidth.\n"
                    f"Your quota resets tomorrow at 00:00 UTC."
                )

            if (bytes_today + requested_bytes) > daily_bw_bytes:
                remaining_bytes = max(0, daily_bw_bytes - bytes_today)
                return False, (
                    f"⚠️ <b>DAILY BANDWIDTH LIMIT EXCEEDED</b>\n\n"
                    f"Today's usage: <b>{human_readable_size(bytes_today)} / {int(daily_bw_gb)} GB</b>\n"
                    f"Transfer requires: <b>{human_readable_size(requested_bytes)}</b> (Remaining: <b>{human_readable_size(remaining_bytes)}</b>)\n\n"
                    f"The maximum total bandwidth is <b>{int(daily_bw_gb)} GB</b> per day.\n"
                    f"Your quota resets tomorrow at 00:00 UTC."
                )

            return True, ""

    def record_download_success(self, user_id: int, admin_id: int, bytes_transferred: int = 0):
        with self.lock:
            uid = str(user_id)
            user = self.data["users"].get(uid)
            if user:
                today = self._today_str()
                if user.get("today_date") != today:
                    user["today_date"] = today
                    user["downloads_today"] = 0
                    user["bytes_today"] = 0
                user["downloads_today"] = user.get("downloads_today", 0) + 1
                user["bytes_today"] = user.get("bytes_today", 0) + max(0, bytes_transferred)
                user["total_downloads"] = user.get("total_downloads", 0) + 1
                user["total_bytes"] = user.get("total_bytes", 0) + max(0, bytes_transferred)
                self._has_pending_changes = True
                self._save_cache()

    # ════════════════ Settings Management ════════════════

    def get_setting(self, key: str, default=None):
        with self.lock:
            return self.data["settings"].get(key, default)

    def set_setting(self, key: str, value) -> bool:
        with self.lock:
            self.data["settings"][key] = value
            self._has_pending_changes = True
            self._save_cache()
            return True

    def reset_database(self) -> bool:
        """Reset user database while preserving settings."""
        with self.lock:
            self.data["users"] = {}
            self._has_pending_changes = True
            self._save_cache()
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
            bytes_today = sum(
                u.get("bytes_today", 0)
                for u in self.data["users"].values()
                if u.get("today_date") == today
            )
            return {
                "total_users": total_users,
                "banned_users": banned_users,
                "downloads_today": downloads_today,
                "bytes_today": bytes_today,
                "bot_mode": self.data["settings"].get("bot_mode", "admin_only"),
                "daily_limit": self.data["settings"].get("daily_limit_regular", 5),
                "max_size_gb": self.data["settings"].get("max_size_gb_regular", 40.0),
                "daily_bandwidth_gb": self.data["settings"].get("daily_bandwidth_gb_regular", 100.0),
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
