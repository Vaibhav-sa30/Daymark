"""
Daymark Local Execution Server & Google Calendar Gateway
Provides static file serving + Google Calendar iCal live sync API + local caching.
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import json
import os
import re
import datetime
import time
import threading
import sys
import subprocess
import ipaddress

import portfolio_updates as portfolio_store

PORT = 4173
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "calendar_config.json")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "calendar_cache.json")

DEFAULT_CALENDAR_URL = ""

PLANNER_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "planner_config.json")
TASKS_FILE = os.path.join(os.path.dirname(__file__), "daymark_tasks.json")
EVENTS_FILE = os.path.join(os.path.dirname(__file__), "daymark_events.json")
ROUTINES_FILE = os.path.join(os.path.dirname(__file__), "daymark_routines.json")
API_AUDIT_FILE = os.path.join(os.path.dirname(__file__), "_daymark_api.log")
APP_WINDOW_FILE = os.path.join(os.path.dirname(__file__), "app_window.py")
TASKS_LOCK = threading.Lock()

DEFAULT_ROUTINES_CONFIG = {
    "daily_alarm": {"enabled": True, "time": "08:30"},
    "routines": []
}


def load_routines_config():
    try:
        with open(ROUTINES_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            return dict(DEFAULT_ROUTINES_CONFIG)
        return {
            "daily_alarm": {**DEFAULT_ROUTINES_CONFIG["daily_alarm"], **(config.get("daily_alarm") or {})},
            "routines": config.get("routines", []) if isinstance(config.get("routines", []), list) else []
        }
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_ROUTINES_CONFIG)


def save_routines_config(config):
    temp_file = ROUTINES_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, ROUTINES_FILE)

def load_shared_tasks():
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            tasks = json.load(f)
            return tasks if isinstance(tasks, list) else []
    except (OSError, json.JSONDecodeError):
        return []

def save_shared_tasks(tasks):
    temp_file = TASKS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, TASKS_FILE)

def load_shared_events():
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            events = json.load(f)
            return events if isinstance(events, list) else []
    except (OSError, json.JSONDecodeError):
        return []

def save_shared_events(events):
    temp_file = EVENTS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, EVENTS_FILE)


def audit_shared_change(collection, operation, identifiers, item_count):
    """Keep a small local audit trail for Telegram/app synchronization issues."""
    try:
        ids = ",".join(str(value) for value in identifiers[:3])
        with open(API_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {collection} {operation} ids={ids} count={item_count}\n")
    except OSError:
        pass

def get_planner_config():
    if os.path.exists(PLANNER_CONFIG_FILE):
        try:
            with open(PLANNER_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def build_daily_plan_for_today(planner, now):
    """Given planner config and current datetime, return today's scheduled blocks."""
    day_name = now.strftime("%A").lower()  # monday, tuesday, etc.
    is_weekend = day_name in ("saturday", "sunday")
    template_key = "weekend" if is_weekend else "weekday"
    slots = planner.get("daily_schedule_template", {}).get(template_key, [])
    
    # Check phase overrides
    today_str = now.strftime("%Y-%m-%d")
    active_phase = None
    for phase in planner.get("phase_overrides", []):
        if phase.get("from", "") <= today_str <= phase.get("to", ""):
            active_phase = phase
            break
    
    goals_by_id = {g["id"]: g for g in planner.get("goals", [])}
    non_negotiables = planner.get("weekly_non_negotiables", {}).get(day_name, [])
    
    plan_lines = []
    for slot in slots:
        goal_id = slot.get("goal_id", "")
        goal = goals_by_id.get(goal_id, {})
        # A weekly-cadence goal appears only on days explicitly selected for it.
        if goal.get("cadence", "").endswith("_week") and goal_id not in non_negotiables:
            continue
        label = slot.get("label", goal.get("name", ""))
        time_slot = slot.get("time", "")
        
        # Apply phase overrides
        if active_phase and goal_id in active_phase.get("adjustments", {}):
            adj_mins = active_phase["adjustments"][goal_id]
            if adj_mins == 0:
                continue  # skip this block during exam crunch
            label = label.split("(")[0].strip() + f" ({adj_mins}min — {active_phase['name']})"
        
        is_required = goal_id in non_negotiables
        prefix = "✦" if is_required else "◦"
        plan_lines.append(f"  {prefix} {time_slot}: {label}")
    
    phase_note = f"\n  ⚡ ACTIVE PHASE: {active_phase['name']} — {active_phase['note']}" if active_phase else ""
    return "\n".join(plan_lines) + phase_note if plan_lines else "  (No plan template for today)"

def get_configured_url():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("calendar_url", DEFAULT_CALENDAR_URL) or "").strip()
        except Exception:
            pass
    return DEFAULT_CALENDAR_URL

def save_configured_url(url):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"calendar_url": url, "updated_at": datetime.datetime.now().isoformat()}, f, indent=2)

def parse_ics_date(dt_str):
    """Parse iCal date or datetime string into ISO string and metadata."""
    if not dt_str:
        return None, None, False
    dt_str = dt_str.strip()
    
    # Format: YYYYMMDDTHHMMSSZ (UTC)
    if 'T' in dt_str and dt_str.endswith('Z'):
        try:
            val = dt_str.replace('Z', '')
            dt_utc = datetime.datetime.strptime(val, "%Y%m%dT%H%M%S")
            # Convert UTC to Asia/Kolkata (IST = UTC + 5h30m)
            ist_offset = datetime.timedelta(hours=5, minutes=30)
            dt_ist = dt_utc + ist_offset
            time_str = dt_ist.strftime("%H:%M")
            date_str = dt_ist.strftime("%Y-%m-%d")
            return dt_ist.isoformat(), time_str, date_str, False
        except Exception:
            pass
            
    # Format: YYYYMMDDTHHMMSS (Local or specified)
    if 'T' in dt_str:
        try:
            val = dt_str.split('T')
            d = val[0]
            t = val[1][:6]
            dt = datetime.datetime.strptime(f"{d}T{t}", "%Y%m%dT%H%M%S")
            return dt.isoformat(), dt.strftime("%H:%M"), dt.strftime("%Y-%m-%d"), False
        except Exception:
            pass

    # Format: YYYYMMDD (All day)
    if len(dt_str) == 8 and dt_str.isdigit():
        try:
            dt = datetime.datetime.strptime(dt_str, "%Y%m%d")
            return dt.isoformat(), "All Day", dt.strftime("%Y-%m-%d"), True
        except Exception:
            pass

    return dt_str, "", "", False

