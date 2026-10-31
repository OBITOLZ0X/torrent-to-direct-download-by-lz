import os
import sys
import json
import shutil
import glob

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
METADATA_DIR = os.path.join(DOWNLOADS_DIR, "metadata")
DATABASE_FILE = os.path.join(BASE_DIR, "database.json")

os.makedirs(METADATA_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "bot_token": "",
    "admin_id": 0,
    "buzzheavier_account_id": "",
    "gofile_api_token": "",
    "aria2_port": 6800,
    "aria2_path": "",
    "progress_update_interval": 2.5,
    "files_per_page": 8
}

def load_or_prompt_config() -> dict:
    """Load configuration from config.json or prompt in console on first run."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                config.update(loaded)
        except Exception as e:
            print(f"[!] Warning reading config.json: {e}")

    # Check if we need interactive setup
    token = config.get("bot_token", "").strip()
    admin_id = config.get("admin_id", 0)

    if not token or token == "YOUR_BOT_TOKEN_HERE" or not admin_id:
        print("\n" + "=" * 60)
        print("      Telegram Torrent-to-Stream Bot - Initial Setup")
        print("=" * 60)
        print("[!] No configuration found. Please enter required details:\n")

        while not token or token == "YOUR_BOT_TOKEN_HERE":
            try:
                token = input("Enter Telegram Bot Token (from @BotFather): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Setup cancelled.")
                sys.exit(1)
            if not token:
                print("[-] Token cannot be empty. Try again.")

        while not admin_id:
            try:
                raw_admin = input("Enter Admin Chat ID (e.g. 123456789 from @userinfobot): ").strip()
                if raw_admin.isdigit() or (raw_admin.startswith("-") and raw_admin[1:].isdigit()):
                    admin_id = int(raw_admin)
                else:
                    print("[-] Please enter a valid numerical Chat ID.")
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Setup cancelled.")
                sys.exit(1)

        config["bot_token"] = token
        config["admin_id"] = admin_id

        # Save to config.json
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            print(f"[+] Configuration saved successfully to: {CONFIG_FILE}\n")
        except Exception as e:
            print(f"[!] Failed to write config.json: {e}")

    return config

# Load config once
CURRENT_CONFIG = load_or_prompt_config()

BOT_TOKEN = CURRENT_CONFIG.get("bot_token", "")
ADMIN_ID = CURRENT_CONFIG.get("admin_id", 0)
BUZZHEAVIER_ACCOUNT_ID = CURRENT_CONFIG.get("buzzheavier_account_id", "")
GOFILE_API_TOKEN = CURRENT_CONFIG.get("gofile_api_token", "")
ARIA2_PORT = int(CURRENT_CONFIG.get("aria2_port", 6800))
ARIA2_RPC_URL = f"http://localhost:{ARIA2_PORT}/jsonrpc"
PROGRESS_UPDATE_INTERVAL = float(CURRENT_CONFIG.get("progress_update_interval", 2.5))
FILES_PER_PAGE = int(CURRENT_CONFIG.get("files_per_page", 8))

def find_aria2_executable() -> str:
    """Locate the aria2c executable across common installation paths."""
    custom_path = CURRENT_CONFIG.get("aria2_path")
    if custom_path and os.path.exists(custom_path):
        return custom_path

    # Check system PATH
    which_path = shutil.which("aria2c") or shutil.which("aria2c.exe")
    if which_path:
        return which_path

    # Check current directory & bundle directory
    for test_dir in [BASE_DIR, BUNDLE_DIR]:
        local_exe = os.path.join(test_dir, "aria2c.exe")
        if os.path.exists(local_exe):
            return local_exe

    # Check Windows WinGet directory
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        pattern = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages", "**", "aria2c.exe")
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    return "aria2c"

ARIA2_PATH = find_aria2_executable()

def update_config_value(key: str, value) -> bool:
    """Update a setting and persist to config.json."""
    global CURRENT_CONFIG
    CURRENT_CONFIG[key] = value
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CURRENT_CONFIG, f, indent=4)
        return True
    except Exception as e:
        print(f"[!] Error updating config.json: {e}")
        return False
