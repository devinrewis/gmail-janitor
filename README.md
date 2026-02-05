# Gmail Janitor

A powerful Python tool to automatically scan your Gmail inbox, unsubscribe from marketing emails, and clean up thousands of old promotional messages. Features intelligent batch processing, automatic retries, progress caching, and exponential backoff to handle large mailboxes efficiently.

## Features

- 🚀 **Fast batch processing** - Fetches 20 emails per request with automatic retries
- 💾 **Smart caching** - Saves progress every 10 batches, survives crashes
- 🔄 **Automatic retry logic** - Handles timeouts and rate limits gracefully
- 📊 **Domain grouping** - Groups emails by sender domain (e.g., @company.com)
- 🎯 **Activity filtering** - Only shows recently active subscriptions
- 🗑️ **Bulk cleanup** - Delete thousands of old marketing emails quickly
- 📈 **Progress tracking** - Real-time progress with percentage complete

## Quick Start

### Prerequisites

- Python 3.7 or higher
- A Gmail account
- 5-10 minutes for setup

### Installation

1. **Clone or download this project**
   ```bash
   cd ~/Projects/inbox-cleaner
   ```

2. **Run the setup script**
   ```bash
   ./setup.sh
   ```

   This will:
   - Create a Python virtual environment
   - Install all required dependencies
   - Set up the project structure

3. **Activate the virtual environment**
   ```bash
   source venv/bin/activate
   ```

---

## Setting Up Gmail API Access (First Time Only)

This is the most important step. Follow these instructions carefully:

### Step 1: Create a Google Cloud Project

1. **Go to Google Cloud Console**
   - Visit https://console.cloud.google.com/
   - Sign in with your Gmail account

2. **Create a new project**
   - Click the project dropdown at the top (says "Select a project")
   - Click "NEW PROJECT" in the top-right
   - Enter project name: `Inbox Cleaner` (or any name you like)
   - Click "CREATE"
   - Wait 10-20 seconds for the project to be created

3. **Select your new project**
   - Click the project dropdown again
   - Select your newly created "Inbox Cleaner" project

### Step 2: Enable the Gmail API

1. **Open the API Library**
   - In the left sidebar, click "APIs & Services" → "Library"
   - Or search for "API Library" in the search bar at the top

2. **Find and enable Gmail API**
   - In the search box, type `Gmail API`
   - Click on "Gmail API" in the results
   - Click the blue "ENABLE" button
   - Wait for it to enable (takes a few seconds)

### Step 3: Configure OAuth Consent Screen

This step tells Gmail what permissions your app needs.

1. **Go to OAuth consent screen**
   - Click "APIs & Services" → "OAuth consent screen" in the left sidebar

2. **Choose User Type**
   - Select "External" (allows you to use your personal Gmail)
   - Click "CREATE"

3. **Fill in App Information**
   - **App name**: `Inbox Cleaner` (or any name)
   - **User support email**: Your email address
   - **Developer contact**: Your email address
   - Leave everything else blank
   - Click "SAVE AND CONTINUE"

4. **Scopes** (Step 2)
   - Click "SAVE AND CONTINUE" (no changes needed)

5. **Test users** (Step 3)
   - Click "+ ADD USERS"
   - Enter your Gmail address
   - Click "ADD"
   - Click "SAVE AND CONTINUE"

6. **Summary** (Step 4)
   - Review and click "BACK TO DASHBOARD"

### Step 4: Create OAuth Credentials

1. **Go to Credentials page**
   - Click "APIs & Services" → "Credentials" in the left sidebar

2. **Create OAuth Client ID**
   - Click "+ CREATE CREDENTIALS" at the top
   - Select "OAuth client ID"

3. **Choose Application Type**
   - Application type: **Desktop app**
   - Name: `Inbox Cleaner Desktop` (or any name)
   - Click "CREATE"

4. **Download the credentials**
   - A popup appears with "Client ID" and "Client secret"
   - Click "DOWNLOAD JSON" button
   - This downloads a file like `client_secret_xxxxx.json`

5. **Rename and move the file**
   - Rename the downloaded file to exactly `credentials.json`
   - Move it to your `inbox-cleaner` project directory
   - The file should be at: `~/Projects/inbox-cleaner/credentials.json`

### Step 5: First Run - Authorize the App

1. **Run the script**
   ```bash
   python inbox_cleaner.py --scan
   ```

2. **Browser opens automatically**
   - Your default browser will open to Google's sign-in page
   - If browser doesn't open, copy the URL from the terminal

3. **Sign in and grant permissions**
   - Select your Gmail account
   - You'll see: "Google hasn't verified this app" - This is normal!
   - Click "Advanced" → "Go to Inbox Cleaner (unsafe)"
   - Review the permissions (read, modify, delete emails)
   - Click "Continue"

