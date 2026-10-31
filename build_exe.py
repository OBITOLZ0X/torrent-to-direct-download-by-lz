import os
import sys
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def build():
    print("=" * 60)
    print("       Building Standalone TorrentStreamBot.exe        ")
    print("=" * 60)

    # Ensure PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("[*] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # PyInstaller arguments
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",  # onedir or onefile (onedir is much faster to start and preserves node_modules reliably)
        "--windowed", # remove console if desired or console mode for setup prompt
        # We need console mode because the bot prompts for Bot Token & Admin ID on first run!
    ]

    # Re-evaluate: Since the bot prompts for token & admin ID on first run, it MUST have console!
    # Ensure node_modules.zip exists
    zip_path = os.path.join(BASE_DIR, "node_modules.zip")
    node_modules_dir = os.path.join(BASE_DIR, "node_modules")
    if not os.path.exists(zip_path) and os.path.exists(node_modules_dir):
        print("[*] Creating node_modules.zip for standalone embedding...")
        shutil.make_archive(os.path.join(BASE_DIR, "node_modules"), "zip", node_modules_dir)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--console",
        "--name",
        "TorrentStreamBot",
        "--add-data",
        f"streamer.mjs{os.pathsep}.",
        "--add-data",
        f"package.json{os.pathsep}.",
        "--add-data",
        f"aria2c.exe{os.pathsep}.",
        "--add-data",
        f"node_modules.zip{os.pathsep}.",
        "--hidden-import",
        "telebot",
        "--hidden-import",
        "requests",
        "--hidden-import",
        "github_client",
        "--hidden-import",
        "torrentool",
        "bot.py",
    ]

    print("[*] Running PyInstaller command:")
    print(" ".join(cmd))
    print()

    subprocess.check_call(cmd, cwd=BASE_DIR)

    dist_dir = os.path.join(BASE_DIR, "dist")
    dist_exe = os.path.join(dist_dir, "TorrentStreamBot.exe")

    # Clean up dist so ONLY TorrentStreamBot.exe exists there
    if os.path.exists(dist_dir):
        for item in os.listdir(dist_dir):
            if item.lower() != "torrentstreambot.exe":
                item_path = os.path.join(dist_dir, item)
                if os.path.isfile(item_path):
                    os.remove(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)

    if os.path.exists(dist_exe):
        print("\n" + "=" * 60)
        print("  BUILD SUCCESSFUL!")
        print(f"  Single Standalone Executable created at: {dist_exe}")
        print("  Everything (Python, Aria2, Streamer, Libraries) is bundled inside this ONE file!")
        print("=" * 60)
    else:
        print("\n[!] Build completed. Check 'dist' folder.")

if __name__ == "__main__":
    build()
