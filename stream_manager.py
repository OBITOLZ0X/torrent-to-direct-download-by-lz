import os
import sys
import json
import time
import subprocess
import threading
from config import BASE_DIR, BUNDLE_DIR, BUZZHEAVIER_ACCOUNT_ID, GOFILE_API_TOKEN

class StreamManager:
    """
    Manages live memory-streaming from BitTorrent swarm directly to Buzzheavier or Gofile.io.
    Zero bytes are written to the RDP hard disk.
    """
    def __init__(self):
        self.streamer_script = os.path.join(BASE_DIR, "streamer.mjs")
        if not os.path.exists(self.streamer_script):
            bundle_script = os.path.join(BUNDLE_DIR, "streamer.mjs")
            if os.path.exists(bundle_script):
                self.streamer_script = bundle_script
            else:
                raise FileNotFoundError(f"Streamer script not found in {BASE_DIR} or {BUNDLE_DIR}")

    def stream_file(
        self,
        torrent_source: str,
        file_zero_index: int,
        destination: str = "buzzheavier",
        progress_callback=None,
        cancel_event: threading.Event = None,
    ) -> tuple[bool, str]:
        """
        Stream a specific file from torrent in RAM straight to the chosen destination (buzzheavier or gofile).
        Returns:
            (True, download_url) or (False, error_message)
        """
        cmd = [
            "node",
            self.streamer_script,
            "--source",
            torrent_source,
            "--file-index",
            str(file_zero_index),
            "--destination",
            destination.lower(),
        ]

        if destination.lower() == "buzzheavier" and BUZZHEAVIER_ACCOUNT_ID:
            cmd.extend(["--account-id", BUZZHEAVIER_ACCOUNT_ID])
        elif destination.lower() == "gofile" and GOFILE_API_TOKEN:
            cmd.extend(["--gofile-token", GOFILE_API_TOKEN])

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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
                env=env,
                creationflags=creationflags,
            )
        except Exception as e:
            return False, f"Failed to start streaming process: {e}"

        result_url = None
        error_msg = None

        while True:
            if cancel_event and cancel_event.is_set():
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
