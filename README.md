# 🚀 Torrent-to-Direct-Download Stream Bot (Buzzheavier & Gofile)
### V2.0 Cloud Edition with Free GitHub Database & Anya-Style Control Panel

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
- **Zero Disk Footprint:** Pieces reside exclusively in RAM, are piped to the remote HTTP socket, and are immediately freed from memory.
- **Extreme Speed:** No disk I/O bottlenecks.
- **Auto-Cleanup:** Torrent metadata files (`.torrent`, `.aria2`) are automatically purged upon transfer completion or cancellation.

---

## ✨ Key Features

### 🐙 Free Token-Based Cloud Database (GitHub Contents API)
- **No local `config.json`**: All operational configurations and user records reside in memory and sync automatically to a secure, free GitHub repository (`database.json`).
- **Gatekeeper Constraint:** The bot cannot be switched to **Public** or **Premium** mode without a valid connected GitHub Database Token! It stays locked in safe **Admin Only** mode until the database is linked.
- **Real-Time Sync:** Every user registration, daily download increment, quota reset, tier change, or settings update immediately commits to your GitHub database.
- **Auto-Provisioning:** Simply enter your GitHub Personal Access Token in `/settings` → the bot automatically creates the private repository and initializes `database.json` for you!

### ⚙️ Anya-Style Interactive Control Panel (`/settings` or `/panel`)
All configuration adjustments utilize **in-place message editing** and **reply-based inputs** (just like Anya Bot):
- **🐙 Database (GitHub):**
  - Add / Update / Test / Remove GitHub Token.
  - Edit remote Database URL.
  - Test Database connection & live sync.
  - Reset remote user database.
  - Step-by-step token generation help guide.
- **📢 Channels Configuration:**
  - 🤖 **Bot Channel:** Channel required for force-subscription registration.
  - 🔗 **Invite Link:** Public invite link for users to join.
  - ✅ **Results Channel:** Automatically broadcasts a rich success card whenever a torrent finishes streaming (File Name, Size, Host, Direct Download URL, Uploader).
  - ❌ **Error Channel:** Automatically broadcasts error alert cards whenever a stream fails.
  - 🧪 Built-in **Test Buttons** for all channels to verify bot administrator permissions.
- **👥 Members Management:**
  - Database overview (Total users, Premium users, Banned users, Today's active count).
  - Paginated user directory (`All Members`, `Premium`, `Banned`) with ◀️ Prev / Next ▶️ buttons.
  - **Search User:** Instant lookup by numerical Telegram ID or `@username`.
  - **User Profile View:** View user's registration date, today's usage, and total downloads.
  - Quick action buttons: `⭐ Toggle Premium`, `🚫 Toggle Ban`, `🔄 Reset Daily Quota`.
- **🔐 Access Modes:**
  - `Admin Only Mode` (safe maintenance mode).
  - `Premium Only Mode` (reserved for upgraded members).
  - `Public Mode` (open to all registered users).
- **⏱️ Quotas & Limits:**
  - Adjust daily download links limit for regular users (default: 5 links/day).
  - Adjust maximum torrent file size in GB for regular users (default: 40 GB).
  - Premium and Admin members enjoy **Unlimited** daily links and file sizes.
- **📢 Global Broadcast:** Send instant announcements to all registered users simultaneously.

### 🎯 Interactive File Selection & Dual Destinations
- Parse any torrent magnet link or `.torrent` file.
- View all contained files with file sizes.
- Interactive inline buttons (`✅` / `❌`) to pick exactly which files you want.
- Batch actions: **`Select All`** and **`Deselect All`**.
- Multi-page navigation for torrents with dozens or hundreds of files.
- Choose your preferred upload provider with one click before downloading:
  - **Buzzheavier.com** (Instant direct streaming, fast CDN)
  - **Gofile.io** (High-speed unlimited storage server)

### 📊 Real-Time Telegram Progress Dashboard
- Visual animated progress bar: `[████████░░] 80.0%`
- Transferred size / Total file size
- Real-time download & upload speeds
- Estimated Time of Arrival (ETA)
- Active BitTorrent peer swarm count
- Real-time disk usage indicator (`0 B - Direct RAM Relay`)
- **🛑 Stop:** Dedicated inline button to instantly cancel the transfer at any moment.

---

## 📁 Project Structure

```text
├── bot.py                  # Main Telegram bot logic, settings menus & event router
├── config.py               # Minimal local authentication (.auth) & terminal styling
├── database.py             # Cloud Database Manager with GitHub real-time sync
├── github_client.py        # Centralized GitHub API client (token test, CRUD on database.json)
├── stream_manager.py       # Python-to-Node subprocess manager & event bridge
├── streamer.mjs            # High-performance WebTorrent RAM streaming engine
├── torrent_manager.py      # Aria2 metadata extractor & torrent parser
├── utils.py                # Formatting helpers (sizes, ETA, progress bars)
├── aria2c.exe              # Bundled standalone Aria2 engine for metadata extraction
├── build_exe.py            # PyInstaller single-file standalone compiler
├── build.bat               # One-click Windows build script
└── run.bat                 # One-click execution launcher
```

---

## 🚀 Quick Start (Running from Source)

### 1. Prerequisites
- **Python 3.10+**
- **Node.js 18+**

### 2. Install Dependencies
```bash
pip install -r requirements.txt
npm install
```

### 3. Run the Bot
```bash
python bot.py
```
On first launch, if credentials are not configured, the console will interactively prompt for:
1. **Telegram Bot Token** (from `@BotFather`)
2. **Admin Chat ID** (your numeric ID from `@userinfobot`)

Credentials are saved in a protected `.auth` file. No `config.json` or `.env` needed!

---

## 📦 Building Standalone Single Executable (`.exe`)

To package everything (Python, WebTorrent, Aria2, and dependencies) into a single standalone `.exe` that runs on any Windows machine:

```bash
python build_exe.py
```
Or simply double-click `build.bat`.

The resulting standalone executable will be located at:
```text
dist/TorrentStreamBot.exe
```
You can move this single `.exe` file to any Windows computer or RDP without installing Python or Node.js!

---

## 🛡️ Crash Protection & Console Experience

Inspired by Anya Bot, the terminal window features:
- **ANSI Color Status Dashboard:** Displays bot name, username, ID, Admin ID, Bot Mode, Cloud DB status, and registered members at a glance.
- **Auto-Reconnect & Crash Immunity:** If Telegram polling experiences network timeouts or disconnects, it logs the exception, waits 5 seconds, and automatically reconnects without ever closing the CMD window!
