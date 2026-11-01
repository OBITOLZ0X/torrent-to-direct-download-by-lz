import os
import time
import urllib.parse
import requests
from typing import Optional, Callable
from database import db

class UploadProgressReader:
    """Streams a file from disk while tracking bytes transferred and emitting progress callbacks."""
    def __init__(
        self,
        filepath: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        cancel_event = None,
        update_interval: float = 1.5,
    ):
        self._f = open(filepath, "rb")
        self.total = os.path.getsize(filepath)
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event
        self.update_interval = update_interval
        self.uploaded = 0
        self.start_time = time.time()
        self.last_update = 0.0

    def read(self, size: int = -1) -> bytes:
        if self.cancel_event and self.cancel_event.is_set():
            raise RuntimeError("Upload cancelled by user.")

        chunk = self._f.read(size)
        if chunk:
            self.uploaded += len(chunk)
            now = time.time()
            if self.progress_callback and (now - self.last_update >= self.update_interval or self.uploaded >= self.total):
                self.last_update = now
                elapsed = max(0.001, now - self.start_time)
                speed = self.uploaded / elapsed
                remaining = max(0, self.total - self.uploaded)
                eta = remaining / speed if speed > 0 else 0.0

                self.progress_callback({
                    "stage": "uploading",
                    "transferred": self.uploaded,
                    "total": self.total,
                    "speed": speed,
                    "eta": eta,
                })
        return chunk

    def __len__(self) -> int:
        return self.total

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass


class CloudUploader:
    """Handles high-speed direct file uploads to Buzzheavier and Gofile with progress reporting."""

    @staticmethod
    def get_gofile_server() -> str:
        """Query available Gofile servers."""
        try:
            r = requests.get("https://api.gofile.io/servers", timeout=10)
            data = r.json()
            if data.get("status") == "ok":
                servers = data.get("data", {}).get("servers", [])
                if servers:
                    return servers[0].get("name", "store-eu-par-5")
        except Exception:
            pass
        return "store-eu-par-5"

    @classmethod
    def upload_to_buzzheavier(
        cls,
        filepath: str,
        account_id: str = "",
        progress_callback: Optional[Callable[[dict], None]] = None,
        cancel_event = None,
    ) -> tuple[bool, str]:
        """Upload local file to Buzzheavier via HTTP PUT."""
        filename = os.path.basename(filepath)
        encoded_name = urllib.parse.quote(filename)
        url = f"https://w.buzzheavier.com/{encoded_name}"

        reader = UploadProgressReader(filepath, progress_callback=progress_callback, cancel_event=cancel_event)
        headers = {
            "Content-Length": str(reader.total)
        }
        if account_id:
            headers["Authorization"] = f"Bearer {account_id}"

        try:
            # 60s connect timeout, 4 hours read timeout for massive files
            res = requests.put(url, data=reader, headers=headers, timeout=(60, 14400))
            if res.status_code in (200, 201):
                try:
                    data = res.json()
                    file_id = data.get("data", {}).get("id")
                    if file_id:
                        return True, f"https://buzzheavier.com/{file_id}"
                except Exception:
                    pass
                return True, f"https://buzzheavier.com/"
            else:
                return False, f"Buzzheavier returned HTTP {res.status_code}: {res.text[:200]}"
        except Exception as e:
            return False, f"Buzzheavier upload error: {e}"
        finally:
            reader.close()

    @classmethod
    def upload_to_gofile(
        cls,
        filepath: str,
        token: str = "",
        progress_callback: Optional[Callable[[dict], None]] = None,
        cancel_event = None,
    ) -> tuple[bool, str]:
        """Upload local file to Gofile via multipart POST."""
        server = cls.get_gofile_server()
        url = f"https://{server}.gofile.io/uploadFile"

        filename = os.path.basename(filepath)
        reader = UploadProgressReader(filepath, progress_callback=progress_callback, cancel_event=cancel_event)

        files = {"file": (filename, reader)}
        data = {}
        if token:
            data["token"] = token

        try:
            res = requests.post(url, files=files, data=data, timeout=(60, 14400))
            if res.status_code == 200:
                try:
                    res_data = res.json()
                    if res_data.get("status") == "ok":
                        d_page = res_data.get("data", {}).get("downloadPage")
                        if d_page:
                            return True, d_page
                        p_code = res_data.get("data", {}).get("parentFolderCode")
                        if p_code:
                            return True, f"https://gofile.io/d/{p_code}"
                        return True, "https://gofile.io/"
                    else:
                        return False, f"Gofile status: {res_data.get('status')}"
                except Exception:
                    return False, f"Could not parse Gofile response: {res.text[:200]}"
            else:
                return False, f"Gofile returned HTTP {res.status_code}: {res.text[:200]}"
        except Exception as e:
            return False, f"Gofile upload error: {e}"
        finally:
            reader.close()

    @classmethod
    def upload_file(
        cls,
        filepath: str,
        destination: str = "buzzheavier",
        progress_callback: Optional[Callable[[dict], None]] = None,
        cancel_event = None,
    ) -> tuple[bool, str]:
        """Upload file to selected destination with credentials from DB."""
        dest = destination.lower()
        if dest == "gofile":
            gofile_tok = db.get_setting("gofile_api_token", "")
            return cls.upload_to_gofile(filepath, token=gofile_tok, progress_callback=progress_callback, cancel_event=cancel_event)
        else:
            buzz_acc = db.get_setting("buzzheavier_account_id", "")
            return cls.upload_to_buzzheavier(filepath, account_id=buzz_acc, progress_callback=progress_callback, cancel_event=cancel_event)

cloud_uploader = CloudUploader()
