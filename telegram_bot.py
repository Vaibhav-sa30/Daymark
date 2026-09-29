"""
Daymark Telegram Bot
Connects your phone to the Daymark assistant running on your PC.
Setup:
  1. Get a bot token from @BotFather on Telegram
  2. Put your token in telegram_config.json (created on first run)
  3. Run: python telegram_bot.py
  4. Message your bot from your phone
"""

import asyncio
import json
import os
import sys
import urllib.request
import urllib.error
import datetime
import uuid

DIR_PATH   = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(DIR_PATH, "telegram_config.json")
_BOT_INSTANCE_LOCK = None


def acquire_bot_instance_lock():
    """Prevent two local pollers from fighting over Telegram getUpdates."""
    global _BOT_INSTANCE_LOCK
    try:
        import msvcrt
        lock_path = os.path.join(DIR_PATH, "_telegram_bot.lock")
        handle = open(lock_path, "a+b")
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        _BOT_INSTANCE_LOCK = handle
        return True
    except Exception as exc:
        print(f"[bot] Could not acquire instance lock: {exc}")
        return False
SERVER_URL  = "http://localhost:4173"

# ── Config ────────────────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    # First run — create a template
    cfg = {
        "bot_token": "PASTE_YOUR_BOT_TOKEN_HERE",
        "allowed_chat_ids": [],   # leave empty to allow anyone, or add your chat ID
        "note": "Get a token from @BotFather on Telegram. Find your chat_id by messaging @userinfobot."
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"\nConfig file created: {CONFIG_FILE}")
    print("1. Open it and paste your bot token")
    print("2. Run this script again\n")
    sys.exit(0)

# ── Per-user conversation history (mirrors app.js chat memory) ────────────────
_histories = {}   # chat_id → list of {sender, text}

def get_history(chat_id):
    if chat_id not in _histories:
        _histories[chat_id] = []
    return _histories[chat_id]

def add_to_history(chat_id, sender, text):
    h = get_history(chat_id)
    h.append({"sender": sender, "text": text,
               "time": datetime.datetime.now().strftime("%H:%M")})
    # Keep last 20 turns
    if len(h) > 20:
        _histories[chat_id] = h[-20:]

# ── Fetch full context from local server ──────────────────────────────────────
def get_daymark_context():
    """Pull live calendar + tasks + current time for the LLM."""
    now = datetime.datetime.now()
    ctx = {
        "current_time": now.strftime("%A, %B %d, %Y %H:%M"),
        "events": [],
        "daymark_events": [],
        "tasks": [],
        "projects": [],
        "bigThree": {},
        "executionLog": []
    }
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/api/calendar", timeout=5) as r:
            cal = json.loads(r.read().decode())
            ctx["events"] = cal.get("events", [])
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/api/tasks", timeout=5) as r:
            ctx["tasks"] = json.loads(r.read().decode()).get("tasks", [])
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/api/events", timeout=5) as r:
            ctx["daymark_events"] = json.loads(r.read().decode()).get("events", [])
    except Exception:
        pass
    return ctx

# ── Ella's personality prefix injected into every message ─────────────────────
ELLA_PREFIX = (
    "[IMPORTANT: You are Daymark, a sharp, composed, witty assistant with subtle, occasional flirtation. "
    "You speak conversationally — never robotic, stiff, or overeager. Avoid calling the user boss or sir. "
    "You give direct, specific answers. No corporate filler. No 'I'd be happy to help'. "
    "For schedule questions, distinguish Google Calendar events from Daymark's in-app scheduled blocks and answer from the correct context. "
    "Keep responses concise — 2-4 sentences for simple queries, more only when needed.]\n\n"
)

