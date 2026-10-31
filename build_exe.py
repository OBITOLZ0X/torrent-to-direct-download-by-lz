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
        "--hidden-import",
        "telebot",
        "--hidden-import",
        "requests",
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
    if os.path.exists(dist_exe):
        # Also copy helper files alongside dist_exe for convenience
        for fname in ["streamer.mjs", "aria2c.exe", "package.json"]:
            src = os.path.join(BASE_DIR, fname)
            dst = os.path.join(dist_dir, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)

        print("\n" + "=" * 60)
        print("  BUILD SUCCESSFUL!")
        print(f"  Standalone Executable created at: {dist_exe}")
        print("=" * 60)
    else:
        print("\n[!] Build completed. Check 'dist' folder.")

if __name__ == "__main__":
    build()
