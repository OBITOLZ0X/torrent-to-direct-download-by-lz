import os
import time
import base64
import subprocess
import requests
from torrentool.api import Torrent
from config import (
    ARIA2_PORT,
    ARIA2_RPC_SECRET,
    ARIA2_RPC_URL,
    ARIA2_PATH,
    DOWNLOADS_DIR,
    METADATA_DIR,
)
from utils import safe_remove

# Default reliable public trackers to accelerate peer discovery
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://opentracker.i2p.rocks:6969/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.cyberia.is:6969/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://opentor.net:6969/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://tracker.filemail.com:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
]

class Aria2Manager:
    """Manages the aria2 RPC daemon, metadata extraction, and downloads."""
    def __init__(self):
        self.process = None
        self._ensure_daemon_running()

    def start_aria2_daemon(self):
        """Ensure aria2 daemon is actively running."""
        return self._ensure_daemon_running()

    def _ensure_daemon_running(self):
        """Check if aria2 RPC is responding; if not, spawn it."""
        try:
            res = requests.post(
                ARIA2_RPC_URL,
                json={"jsonrpc": "2.0", "id": "ping", "method": "aria2.getVersion", "params": []},
                timeout=1.5,
            )
            if res.status_code == 200:
                return  # already running
        except Exception:
            pass

        # Spawn aria2c daemon with optimized BitTorrent & DHT swarm discovery
        trackers_arg = f"--bt-tracker={','.join(DEFAULT_TRACKERS)}"
        cmd = [
            ARIA2_PATH,
            "--enable-rpc",
            f"--rpc-listen-port={ARIA2_PORT}",
            "--rpc-allow-origin-all",
            "--seed-time=0",
            "--file-allocation=none",
            "--bt-tracker-connect-timeout=10",
            "--bt-tracker-timeout=15",
            "--bt-max-peers=120",
            "--bt-request-peer-speed-limit=0",
            "--enable-dht=true",
            "--enable-dht6=false",
            "--enable-peer-exchange=true",
            "--bt-enable-lpd=true",
            "--dht-entry-point=dht.transmissionbt.com:6881",
            "--dht-entry-point=router.bittorrent.com:6881",
            "--dht-entry-point=router.utorrent.com:6881",
            "--max-overall-upload-limit=1K",
            trackers_arg,
            f"--dir={DOWNLOADS_DIR}",
        ]
        if ARIA2_RPC_SECRET:
            cmd.append(f"--rpc-secret={ARIA2_RPC_SECRET}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            time.sleep(1.2)
        except Exception as e:
            print(f"[Aria2Manager] Failed to launch aria2c executable ({ARIA2_PATH}): {e}")

    def call_rpc(self, method: str, params: list = None) -> dict:
        """Call aria2 JSON-RPC method."""
        self._ensure_daemon_running()
        if params is None:
            params = []
        if ARIA2_RPC_SECRET:
            params = [f"token:{ARIA2_RPC_SECRET}"] + params

        payload = {
            "jsonrpc": "2.0",
            "id": f"call_{int(time.time()*1000)}",
            "method": method,
            "params": params,
        }
        res = requests.post(ARIA2_RPC_URL, json=payload, timeout=10)
        data = res.json()
        if "error" in data:
            raise RuntimeError(f"aria2 RPC error: {data['error']}")
        return data.get("result")

    def fetch_magnet_metadata(self, magnet_link: str, timeout: int = 80, status_callback=None) -> tuple[bool, str]:
        """
        Fetch torrent metadata from a magnet link and save as a .torrent file.
        Uses optimized Aria2 with WebTorrent dual-engine fallback.
        Returns:
            (True, path_to_torrent_file) or (False, error_message)
        """
        import re
        os.makedirs(METADATA_DIR, exist_ok=True)

        # Check if already cached by info_hash
        match = re.search(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet_link, re.IGNORECASE)
        hash_str = match.group(1).lower() if match else ""
        if hash_str:
            cached_path = os.path.join(METADATA_DIR, f"{hash_str}.torrent")
            if os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                return True, cached_path
        else:
            cached_path = os.path.join(METADATA_DIR, f"meta_{int(time.time())}.torrent")

        options = {
            "bt-metadata-only": "true",
            "bt-save-metadata": "true",
            "dir": METADATA_DIR,
            "allow-overwrite": "true",
            "auto-file-renaming": "false",
            "bt-tracker-connect-timeout": "10",
            "bt-tracker-timeout": "15",
            "bt-max-peers": "120",
            "enable-dht": "true",
            "enable-peer-exchange": "true",
            "bt-tracker": ",".join(DEFAULT_TRACKERS),
        }

        gid = None
        try:
            gid = self.call_rpc("aria2.addUri", [[magnet_link], options])
        except Exception as e:
            print(f"[Aria2] Warning adding magnet via RPC: {e}")

        start_time = time.time()
        torrent_file = None
        webtorrent_fallback_attempted = False

        while time.time() - start_time < timeout:
            time.sleep(1)
            elapsed = int(time.time() - start_time)

            if gid:
                try:
                    status = self.call_rpc("aria2.tellStatus", [gid])
                    current_status = status.get("status")
                    connections = int(status.get("connections", 0))

                    if status_callback and (elapsed % 3 == 0):
                        status_callback(connections, elapsed)

                    if current_status == "complete":
                        info_hash = status.get("infoHash")
                        if info_hash:
                            expected = os.path.join(METADATA_DIR, f"{info_hash}.torrent")
                            if os.path.exists(expected):
                                torrent_file = expected
                        if not torrent_file:
                            candidates = [os.path.join(METADATA_DIR, f) for f in os.listdir(METADATA_DIR) if f.endswith(".torrent")]
                            if candidates:
                                torrent_file = max(candidates, key=os.path.getmtime)
                        try:
                            self.call_rpc("aria2.removeDownloadResult", [gid])
                        except Exception:
                            pass
                        break

                    elif current_status == "error":
                        gid = None
                except Exception:
                    gid = None

            # If aria2 has not resolved after 30 seconds or failed, try WebTorrent engine in parallel / fallback
            if not torrent_file and elapsed >= 30 and not webtorrent_fallback_attempted:
                webtorrent_fallback_attempted = True
                try:
                    from stream_manager import StreamManager
                    sm = StreamManager()
                    fallback_out = cached_path
                    ok, res = sm.fetch_magnet_metadata(magnet_link, fallback_out, timeout=max(20, int(timeout - elapsed)))
                    if ok and os.path.exists(res) and os.path.getsize(res) > 0:
                        torrent_file = res
                        if gid:
                            try:
                                self.call_rpc("aria2.forceRemove", [gid])
                                self.call_rpc("aria2.removeDownloadResult", [gid])
                            except Exception:
                                pass
                        break
                except Exception as wt_err:
                    print(f"[WebTorrent Fallback] Error: {wt_err}")

        if not torrent_file or not os.path.exists(torrent_file):
            if gid:
                try:
                    self.call_rpc("aria2.forceRemove", [gid])
                    self.call_rpc("aria2.removeDownloadResult", [gid])
                except Exception:
                    pass
            # Final fallback check: look if any recent .torrent was created
            candidates = [os.path.join(METADATA_DIR, f) for f in os.listdir(METADATA_DIR) if f.endswith(".torrent")]
            if candidates:
                latest = max(candidates, key=os.path.getmtime)
                if time.time() - os.path.getmtime(latest) < timeout + 5:
                    return True, latest

            return False, "Could not fetch torrent metadata within timeout. Check peers/trackers."

        return True, torrent_file

    def parse_torrent(self, torrent_path: str) -> tuple[str, int, list[dict]]:
        """
        Parse .torrent file to extract torrent name, total size, and list of files.
        Returns:
            (torrent_name, total_size, [{'index': int, 'name': str, 'length': int}, ...])
        """
        t = Torrent.from_file(torrent_path)
        name = t.name
        total_size = t.total_size
        file_list = []

        if not t.files:
            file_list.append({"index": 1, "name": name, "length": total_size})
        else:
            for idx, f in enumerate(t.files, 1):
                # Clean up nested folder prefix if desired
                clean_name = f.name
                if "\\" in clean_name:
                    clean_name = clean_name.split("\\")[-1]
                elif "/" in clean_name:
                    clean_name = clean_name.split("/")[-1]

                file_list.append({
                    "index": idx,
                    "name": clean_name or f.name,
                    "full_name": f.name,
                    "length": f.length,
                })

        return name, total_size, file_list

    def pause_download(self, gid: str) -> bool:
        """Pause an active aria2 download."""
        try:
            self.call_rpc("aria2.pause", [gid])
            return True
        except Exception as e:
            print(f"[Aria2Manager] Error pausing gid {gid}: {e}")
            return False

    def resume_download(self, gid: str) -> bool:
        """Resume a paused aria2 download."""
        try:
            self.call_rpc("aria2.unpause", [gid])
            return True
        except Exception as e:
            print(f"[Aria2Manager] Error resuming gid {gid}: {e}")
            return False

    def stop_download(self, gid: str) -> bool:
        """Force-stop and remove an aria2 download."""
        try:
            self.call_rpc("aria2.forceRemove", [gid])
        except Exception:
            pass
        try:
            self.call_rpc("aria2.removeDownloadResult", [gid])
        except Exception:
            pass
        return True

    def download_single_file(
        self,
        torrent_path: str,
        file_index: int,
        target_dir: str,
        progress_callback=None,
        cancel_event=None,
        session: dict = None,
    ) -> tuple[bool, str]:
        """
        Download only one specific file from the torrent using Aria2.
        Supports seamless pause/resume and connection-loss resilience.
        Returns:
            (True, downloaded_file_path) or (False, error_reason)
        """
        os.makedirs(target_dir, exist_ok=True)
        with open(torrent_path, "rb") as f:
            b64_torrent = base64.b64encode(f.read()).decode("utf-8")

        options = {
            "select-file": str(file_index),
            "dir": target_dir,
            "seed-time": "0",
            "file-allocation": "none",
            "bt-tracker-connect-timeout": "10",
            "bt-tracker-timeout": "15",
            "bt-max-peers": "120",
            "bt-tracker": ",".join(DEFAULT_TRACKERS),
            "enable-dht": "true",
            "enable-peer-exchange": "true",
            "bt-enable-lpd": "true",
            "max-connection-per-server": "16",
        }

        try:
            gid = self.call_rpc("aria2.addTorrent", [b64_torrent, [], options])
            if session is not None:
                session["current_gid"] = gid
        except Exception as e:
            return False, f"Failed to start aria2 download: {e}"

        last_update = 0.0

        while True:
            if cancel_event and cancel_event.is_set():
                self.stop_download(gid)
                safe_remove(target_dir)
                return False, "Download cancelled by user."

            time.sleep(1)
            try:
                status = self.call_rpc("aria2.tellStatus", [gid])
            except Exception as e:
                # If RPC blips momentarily, retry next loop without aborting
                time.sleep(1)
                continue

            st = status.get("status")
            if st == "complete":
                # Find the completed file path
                completed_path = None
                files = status.get("files", [])
                for f in files:
                    if f.get("selected") == "true" or f.get("index") == str(file_index):
                        path = f.get("path")
                        if path and os.path.exists(path) and os.path.getsize(path) > 0:
                            completed_path = path
                            break

                try:
                    self.call_rpc("aria2.removeDownloadResult", [gid])
                except Exception:
                    pass

                if completed_path:
                    return True, completed_path
                else:
                    # Fallback: scan target_dir for the largest file
                    candidates = []
                    for root, _, fs in os.walk(target_dir):
                        for f in fs:
                            if not f.endswith(".aria2"):
                                fp = os.path.join(root, f)
                                candidates.append((fp, os.path.getsize(fp)))
                    if candidates:
                        best = max(candidates, key=lambda x: x[1])[0]
                        return True, best
                    return False, "Download finished but file could not be found."

            elif st == "error":
                err = status.get("errorMessage", "Aria2 download error.")
                try:
                    self.call_rpc("aria2.removeDownloadResult", [gid])
                except Exception:
                    pass
                safe_remove(target_dir)
                return False, err

            elif st in ("active", "paused", "waiting"):
                files = status.get("files", [])
                target_file_obj = next((f for f in files if f.get("index") == str(file_index)), None)

                if target_file_obj:
                    completed = int(target_file_obj.get("completedLength", 0))
                    total = int(target_file_obj.get("length", 0))
                else:
                    completed = int(status.get("completedLength", 0))
                    total = int(status.get("totalLength", 0))

                dl_speed = int(status.get("downloadSpeed", 0))
                ul_speed = int(status.get("uploadSpeed", 0))
                seeders = int(status.get("numSeeders", 0))
                connections = int(status.get("connections", 0))
                is_p = (st == "paused") or (session and session.get("is_paused", False))

                now = time.time()
                if progress_callback and (now - last_update >= 1.5):
                    last_update = now
                    progress_callback({
                        "stage": "downloading",
                        "completed": completed,
                        "total": total,
                        "dl_speed": dl_speed,
                        "ul_speed": ul_speed,
                        "seeders": seeders,
                        "connections": connections,
                        "isPaused": is_p,
                    })
