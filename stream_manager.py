import os
import sys
import json
import time
import shutil
import glob
import zipfile
import subprocess
import threading
from config import BASE_DIR, BUNDLE_DIR
from database import db

def find_node_executable() -> str:
    """Find the node.exe executable across standard locations."""
    for check_dir in [BUNDLE_DIR, BASE_DIR]:
        local_node = os.path.join(check_dir, "node.exe")
        if os.path.exists(local_node):
            return local_node

    which_node = shutil.which("node") or shutil.which("node.exe")
    if which_node:
        return which_node

    candidates = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\node\node.exe"),
        os.path.expandvars(r"%APPDATA%\npm\node.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        pattern = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages", "**", "node.exe")
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    return "node"

class StreamController:
    """Provides thread-safe pause, resume, and stop controls for an active streamer process."""
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.is_paused: bool = False
        self._lock = threading.Lock()

    def bind(self, proc: subprocess.Popen):
        with self._lock:
            self.proc = proc
            if self.is_paused:
                self._send_cmd("PAUSE")

    def pause(self):
        with self._lock:
            self.is_paused = True
            self._send_cmd("PAUSE")

    def resume(self):
        with self._lock:
            self.is_paused = False
            self._send_cmd("RESUME")

    def stop(self):
        with self._lock:
            self._send_cmd("STOP")
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass

    def _send_cmd(self, cmd: str):
        if self.proc and self.proc.poll() is None and self.proc.stdin:
            try:
                self.proc.stdin.write(f"{cmd}\n")
                self.proc.stdin.flush()
            except Exception:
                pass


class StreamManager:
    """
    Manages live memory-streaming from BitTorrent swarm directly to Buzzheavier or Gofile.io.
    Zero bytes are written to the RDP hard disk.
    """
    def __init__(self):
        self.node_path = find_node_executable()
        self.streamer_script = os.path.join(BASE_DIR, "streamer.mjs")
        if not os.path.exists(self.streamer_script):
            bundle_script = os.path.join(BUNDLE_DIR, "streamer.mjs")
            if os.path.exists(bundle_script):
                self.streamer_script = bundle_script
            else:
                raise FileNotFoundError(f"Streamer script not found in {BASE_DIR} or {BUNDLE_DIR}")

        self._ensure_node_modules()

    def _ensure_node_modules(self):
        """Extract bundled node_modules.zip if node_modules folder is absent."""
        if os.path.isdir(os.path.join(BASE_DIR, "node_modules")) or os.path.isdir(os.path.join(BUNDLE_DIR, "node_modules")):
            return

        for search_dir in [BUNDLE_DIR, BASE_DIR]:
            zip_file = os.path.join(search_dir, "node_modules.zip")
            if os.path.exists(zip_file):
                try:
                    with zipfile.ZipFile(zip_file, "r") as zf:
                        zf.extractall(search_dir)
                    return
                except Exception as e:
                    print(f"[!] Warning unpacking node_modules.zip: {e}")

    def stream_file(
        self,
        torrent_source: str,
        file_zero_index: int,
        destination: str = "buzzheavier",
        progress_callback=None,
        cancel_event: threading.Event = None,
        controller: Optional[StreamController] = None,
    ) -> tuple[bool, str]:
        """
        Stream a specific file from torrent in RAM straight to the chosen destination (buzzheavier or gofile).
        Returns:
            (True, download_url) or (False, error_message)
        """
        cmd = [
            self.node_path,
            self.streamer_script,
            "--source",
            torrent_source,
            "--file-index",
            str(file_zero_index),
            "--destination",
            destination.lower(),
        ]

        buzz_acc = db.get_setting("buzzheavier_account_id", "")
        gofile_tok = db.get_setting("gofile_api_token", "")
        if destination.lower() == "buzzheavier" and buzz_acc:
            cmd.extend(["--account-id", buzz_acc])
        elif destination.lower() == "gofile" and gofile_tok:
            cmd.extend(["--gofile-token", gofile_tok])

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        # Build environment with NODE_PATH pointing to node_modules in BASE_DIR and BUNDLE_DIR
        env = os.environ.copy()
        node_paths = [
            os.path.join(BASE_DIR, "node_modules"),
            os.path.join(BUNDLE_DIR, "node_modules"),
            os.path.join(os.path.dirname(self.streamer_script), "node_modules"),
        ]
        existing_node_path = env.get("NODE_PATH", "")
        if existing_node_path:
            node_paths.append(existing_node_path)
        env["NODE_PATH"] = os.pathsep.join(node_paths)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
                env=env,
                creationflags=creationflags,
            )
            if controller:
                controller.bind(proc)
        except Exception as e:
            return False, f"Failed to start streaming process: {e}"

        result_url = None
        error_msg = None

        while True:
            if cancel_event and cancel_event.is_set():
                if controller:
                    controller.stop()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                return False, "Stream cancelled by user."

            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                etype = event.get("type")

                if etype == "progress":
                    if progress_callback:
                        progress_callback(event)

                elif etype == "done":
                    result_url = event.get("url")

                elif etype == "error":
                    error_msg = event.get("message")

            except json.JSONDecodeError:
                pass

        proc.poll()
        if proc.returncode != 0 and not result_url:
            stderr_out = proc.stderr.read().strip()
            return False, error_msg or stderr_out or "Streaming process exited unexpectedly."

        if result_url:
            return True, result_url

        return False, error_msg or "Stream finished without returning a URL."

    def fetch_magnet_metadata(self, magnet_link: str, output_path: str, timeout: int = 45) -> tuple[bool, str]:
        """
        Fast WebTorrent fallback to fetch magnet metadata and write .torrent file.
        Returns:
            (True, output_path) or (False, error_message)
        """
        cmd = [
            self.node_path,
            self.streamer_script,
            "--source",
            magnet_link,
            "--get-metadata",
            "--output",
            output_path,
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = os.environ.copy()
        node_paths = [
            os.path.join(BASE_DIR, "node_modules"),
            os.path.join(BUNDLE_DIR, "node_modules"),
            os.path.join(os.path.dirname(self.streamer_script), "node_modules"),
        ]
        existing_node_path = env.get("NODE_PATH", "")
        if existing_node_path:
            node_paths.append(existing_node_path)
        env["NODE_PATH"] = os.pathsep.join(node_paths)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
                env=env,
                creationflags=creationflags,
            )
            stdout_data, stderr_data = proc.communicate(timeout=timeout)
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True, output_path
            return False, stderr_data.strip() or "WebTorrent could not resolve metadata within timeout."
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "WebTorrent metadata resolution timed out."
        except Exception as e:
            return False, str(e)
