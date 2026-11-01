import os
import sys
import json
import time
import requests
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
    admin_username = auth.get("admin_username", "")

    if not token or token == "YOUR_BOT_TOKEN_HERE" or not admin_id:
        if os.name == "nt":
            os.system("title tDownUp Bot V2 - Initial Setup")

        def _clear_console():
            os.system("cls" if os.name == "nt" else "clear")

        def _render_setup_screen(verified_token_msg: str = "", verified_admin_msg: str = ""):
            _clear_console()
            width = 65
            print("\n" + CYAN + "=" * width + RESET)
            raw_title = "⚡ tDownUp BOT V2 - INITIAL SETUP ⚡"
            pad = (width - len(raw_title)) // 2
            print(f"{BOLD}{WHITE}{' ' * pad}{raw_title}{RESET}")
            print(CYAN + "=" * width + RESET)
            print(f"{YELLOW}[!] Please configure bot credentials below:{RESET}\n")
            if verified_token_msg:
                print(f"  {GREEN}✓{RESET} {WHITE}Token verified:{RESET} {GREEN}{verified_token_msg}{RESET}")
            if verified_admin_msg:
                print(f"  {GREEN}✓{RESET} {WHITE}Admin verified:{RESET} {YELLOW}{verified_admin_msg}{RESET}")
            if verified_token_msg or verified_admin_msg:
                print()

        # Step 1: Prompt Bot Token
        bot_username = ""
        while not token or token == "YOUR_BOT_TOKEN_HERE":
            _render_setup_screen()
            try:
                entered_token = input(f"  {CYAN}▸{RESET} {WHITE}Enter Telegram Bot Token (from @BotFather):{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}[!] Setup cancelled by user.{RESET}")
                input("\nPress Enter to exit...")
                sys.exit(1)

            if not entered_token:
                continue

            try:
                resp = requests.get(f"https://api.telegram.org/bot{entered_token}/getMe", timeout=10).json()
                if resp.get("ok") and "username" in resp.get("result", {}):
                    token = entered_token
                    bot_username = f"@{resp['result']['username']}"
                    break
                else:
                    _render_setup_screen()
                    print(f"  {RED}✗ Invalid Bot Token. Telegram responded: {resp.get('description', 'Unauthorized')}{RESET}\n")
                    time.sleep(2)
            except Exception:
                token = entered_token
                bot_username = "Verified"
                break

        # Step 2: Prompt Admin Chat ID
        while not admin_id:
            _render_setup_screen(verified_token_msg=bot_username)
            try:
                raw_admin = input(f"  {CYAN}▸{RESET} {WHITE}Enter Admin Chat ID (e.g. 7173671590 from @userinfobot):{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{RED}[!] Setup cancelled by user.{RESET}")
                input("\nPress Enter to exit...")
                sys.exit(1)

            if raw_admin.isdigit() or (raw_admin.startswith("-") and raw_admin[1:].isdigit()):
                admin_id = int(raw_admin)
                try:
                    chat_resp = requests.get(
                        f"https://api.telegram.org/bot{token}/getChat",
                        params={"chat_id": admin_id},
                        timeout=10
                    ).json()
                    if chat_resp.get("ok"):
                        res = chat_resp["result"]
                        if res.get("username"):
                            admin_username = f"@{res['username']}"
                        elif res.get("first_name"):
                            admin_username = res["first_name"]
                except Exception:
                    pass
                if not admin_username:
                    admin_username = str(admin_id)
                break
            else:
                _render_setup_screen(verified_token_msg=bot_username)
                print(f"  {RED}✗ Please enter a valid numerical Chat ID.{RESET}\n")
                time.sleep(1.5)

        auth["bot_token"] = token
        auth["admin_id"] = admin_id
        if admin_username:
            auth["admin_username"] = admin_username

        _render_setup_screen(verified_token_msg=bot_username, verified_admin_msg=admin_username)
        print(f"  {GREEN}✓{RESET} {WHITE}Credentials saved successfully.{RESET}")

        try:
            with open(AUTH_FILE, "w", encoding="utf-8") as f:
                json.dump(auth, f, indent=2)
        except Exception as e:
            print(f"  {RED}✗ Failed to write credentials: {e}{RESET}")

        time.sleep(1)
        _clear_console()

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
