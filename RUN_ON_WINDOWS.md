# Running IACF Finance on Windows

These steps are for the Windows PC **iacf-bac** (Tailscale IP **100.68.245.87**). They also work on any Windows 10 or 11 PC.
You don't need Node.js, Docker or WSL: the web pages ship ready to use, and only Python is required.

---

## 1. Prerequisites

| What | Notes |
|---|---|
| **Python 3.11 or newer** (3.12 recommended) | From **https://www.python.org/downloads/windows/**, use the "Windows installer (64-bit)". In the first installer screen, **tick "Add python.exe to PATH"** and keep "py launcher" selected. |
| Tailscale (already installed) | Only needed to open the app from your other devices. |
| Git | Optional. Only useful if you want to update with `git pull` instead of copying a new zip. |
| rclone | Optional, for Google Drive backup uploads (see section 10). |

Check Python in a **new** PowerShell window:

```powershell
py --version        # or: python --version   -> must say 3.11 or higher
```

If `python` opens the Microsoft Store, turn the alias off: **Settings → Apps → Advanced app settings → App execution aliases** → switch off *python.exe* and *python3.exe*.

## 2. Put the files in place

1. Copy `iacf-finance-windows.zip` from `\\iacf-bac\temp\finance` to a folder in **your user profile**, for example `C:\Users\<you>\IACF-Finance`.
   Don't run the app from the shared `temp` folder: other people on the network could read your database there.
2. Right-click the zip → **Properties** → if there's an **Unblock** checkbox, tick it → OK. Then **Extract All…** into that folder.
   The folder should contain `start.bat`, `setup.ps1`, the `app` folder and so on (not a second nested folder).
3. If you skipped "Unblock", run this once in PowerShell inside the folder:
   ```powershell
   Get-ChildItem -Recurse | Unblock-File
   ```

## 3. PowerShell execution policy

The `.bat` files already start PowerShell with `-ExecutionPolicy Bypass`, so double-clicking them just works.
To run the `.ps1` scripts directly in a PowerShell window, allow scripts **for that window only** first:

```powershell
cd C:\Users\<you>\IACF-Finance
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This lasts until you close the window and doesn't change any system setting.

## 4. Setup (once)

Double-click **`setup.bat`**, or run `.\setup.ps1`. It:

* finds Python 3.11+ (and tells you exactly what to do if it can't),
* creates a private Python environment in `.venv` and installs `requirements.txt` (takes a few minutes the first time),
* creates the `data` folder and limits its permissions to you, SYSTEM and Administrators,
* creates `.env` from `.env.example`. Every setting in it is commented out, which means "use the defaults".

## 5. First run

1. Double-click **`start.bat`**, or run `.\start.ps1`. The server runs in the background (no window stays open).
   * Use `.\start.ps1 -Foreground` (or `start.bat -Foreground`) to see the log in the window. Ctrl+C stops it.
2. Open **http://127.0.0.1:8088** in a browser on that PC.
3. The first visit shows **/setup**: choose your password. This creates an empty, fresh database. To bring over your existing data, see section 9.
4. To stop the server, double-click **`stop.bat`** or run `.\stop.ps1`.

Logs are in `data\logs\server.log`.

## 6. Settings (`.env`)

Edit `.env` in Notepad (remove the `#` in front of a line to use it), then run stop and start again.

```ini
HOST=127.0.0.1                      # address(es) to listen on, comma separated (default 127.0.0.1 = this PC only)
PORT=8088                           # default 8088
DATA_DIR=data                       # default: the data folder next to the app; e.g. D:\FinanceData
```

You can set the same names as Windows environment variables instead. Environment variables take priority over `.env`.

## 7. Access from your phone and other computers (Tailscale)

1. In `.env`, listen on the Tailscale address as well as this PC (recommended):
   ```ini
   HOST=127.0.0.1,100.68.245.87
   ```
   `HOST=0.0.0.0` (every network card, including Wi-Fi and Ethernet) also works, but then the firewall rule below is what keeps
   the app away from your local network. Prefer the Tailscale IP.