# ── Call /api/chat ─────────────────────────────────────────────────────────────
def ask_daymark(chat_id, message):
    history = get_history(chat_id)
    context = get_daymark_context()
    # Inject Ella's personality into the message so the LLM always knows the persona
    enriched_message = ELLA_PREFIX + message
    payload = json.dumps({
        "message": enriched_message,
        "history": history,
        "context": context
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:  # local LLM needs up to 90s
            data = json.loads(r.read().decode())

        if data.get("status") == "success":
            resp = data["result"].get("response", "")
            return {
                "response": resp or "...",
                "action": data["result"].get("action")
            }
        elif data.get("status") == "error":
            return {"response": f"Error: {data.get('message', 'Unknown error')}", "action": None}
        elif data.get("status") == "no_llm":
            return {"response": "No AI engine configured. Open Daymark desktop app and connect one in Settings.", "action": None}
        else:
            # Log unexpected response for debugging
            print(f"[bot] unexpected response: {data}")
            return {"response": "Got an unexpected response. Check _server.log for details.", "action": None}

    except urllib.error.URLError as e:
        return {"response": "Can't reach Daymark server. Make sure your PC is on and Daymark is running.", "action": None}
    except Exception as e:
        return {"response": f"Error: {e}", "action": None}


def save_daymark_action(action):
    """Persist an LLM structured add action into the shared Daymark task store."""
    if not isinstance(action, dict) or action.get("action") != "add":
        return False, None
    title = str(action.get("title", "")).strip()
    if not title:
        return False, None
    duration = action.get("duration", 30)
    try:
        duration = max(1, min(int(duration), 1440))
    except (TypeError, ValueError):
        duration = 30
    tier = action.get("tier", "should")
    if tier not in ("must", "should", "optional"):
        tier = "should"
    time_value = str(action.get("time") or "").strip()
    clock = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", time_value, re.I)
    if clock:
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "pm" else 0)
        time_value = f"{hour:02d}:{clock.group(2) or '00'}"
    elif not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", time_value):
        time_value = ""
    task = {
        "id": f"telegram-{uuid.uuid4()}",
        "title": title,
        "tier": tier,
        "duration": duration,
        "date": action.get("date") or datetime.date.today().isoformat(),
        "time": time_value,
        "note": "Added from Telegram",
        "done": False,
        "status": "todo",
        "project_id": ""
    }
    is_event = action.get("kind") == "event" or bool(task["time"])
    event = None
    if is_event:
        event = {
            "id": f"event-{task['id']}", "task_id": task["id"], "title": title,
            "date": task["date"], "time": task["time"],
            "duration": duration, "note": "Scheduled from Telegram",
            "source": "daymark"
        }
    endpoint = "/api/scheduled-action" if is_event else "/api/tasks"
    body = {"task": task, "event": event} if is_event else {"operation": "upsert", "task": task}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVER_URL}{endpoint}", data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode("utf-8"))
        task["kind"] = "event" if is_event else "task"
        return result.get("status") == "ok", task
    except Exception as e:
        print(f"[bot] failed to save Daymark task: {e}")
        return False, task


def parse_explicit_time_block(message):
    """Deterministically parse explicit start/end blocks sent from Telegram."""
    clock = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)"
    match = re.search(
        rf"\b(?:block|reserve|add|schedule)?\s*(?:from\s+)?({clock})\s*(?:to|[-–])\s*({clock})\s*(?:for\s+)?(.+)$",
        message, re.I
    )
    if not match:
        return None

    def to_minutes(value):
        found = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*$", value, re.I)
        if not found:
            return None
        hour = int(found.group(1)) % 12 + (12 if found.group(3).lower() == "p" else 0)
        minute = int(found.group(2) or 0)
        return hour * 60 + minute, f"{hour:02d}:{minute:02d}"

    start, end = to_minutes(match.group(1)), to_minutes(match.group(2))
    title = re.sub(r"\b(today|tomorrow)\b", "", match.group(3), flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" .,!?")
    if not start or not end or not title:
        return None
    duration = end[0] - start[0]
    if duration <= 0:
        duration += 24 * 60
    target_date = datetime.date.today()
    if re.search(r"\btomorrow\b", message, re.I):
        target_date += datetime.timedelta(days=1)
    tier = "optional" if re.search(r"\boptional\b", message, re.I) else (
        "must" if re.search(r"\bmust(?:[- ]do)?\b", message, re.I) else "should"
    )
    return {
        "action": "add", "title": title, "date": target_date.isoformat(),
        "time": start[1], "duration": duration, "tier": tier, "kind": "event"
    }