4. **Success!**
   - Browser shows: "The authentication flow has completed"
   - Terminal shows: "✓ Successfully authenticated with Gmail"
   - A `token.pickle` file is created (stores your login)

You're now ready to use the script! You only need to do this setup once.

---

## Usage

### Scan Mode - Find Active Subscriptions

Shows all senders you're currently subscribed to with unsubscribe links.

```bash
# Basic scan
python inbox_cleaner.py --scan

# Only show subscriptions active in last 30 days (recommended)
python inbox_cleaner.py --scan --recent-days 30

# Preview without making changes
python inbox_cleaner.py --scan --dry-run
```

**What it does:**
- Searches your mailbox for emails with unsubscribe links
- Groups by sender domain
- Shows most recent email date
- Displays example email subjects

**Output example:**
```
1. From: Company Newsletter <newsletter@company.com>
   Email count: 15
   Last email: 2026-01-15 (19 days ago)
   Example: Weekly digest - January 2026
   Unsubscribe: https://company.com/unsubscribe/xyz789...
```

### Interactive Mode - Choose What to Unsubscribe

Review each subscription and decide whether to unsubscribe.

```bash
# Interactive mode with recent filter
python inbox_cleaner.py --interactive --recent-days 30
```

**For each subscription, choose:**
- `y` - Unsubscribe from this sender
- `n` - Skip this sender
- `q` - Quit and see summary

### Cleanup Mode - Delete Old Marketing Emails

**⚠️ Important:** This mode deletes emails. Use `--dry-run` first to preview!

Free up mailbox space by deleting old marketing emails in bulk.

```bash
# Preview what would be deleted (safe, recommended first)
python inbox_cleaner.py --cleanup --dry-run

# Delete emails older than 90 days (moved to trash, recoverable)
python inbox_cleaner.py --cleanup

# Delete emails older than 180 days
python inbox_cleaner.py --cleanup --older-than 180

# Permanent deletion (non-recoverable, use with caution!)
python inbox_cleaner.py --cleanup --permanent
```

**What cleanup mode does:**

1. **Searches your entire mailbox** for marketing emails older than specified days
2. **Groups by sender domain** (e.g., all @company.com emails together)
3. **Sorts by size** - Shows biggest space offenders first
4. **Shows you batches interactively:**
   ```
   Domain: company.com
   Email count: 156
   Example senders:
     • marketing@company.com
     • newsletter@company.com
   Date range: 2024-08-15 to 2025-11-20
   Example subjects:
     • Weekly Sale Alert
     • New Products Just Arrived

   Delete 156 emails from @company.com?
   (y)es / (n)o / (q)uit / (s)how all subjects:
   ```

5. **Batch deletes approved emails** - 100 at a time with progress tracking

**Interactive commands:**
- `y` - Delete all emails from this domain
- `n` - Skip this domain
- `s` - Show all email subjects first, then decide
- `q` - Quit and see summary

**Safety features:**
- Default: Moves to trash (recoverable for 30 days)
- Requires `--permanent` flag for permanent deletion
- Shows confirmation prompt before permanent deletion
- `--dry-run` mode to preview without changes

### Advanced Options

```bash
# Filter by recent activity (only show active subscriptions)
python inbox_cleaner.py --scan --recent-days 30

# Cleanup with custom date threshold
python inbox_cleaner.py --cleanup --older-than 365  # 1 year

# Dry run (preview mode - no changes made)
python inbox_cleaner.py --cleanup --dry-run

# Auto mode (automatically unsubscribe from everything - use carefully!)
python inbox_cleaner.py --auto --recent-days 30
```

---

## Troubleshooting

### Common Issues

**"Credentials not found" error**
- Make sure `credentials.json` is in the project directory
- Check the filename is exactly `credentials.json` (not `client_secret_xxx.json`)
- Run `ls -la` to verify the file exists

**"Google hasn't verified this app" warning**
- This is normal for personal apps!
- Click "Advanced" → "Go to Inbox Cleaner (unsafe)"
- This warning appears because Google hasn't reviewed your personal app
- It's safe - you created the app yourself

**"Insufficient permissions" error**
- Delete `token.pickle` file
- Run the script again and re-authorize
- Make sure you approve all requested permissions

**"Timeout" errors**
- The script automatically retries timeouts (up to 3 times)
- If persistent, your internet connection may be slow
- Try running during off-peak hours

**Browser doesn't open automatically**
- Copy the authorization URL from the terminal
- Paste it into your browser manually
- Complete the authorization process

**Cache file getting too large**
- Cache is stored in `email_metadata_cache.json`
- Safe to delete anytime to start fresh
- File is typically 10-50MB for large mailboxes