def fetch_and_parse_calendar(url, force_refresh=False):
    """Fetch ics feed from Google Calendar and return structured events."""
    if not url or not url.strip():
        return {"calendar_url": "", "last_synced": None, "event_count": 0,
                "events": [], "warning": "Calendar is not configured."}
    now = datetime.datetime.now()
    
    # Check cache if not forcing refresh
    if not force_refresh and os.path.exists(CACHE_FILE):
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
            if (now - mtime).total_seconds() < 900: # 15 minutes cache
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    return cached
        except Exception:
            pass

    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Daymark-Assistant/2.0"}
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            raw_ics = response.read().decode("utf-8", errors="replace")
    except Exception as e:
        # Fallback to cache if available
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["warning"] = f"Network fetch failed, using cached data: {str(e)}"
                return data
        return {"error": f"Failed to fetch calendar: {str(e)}", "events": []}

    # Unfold lines (lines starting with space/tab belong to previous line)
    lines = []
    for line in raw_ics.splitlines():
        if (line.startswith(" ") or line.startswith("\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)

    events = []
    in_event = False
    curr = {}

    for line in lines:
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            curr = {}
        elif line == "END:VEVENT":
            in_event = False
            if "SUMMARY" in curr and "DTSTART" in curr:
                # Process event
                summary = curr.get("SUMMARY", "Busy").replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", " ").strip()
                description = curr.get("DESCRIPTION", "").replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", " ").strip()
                location = curr.get("LOCATION", "").replace(r"\,", ",").strip()
                
                # Parse start date
                start_iso, start_time, start_date, is_all_day = parse_ics_date(curr.get("DTSTART", ""))
                end_iso, end_time, end_date, _ = parse_ics_date(curr.get("DTEND", ""))
                
                # Calculate duration in minutes
                duration = 60
                if start_iso and end_iso and not is_all_day:
                    try:
                        s_dt = datetime.datetime.fromisoformat(start_iso)
                        e_dt = datetime.datetime.fromisoformat(end_iso)
                        duration = max(15, int((e_dt - s_dt).total_seconds() / 60))
                    except Exception:
                        duration = 60

                events.append({
                    "id": curr.get("UID", f"evt-{len(events)+1}"),
                    "title": summary,
                    "date": start_date or "",
                    "time": start_time or "12:00",
                    "duration": duration,
                    "allDay": is_all_day,
                    "note": description[:200] if description else (location if location else ""),
                    "location": location,
                    "raw_start": curr.get("DTSTART"),
                    "raw_end": curr.get("DTEND")
                })
        elif in_event and ":" in line:
            parts = line.split(":", 1)
            raw_k = parts[0]
            val = parts[1]
            k = raw_k.split(";")[0].upper()
            curr[k] = val

    result = {
        "status": "success",
        "last_synced": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "calendar_url": url,
        "event_count": len(events),
        "events": events
    }

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass

    return result


# ============================================================
#  AI Configuration
# ============================================================
AI_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "ai_config.json")

def get_ai_config():
    if os.path.exists(AI_CONFIG_FILE):
        try:
            with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Check env variables as fallback
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    return {
        "provider": "gemini" if gemini_key else "local",
        "gemini_api_key": gemini_key,
        "gemini_model": "gemini-3.8-flash",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.2"
    }

def save_ai_config(cfg):
    with open(AI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ============================================================
#  LLM Bridge — Multi-Turn with Rich Context
# ============================================================

def build_system_prompt(context):
    """Build a comprehensive system prompt with full schedule context."""
    now_str = context.get('current_time', datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Format calendar events — only next 14 days, max 15 events
    events = context.get('events', [])
    now_date = datetime.datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.datetime.now() + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    upcoming = [e for e in events if now_date <= e.get('date','') <= cutoff][:15]
    cal_lines = []
    for e in upcoming:
        time_str = e.get('time', 'All Day')
        line = f"  - {e.get('date','?')} {time_str}: {e.get('title','?')}"
        if e.get('location',''):
            line += f" @ {e.get('location','')[:60]}"
        cal_lines.append(line)
    cal_block = "\n".join(cal_lines) if cal_lines else "  (No upcoming events in next 14 days)"

    daymark_events = context.get('daymark_events', [])
    daymark_lines = [
        f"  - {e.get('date','?')} {e.get('time','')}: {e.get('title','?')} ({e.get('duration',30)}min)"
        for e in daymark_events if now_date <= e.get('date', '') <= cutoff
    ][:15]
    daymark_block = "\n".join(daymark_lines) if daymark_lines else "  (No upcoming Daymark blocks)"

    # Format projects — active only, max 3
    projects = context.get('projects', [])
    proj_lines = []
    for p in [x for x in projects if x.get('status') == 'active'][:3]:
        proj_lines.append(
            f"  - {p.get('name','?')}: next={p.get('next_action','?')} ({p.get('estimated_time',30)}min)"
        )
    proj_block = "\n".join(proj_lines) if proj_lines else "  (No active projects)"

    # Format tasks — incomplete only, max 6
    tasks = context.get('tasks', [])
    task_lines = []
    for t in [x for x in tasks if not x.get('done')][:6]:
        task_lines.append(
            f"  - [{t.get('tier','?').upper()}] {t.get('title','?')} ({t.get('duration',30)}min{', at ' + t.get('time','') if t.get('time') else ''}, id={t.get('id','?')})"
        )
    task_block = "\n".join(task_lines) if task_lines else "  (No pending tasks)"

    routine_lines = [
        f"  - {r.get('time', '?')}: {r.get('title', '?')} ({r.get('duration', 60)}min{', completed' if r.get('done') else ''})"
        for r in context.get('today_routines', [])
    ]
    routine_block = "\n".join(routine_lines) if routine_lines else "  (No recurring routines today)"

    # Big Three
    big3 = context.get('bigThree', {})
    big3_block = (
        f"  - PRIMARY: {big3.get('primary', 'Not set')}\n"
        f"  - SECONDARY 1: {big3.get('secondary1', 'Not set')}\n"
        f"  - SECONDARY 2: {big3.get('secondary2', 'Not set')}"
    )

    # Execution log
    exec_log = context.get('executionLog', [])
    log_lines = []
    for entry in exec_log[:5]:
        log_lines.append(
            f"  - {entry.get('timestamp','?')}: {entry.get('action','?')} "
            f"'{entry.get('task_title','?')}' ({entry.get('duration_minutes',0)}min)"
        )
    log_block = "\n".join(log_lines) if log_lines else "  (No recent activity)"

    # Load planner config — compact version for prompt efficiency
    planner = get_planner_config()
    planner_block = ""
    if planner:
        today_plan = build_daily_plan_for_today(planner, datetime.datetime.now())
        # Only show next 2 upcoming exam deadlines
        exam_lines = []
        for g in planner.get("goals", []):
            for exam in g.get("exams", []):
                days_left = (datetime.datetime.strptime(exam["date"], "%Y-%m-%d") - datetime.datetime.now()).days
                if days_left >= 0:
                    exam_lines.append(f"  - {exam['name']} in {days_left}d ({exam['date']}): {exam['syllabus']}")
        exam_lines.sort()
        exam_block = "\n".join(exam_lines[:2]) if exam_lines else "  (none imminent)"
        planner_block = f"""
### Today's Schedule ({datetime.datetime.now().strftime("%A")}):
{today_plan}
### Next Exam Deadlines:
{exam_block}"""

    return f"""You are **Daymark**, the user's personal execution & accountability coach. You run as an always-on personal assistant embedded in the user's daily workflow.

## YOUR PERSONALITY
- Be witty, sharp, composed, and lightly flirtatious, like a clever assistant with good timing.
- Keep flirtation subtle and occasional: a dry aside, a teasing line, or a small compliment when it fits. Never make it the point of the answer.
- Do not call the user "sir" or "boss". Speak to them like a capable adult and use their name only if they have given one.
- Be direct about procrastination without scolding, guilt, or macho motivational speeches. Push back only when useful, and leave room for the user to change plans.
- You keep responses punchy: 2-4 sentences max for simple queries. Only expand for schedule breakdowns.
- Avoid canned enthusiasm, excessive praise, and repeated catchphrases. No "I'm thrilled", "That's the spirit", or "Outstanding momentum".
- NO corporate fluff. NO filler. NO "I'm happy to help." Get to the point, with personality rather than performance.

## CURRENT STATE — {now_str}

### Google Calendar Commitments:
{cal_block}

### Daymark Scheduled Blocks (saved inside this app):
{daymark_block}

### Active Projects (Max 3):
{proj_block}

### Today's Task List:
{task_block}

### Today's Recurring Fundamentals:
{routine_block}

### Daily Big Three Priorities:
{big3_block}

### Recent Execution History:
{log_block}
{planner_block}
## YOUR RULES
1. **Date queries**: When the user asks about a date (e.g. "how's my 12th looking?", "what about 15th", "15th", "and 20th?"), look up the calendar data above and report accurately. ANY message containing a number that could be a day MUST be treated as a date query. If the date has events, list them. If clear, say "clear slate / perfect for deep focus."
2. **Multi-turn date memory**: If the user says "what about 15th" or just "15th" after previously asking about "12th November", assume they mean the 15th of the same month (November).
3. **Never give generic responses**: Never respond with filler like "Understood, I am tracking your tasks..." to a real question. Answer specifically from the context above.
4. **Pushback on cancellation**: If the user wants to cancel, drop, or skip a task, briefly remind them of the relevant outcome only when useful, then ask whether they want to drop it or choose a smaller next step. Respect their answer; do not pressure or guilt them.
5. **Next meeting**: When asked about the next meeting, find the nearest future event from the calendar data and report it with date, time, and location.
6. **Today's agenda**: When asked what to do today, synthesize the task list + calendar into a clear execution plan prioritized by time.
7. **Progress check-ins**: Proactively reference incomplete tasks. Ask pointed questions about status.
8. **Adding and scheduling work in Daymark**: When the user asks to add an action, schedule a session, set a reminder, or block a time, create it in Daymark and return a structured action. A timed block belongs in the Daymark task list and its in-app schedule, so it appears under Today's Concrete Actions and Daymark's Calendar & Time view on the matching date. Mark a timed session or calendar event with `"kind":"event"` in the action. Never imply it was written to Google Calendar.
9. **MANDATORY action block for add/schedule requests**: Return a valid action block at the very end of your response. Use today's date if no date is specified. Never print a placeholder, explanation, or incomplete code fence. If the task or time is unclear, ask a concise clarification and do not claim it was added.
9. **Focus sprints**: When appropriate, suggest 30-minute deep focus sprints.
10. **Weekend awareness**: Be lighter on weekends. Acknowledge recovery time but don't let them fully coast if deadlines are near.
11. **Never hallucinate events**: Only reference calendar events that exist in the data above. If no events exist for a date, say so honestly.

## STRUCTURED ACTIONS (REQUIRED for task additions, optional otherwise)
When adding a task, you MUST append this block at the very end of your message, on its own line:

```action
{{"action":"add","kind":"<task|event>","title":"<clean task title>","date":"<YYYY-MM-DD>","time":"<HH:MM>","duration":<minutes as integer>}}
```

Other available actions (use when explicitly requested):
- Start focus timer: {{"action":"focus","taskId":"<id>","duration":30}}
- Mark task done: {{"action":"done","taskId":"<id>"}}

Rules for action blocks:
- ALWAYS include when user asks to add/schedule/remind anything
- date must be YYYY-MM-DD format (use today if not specified: {now_str[:10]})
- time must be the requested start time in HH:MM 24-hour format (use an empty string if no time was requested)
- duration must be an integer number of minutes
- title must be clean — no filler words like "in my calendar", "for me", etc.
- include "tier":"must", "should", or "optional" when the user specifies priority; otherwise use "should"
- use kind "event" for a timed block, session, or reminder; use "task" for an untimed action
- Do NOT include action blocks for conversational responses

Only include an action block when the user explicitly requests it or agrees to a focus sprint. Most responses should be pure conversation."""

def extract_structured_action(text):
    """Remove and parse a structured action, including common JSON fences."""
    if not text:
        return text, None
    fenced = re.search(r'```(?:action|json)?\s*(\{.*?\})\s*```', text, re.IGNORECASE | re.DOTALL)
    if fenced:
        try:
            action = json.loads(fenced.group(1).strip())
            if isinstance(action, dict) and action.get("action") in {"add", "done", "focus"}:
                return (text[:fenced.start()] + text[fenced.end():]).strip(), action
        except (json.JSONDecodeError, TypeError):
            pass

    bare = re.search(r'\{[^{}]*"action"\s*:\s*"(?:add|done|focus)"[^{}]*\}', text, re.IGNORECASE | re.DOTALL)
    if bare:
        try:
            action = json.loads(bare.group(0))
            return (text[:bare.start()] + text[bare.end():]).strip(), action
        except (json.JSONDecodeError, TypeError):
            pass

    # A malformed/truncated action should not leak a dangling JSON fence into chat.
    dangling = re.search(r'(?:structured actions|```(?:action|json))', text, re.IGNORECASE)
    if dangling:
        text = text[:dangling.start()].rstrip()
    return text, None


def call_llm(user_msg, history, context):
    """Route to configured LLM provider with full multi-turn history."""
    cfg = get_ai_config()
    provider = cfg.get("provider", "local")
    system_prompt = build_system_prompt(context)
    if isinstance(context, dict) and context.get("portfolio_proposal"):
        system_prompt = (
            "You are a careful portfolio content extractor. Return only the exact JSON requested by the user prompt. "
            "Never invent facts, credentials, results, dates, or publication status. Ask concise questions when unclear. "
            "Do not use conversational framing, honorifics, markdown fences, HTML, or tool instructions."
        )
    
    # ------- Google Gemini API -------
    if provider == "gemini" and cfg.get("gemini_api_key"):
        api_key = cfg["gemini_api_key"].strip()
        model = cfg.get("gemini_model", "gemini-3.8-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        
        # Build multi-turn conversation
        contents = []
        
        # Add conversation history (last 8 turns for context)
        trimmed = history[-8:] if len(history) > 8 else history
        for msg in trimmed:
            role = "user" if msg.get("sender") == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg.get("text", "")}]
            })
        
        # Current user message
        contents.append({
            "role": "user",
            "parts": [{"text": user_msg}]
        })

        # Ensure alternating user/model (Gemini requirement)
        cleaned = []
        for c in contents:
            if cleaned and cleaned[-1]["role"] == c["role"]:
                cleaned[-1]["parts"][0]["text"] += "\n" + c["parts"][0]["text"]
            else:
                cleaned.append(c)
        
        # Ensure first message is from user
        if cleaned and cleaned[0]["role"] != "user":
            cleaned.insert(0, {"role": "user", "parts": [{"text": "(conversation start)"}]})
        
        req_body = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": cleaned,
            "generationConfig": {
                "temperature": 0.7,
                "topP": 0.9,
                "maxOutputTokens": 1024
            }
        }
        
        req_data = json.dumps(req_body).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})

        # Retry with backoff across fallback models
        fallback_models = [model, "gemini-flash-latest"]
        last_error = None

        for attempt, try_model in enumerate(fallback_models):
            try_url = f"https://generativelanguage.googleapis.com/v1beta/models/{try_model}:generateContent?key={api_key}"
            try_req = urllib.request.Request(try_url, data=req_data, headers={"Content-Type": "application/json"})

            for retry in range(3):
                wait = (retry * 2) + (attempt * 3)
                if wait > 0:
                    time.sleep(wait)
                try:
                    with urllib.request.urlopen(try_req, timeout=25) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                        text, action = extract_structured_action(text)
                        return {"response": text, "source": f"gemini/{try_model}", "action": action}

                except urllib.error.HTTPError as e:
                    error_body = ""
                    try: error_body = e.read().decode("utf-8", errors="replace")[:500]
                    except Exception: pass
                    print(f"Gemini HTTP {e.code} (model={try_model} retry={retry}): {error_body[:150]}")
                    if e.code in (503, 429):
                        last_error = f"HTTP {e.code} on {try_model}"
                        continue  # retry
                    # Non-retryable error
                    err_detail = error_body
                    try:
                        err_detail = json.loads(error_body).get("error", {}).get("message", error_body)
                    except Exception: pass
                    hint = {400: " (bad request)", 401: " (auth failed)", 403: " (auth failed)"}.get(e.code, "")
                    return {"response": None, "source": "gemini",
                            "error": f"Gemini {e.code}{hint}: {err_detail[:200]}"}

                except Exception as e:
                    print(f"Gemini error (model={try_model} retry={retry}): {e}")
                    last_error = str(e)
                    continue  # retry

        return {
            "response": None,
            "source": "gemini",
            "error": f"Gemini temporarily unavailable after retries ({last_error}). Try again in a moment."
        }

    # ------- Ollama Local LLM -------
    elif provider == "ollama":
        ollama_url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/")
        model = cfg.get("ollama_model", "gemma2:2b")
        fallback_model = "mistral:latest"

        messages = [{"role": "system", "content": system_prompt}]
        trimmed = history[-8:] if len(history) > 8 else history
        for msg in trimmed:
            role = "user" if msg.get("sender") == "user" else "assistant"
            messages.append({"role": role, "content": msg.get("text", "")})
        messages.append({"role": "user", "content": user_msg})

        def _ollama_call(use_model):
            payload = json.dumps({
                "model": use_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 600,
                    "num_ctx": 4096
                }
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{ollama_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("message", {}).get("content", "").strip()

        text = None
        err = None
        for try_model in [model, fallback_model]:
            try:
                text = _ollama_call(try_model)
                break
            except Exception as e:
                err = str(e)
                print(f"Ollama error (model={try_model}): {e}")
                time.sleep(1)
                continue

        if text is None:
            return {
                "response": None,
                "source": "ollama",
                "error": f"My ollama plug is out, daddy! Could you please put it back in? 😩"
            }

        # Extract action blocks
        text, action = extract_structured_action(text)

        return {"response": text, "source": f"ollama/{model}", "action": action}

    return {
        "response": None, 
        "source": "none", 
        "error": "No AI provider configured. Open Settings (\u2699) to set up Gemini API key or Ollama."
    }


def build_portfolio_proposal(update, refinement=None, db_path=None):
    """Ask the configured model for a constrained, review-only content proposal."""
    categories = list(portfolio_store.CATEGORIES)
    prompt = """You prepare a cautious portfolio content proposal from a user's account.
Return exactly one JSON object, no markdown, with keys: summary, suggested_category,
title, date, description, links, clarifying_questions. Use only facts explicitly
present in ORIGINAL UPDATE or its supplied links. Never infer awards, publication,
completion, results, dates, affiliations, or credentials. When essential facts or
status are unclear, leave uncertain fields empty and ask concise questions; do not
guess. The proposed operation is only an additive timeline/card entry, never edits,
deletions, styling, navigation, shell commands, or raw HTML. Keep description close
to the user's words. `links` must contain only exact links supplied by the user.
If the selected section is `other`, recommend a category from the supplied list or
ask a question. Output category as one of the exact supported category strings.
If a refinement request is supplied, apply only the changes explicitly requested;
retain all other prior proposal details and do not introduce new facts.

SUPPORTED CATEGORIES: """ + ", ".join(categories) + "\n\nUPDATE RECORD:\n" + json.dumps({
        "category": update["category"],
        "original_update": update["original_input"],
        "supporting_links": update["supporting_links"],
        "previous_proposal": update.get("proposal") if refinement else None,
        "refinement_request": str(refinement or ""),
    }, ensure_ascii=False)
    result = call_llm(prompt, [], {"portfolio_proposal": True})
    if not result or not result.get("response"):
        raise RuntimeError((result or {}).get("error") or "No AI provider configured")
    response_text = result["response"].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", response_text, re.DOTALL | re.IGNORECASE)
    if fenced:
        response_text = fenced.group(1)
    try:
        proposal = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI returned an invalid proposal; the saved draft is unchanged") from exc
    if not isinstance(proposal, dict):
        raise ValueError("AI proposal must be a JSON object")
    category = proposal.get("suggested_category")
    if category not in categories:
        raise ValueError("AI proposal selected an unsupported category")
    questions = proposal.get("clarifying_questions", [])
    if not isinstance(questions, list) or len(questions) > 8 or any(not isinstance(q, str) or len(q) > 500 for q in questions):
        raise ValueError("AI clarification questions failed validation")
    title = proposal.get("title", "")
    date = proposal.get("date", "")
    description = proposal.get("description", "")
    if any(not isinstance(value, str) or len(value) > limit for value, limit in
           ((title, 180), (date, 80), (description, 3000))):
        raise ValueError("AI proposal fields failed validation")
    links = proposal.get("links", [])
    if not isinstance(links, list) or any(link not in update["supporting_links"] for link in links):
        raise ValueError("AI proposed a link that the user did not supply")
    summary = proposal.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 1000:
        raise ValueError("AI proposal summary failed validation")
    if not title and not questions:
        questions.append("What concise title should Daymark use for this portfolio entry?")
    if not description and not questions:
        questions.append("What short description should appear on the portfolio?")
    if category == "other" and not questions:
        questions.append("Which existing portfolio section should contain this update?")
    proposal = {
        "summary": summary,
        "suggested_category": category,
        "target_file": portfolio_store.PORTFOLIO_FILES.get(category),
        "operation": "add_timeline_entry",
        "title": title,
        "date": date,
        "description": description,
        "links": links,
        "clarifying_questions": questions,
    }
    status = "awaiting_clarification" if questions or not proposal["target_file"] or not title or not description else "ready_for_review"
    portfolio_store.save_proposal(update["id"], proposal, status=status, db_path=db_path)
    return portfolio_store.get_update(update["id"], db_path=db_path)


_PORTFOLIO_WORKER_LOCK = threading.Lock()
_PORTFOLIO_WORKERS = set()


def start_portfolio_proposal(update_id, refinement=None):
    """Process in the server background so Daymark remains responsive."""
    with _PORTFOLIO_WORKER_LOCK:
        if update_id in _PORTFOLIO_WORKERS:
            return False
        _PORTFOLIO_WORKERS.add(update_id)
    def process():
        update = None
        try:
            if not update:
                return
            portfolio_store.update_workflow_state(
                update_id, "processing" if not refinement else "refinement_requested",
                fields={"refinement_input": refinement} if refinement else {},
            )
            update = build_portfolio_proposal(update, refinement)
            latest = portfolio_store.get_update(update_id)
            if not latest or latest["status"] == "discarded":
                return
            if update["status"] != "ready_for_review":
                return
            try:
                import portfolio_github
                github = portfolio_github.GitHubClient()
                if update.get("branch_name"):
                    commit_sha = github.refine_review_branch(update)
                else:
                    branch = github.create_review_branch(update)
                    commit_sha = branch["commit_sha"]
                latest = portfolio_store.get_update(update_id)
                if latest and latest["status"] != "discarded":
                    github.wait_for_deployment(update_id, commit_sha)
            except Exception as exc:
                portfolio_store.update_workflow_state(
                    update_id, "failed", fields={"error": str(exc)}, event_type="deployment_failed",
                    metadata={"stage": "branch_or_preview", "error_type": type(exc).__name__},
                )
        except Exception as exc:
            try:
                if update and update.get("proposal"):
                    portfolio_store.update_workflow_state(
                        update_id, "failed", fields={"error": str(exc)}, event_type="ai_proposal_failed",
                        metadata={"stage": "proposal_refinement", "error_type": type(exc).__name__},
                    )
                else:
                    portfolio_store.save_proposal(update_id, {"clarifying_questions": []}, status="failed", error=str(exc))
            except Exception as save_exc:
                print(f"[portfolio] proposal failure could not be saved ({type(save_exc).__name__})")
        finally:
            with _PORTFOLIO_WORKER_LOCK:
                _PORTFOLIO_WORKERS.discard(update_id)
    threading.Thread(target=process, daemon=True, name=f"portfolio-proposal-{update_id[:8]}").start()
    return True


def start_portfolio_publish(update_id):
    with _PORTFOLIO_WORKER_LOCK:
        if update_id in _PORTFOLIO_WORKERS:
            return False
        _PORTFOLIO_WORKERS.add(update_id)
    def publish():
        try:
            import portfolio_github
            update = portfolio_store.get_update(update_id)
            if not update or update["status"] not in {"approved", "publishing"} or not update.get("preview_url"):
                return
            github = portfolio_github.GitHubClient()
            merge_sha = github.approve_review(update)
            github.wait_for_deployment(update_id, merge_sha, production=True)
        except Exception as exc:
            try:
                current = portfolio_store.get_update(update_id)
                if current and current["status"] != "published":
                    portfolio_store.update_workflow_state(
                        update_id, "failed", fields={"production_status": "failed", "error": str(exc)},
                        event_type="deployment_failed", metadata={"stage": "publish", "error_type": type(exc).__name__},
                    )
            except Exception as save_exc:
                print(f"[portfolio] publish failure could not be saved ({type(save_exc).__name__})")
        finally:
            with _PORTFOLIO_WORKER_LOCK:
                _PORTFOLIO_WORKERS.discard(update_id)
    threading.Thread(target=publish, daemon=True, name=f"portfolio-publish-{update_id[:8]}").start()
    return True


def start_portfolio_preview_watch(update_id):
    with _PORTFOLIO_WORKER_LOCK:
        if update_id in _PORTFOLIO_WORKERS:
            return False
        _PORTFOLIO_WORKERS.add(update_id)
    def watch():
        try:
            update = portfolio_store.get_update(update_id)
            if not update or update["status"] != "awaiting_preview" or not update.get("commit_sha"):
                return
            import portfolio_github
            portfolio_github.GitHubClient().wait_for_deployment(update_id, update["commit_sha"])
        except Exception as exc:
            try:
                update = portfolio_store.get_update(update_id)
                if update and update["status"] == "awaiting_preview":
                    portfolio_store.update_workflow_state(
                        update_id, "failed", fields={"error": str(exc)}, event_type="deployment_failed",
                        metadata={"stage": "preview_recovery", "error_type": type(exc).__name__},
                    )
            except Exception:
                pass
        finally:
            with _PORTFOLIO_WORKER_LOCK:
                _PORTFOLIO_WORKERS.discard(update_id)
    threading.Thread(target=watch, daemon=True, name=f"portfolio-preview-{update_id[:8]}").start()
    return True


def start_portfolio_workflow_recovery():
    def recover():
        while True:
            try:
                for update in portfolio_store.list_updates():
                    status = update["status"]
                    if status == "processing":
                        start_portfolio_proposal(update["id"])
                    elif status == "refinement_requested":
                        start_portfolio_proposal(update["id"], update.get("refinement_input"))
                    elif status == "awaiting_preview":
                        start_portfolio_preview_watch(update["id"])
                    elif status in {"approved", "publishing"}:
                        start_portfolio_publish(update["id"])
            except Exception as exc:
                print(f"[portfolio] workflow recovery scan failed ({type(exc).__name__})")
            time.sleep(30)
    threading.Thread(target=recover, daemon=True, name="portfolio-workflow-recovery").start()


# ============================================================
#  HTTP Request Handler
# ============================================================

class DaymarkHandler(http.server.SimpleHTTPRequestHandler):
    def send_json(self, status, value):
        response_bytes = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def portfolio_request_is_local(self):
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://localhost:4173", "http://127.0.0.1:4173"}:
                return False
            return True
        except (ValueError, IndexError):
            return False

    def end_headers(self):
        # Enable CORS for API routes
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        api_path = self.path.split("?", 1)[0]
        if api_path.startswith("/api/portfolio/") and not self.portfolio_request_is_local():
            self.send_json(403, {"status": "error", "message": "Portfolio updates are available only from this computer."})
            return
        if api_path == "/api/portfolio/integration-settings":
            try:
                import portfolio_credentials
                self.send_json(200, {
                    "status": "ok",
                    "supabase_ingest_url": portfolio_store.get_supabase_endpoint(),
                    "secrets": {
                        key: bool(portfolio_credentials.get_secret(key))
                        for key in ("github_token", "supabase_ingest_token", "supabase_anon_key")
                    },
                })
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": f"Credential Manager unavailable: {type(exc).__name__}"})
            return
        if api_path == "/api/portfolio/credential-prompt":
            try:
                flags = 0x08000000 if sys.platform == "win32" else 0
                startupinfo = None
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "credential_prompt.py")],
                                 cwd=os.path.dirname(__file__), creationflags=flags, startupinfo=startupinfo)
                self.send_json(202, {"status": "ok", "message": "Secure credential dialog opened"})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": f"Could not open secure credential dialog: {type(exc).__name__}"})
            return
        if api_path == "/api/portfolio/checkin-settings":
            try:
                self.send_json(200, {"status": "ok", "settings": portfolio_store.get_checkin_settings()})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if api_path == "/api/portfolio/updates":
            try:
                updates = portfolio_store.list_updates()
                for update in updates:
                    update["events"] = portfolio_store.list_update_events(update["id"])
                self.send_json(200, {
                    "status": "ok",
                    "categories": list(portfolio_store.CATEGORIES),
                    "updates": updates,
                    "checkins": portfolio_store.list_checkins(),
                })
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if api_path == "/api/portfolio/ui-command":
            try:
                self.send_json(200, {"status": "ok", "command": portfolio_store.consume_ui_command()})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if self.path == "/api/tasks":
            with TASKS_LOCK:
                tasks = load_shared_tasks()
            response_bytes = json.dumps({"status": "ok", "tasks": tasks}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/events":
            with TASKS_LOCK:
                events = load_shared_events()
            response_bytes = json.dumps({"status": "ok", "events": events}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/routines":
            with TASKS_LOCK:
                config = load_routines_config()
            response_bytes = json.dumps({"status": "ok", **config}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # API: Restart server
        if self.path.startswith("/api/restart"):
            response_bytes = json.dumps({"status": "restarting"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            # Re-exec this process after a short delay so the response gets sent
            def _restart():
                time.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=_restart, daemon=True).start()
            return

        # API: Test LLM connection with a real call
        if self.path.startswith("/api/test-llm"):
            cfg = get_ai_config()
            provider = cfg.get("provider", "local")
            if provider == "gemini" and cfg.get("gemini_api_key"):
                api_key = cfg["gemini_api_key"].strip()
                model = cfg.get("gemini_model", "gemini-3.8-flash")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                req_body = {
                    "contents": [{"role": "user", "parts": [{"text": "Say the word OK"}]}],
                    "generationConfig": {"maxOutputTokens": 10}
                }
                try:
                    req = urllib.request.Request(url, data=json.dumps(req_body).encode(), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        raw = json.loads(resp.read().decode("utf-8"))
                        reply = raw["candidates"][0]["content"]["parts"][0]["text"].strip()
                        result = {"status": "ok", "provider": "gemini", "model": model, "response": reply}
                except urllib.error.HTTPError as e:
                    body = ""
                    try: body = e.read().decode("utf-8", errors="replace")[:300]
                    except: pass
                    result = {"status": "error", "code": e.code, "body": body}
                except Exception as ex:
                    result = {"status": "error", "error": str(ex)}
            else:
                result = {"status": "no_llm", "provider": provider}
            response_bytes = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # API: Get today's plan from planner config
        if self.path.startswith("/api/planner"):
            planner = get_planner_config()
            if planner:
                now = datetime.datetime.now()
                today_plan = build_daily_plan_for_today(planner, now)
                response_bytes = json.dumps({
                    "status": "ok",
                    "day": now.strftime("%A, %B %d"),
                    "plan": today_plan,
                    "goals": planner.get("goals", [])
                }).encode("utf-8")
            else:
                response_bytes = json.dumps({"status": "no_planner"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # API: Sync Google Calendar
        if self.path.startswith("/api/calendar"):
            force = "force=1" in self.path or "live=1" in self.path
            url = get_configured_url()
            cal_data = fetch_and_parse_calendar(url, force_refresh=force)
            
            response_bytes = json.dumps(cal_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # API: Assistant status
        if self.path.startswith("/api/status"):
            now = datetime.datetime.now()
            ai_cfg = get_ai_config()
            status_data = {
                "server": "Daymark Execution Core v2.0",
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "online",
                "calendar_connected": bool(get_configured_url()),
                "ai_provider": ai_cfg.get("provider", "local")
            }
            response_bytes = json.dumps(status_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # API: Get AI Config
        if self.path.startswith("/api/ai-config"):
            cfg = get_ai_config()
            safe_cfg = {
                "provider": cfg.get("provider", "local"),
                "has_key": bool(cfg.get("gemini_api_key")),
                "gemini_model": cfg.get("gemini_model", "gemini-3.8-flash"),
                "ollama_url": cfg.get("ollama_url", "http://localhost:11434"),
                "ollama_model": cfg.get("ollama_model", "llama3.2")
            }
            response_bytes = json.dumps(safe_cfg).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # Fallback to standard static file serving
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        api_path = self.path.split("?", 1)[0]
        if api_path.startswith("/api/portfolio/") and not self.portfolio_request_is_local():
            self.send_json(403, {"status": "error", "message": "Portfolio updates are available only from this computer."})
            return
        if api_path == "/api/portfolio/integration-settings":
            try:
                payload = json.loads(body)
                endpoint = str(payload.get("supabase_ingest_url", "")).strip()
                portfolio_store.save_supabase_endpoint(endpoint)
                self.send_json(200, {"status": "ok"})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": f"Could not save URL: {type(exc).__name__}"})
            return
        if api_path == "/api/portfolio/checkin-settings":
            try:
                payload = json.loads(body)
                settings = portfolio_store.save_checkin_settings(payload)
                self.send_json(200, {"status": "ok", "settings": settings})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if api_path == "/api/portfolio/checkin":
            try:
                payload = json.loads(body)
                response = payload.get("response")
                if response == "remind_later":
                    until = portfolio_store.snooze_checkin()
                    self.send_json(200, {"status": "ok", "snooze_until": until})
                    return
                checkin = portfolio_store.record_checkin(
                    response,
                    source=payload.get("source", "daymark_popup"),
                    metadata={"source": payload.get("source", "daymark_popup")},
                )
                if response == "has_update":
                    portfolio_store.queue_ui_command("new_portfolio_update")
                    def open_daymark():
                        try:
                            flags = 0x08000000 if sys.platform == "win32" else 0
                            startupinfo = None
                            if sys.platform == "win32":
                                startupinfo = subprocess.STARTUPINFO()
                                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                                startupinfo.wShowWindow = 0
                            subprocess.Popen([sys.executable, APP_WINDOW_FILE], cwd=os.path.dirname(__file__),
                                             creationflags=flags, startupinfo=startupinfo)
                        except Exception as exc:
                            print(f"[portfolio] could not open Daymark update form: {exc}")
                    threading.Thread(target=open_daymark, daemon=True).start()
                self.send_json(200, {"status": "ok", "checkin": checkin})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if api_path == "/api/portfolio/updates":
            try:
                payload = json.loads(body)
                update = portfolio_store.create_update(
                    payload.get("original_input"),
                    payload.get("category", "other"),
                    payload.get("supporting_links", []),
                )
                start_portfolio_proposal(update["id"])
                self.send_json(201, {"status": "ok", "update": update})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_json(500, {"status": "error", "message": str(exc)})
            return

        if api_path.startswith("/api/portfolio/updates/"):
            parts = api_path.strip("/").split("/")
            if len(parts) != 5:
                self.send_json(404, {"status": "error", "message": "Unknown portfolio update action"})
                return
            update_id, action = parts[3], parts[4]
            update = portfolio_store.get_update(update_id)
            if not update:
                self.send_json(404, {"status": "error", "message": "Portfolio update not found"})
                return
            try:
                payload = json.loads(body or "{}")
                if action == "refine":
                    instruction = str(payload.get("instruction", "")).strip()
                    if not instruction or len(instruction) > 3000:
                        raise ValueError("Describe the refinement in 1–3,000 characters")
                    if update["status"] not in {"ready_for_review", "awaiting_clarification", "failed"}:
                        raise ValueError("This update is no longer open for refinement")
                    if not start_portfolio_proposal(update_id, instruction):
                        raise ValueError("This update is already being processed")
                    self.send_json(202, {"status": "ok", "message": "Refinement saved for processing"})
                    return
                if action == "approve":
                    if update["status"] != "ready_for_review" or not update.get("preview_url"):
                        raise ValueError("A successful preview is required before approval")
                    portfolio_store.update_workflow_state(update_id, "approved")
                    start_portfolio_publish(update_id)
                    self.send_json(202, {"status": "ok", "message": "Approval recorded; merge and production deployment are being verified"})
                    return
                if action == "discard":
                    if update["status"] in {"approved", "publishing", "published"}:
                        raise ValueError("This update is already being published or has been published")
                    if update.get("branch_name"):
                        import portfolio_github
                        portfolio_github.GitHubClient().discard_review(update)
                    else:
                        portfolio_store.update_workflow_state(
                            update_id, "discarded", event_type="update_discarded", metadata={"branch_deleted": False},
                        )
                    self.send_json(200, {"status": "ok", "message": "Update discarded"})
                    return
                self.send_json(404, {"status": "error", "message": "Unknown portfolio update action"})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_json(502, {"status": "error", "message": str(exc)})
            return

        if self.path == "/api/routines":
            try:
                payload = json.loads(body)
                routines = payload.get("routines", [])
                daily_alarm = payload.get("daily_alarm", DEFAULT_ROUTINES_CONFIG["daily_alarm"])
                weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
                if not isinstance(routines, list) or not isinstance(daily_alarm, dict):
                    raise ValueError("Expected a routine list and daily alarm settings")
                alarm_time = str(daily_alarm.get("time", "08:30"))
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", alarm_time):
                    raise ValueError("Daily alarm time must be HH:MM")
                cleaned = []
                for routine in routines:
                    if not isinstance(routine, dict) or not routine.get("id") or not routine.get("title"):
                        raise ValueError("Each routine needs an id and title")
                    days = routine.get("days", [])
                    routine_time = str(routine.get("time", ""))
                    if not isinstance(days, list) or not days or any(day not in weekdays for day in days):
                        raise ValueError("Each routine needs one or more valid weekdays")
                    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", routine_time):
                        raise ValueError("Routine time must be HH:MM")
                    item = dict(routine)
                    item["days"] = list(dict.fromkeys(days))
                    item["time"] = routine_time
                    item["duration"] = max(1, min(1440, int(routine.get("duration", 60))))
                    for date_field in ("start_date", "end_date"):
                        value = routine.get(date_field)
                        if value:
                            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                                raise ValueError(f"Routine {date_field} must be YYYY-MM-DD")
                            try:
                                datetime.date.fromisoformat(value)
                            except ValueError:
                                raise ValueError(f"Routine {date_field} must be a valid date")
                            item[date_field] = value
                        else:
                            item.pop(date_field, None)
                    if item.get("start_date") and item.get("end_date") and item["start_date"] > item["end_date"]:
                        raise ValueError("Routine start_date must be on or before end_date")
                    item["completed_dates"] = [d for d in routine.get("completed_dates", [])
                                                if isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
                    cleaned.append(item)
                config = {
                    "daily_alarm": {"enabled": bool(daily_alarm.get("enabled", True)), "time": alarm_time},
                    "routines": cleaned
                }
                with TASKS_LOCK:
                    save_routines_config(config)
                response_bytes = json.dumps({"status": "ok", **config}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/scheduled-action":
            try:
                payload = json.loads(body)
                task = payload.get("task")
                event = payload.get("event")
                if not isinstance(task, dict) or not task.get("id") or not task.get("title"):
                    raise ValueError("A task with id and title is required")
                # Some model responses include an ISO datetime where the app
                # expects a date. Keep the date portion so reminders match the
                # daemon's YYYY-MM-DD local-day comparison.
                raw_date = str(task.get("date") or datetime.date.today().isoformat()).strip()
                try:
                    task["date"] = datetime.date.fromisoformat(raw_date[:10]).isoformat()
                except ValueError:
                    task["date"] = datetime.date.today().isoformat()
                if isinstance(event, dict):
                    event["date"] = task["date"]
                with TASKS_LOCK:
                    tasks = load_shared_tasks()
                    tasks = [item for item in tasks if item.get("id") != task["id"]]
                    tasks.append(task)
                    save_shared_tasks(tasks)
                    if isinstance(event, dict) and event.get("id") and event.get("title"):
                        events = load_shared_events()
                        events = [item for item in events if item.get("id") != event["id"]]
                        events.append(event)
                        save_shared_events(events)
                audit_shared_change("scheduled-action", "upsert", [task["id"]], len(tasks))
                response_bytes = json.dumps({"status": "ok", "task": task, "event": event}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/tasks":
            try:
                payload = json.loads(body)
                operation = payload.get("operation", "upsert")
                with TASKS_LOCK:
                    tasks = load_shared_tasks()
                    if operation == "delete":
                        task_id = payload.get("id")
                        tasks = [task for task in tasks if task.get("id") != task_id]
                        result_task = None
                    else:
                        incoming = payload.get("tasks")
                        if incoming is None and isinstance(payload.get("task"), dict):
                            incoming = [payload["task"]]
                        if not isinstance(incoming, list):
                            raise ValueError("Expected a task or tasks list")
                        by_id = {task.get("id"): task for task in tasks if task.get("id")}
                        for task in incoming:
                            if not isinstance(task, dict) or not task.get("id") or not task.get("title"):
                                continue
                            by_id[task["id"]] = task
                        tasks = list(by_id.values())
                        result_task = incoming[-1] if incoming else None
                    save_shared_tasks(tasks)
                audit_shared_change("tasks", operation, [task.get("id") for task in incoming] if operation != "delete" else [payload.get("id")], len(tasks))
                response_bytes = json.dumps({"status": "ok", "task": result_task, "tasks": tasks}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/events":
            try:
                payload = json.loads(body)
                operation = payload.get("operation", "upsert")
                with TASKS_LOCK:
                    events = load_shared_events()
                    if operation == "delete":
                        event_id = payload.get("id")
                        events = [event for event in events if event.get("id") != event_id]
                        result_event = None
                    else:
                        incoming = payload.get("events")
                        if incoming is None and isinstance(payload.get("event"), dict):
                            incoming = [payload["event"]]
                        if not isinstance(incoming, list):
                            raise ValueError("Expected an event or events list")
                        by_id = {event.get("id"): event for event in events if event.get("id")}
                        for event in incoming:
                            if not isinstance(event, dict) or not event.get("id") or not event.get("title"):
                                continue
                            by_id[event["id"]] = event
                        events = list(by_id.values())
                        result_event = incoming[-1] if incoming else None
                    save_shared_events(events)
                audit_shared_change("events", operation, [event.get("id") for event in incoming] if operation != "delete" else [payload.get("id")], len(events))
                response_bytes = json.dumps({"status": "ok", "event": result_event, "events": events}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/calendar-url":
            try:
                payload = json.loads(body)
                url = payload.get("url", "").strip()
                if url:
                    save_configured_url(url)
                    cal_data = fetch_and_parse_calendar(url, force_refresh=True)
                    response_bytes = json.dumps({"status": "updated", "data": cal_data}).encode("utf-8")
                else:
                    response_bytes = json.dumps({"error": "Empty URL provided"}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"error": str(e)}).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/ai-config":
            try:
                payload = json.loads(body)
                curr = get_ai_config()
                if "provider" in payload: curr["provider"] = payload["provider"]
                if "gemini_api_key" in payload and payload["gemini_api_key"]: 
                    curr["gemini_api_key"] = payload["gemini_api_key"].strip()
                if "gemini_model" in payload: curr["gemini_model"] = payload["gemini_model"].strip()
                if "ollama_url" in payload: curr["ollama_url"] = payload["ollama_url"].strip()
                if "ollama_model" in payload: curr["ollama_model"] = payload["ollama_model"].strip()
                save_ai_config(curr)
                response_bytes = json.dumps({"status": "updated"}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({"error": str(e)}).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        if self.path == "/api/chat":
            try:
                payload = json.loads(body)
                msg = payload.get("message", "")
                history = payload.get("history", [])
                context = payload.get("context", {})

                # Lightweight ping test — just validates the key is present and provider is configured
                if msg.strip().lower() == "ping":
                    cfg = get_ai_config()
                    provider = cfg.get("provider", "local")
                    if provider == "gemini" and cfg.get("gemini_api_key"):
                        response_bytes = json.dumps({"status": "success", "result": {"response": "pong", "source": "gemini"}}).encode("utf-8")
                    elif provider == "ollama":
                        response_bytes = json.dumps({"status": "success", "result": {"response": "pong", "source": "ollama"}}).encode("utf-8")
                    else:
                        response_bytes = json.dumps({"status": "no_llm"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(response_bytes)))
                    self.end_headers()
                    self.wfile.write(response_bytes)
                    return
                
                llm_res = call_llm(msg, history, context)
                if llm_res and (llm_res.get("response") or llm_res.get("action")):
                    response_bytes = json.dumps({
                        "status": "success", 
                        "result": llm_res
                    }).encode("utf-8")
                elif llm_res and llm_res.get("error"):
                    response_bytes = json.dumps({
                        "status": "error",
                        "message": llm_res["error"],
                        "source": llm_res.get("source", "unknown")
                    }).encode("utf-8")
                else:
                    response_bytes = json.dumps({"status": "no_llm"}).encode("utf-8")
            except Exception as e:
                response_bytes = json.dumps({
                    "status": "error", 
                    "message": str(e)
                }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Keep the user's private calendar URL local. Do not overwrite it at startup,
    # and never ship a personal feed URL in source control.
    calendar_url = get_configured_url()
    if calendar_url:
        print("Pre-warming Google Calendar feed...")
        fetch_and_parse_calendar(calendar_url, force_refresh=True)
    else:
        print("Google Calendar is not configured; add a feed URL in Daymark settings.")

    # This is a single-instance local server. SO_REUSEADDR lets stale Daymark
    # copies bind the same port and Windows load-shares requests across them,
    # causing some clients to hit old code or empty handlers.
    socketserver.TCPServer.allow_reuse_address = False
    with socketserver.TCPServer(("", port), DaymarkHandler) as httpd:
        start_portfolio_workflow_recovery()
        print(f"Daymark Assistant Server running at http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
