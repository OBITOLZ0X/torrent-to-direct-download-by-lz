"""
GitHub API Client for Cloud Database Management.
Uses GitHub Contents API for reading and writing database.json securely and for free.
"""

import os
import json
import base64
import time
import requests
import threading
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timezone

class GitHubClient:
    """GitHub API client for remote cloud database storage."""

    _API_BASE = "https://api.github.com"

    def __init__(self, token: Optional[str] = None):
        self.token = (token or "").strip()
        self._rate_limit_remaining = 5000
        self._rate_limit_reset = 0
        self._lock = threading.Lock()

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "tDownUpBot"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def test_token(self) -> bool:
        if not self.token:
            return False
        try:
            resp = requests.get(f"{self._API_BASE}/user", headers=self._headers(), timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def get_token_info(self) -> dict:
        if not self.test_token():
            return {"valid": False, "error": "Invalid token"}
        try:
            resp = requests.get(f"{self._API_BASE}/user", headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "valid": True,
                    "username": data.get("login"),
                    "name": data.get("name") or data.get("login"),
                    "avatar_url": data.get("avatar_url"),
                    "public_repos": data.get("public_repos", 0),
                    "created_at": data.get("created_at"),
                }
            return {"valid": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    @staticmethod
    def _split_raw_url(raw_url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Split raw URL into (owner, repo, branch, path)."""
        try:
            if "raw.githubusercontent.com" not in raw_url:
                # Also support github.com/owner/repo/blob/branch/path format
                if "github.com" in raw_url:
                    parts = raw_url.split("github.com/", 1)[1].split("?")[0].split("/")
                    if len(parts) >= 4 and parts[2] in ("blob", "raw"):
                        return parts[0], parts[1], parts[3], "/".join(parts[4:])
                return None, None, None, None

            base = raw_url.split("raw.githubusercontent.com/", 1)[1].split("?")[0]
            parts = [p for p in base.split("/") if p]
            if len(parts) < 4:
                return None, None, None, None
            owner, repo = parts[0], parts[1]
            if len(parts) >= 6 and parts[2] == "refs" and parts[3] == "heads":
                return owner, repo, parts[4], "/".join(parts[5:])
            return owner, repo, parts[2], "/".join(parts[3:])
        except Exception:
            return None, None, None, None

    @staticmethod
    def parse_raw_url(raw_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        owner, repo, _branch, path = GitHubClient._split_raw_url(raw_url)
        if not all((owner, repo, path)):
            return None, None, None
        return owner, repo, path

    def read_file(self, raw_url: str, as_json: bool = True) -> Optional[Any]:
        """Read file from GitHub via raw URL or Contents API fallback."""
        if not raw_url:
            return None
        owner, repo, branch, path = self._split_raw_url(raw_url)
        branch = branch or "main"

        # 1. Try Contents API directly if we have a token
        if self.token and owner and repo and path:
            try:
                api_url = f"{self._API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
                resp = requests.get(api_url, headers=self._headers(), timeout=15)
                if resp.status_code == 200:
                    payload = resp.json()
                    content_b64 = payload.get("content", "")
                    if payload.get("encoding") == "base64" and content_b64:
                        raw_text = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                        return json.loads(raw_text) if as_json else raw_text
                    return payload
            except Exception:
                pass

        # 2. Try raw URL as fallback
        try:
            resp = requests.get(raw_url, headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                return resp.json() if as_json else resp.text
        except Exception:
            pass

        return None

    def write_file(self, raw_url: str, content: Any, commit_msg: str = None) -> bool:
        """Write/update file on GitHub via Contents API."""
        if not self.token or not raw_url:
            return False

        owner, repo, branch, path = self._split_raw_url(raw_url)
        if not all((owner, repo, path)):
            return False
        branch = branch or "main"

        api_url = f"{self._API_BASE}/repos/{owner}/{repo}/contents/{path}"

        try:
            # Check existing file SHA
            sha = None
            resp = requests.get(f"{api_url}?ref={branch}", headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                sha = resp.json().get("sha")

            content_str = json.dumps(content, indent=2, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
            commit_msg = commit_msg or f"Update database - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"

            payload = {
                "message": commit_msg,
                "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
                "branch": branch
            }
            if sha:
                payload["sha"] = sha

            put_resp = requests.put(api_url, headers=self._headers(), json=payload, timeout=20)
            return put_resp.status_code in (200, 201)
        except Exception:
            return False

    def auto_setup_repo_and_file(self, repo_name: str = "torrent-stream-db", file_path: str = "database.json", initial_content: dict = None) -> Tuple[bool, str]:
        """Automatically create private repository and database file if not present."""
        if not self.token:
            return False, "No GitHub token configured."

        info = self.get_token_info()
        if not info.get("valid"):
            return False, "Invalid GitHub token."

        owner = info["username"]
        branch = "main"

        # Check if repo exists
        repo_url = f"{self._API_BASE}/repos/{owner}/{repo_name}"
        try:
            r = requests.get(repo_url, headers=self._headers(), timeout=10)
            if r.status_code == 404:
                # Create private repo
                create_payload = {
                    "name": repo_name,
                    "description": "Cloud Database for tDownUp Bot",
                    "private": True,
                    "auto_init": True
                }
                cr = requests.post(f"{self._API_BASE}/user/repos", headers=self._headers(), json=create_payload, timeout=15)
                if cr.status_code not in (200, 201):
                    return False, f"Failed to create repo: HTTP {cr.status_code}"
                time.sleep(1.5)  # Give GitHub a moment to initialize the repo
            elif r.status_code != 200:
                return False, f"Failed to access repo: HTTP {r.status_code}"

            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}/{file_path}"

            # Check if file exists, if not write initial content
            existing = self.read_file(raw_url, as_json=True)
            if existing is None:
                content = initial_content or {"settings": {}, "users": {}}
                ok = self.write_file(raw_url, content, commit_msg="Initial database setup")
                if not ok:
                    return False, "Failed to initialize database.json in repository."

            return True, raw_url
        except Exception as e:
            return False, str(e)

    def test_database_connection(self, raw_url: str) -> dict:
        """Test read & write capability to the database file."""
        if not self.token:
            return {"success": False, "error": "No GitHub token set."}
        if not raw_url:
            return {"success": False, "error": "No database URL set."}

        owner, repo, path = self.parse_raw_url(raw_url)
        if not all((owner, repo, path)):
            return {"success": False, "error": "Invalid GitHub Raw URL format."}

        try:
            data = self.read_file(raw_url, as_json=True)
            if data is None:
                return {"success": False, "error": "Could not read database file from GitHub (HTTP 404 or connection issue)."}

            users_count = len(data.get("users", {}))
            return {"success": True, "users_count": users_count, "size": len(str(data))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def format_token_display(token: str) -> str:
        if not token:
            return "🔴 Not Set"
        if len(token) <= 8:
            return token[:2] + "***"
        return f"{token[:4]}{'*' * 8}{token[-4:]}"
