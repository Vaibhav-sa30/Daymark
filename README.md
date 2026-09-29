# Daymark

Daymark is a local-first execution assistant for daily actions, scheduled work blocks, reminders, and project planning. It includes a desktop launcher, an optional Telegram bot, and optional Google Calendar and AI integrations.

## Run on Windows

1. Install Python 3.10 or newer.
2. Install the dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Start Daymark with `launch_daymark.bat`, or run `python daemon.py` and open [http://localhost:4173](http://localhost:4173).

Daymark keeps actions, calendar settings, AI credentials, and Telegram credentials in local JSON files. Those files are excluded from Git. Add integrations from Daymark settings or configure `telegram_config.json` locally for the Telegram bot.

## Development

Run the API and static app server directly with:

```powershell
python server.py
```

The server listens on port 4173. `daemon.py` adds the system tray service and background reminders. The optional Telegram bot starts when a valid local bot token is configured.
