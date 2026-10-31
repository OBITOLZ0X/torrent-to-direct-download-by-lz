# 🚀 Torrent-to-Direct-Download Stream Bot (Buzzheavier & Gofile)

An enterprise-grade Telegram Bot built with Python (`pyTelegramBotAPI`) and Node.js (`WebTorrent`) that converts BitTorrent magnet links and `.torrent` files into direct high-speed download links on **[Buzzheavier.com](https://buzzheavier.com)** and **[Gofile.io](https://gofile.io)** in real-time with **0 BYTES written to the server/RDP hard disk!**

---

## ⚡ Zero-Disk Pure RAM Streaming Architecture

Conventional torrent bots download entire files (often 10GB–50GB+) onto the server disk before uploading them to cloud hosts. This consumes massive disk space, wears out SSDs, and doubles transfer times.

This bot transforms your server or RDP into a **direct in-memory network conduit**:

```text
┌──────────────────┐         Pure RAM Stream          ┌───────────────────────┐
│ BitTorrent Swarm │ ───────────────────────────────► │ RDP Memory (RAM Only) │
└──────────────────┘                                  └───────────────────────┘
                                                                  │
                                                      Real-time HTTP Stream
                                                                  │
                                                                  ▼
                                                      ┌───────────────────────┐
                                                      │ Buzzheavier / Gofile  │
                                                      └───────────────────────┘
                                                                  ▲
                                                      [ Disk Usage = 0 Bytes ]
```

- **Simultaneous Transfer:** Data is downloaded from peers and uploaded to the destination at the exact same millisecond.
- **Zero Disk Footprint:** Pieces reside exclusively in RAM (`memory-chunk-store`), are piped to the remote HTTP socket, and are immediately freed from memory.
- **Extreme Speed:** No disk I/O bottlenecks.

---

## ✨ Key Features

### 🎯 Interactive File Selection
- Parse any torrent magnet link or `.torrent` file.
- View all contained files with file sizes.
- Interactive inline buttons (`✅` / `❌`) to pick exactly which files you want.
- Batch actions: **`✅ Select All`** and **`❌ Deselect All`**.
- Multi-page navigation for torrents with dozens or hundreds of files.

### 🌐 Dual Cloud Upload Destinations
- Choose your preferred upload provider with one click before downloading:
  - **Buzzheavier.com** (Instant direct streaming, fast CDN)
  - **Gofile.io** (High-speed unlimited storage server)

### 📊 Real-Time Telegram Progress Dashboard
- Dynamically updates the message via `edit_message_text`:
  - Visual animated progress bar: `[████████░░] 80.0%`
  - Transferred size / Total file size
  - Real-time download & upload speeds
  - Estimated Time of Arrival (ETA)
  - Active BitTorrent peer swarm count
  - Real-time disk usage indicator (`0 Bytes - RAM Relay`)
- **🛑 Stop / Cancel:** Dedicated inline button to instantly kill the transfer at any moment.

### 🛡️ Anya-Style User Registration & Channel Gate
- Mandatory channel membership verification.
- Non-registered users are greeted with channel join links and a **`Register Now`** button.
- Clean layout with verification badges and user status tracking.

### 💎 Quotas & Tier System
- **Regular Users:**
  - 5 torrent links per day (resets automatically every 24 hours).
  - Maximum 40 GB single file size limit.
- **Premium Users & Admin:**
  - **Unlimited** downloads per day.
  - **Unlimited** file sizes.
  - Priority processing.

### ⚙️ Full Admin Control Panel (`/settings` or `/panel`)
Restricted strictly to the `admin_id` configured at launch:
- **Bot Modes:** Toggle between `Public`, `Premium Only`, and `Admin Only`.
- **Daily Limit Control:** Adjust the default daily torrent quota for regular users.
- **Max File Size Control:** Adjust the maximum allowed file size cap.
- **Channel Membership Settings:** Update the required channel ID and invite link directly from Telegram.
- **User Management:**
  - Add or remove Premium users by Telegram User ID.
  - Ban or unban disruptive users.
- **Global Broadcast:** Send announcements or updates to all registered users simultaneously.

### 🔑 Zero `.env` Dependency
- No messy environment files to configure manually.
- On first launch, the bot interactively prompts in the console for your **Bot Token** and **Admin Chat ID**, automatically creating a secure `config.json`.

---

## 📁 Project Structure

```text
├── bot.py                  # Main Telegram bot logic and UI handlers
├── config.py               # Interactive setup prompt & configuration manager
├── database.py             # User accounts, quotas, premium tiers & ban system
├── stream_manager.py       # Python-to-Node subprocess manager & event bridge
├── streamer.mjs            # High-performance WebTorrent RAM streaming engine
├── torrent_manager.py      # Aria2 metadata extractor & torrent parser
├── utils.py                # Formatting helpers (sizes, ETA, progress bars)
├── aria2c.exe              # Bundled standalone Aria2 engine for metadata extraction
├── build_exe.py            # PyInstaller build automation script
├── build.bat               # One-click Windows batch compiler
├── run.bat                 # One-click startup script (installs deps and runs bot)
├── requirements.txt        # Python package dependencies
├── package.json            # Node.js dependencies
└── README.md               # Documentation
```

---

## 🚀 Getting Started

### Prerequisites
1. **Python 3.10+**
2. **Node.js 18+**

### Method 1: One-Click Windows Executable (`.exe`)
1. Double-click `build.bat` (or run `python build_exe.py`).
2. The standalone executable is created inside `dist/TorrentStreamBot.exe`.
3. Run `TorrentStreamBot.exe`. On first startup, enter your Telegram Bot Token and Admin Chat ID.

### Method 2: Running from Source
1. Clone or extract the repository:
   ```bash
   git clone https://github.com/OBITOLZ0X/torrent-to-direct-download-by-lz.git
   cd torrent-to-direct-download-by-lz
   ```
2. Double-click **`run.bat`** (it will install pip and npm dependencies automatically and start the bot).
   *Or manually:*
   ```bash
   pip install -r requirements.txt
   npm install
   python bot.py
   ```
3. Enter your **Bot Token** (from `@BotFather`) and **Admin Chat ID** (from `@userinfobot`).

---

## 💬 Bot Commands

| Command | Access | Description |
| :--- | :--- | :--- |
| `/start` | Everyone | Welcome message, registration prompt, or user dashboard |
| `/dw <magnet>` | Registered | Start downloading and streaming a torrent |
| `/account` | Registered | View your tier, used/remaining daily quota, and reset time |
| `/help` | Everyone | Usage instructions and guidelines |
| `/settings` or `/panel` | **Admin Only** | Open the interactive Admin Control Panel |

---

## 🔒 Security & Privacy

- All torrent piece data is processed exclusively in volatile RAM and is never committed to persistent storage.
- Download links generated are private to the user who requested them.
- Administrative controls are verified server-side against the numerical `admin_id`.

---

## 📜 License
Distributed under the MIT License. See `LICENSE` for more information.
