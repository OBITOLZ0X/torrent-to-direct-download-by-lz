<p align="center">
  <img src="icon.png" width="180" height="180" alt="tDownUp Logo" style="border-radius: 24px;">
</p>

<h1 align="center">⚡ tDownUp</h1>

<p align="center">
  <b>High-Speed Torrent & Magnet to Direct Cloud Uploader Telegram Bot</b><br>
  <i>Convert BitTorrent links into direct download links on Buzzheavier and Gofile instantly!</i>
</p>

---

## 📖 What is tDownUp?

**tDownUp** is an ultra-fast Telegram Bot built with Python (`pyTelegramBotAPI`) and Node.js (`WebTorrent`) that allows users to send any `.torrent` file or magnet link and receive direct high-speed download links on **[Buzzheavier.com](https://buzzheavier.com)** or **[Gofile.io](https://gofile.io)**.

---

## 📥 How to Use

Using **tDownUp** is simple and intuitive:

1. **Send a Torrent**:
   - Send `/dw <magnet_link>`
   - Or paste any magnet link directly (`magnet:?xt=...`)
   - Or upload a `.torrent` file directly
   - Or reply to any `.torrent` file with `/dw`

2. **Select Files**:
   - The bot reads the torrent contents and presents an interactive file checklist.
   - Tap any file to toggle it on (`✅`) or off (`❌`).
   - Use **`Select All`** or **`Deselect All`** for fast selection.

3. **Choose Destination**:
   - Tap **`🐝 Buzzheavier`** or **`📁 Gofile.io`**.

4. **Get Direct Links**:
   - Watch the live progress dashboard.
   - When finished, click your direct download links!
   - Tap **`☕ Support & Donate`** to support server hosting and development.

---

## ✨ Features & Architecture

### 🐙 Token-Based Cloud Database (GitHub Contents API)
- **Zero local `config.json`**: All settings and user data sync in real-time to a private, versioned GitHub database (`database.json`).
- **Gatekeeper Protection:** The bot remains locked in safe **Admin Only Mode** until a valid GitHub token is provided in settings.
- **Instant Sync:** User registrations, download counters, and channel settings commit immediately to your GitHub cloud storage.

### ⚙️ Covert Admin Control Panel (`/settings` or `/panel`)
Hidden from regular users and accessible only by the configured `ADMIN_ID`:
- **🐙 Database:** Connect, update, test, or refresh your GitHub Database Token and URL.
- **📢 Channels:** Configure Bot Channel (force subscription), Invite Link, Results Channel (completed uploads feed), and Error Channel.
- **☕ Donation Settings:** Set up PayPal, Binance Pay ID, and RedotPay-supported crypto networks:
  - 🔴 Tron (TRC20 USDT)
  - 🟡 BNB Smart Chain (BEP20)
  - 🟣 Solana (SOL)
  - 💎 The Open Network (TON)
  - 🔷 Ethereum (ERC20)
  - 🔵 Arbitrum One
  - 🟡 opBNB
- **👥 Members:** Search users by ID or username, view profiles, ban/unban, and reset daily quotas.
- **⏱️ Quotas & Limits:** Configure daily link limits and max file sizes applied equally to all registered users.
- **📢 Broadcast:** Send announcements to all registered users simultaneously.

### 🎨 Clean User Experience
- No internal server jargon or confusing technical descriptions in chat.
- Transparent progress bar with transfer rate, ETA, and active peer count.
- Dynamic contact info to reach the bot owner for help.

---

## 📦 Standalone Executable (`tDownUp.exe`)

The entire project (Python runtime, Node.js, Aria2c, WebTorrent engine, packages, and custom icon) compiles into a **single standalone file**:

```text
dist/tDownUp.exe
```

### Build Instructions:
```bash
python build_exe.py
```
Or simply double-click `build.bat`.

You can move `tDownUp.exe` alone to any Windows computer or RDP without installing Python, Node.js, or Aria2!