**"High error rate" messages**
- Script automatically backs off when hitting rate limits
- Wait for the backoff delay - it will resume automatically
- This is normal for very large mailboxes (100k+ emails)

### Performance Tips

1. **Use `--recent-days` for faster scans**
   ```bash
   python inbox_cleaner.py --scan --recent-days 30
   ```
   Only processes recently active subscriptions

2. **Run cleanup in stages**
   ```bash
   # First, review very old emails
   python inbox_cleaner.py --cleanup --older-than 365

   # Then, process slightly newer emails
   python inbox_cleaner.py --cleanup --older-than 180
   ```

3. **Use cache between runs**
   - Don't delete `email_metadata_cache.json`
   - Subsequent runs will be much faster

4. **Process during off-peak hours**
   - Gmail API is faster during off-peak hours
   - Late evening or early morning typically best

---

## Security & Privacy

**What permissions does the script need?**
- Read your Gmail messages (to find subscriptions)
- Modify messages (to move to trash)
- Send email (for unsubscribe requests via email)

**Where is your data stored?**
- **Locally only** - All data stays on your computer
- OAuth token: `token.pickle` (can be deleted anytime)
- Cache: `email_metadata_cache.json` (can be deleted anytime)
- No data is sent to any server except Gmail's official API

**Can I revoke access?**
- Yes! Go to https://myaccount.google.com/permissions
- Find "Inbox Cleaner" and click "Remove Access"
- Delete `token.pickle` to force re-authorization

**Is my password stored?**
- No! The script uses OAuth 2.0 (no password ever shared)
- Google provides a secure token after you log in
- Token is stored locally in `token.pickle`

**What about the credentials.json file?**
- Contains your app's OAuth client ID (not a password)
- Does NOT contain your Gmail password
- Safe to keep, but included in `.gitignore` for safety
- Unique to your Google Cloud project

---

## Files in This Project

| File | Purpose | Safe to Delete? |
|------|---------|----------------|
| `inbox_cleaner.py` | Main script | No |
| `requirements.txt` | Python dependencies | No |
| `credentials.json` | OAuth app credentials | No (need to re-download) |
| `token.pickle` | Your login token | Yes (will re-authenticate) |
| `email_metadata_cache.json` | Cached email data | Yes (will re-fetch emails) |
| `.gitignore` | Protects sensitive files | No |
| `venv/` | Python virtual environment | Yes (run setup.sh again) |

---

## Advanced Usage

### Automating with Cron (macOS/Linux)

Run cleanup automatically every Sunday at 2 AM:

```bash
# Edit crontab
crontab -e

# Add this line:
0 2 * * 0 cd ~/Projects/inbox-cleaner && source venv/bin/activate && python inbox_cleaner.py --cleanup --older-than 90 --auto
```

⚠️ **Note:** `--auto` mode automatically approves all deletions. Use carefully!

### Processing Very Large Mailboxes (100k+ emails)

For mailboxes with 100,000+ emails:

1. **Increase timeout tolerance**
   - The script will automatically retry timeouts
   - Expect 10-20 minute processing time

2. **Process in stages**
   ```bash
   # Stage 1: Very old emails (minimal regret risk)
   python inbox_cleaner.py --cleanup --older-than 730  # 2 years

   # Stage 2: Old emails
   python inbox_cleaner.py --cleanup --older-than 365  # 1 year

   # Stage 3: Recent emails
   python inbox_cleaner.py --cleanup --older-than 90   # 3 months
   ```

3. **Monitor progress**
   - Cache is saved every 10 batches
   - Can safely Ctrl+C and resume later
   - Progress is preserved in cache file

---

## FAQ

**Q: Will this delete important emails?**
A: The script only targets marketing/promotional emails with unsubscribe links. Cleanup mode shows you each sender before deleting. Use `--dry-run` first!

**Q: Can I undo deletions?**
A: Yes! By default, emails are moved to trash (recoverable for 30 days). Only `--permanent` flag deletes forever.

**Q: How long does it take?**
A: For 50,000 emails: 2-5 minutes first run, 5-10 seconds subsequent runs (uses cache).

**Q: Will I hit Gmail API limits?**
A: The script has built-in exponential backoff and rate limit handling. It automatically slows down if needed.

**Q: Can I run this on multiple Gmail accounts?**
A: Yes! Each account needs its own `credentials.json` and `token.pickle`. Create separate project folders.

**Q: Does this work with G Suite / Google Workspace?**
A: Yes! Follow the same setup process.

**Q: What happens if the script crashes?**
A: Progress is auto-saved every 10 batches. Maximum loss: ~900 emails (1-2%).


---

## License

MIT License - Free to use and modify