2. Allow port 8088 through Windows Firewall **for Tailscale only**. In an **Administrator** PowerShell:
   ```powershell
   New-NetFirewallRule -DisplayName "IACF Finance (Tailscale only)" -Direction Inbound -Action Allow `
     -Protocol TCP -LocalPort 8088 -RemoteAddress 100.64.0.0/10 -InterfaceAlias "Tailscale" -Profile Any
   ```
   * `-RemoteAddress 100.64.0.0/10` allows only Tailscale addresses, and `-InterfaceAlias "Tailscale"` allows only the Tailscale network adapter.
     Check the adapter name with `Get-NetAdapter`. It's normally `Tailscale`.
   * To remove the rule later: `Remove-NetFirewallRule -DisplayName "IACF Finance (Tailscale only)"`
   * If you changed `PORT`, use that number instead of 8088.
3. Restart the app (stop, then start). Then on another Tailscale device open **http://100.68.245.87:8088**
   (or `http://iacf-bac:8088` if MagicDNS is on).

The app uses plain HTTP. That's fine inside Tailscale, which encrypts the connection, but don't forward the port on your router.

## 8. Start automatically at boot (optional)

In an **Administrator** PowerShell inside the app folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install-service.ps1 -StartNow        # registers the scheduled task "IACF Finance"
```

* The task runs at boot (30 seconds after startup) **as your user, whether or not you are logged in**. No password is stored (logon type S4U).
  Use `-AsSystem` to run it as the SYSTEM account instead.
* It waits up to 3 minutes for the Tailscale address to come up. If the app stops with an error, Windows restarts it every minute.
* You can see and manage it in **Task Scheduler** (`taskschd.msc`) under "IACF Finance". To remove it: `.\install-service.ps1 -Uninstall`.
* `stop.ps1` also stops the task. After that, run `start.bat` to start the app again, or reboot.
* If the app is already running from `start.bat`, `install-service.ps1 -StartNow` leaves it alone, and the task takes over after the next reboot.

**Alternative: NSSM** (a real Windows service). Download `nssm.exe` from https://nssm.cc, then in an Administrator prompt:

```bat
nssm install IACFFinance "C:\Users\<you>\IACF-Finance\.venv\Scripts\python.exe" "-m app.serve"
nssm set IACFFinance AppDirectory "C:\Users\<you>\IACF-Finance"
nssm set IACFFinance AppEnvironmentExtra PYTHONUTF8=1
nssm set IACFFinance AppStdout "C:\Users\<you>\IACF-Finance\data\logs\server.log"
nssm set IACFFinance AppStderr "C:\Users\<you>\IACF-Finance\data\logs\server.log"
nssm start IACFFinance
```

Use either the scheduled task or NSSM, not both.

## 9. Moving your existing data from the current server

1. On the **current** instance, go to **Settings → Backup → "⬇ Download a fresh backup"**. This saves a `finance-….db` file.
2. Copy that file to the Windows PC. Keep it private: it contains all your finances.
3. On the **new** instance, finish the `/setup` page with any password (needed only to get in), then go to
   **Settings → Backup → Restore**, choose the `.db` file and click Restore.
4. The page reloads. **Log in with the password from your old server**, because the backup's password replaces the temporary one.
5. What moves with the backup: all accounts, transactions, categories, budgets, schedules, reconciliations, settings, **the API key**
   (a fresh `data\API_KEY.txt` is written) and the email-monitor settings.
6. What does **not** move:
   * **Email-monitor mailbox passwords** are encrypted with the old server's `secret.key` file, which isn't in the backup.
     Re-enter each mailbox password in **Settings → Email Monitoring**. Copying the old `data/secret.key` into `data\` *before the first start* also works,
     but treat that file like a password.
   * Receipt attachments (`data/attachments`). Copy that folder across if you need them.
   * Telegram settings, which live in `.env` (section 10).
7. Once you're happy with the Windows copy, stop the old server, so the two copies don't drift apart and email isn't imported twice.

## 10. Backups and where your data lives

Everything is in the **data folder** (`DATA_DIR`, by default `data\` next to the app):

| File / folder | What |
|---|---|
| `finance.db` | the database (plus `finance.db-wal` / `-shm` while it's running) |
| `backups\` | automatic daily backups and "Back up now" copies (the last 30 are kept) |
| `secret.key` | encrypts the stored mailbox passwords. **Keep it private.** |
| `API_KEY.txt` | a copy of the API key, for scripts |
| `attachments\`, `imports\`, `logs\` | receipts, temporary import uploads, server logs |

To keep an extra copy elsewhere, copy the newest file in `data\backups` (or use Settings → Backup → Download) to another disk or a cloud drive.
Don't copy `finance.db` itself while the app is running. Use the backup files.

**Brief notes on the integrations:**

* **Telegram bot:** put `TELEGRAM_BOT_TOKEN=...` and `TELEGRAM_ALLOWED_CHAT_IDS=...` in `.env`, then restart. Only one copy of the app may use a given bot token,
  so remove the token from the old server.
* **Email monitoring (IMAP):** works the same on Windows. Re-enter the mailbox passwords after moving (section 9).
* **Google Drive (rclone):** install rclone (`winget install Rclone.Rclone`, or from https://rclone.org/downloads/), run `rclone config` and create a
  remote named exactly **`gdrive`**. Backups then upload to `gdrive:FinanceAppBackups`. Open a new window after installing so `rclone` is on PATH.
  If you use the boot task, set rclone up as the same Windows user.

## 11. Updating to a new version

1. Download a fresh backup (Settings → Backup), then run **stop.bat**.
2. Extract the new zip **over** the app folder, replacing files. The zip never contains `data\` or `.env`, so your data and settings stay.
   Don't delete the `data` folder.
3. Run **setup.bat** again (it installs any new Python packages). Then run **start.bat**.

Database changes are applied automatically and only ever add things. Your data is kept.

## 12. Troubleshooting

| Problem | What to do |
|---|---|
| **"Python 3.11 or newer was not found"** / `python` opens the Store | Install Python from python.org with "Add python.exe to PATH", turn off the App execution aliases (section 1), open a **new** window and run setup again. |
| **"running scripts is disabled on this system"** | Use the `.bat` files, or run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first. If it says the file "is not digitally signed", run `Get-ChildItem -Recurse \| Unblock-File`. |
| **"Port 8088 is already in use"** | Something else is using the port. Find it with `Get-NetTCPConnection -LocalPort 8088 -State Listen` (the `OwningProcess` is the PID; check it with `Get-Process -Id <pid>`). Often it's a copy of this app that's already running: run stop.bat. Or set `PORT=8089` in `.env`. |
| **Works on the PC but not from the phone** | 1) Does `.env` `HOST` include `100.68.245.87` (and did you restart)? Check `data\logs\server.log` for `Listening on http://100.68.245.87:8088`. 2) Was the firewall rule created in an Administrator window? Check it with `Get-NetFirewallRule -DisplayName "IACF Finance*"`. 3) Is Tailscale connected on both devices? Try `tailscale ping iacf-bac`. 4) From the other device: `Test-NetConnection 100.68.245.87 -Port 8088`. |
| **"Could not bind 100.68.245.87"** in the log | Tailscale wasn't running (or the IP changed). Start Tailscale, check the IP with `tailscale ip -4`, then restart the app. |
| **Forgot the password** | `.venv\Scripts\python.exe -m app.cli reset-password` (after stop.bat). The next visit shows /setup again. Your data is kept. |
| **Show the API key** | `.venv\Scripts\python.exe -m app.cli show-api-key` |
| **Anything else** | Look at `data\logs\server.log`, or run `.\start.ps1 -Foreground` to watch the messages live. |
