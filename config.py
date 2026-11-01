import os
import sys
import json
import shutil
import glob

# Ensure UTF-8 output on Windows consoles
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

AUTH_FILE = os.path.join(BASE_DIR, ".auth")
OLD_CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
METADATA_DIR = os.path.join(DOWNLOADS_DIR, "metadata")

os.makedirs(METADATA_DIR, exist_ok=True)

# Terminal styling helpers (Anya Bot Theme)
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

if os.name == "nt":
    os.system("color")

def load_or_prompt_auth() -> dict:
    """Load credentials from .auth or prompt in console on first run."""
    auth = {}

    # Migrate from old config.json if present, then delete config.json
    if os.path.exists(OLD_CONFIG_FILE):
        try:
            with open(OLD_CONFIG_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
                if old.get("bot_token"):
                    auth["bot_token"] = old["bot_token"].strip()
                if old.get("admin_id"):
                    auth["admin_id"] = int(old["admin_id"])
        except Exception:
            pass
        try:
            os.remove(OLD_CONFIG_FILE)
            print(f"{GREEN}[✓] Removed legacy config.json (settings moved to cloud DB){RESET}")
        except Exception:
            pass

    # Read .auth file if exists
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                auth.update(loaded)
        except Exception:
            pass

    token = auth.get("bot_token", "").strip()
    admin_id = auth.get("admin_id", 0)

    if not token or token == "YOUR_BOT_TOKEN_HERE" or not admin_id:
        if os.name == "nt":
            os.system("title tDownUp - Initial Setup")
        print("\n" + CYAN + "=" * 65 + RESET)
        print(f"{BOLD}{WHITE}             ⚡ tDownUp BOT - INITIAL SETUP ⚡{RESET}")
        print(CYAN + "=" * 65 + RESET)
        print(f"{YELLOW}[!] No saved credentials found. Please enter bot credentials:{RESET}\n")

        while not token or token == "YOUR_BOT_TOKEN_HERE":
            try:
                token = input(f"  {CYAN}▸{RESET} {WHITE}Enter Telegram Bot Token (from @BotFather):{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}[!] Setup cancelled by user.{RESET}")
                input("\nPress Enter to exit...")
                sys.exit(1)
            if not token:
                print(f"  {RED}✗ Token cannot be empty. Try again.{RESET}")

        while not admin_id:
            try:
                raw_admin = input(f"  {CYAN}▸{RESET} {WHITE}Enter Admin Chat ID (e.g. 123456789 from @userinfobot):{RESET} ").strip()
                if raw_admin.isdigit() or (raw_admin.startswith("-") and raw_admin[1:].isdigit()):
                    admin_id = int(raw_admin)
                else:
                    print(f"  {RED}✗ Please enter a valid numerical Chat ID.{RESET}")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}[!] Setup cancelled by user.{RESET}")
                input("\nPress Enter to exit...")
                sys.exit(1)

        auth["bot_token"] = token
        auth["admin_id"] = admin_id

        try:
            with open(AUTH_FILE, "w", encoding="utf-8") as f:
                json.dump(auth, f, indent=2)
            print(f"\n  {GREEN}✓{RESET} {WHITE}Credentials saved successfully.{RESET}\n")
        except Exception as e:
            print(f"  {RED}✗ Failed to write credentials: {e}{RESET}")

    return auth

AUTH_DATA = load_or_prompt_auth()
BOT_TOKEN = AUTH_DATA.get("bot_token", "")
ADMIN_ID = AUTH_DATA.get("admin_id", 0)

ARIA2_PORT = 6800
ARIA2_RPC_SECRET = ""
ARIA2_RPC_URL = f"http://localhost:{ARIA2_PORT}/jsonrpc"
PROGRESS_UPDATE_INTERVAL = 2.5
FILES_PER_PAGE = 8

def find_aria2_executable() -> str:
    """Locate the aria2c executable across common installation paths."""
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