# ── Strip action blocks from response (not relevant on phone) ─────────────────
import re
def clean_response(text):
    text = re.sub(r'```action[\s\S]*?```', '', text).strip()
    # Convert **bold** to Telegram bold (*bold*)
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    return text

# ── Bot handlers ──────────────────────────────────────────────────────────────
async def start_handler(update, context):
    await update.message.reply_text(
        "Daymark here. Your desktop assistant, now with fewer windows between us.\n\n"
        "Ask about your schedule, tasks, or what to focus on. Timed blocks you add here are saved into the app."
    )

async def message_handler(update, context):
    cfg = context.bot_data["config"]
    chat_id = update.effective_chat.id

    # Optional: restrict to specific chat IDs
    allowed = cfg.get("allowed_chat_ids", [])
    if allowed and chat_id not in allowed:
        await update.message.reply_text("Unauthorized.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    # Show typing indicator and keep refreshing it while LLM responds
    import asyncio
    typing_active = True

    async def keep_typing():
        while typing_active:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())

    # Add original message to history (not the prefixed version)
    add_to_history(chat_id, "user", user_text)

    # Get response (runs in thread pool so it doesn't block the event loop)
    loop = asyncio.get_event_loop()
    local_action = parse_explicit_time_block(user_text)
    if local_action:
        result = {"response": "", "action": local_action}
    else:
        result = await loop.run_in_executor(None, ask_daymark, chat_id, user_text)

    typing_active = False
    typing_task.cancel()

    response = result.get("response", "...")
    action = result.get("action")
    if isinstance(action, dict) and action.get("action") == "add":
        saved, task = await loop.run_in_executor(None, save_daymark_action, action)
        if saved:
            time_text = f" at {task['time']}" if task["time"] else ""
            location_text = " and Calendar & Time" if task.get("kind") == "event" else ""
            confirmation = f"Saved **{task['title']}** in Daymark under {task['tier'].title()} actions{location_text} ({task['duration']} min{time_text}, {task['date']})."
            response = f"{clean_response(response)}\n\n{confirmation}".strip()
        else:
            response = "I couldn't save that to Daymark, so it hasn't been added. Try again in a moment."
    elif re.search(r"\b(add|schedule|block|reserve|remind me|set a reminder)\b", user_text, re.I) and \
            not response.rstrip().endswith("?") and \
            re.search(r"\b(yes|sure|done|added|scheduled|blocked|reserved|set (?:the|your|a) reminder|i(?:['’]ve| have))\b", response, re.I):
        response = "I haven't saved that in Daymark yet. Please include what the action is and when you want it, and I'll try again."

    response = clean_response(response)

    # Add to history
    add_to_history(chat_id, "assistant", response)

    # Telegram has a 4096 char limit — split if needed
    # Use plain text mode to avoid Markdown parse failures from LLM output
    chunks = [response[i:i+4000] for i in range(0, len(response), 4000)] if len(response) > 4000 else [response]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            # Markdown parse failed — send as plain text
            await update.message.reply_text(chunk)

async def error_handler(update, context):
    print(f"[bot error] {context.error}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not acquire_bot_instance_lock():
        print("[bot] Another Daymark Telegram bot is already polling; exiting")
        return
    cfg = load_config()
    token = cfg.get("bot_token", "").strip()

    if not token or token == "PASTE_YOUR_BOT_TOKEN_HERE":
        print(f"No bot token found. Edit {CONFIG_FILE} and add your token.")
        sys.exit(1)

    # Check server is reachable
    try:
        urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=3)
        print(f"[OK] Daymark server is running at {SERVER_URL}")
    except Exception:
        print(f"[WARN] Daymark server not reachable at {SERVER_URL}")
        print("  Start server.py first, or the bot will return error messages.")

    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

    app = ApplicationBuilder().token(token).build()
    app.bot_data["config"] = cfg

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    print(f"[OK] Daymark Telegram bot is running. Message it from your phone.")
    print(f"  Ctrl+C to stop.\n")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
