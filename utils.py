import os
import math
import shutil

def human_readable_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB, etc.)."""
    if size_bytes is None or size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {units[i]}"

def format_time(seconds: float) -> str:
    """Format seconds into readable ETA string (e.g. 02m 15s)."""
    if seconds is None or seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "Calculating..."
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rem_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes:02d}m {rem_seconds:02d}s"
    hours = minutes // 60
    rem_minutes = minutes % 60
    return f"{hours:02d}h {rem_minutes:02d}m"

def progress_bar(percentage: float, length: int = 10) -> str:
    """Generate a sleek, modern visual progress bar e.g. [▰▰▰▰▰▰▰▱▱▱] 65.8%."""
    percentage = max(0.0, min(100.0, percentage))
    filled = int(round(length * (percentage / 100.0)))
    bar = "▰" * filled + "▱" * (length - filled)
    return f"[{bar}] {percentage:.1f}%"

def truncate_text(text: str, max_len: int = 32) -> str:
    """Truncate filename preserving extension if possible."""
    if len(text) <= max_len:
        return text
    base, ext = os.path.splitext(text)
    if ext and len(ext) < 10:
        keep = max_len - len(ext) - 3
        if keep > 5:
            return f"{base[:keep]}..{ext}"
    return f"{text[:max_len - 3]}..."

def safe_remove(path: str):
    """Safely remove a file or directory without raising errors."""
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
