"""
PMS Sheet Q&A Bot
-----------------
Ek chat-bot jo tumhare Google Sheets (PMS 3.2 aur 1 june) ka LIVE data padh kar
Gemini AI se accurate answers deta hai. Ab yeh naya task add karna (sheet mein
likhna) aur WhatsApp reminder bhejna bhi kar sakta hai.

SETUP (README.md mein detail hai):
1. pip install -r requirements.txt
2. GEMINI_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, WHATSAPP_TOKEN,
   WHATSAPP_PHONE_NUMBER_ID environment variables set karo
3. python app.py
4. Browser mein http://localhost:5000 kholo
"""

import os
import csv
import io
import json
import time
import re
import urllib.parse
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, render_template
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# WhatsApp (Meta Business API)
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

# Google Sheets likhne (write) ke liye Service Account JSON (poora JSON content, ek env var mein)
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# ---------------------------------------------------------------------------
# 1. SPREADSHEET CONFIG
# ---------------------------------------------------------------------------
SPREADSHEETS = {
    "PMS 3.2": {
        "id": "1tCsnu6ftqf3a-y-Qc4xE_Udbs2ng0u3kjxk3yX5caf4",
        "tabs": [
            "Drawings", "Daily Checklist", "Daily Checklist Log",
            "Recurring Task Instance", "TaskTypes", "Analytics",
            "Site Progress", "Recurring Task Drafts",
            "Recurring Task Submissions", "Logins", "Employees", "Sites",
            "Verification Requests", "Tasks", "Rescheduling Requests",
            "Leave Requests", "Tickets", "Recurring Tasks",
            "Recurring Task Instances", "Sessions", "Site Tasks",
        ],
    },
    "1 june": {
        "id": "1nuSNuVosoGXpXA6HmnFJ6YL43X7xDB22GiqFi1mnbpI",
        "tabs": [
            "DPRSHEET", "Material Requirement", "WeeklyPlans", "WeeklyTasks",
            "WeeklyWALog", "WPRInstances", "Employees", "EquipmentName",
            "RecurringTasks", "RecurringTaskInstance", "Attendance",
            "REPORT JULY 1 TO 10", "Leaves", "SiteName", "Assets",
            "Insurance", "Expenses", "EMP PROJECT LIST", "TaskEmployees",
        ],
    },
    # Yeh asli DPR/WPR data waali spreadsheet hai (jo user ne link se di --
    # "1 june" ki DPRSHEET/WPRInstances sirf khaali placeholder tabs the).
    "DPR WPR Sheet": {
        "id": "1uwaMMcQVhqnW3JAM99XpZr6D7Mcz11Rm-nqdB_Ko6d0",
        "tabs": [
            "ProgressReport", "SVRInstances", "MorningInstances", "DraftWPR",
            "DraftDPR", "Man_Scope", "Manpower", "Man_Type", "WorkCategory",
            "EquipmentName", "EquipmentNames", "EmpTasks", "SkillSet",
            "MaterialUnit", "MaterialMaster", "SiteImages", "Instructor",
            "DPRDailyData", "DPRInstances", "WPRInstances",
        ],
    },
}

# Naya task yahan (kaunsi spreadsheet + tab) add hoga
TASK_SHEET = {"spreadsheet": "PMS 3.2", "tab": "Tasks"}

# Phone number dhundhne ke liye kin-kin tabs mein "Employees" data hai
EMPLOYEE_TABS = [("PMS 3.2", "Employees"), ("1 june", "Employees")]

MAX_ROWS_PER_TAB = 400
TODAY_KEYWORDS = [
    "aaj", "today", "abhi", "current", "report", "list", "attendance",
    "check in", "checkin", "clock in", "clockin", "present", "status",
]

# In-memory session store: multi-turn "add task" conversation ka state yahan
# rakha jaata hai (session_id -> state). NOTE: yeh sirf ek server-instance ke
# liye kaam karta hai; agar Vercel jaise serverless pe multiple instances
# chalte hain to yeh state kabhi-kabhi reset ho sakta hai -- production ke
# liye ise Redis/DB mein move karna better hoga.
TASK_SESSIONS = {}


# ---------------------------------------------------------------------------
# GOOGLE SHEETS: READ (public CSV export, "Anyone with link" access)
# ---------------------------------------------------------------------------
def fetch_tab_csv(spreadsheet_id: str, sheet_name: str, max_retries: int = 3) -> str:
    """Sheet ka ek tab CSV format mein fetch karta hai. Bade tabs (jaise 2000+
    rows wali Attendance) kabhi-kabhi slow/timeout ho sakti hain, isliye retry
    + lamba timeout diya gaya hai."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=45)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as e:
            last_error = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"'{sheet_name}' tab fetch nahi ho payi, {max_retries} baar try kiya: {last_error}")


def get_tab_rows(spreadsheet_id: str, sheet_name: str):
    """CSV ko list-of-lists mein parse karta hai. rows[0] = header."""
    csv_text = fetch_tab_csv(spreadsheet_id, sheet_name)
    return list(csv.reader(io.StringIO(csv_text)))


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)


def extract_date_range(question: str):
    """Sawaal mein se koi bhi date/date-range dhoondh kar (start_date, end_date)
    return karta hai. Agar sirf ek date mili, dono same hongi. Kuch na mile to
    None. Isse "1 July se 24 July tak" jaise sawaal bhi sahi filter hote hain,
    na ki sirf 'aaj' wale."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    found = []

    for m in re.finditer(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\.?\s*(\d{{4}})?", question, re.IGNORECASE):
        day = int(m.group(1))
        month = _MONTH_MAP.get(m.group(2)[:3].lower())
        year = int(m.group(3)) if m.group(3) else now.year
        try:
            found.append(datetime(year, month, day).date())
        except (ValueError, TypeError):
            pass

    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", question):
        try:
            found.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date())
        except ValueError:
            pass

    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", question):
        try:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if year < 100:
                year += 2000
            found.append(datetime(year, month, day).date())
        except ValueError:
            pass

    if not found:
        return None

    # "till date" / "aaj tak" / "abhi tak" / "till today" jaise phrase ka
    # matlab hai range AAJ tak extend honi chahiye
    open_ended_phrases = ["till date", "till today", "aaj tak", "abhi tak", "abtak", "ab tak"]
    if any(p in question.lower() for p in open_ended_phrases):
        found.append(now.date())

    return min(found), max(found)


def date_range_variants(start_date, end_date) -> set:
    """Diye gaye date range ke har din ke liye alag-alag format variants
    (jo sheet mein ho sakte hain) generate karta hai."""
    variants = set()
    d = start_date
    while d <= end_date:
        variants.add(d.strftime("%Y-%m-%d"))
        variants.add(d.strftime("%d-%m-%Y"))
        variants.add(d.strftime("%d/%m/%Y"))
        variants.add(d.strftime("%m/%d/%Y"))
        d += timedelta(days=1)
    return variants


def csv_to_trimmed_text(csv_text: str, max_rows: int = MAX_ROWS_PER_TAB, date_variants: set = None) -> str:
    """CSV ko readable text table mein badalta hai aur bahut lambi sheets ko trim karta hai.

    date_variants diya gaya ho (ek specific date ya date-range ke variants),
    to sirf un dates wali rows Python se hi filter karke bheji jaati hain
    (Gemini pe depend nahi karte) -- isse koi row miss/drop nahi hoti, chahe
    request "aaj" ki ho ya "1 July se 24 July tak" jaisi range ki.
    """
    reader = list(csv.reader(io.StringIO(csv_text)))
    if not reader:
        return "(khaali sheet / data nahi mila)"
    header, rows = reader[0], reader[1:]
    rows = [r for r in rows if any(cell.strip() for cell in r)]

    if date_variants:
        matched = [r for r in rows if any(any(dv in cell for dv in date_variants) for cell in r)]
        if matched:
            lines = [" | ".join(header)] + [" | ".join(r) for r in matched]
            return "\n".join(lines) + f"\n\n[NOTE: yeh requested date-range ki POORI list hai ({len(matched)} rows), koi row skip nahi ki gayi]"

    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[-max_rows:]
    lines = [" | ".join(header)] + [" | ".join(r) for r in rows]
    text = "\n".join(lines)
    if truncated:
        text += f"\n\n[NOTE: is tab mein aur bhi (purani) rows hain, sirf sabse RECENT {max_rows} rows dikhayi gayi hain]"
    return text


def today_context() -> str:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    return f"Aaj ki date hai: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}), time zone: India (IST)."


# ---------------------------------------------------------------------------
# GOOGLE SHEETS: WRITE (Service Account, Sheets API v4)
# ---------------------------------------------------------------------------
def sheets_write_service():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON set nahi hai. Sheet mein likhne ke liye "
            "Service Account chahiye -- README ka 'Task add karna' section dekho."
        )
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def append_row(spreadsheet_id: str, tab: str, values: list):
    service = sheets_write_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


# ---------------------------------------------------------------------------
# WHATSAPP (Meta Business API)
# ---------------------------------------------------------------------------
def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "91" + digits  # India country code default
    return digits


def find_phone_number(name: str):
    """Employees tabs mein naam dhoondh kar (phone_number, matched_name) return karta hai."""
    name_lower = (name or "").strip().lower()
    if not name_lower:
        return None, None
    for label, tab in EMPLOYEE_TABS:
        sid = SPREADSHEETS[label]["id"]
        try:
            rows = get_tab_rows(sid, tab)
        except Exception:
            continue
        if not rows:
            continue
        header = [h.strip().lower() for h in rows[0]]
        name_idx = next((i for i, h in enumerate(header) if "name" in h), None)
        phone_idx = next(
            (i for i, h in enumerate(header) if any(k in h for k in ["phone", "mobile", "contact", "whatsapp"])),
            None,
        )
        if name_idx is None or phone_idx is None:
            continue
        for r in rows[1:]:
            if len(r) > name_idx and name_lower in r[name_idx].strip().lower():
                if len(r) > phone_idx and r[phone_idx].strip():
                    return r[phone_idx].strip(), r[name_idx].strip()
    return None, None


def send_whatsapp_text(phone_e164: str, message: str):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError(
            "WhatsApp API configure nahi hai (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID missing). "
            "README dekho."
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_e164,
        "type": "text",
        "text": {"body": message},
    }
    resp = requests.post(
        WHATSAPP_API_URL,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp send fail ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# GEMINI
# ---------------------------------------------------------------------------
def call_gemini(prompt: str, max_retries: int = 3, max_output_tokens: int = 4096) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY set nahi hai. README dekho.")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_output_tokens},
    }
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(GEMINI_URL, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)
            if resp.status_code in (429, 500, 503):
                last_error = f"{resp.status_code} Server busy"
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                return "Gemini se response parse nahi ho paya: " + json.dumps(data)[:500]
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        f"Gemini abhi overloaded/unavailable hai, {max_retries} baar try kiya. "
        f"Thodi der baad phir se pooch lo. (Detail: {last_error})"
    )


def _parse_json_block(raw: str):
    raw = raw.strip().strip("`")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    return json.loads(raw)


# Common synonyms/phrases jo log use karte hain, tab ke exact naam ke bajaye
TAB_SYNONYMS = {
    "Attendance": ["clock in", "clock-in", "clockin", "punch in", "punch-in",
                   "in-time", "in time", "intime", "check in", "check-in",
                   "checkin", "check out", "checkout", "daily attendance"],
    "Logins": ["login", "logged in", "log in"],
    "Sessions": ["session"],
    "Tasks": ["task list", "to-do", "todo"],
    "Site Tasks": ["site task"],
    "Leave Requests": ["leave request", "on leave"],
    "Leaves": ["leave"],
}

# Kuch tab-naam duplicate hote hain alag-alag spreadsheets mein (jaise
# "WPRInstances" purani khaali "1 june" sheet mein bhi hai aur asli
# "DPR WPR Sheet" mein bhi). Isliye DPR/WPR synonyms ko SIRF us specific
# (spreadsheet, tab) pair par lagao jahan asli data hai -- warna purani
# khaali sheet bhi har baar match ho kar 5-tab limit mein jagah le legi.
SCOPED_TAB_SYNONYMS = {
    ("DPR WPR Sheet", "DPRDailyData"): [
        "dpr", "wpr", "daily progress report", "weekly progress report",
        "daily progress", "weekly progress",
    ],
    ("DPR WPR Sheet", "DPRInstances"): ["dpr", "daily progress report", "daily progress"],
    ("DPR WPR Sheet", "WPRInstances"): ["wpr", "weekly progress report", "weekly progress"],
    ("DPR WPR Sheet", "ProgressReport"): ["dpr", "wpr", "progress report"],
}


def keyword_match_tabs(question: str) -> list:
    """Agar sawaal mein seedha kisi tab ka naam (ya uska common synonym) mention
    hai (jaise 'attendance sheet se lena hai' ya 'clock-in kab kiya'), to Gemini
    call kiye bina hi wahi tab match kar do. Fast, free, aur reliable -- Gemini
    rate-limit ka risk nahi."""
    q_nospace = question.lower().replace(" ", "")
    q = question.lower()
    matches = []
    for label, cfg in SPREADSHEETS.items():
        for tab in cfg["tabs"]:
            tab_key = tab.lower().replace(" ", "")
            hit = tab_key in q_nospace
            if not hit and (label, tab) in SCOPED_TAB_SYNONYMS:
                hit = any(syn in q for syn in SCOPED_TAB_SYNONYMS[(label, tab)])
            if not hit and tab in TAB_SYNONYMS:
                hit = any(syn in q for syn in TAB_SYNONYMS[tab])
            if hit:
                pair = (label, tab)
                if pair not in matches:
                    matches.append(pair)
    return matches


def pick_relevant_tabs(question: str) -> list:
    # Fast path: agar tab ka naam seedha sawaal mein hai, Gemini call skip karo
    kw_matches = keyword_match_tabs(question)
    if kw_matches:
        return kw_matches[:5]

    catalog_lines = []
    for sheet_label, cfg in SPREADSHEETS.items():
        for tab in cfg["tabs"]:
            catalog_lines.append(f"- Spreadsheet: \"{sheet_label}\" | Tab: \"{tab}\"")
    catalog = "\n".join(catalog_lines)
    prompt = f"""Tum ek routing assistant ho. Neeche ek Project Management System ke
saare available spreadsheet tabs ki list hai:

{catalog}

User ka sawaal: "{question}"

Bataao is sawaal ka jawab dhoondhne ke liye kaunse tabs (1 se 5 tak) sabse
zyada relevant hain. Agar sawaal complex hai (jaise multiple cheezein ek
saath poochi gayi hain -- tasks + attendance + logins, etc.), to zyada tabs
(4-5) choose karo taaki poora jawab mil sake. SIRF ek JSON array return
karo, is exact format mein, kuch aur text mat likho:
[{{"spreadsheet": "<spreadsheet name>", "tab": "<tab name>"}}, ...]
"""
    # NOTE: yahan call_gemini ki exception ko jaan-boojh kar catch NAHI kiya
    # jaata -- taaki caller (answer_question) ko pata chale ki yeh genuinely
    # "Gemini busy/fail hua" hai, na ki "sawaal samajh nahi aaya".
    raw = call_gemini(prompt, max_output_tokens=500)
    try:
        picks = _parse_json_block(raw)
    except Exception:
        return []  # Gemini ne valid JSON nahi diya -- genuinely ambiguous sawaal

    # Gemini kabhi-kabhi spreadsheet/tab naam thoda alag case/spacing mein
    # likh deta hai (jaise "DPR Sheet" vs "DPRSHEET") -- isliye exact-match
    # ke bajaye normalized (lowercase, no-space) lookup use karo, warna
    # valid pick 0 aa jaata hai aur bot "samajh nahi aaya" bol deta hai.
    def _norm(s):
        return re.sub(r"\s+", "", (s or "").strip().lower())

    norm_lookup = {}
    for s_label, cfg in SPREADSHEETS.items():
        for t in cfg["tabs"]:
            norm_lookup[(_norm(s_label), _norm(t))] = (s_label, t)

    valid = []
    for p in picks:
        s, t = p.get("spreadsheet"), p.get("tab")
        pair = norm_lookup.get((_norm(s), _norm(t)))
        if pair and pair not in valid:
            valid.append(pair)
    return valid[:5]


def detect_action(question: str) -> dict:
    """Classify karta hai: normal sawaal hai, ya task add karna hai, ya WhatsApp bhejna hai."""
    prompt = f"""Tum ek intent classifier ho ek Project Management chatbot ke liye.
User ka message: "{question}"

Iska intent classify karo aur SIRF ek JSON object return karo, kuch aur text mat likho:
{{"action": "<add_task|send_whatsapp|pending_reminder|qa>", "to_name": "<naam ya null>", "message": "<message text ya null>"}}

Rules:
- "add_task": jab user naya task/entry create/add/dalna chahta ho.
- "send_whatsapp": jab user kisi EK specific person ko WhatsApp/message bhejne
  ko bole. "to_name" mein us person ka naam, "message" mein jo bhejna hai.
- "pending_reminder": jab user chahta ho ki jin logon ke tasks abhi PENDING
  hain unko sabko automatically WhatsApp reminder chala jaye.
- "qa": baaki sab normal data-related sawaal (default).
"""
    try:
        result = _parse_json_block(call_gemini(prompt, max_output_tokens=300))
        if isinstance(result, dict) and "action" in result:
            return result
    except Exception:
        pass
    return {"action": "qa"}


# ---------------------------------------------------------------------------
# FEATURE 1: NORMAL Q&A (existing sheet-lookup flow)
# ---------------------------------------------------------------------------
def build_raw_markdown(picks: list, date_variants: set = None) -> str:
    """Gemini ke bina, seedha sheet se data nikaal kar ek clean markdown table
    bana deta hai. Yeh Gemini fail/busy hone par fallback ke roop mein use hota hai."""
    blocks = []
    for label, tab in picks:
        sid = SPREADSHEETS[label]["id"]
        try:
            reader = get_tab_rows(sid, tab)
            if not reader:
                blocks.append(f"### {label} → {tab}\n(khaali sheet)")
                continue
            header, rows = reader[0], reader[1:]
            rows = [r for r in rows if any(c.strip() for c in r)]

            if date_variants:
                matched = [r for r in rows if any(any(dv in c for dv in date_variants) for c in r)]
                rows = matched if matched else rows[-100:]
            else:
                rows = rows[-100:]

            md = "| " + " | ".join(header) + " |\n"
            md += "|" + "|".join(["---"] * len(header)) + "|\n"
            for r in rows:
                if len(r) < len(header):
                    r = r + [""] * (len(header) - len(r))
                else:
                    r = r[:len(header)]
                md += "| " + " | ".join(c.replace("|", "/") for c in r) + " |\n"
            blocks.append(f"### {label} → {tab}\n\n{md}")
        except Exception as e:
            blocks.append(f"### {label} → {tab}\n(fetch error: {e})")
    return "\n\n".join(blocks)


from datetime import time as _time


def extract_time_filter(question: str):
    """Sawaal mein 'after 9:30' / '9:30 ke baad' / 'before 10 AM' jaisa
    time-condition dhoondh kar (direction, time) return karta hai, warna None."""
    # English order: "after 9:30" / "before 10 AM"
    m = re.search(r"(after|before)\s*(\d{1,2})[:.]?(\d{2})?\s*(am|pm)?", question, re.IGNORECASE)
    if m:
        direction = m.group(1).lower()
        hour, minute, ampm = int(m.group(2)), int(m.group(3) or 0), (m.group(4) or "").lower()
    else:
        # Hindi order: "9:30 ke baad" / "9:30 se pehle"
        m = re.search(r"(\d{1,2})[:.]?(\d{2})?\s*(am|pm)?\s*(ke\s*baad|se\s*pehle)", question, re.IGNORECASE)
        if not m:
            return None
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        direction = "after" if "baad" in m.group(4).lower() else "before"

    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return ("after" if direction in ("after", "ke baad") else "before"), _time(hour, minute)


def parse_time_cell(cell: str):
    cell = (cell or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(cell, fmt).time()
        except ValueError:
            continue
    return None


def build_time_filtered_table(picks: list, date_variants, time_filter) -> str:
    """Attendance-jaisi tabs mein 'checkin time > /< X' condition ko seedha
    Python se filter karta hai (Gemini se nahi) -- isse chahe kitni bhi rows
    (25 din ka data ho ya 2500) hon, output kabhi bhi token-limit se cut nahi
    hota, kyunki hum khud table bana rahe hain, Gemini se generate nahi
    karwa rahe."""
    direction, cutoff = time_filter
    all_results = []
    used_any_tab = False

    for label, tab in picks:
        sid = SPREADSHEETS[label]["id"]
        try:
            rows = get_tab_rows(sid, tab)
        except Exception:
            continue
        if not rows:
            continue
        header = rows[0]
        header_lower = [h.strip().lower() for h in header]
        checkin_idx = next(
            (i for i, h in enumerate(header_lower) if "check in" in h or "clock in" in h or ("in" in h and "time" in h)),
            None,
        )
        if checkin_idx is None:
            continue  # yeh tab attendance-type nahi hai, skip karo
        used_any_tab = True
        date_idx = next((i for i, h in enumerate(header_lower) if h == "date"), None)
        name_idx = next((i for i, h in enumerate(header_lower) if "name" in h), None)
        checkout_idx = next((i for i, h in enumerate(header_lower) if "check out" in h or "clock out" in h), None)
        status_idx = next((i for i, h in enumerate(header_lower) if "status" in h), None)

        for r in rows[1:]:
            if not any(c.strip() for c in r):
                continue
            if date_variants and not any(any(dv in c for dv in date_variants) for c in r):
                continue
            if len(r) <= checkin_idx:
                continue
            t = parse_time_cell(r[checkin_idx])
            if not t:
                continue
            match = (t > cutoff) if direction == "after" else (t < cutoff)
            if not match:
                continue
            all_results.append({
                "date": r[date_idx].strip() if date_idx is not None and len(r) > date_idx else "",
                "name": r[name_idx].strip() if name_idx is not None and len(r) > name_idx else "",
                "checkin": r[checkin_idx].strip(),
                "checkout": (r[checkout_idx].strip() if checkout_idx is not None and len(r) > checkout_idx and r[checkout_idx].strip() else "PENDING"),
                "status": r[status_idx].strip() if status_idx is not None and len(r) > status_idx else "",
            })

    if not used_any_tab:
        return None  # koi attendance-type tab nahi mila, normal Gemini flow use karo

    if not all_results:
        cutoff_str = cutoff.strftime("%H:%M")
        return f"Diye gaye date-range mein koi bhi employee {cutoff_str} ke {direction} check-in karta hua nahi mila."

    all_results.sort(key=lambda x: (x["date"], x["checkin"]))
    lines = ["| Sr. No. | Date | Employee Name | Check In Time | Check Out Time | Status |",
             "|---|---|---|---|---|---|"]
    for i, r in enumerate(all_results, 1):
        lines.append(f"| {i} | {r['date']} | {r['name']} | {r['checkin']} | {r['checkout']} | {r['status']} |")

    direction_hindi = "ke baad" if direction == "after" else "se pehle"
    cutoff_str = cutoff.strftime("%H:%M")
    header_text = f"**{cutoff_str} {direction_hindi} check-in karne wale sabhi employees ({len(all_results)} entries):**\n\n"
    return header_text + "\n".join(lines)


# Agar current question itne kam words ka hai, tabhi use ek "follow-up"
# maano (jaise "sirf after 9:30", "usi din ka batao"). Lambe/standalone
# sawaal (jaise DPR/WPR wala poora paragraph) follow-up NAHI hote -- unke
# liye purana recent_context kabhi use nahi karna chahiye, warna purani
# attendance/date/time keywords wapis leak ho kar galat jawab de dete hain.
FOLLOWUP_MAX_WORDS = 8


def _looks_like_followup(question: str) -> bool:
    return len(question.split()) <= FOLLOWUP_MAX_WORDS


def answer_question(question: str, recent_context: str = "") -> dict:
    # Sirf chhote follow-up jaise sawaalon ke liye hi purana context jodo.
    # Lamba/standalone naya sawaal apne aap mein hi (bina purane context ke)
    # route hona chahiye -- fail ho to clarification do, purana context
    # zabardasti mat jodo.
    is_followup = bool(recent_context) and _looks_like_followup(question)
    routing_text = f"{recent_context}\n{question}" if is_followup else question

    try:
        picks = pick_relevant_tabs(question)
        if not picks and is_followup:
            picks = pick_relevant_tabs(routing_text)
    except Exception as e:
        return {
            "answer": f"⚠️ Gemini abhi busy/overloaded hai isliye jawab nahi de paya. "
                      f"Thodi der (30 sec-1 min) ruk kar phir se try karo.\n\n(Detail: {e})",
            "sources": [],
        }

    if not picks:
        return {
            "answer": (
                "Yeh sawaal thoda complex/general hai, mujhe pakka nahi laga ki kis "
                "sheet/tab se jawab dhoondhu. Thoda tod kar poocho -- jaise:\n\n"
                "- Project/employee ka **naam** batao\n"
                "- Ek waqt mein **ek hi cheez** poocho (jaise pehle tasks, phir attendance)\n"
                "- Kis **tab** se data chahiye woh mention karo (Tasks, Attendance, Drawings, etc.)"
            ),
            "sources": [],
        }

    date_range = extract_date_range(question)
    if not date_range and is_followup:
        date_range = extract_date_range(routing_text)
    date_variants = date_range_variants(*date_range) if date_range else None

    # Agar sawaal mein time-condition hai (jaise "after 9:30"), to seedha
    # Python se filter karke poora, complete jawab do -- Gemini ki zaroorat
    # nahi, isliye bade date-range (jaise 25 din) mein bhi output kabhi cut
    # nahi hoga.
    time_filter = extract_time_filter(question)
    if not time_filter and is_followup:
        time_filter = extract_time_filter(routing_text)
    if time_filter:
        direct_answer = build_time_filtered_table(picks, date_variants, time_filter)
        if direct_answer is not None:
            sources = [f"{label} → {tab}" for label, tab in picks]
            return {"answer": direct_answer, "sources": sources}

    context_blocks, sources = [], []
    for spreadsheet_label, tab in picks:
        sid = SPREADSHEETS[spreadsheet_label]["id"]
        try:
            csv_text = fetch_tab_csv(sid, tab)
            table_text = csv_to_trimmed_text(csv_text, date_variants=date_variants)
        except Exception as e:
            table_text = f"(is tab ka data fetch nahi ho paya: {e})"
        context_blocks.append(f"### Spreadsheet: {spreadsheet_label} | Tab: {tab}\n{table_text}")
        sources.append(f"{spreadsheet_label} → {tab}")

    context = "\n\n".join(context_blocks)
    final_prompt = f"""Tum ek Project Management System ke liye data assistant ho.
{today_context()}

Neeche kuch Google Sheet tabs ka actual data diya gaya hai. SIRF isi data ke
aadhar par user ke sawaal ka accurate jawab do. Agar data mein jawab nahi hai
to saaf keh do ki "yeh jaankari sheet mein nahi mili", kabhi bhi khud se
guess/hallucinate mat karo. "Aaj" ka matlab hai upar di gayi aaj ki date.

=== SHEET DATA ===
{context}
=== END SHEET DATA ===

User ka sawaal: {question}

Jawab clear aur seedha do (Hinglish ya jis language mein sawaal poocha gaya
usi mein), zaroorat ho to numbers/dates/names sheet se exact copy karo.

Agar user ne "list", "report", "sab logo ka", ya multiple entries maangi hain,
to jawab ek CLEAN TABLE (markdown table: | column | column |) format mein do.
Table ko MOBILE-FRIENDLY rakho: sirf zaroori columns dikhao (jaise Date, Name,
Check In Time, Check Out Time, Status) -- lambi cheezein jaise Photo URL,
GPS Address, IP Address ko table mein mat daalo (jab tak user ne specifically
na maanga ho), warna table screen se bahar chala jaata hai.

BAHUT ZAROORI: SHEET DATA mein jitni bhi rows di gayi hain, un SABKO table
mein daalo -- EK BHI row skip/summarize/drop mat karo.
"""
    try:
        answer = call_gemini(final_prompt, max_output_tokens=8192)
        return {"answer": answer, "sources": sources}
    except Exception as e:
        # Gemini abhi busy/fail hua -- poori tarah rukne ke bajaye seedha sheet
        # ka RAW data (bina AI formatting/summary ke) dikha do, taaki data mil
        # to jaaye, bas Gemini ki "smart" summary miss ho.
        raw = build_raw_markdown(picks, date_variants)
        return {
            "answer": (
                f"⚠️ Gemini abhi busy/overloaded hai, isliye AI-summary nahi ban paya. "
                f"Neeche **raw sheet data** direct dikha raha hu (bina AI ke):\n\n{raw}"
            ),
            "sources": sources,
        }


# ---------------------------------------------------------------------------
# FEATURE 2: ADD TASK (multi-turn conversational form)
# ---------------------------------------------------------------------------
def start_task_flow(session_id: str) -> str:
    sid = SPREADSHEETS[TASK_SHEET["spreadsheet"]]["id"]
    rows = get_tab_rows(sid, TASK_SHEET["tab"])
    header = rows[0] if rows else []
    if not header:
        return "Tasks tab ka header nahi mil paya, sheet check karo."
    skip_keywords = ["timestamp"]
    fields = [h for h in header if h.strip() and not any(k in h.lower() for k in skip_keywords)]
    if not fields:
        return "Tasks tab mein koi fillable column nahi mila."
    TASK_SESSIONS[session_id] = {
        "header": header, "fields": fields, "answers": {}, "step": 0, "confirming": False,
    }
    return f"Theek hai, naya task add karte hain! Ek-ek karke poochta hu.\n\n**{fields[0]}** kya hai?"


def continue_task_flow(session_id: str, answer: str) -> str:
    state = TASK_SESSIONS.get(session_id)
    if not state:
        return None

    if state["confirming"]:
        if answer.strip().lower() in ("haan", "yes", "y", "confirm", "ok", "haa", "theek hai"):
            row = [state["answers"].get(h, "") for h in state["header"]]
            for i, h in enumerate(state["header"]):
                if "timestamp" in h.lower():
                    row[i] = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%m/%d/%Y %H:%M:%S")
            sid = SPREADSHEETS[TASK_SHEET["spreadsheet"]]["id"]
            del TASK_SESSIONS[session_id]
            try:
                append_row(sid, TASK_SHEET["tab"], row)
                return "✅ Task successfully sheet mein add ho gaya!"
            except Exception as e:
                return f"❌ Task add nahi ho paya: {e}"
        else:
            del TASK_SESSIONS[session_id]
            return "Theek hai, task add karna cancel kar diya."

    field = state["fields"][state["step"]]
    state["answers"][field] = answer.strip()
    state["step"] += 1

    if state["step"] >= len(state["fields"]):
        summary = "\n".join(f"- **{k}**: {v}" for k, v in state["answers"].items())
        state["confirming"] = True
        return f"Yeh details confirm karo:\n\n{summary}\n\nSab sahi hai? (**haan** likho add karne ke liye, ya **cancel**)"

    next_field = state["fields"][state["step"]]
    return f"**{next_field}** kya hai?"


# ---------------------------------------------------------------------------
# FEATURE 3: WHATSAPP MESSAGING
# ---------------------------------------------------------------------------
def handle_send_whatsapp(to_name: str, message: str) -> str:
    if not to_name:
        return "Kisko WhatsApp bhejna hai, naam batao (jaise: 'Ankit Shah ko WhatsApp bhejo ki...')."
    phone, matched_name = find_phone_number(to_name)
    if not phone:
        return f"'{to_name}' ka phone number Employees sheet mein nahi mila."
    try:
        send_whatsapp_text(normalize_phone(phone), message or f"Hi {matched_name}, yeh ek reminder hai.")
        return f"✅ WhatsApp message **{matched_name}** ({phone}) ko bhej diya gaya."
    except Exception as e:
        return f"❌ WhatsApp bhejne mein error aayi: {e}"


def handle_pending_reminder() -> str:
    sid = SPREADSHEETS[TASK_SHEET["spreadsheet"]]["id"]
    rows = get_tab_rows(sid, TASK_SHEET["tab"])
    if not rows:
        return "Tasks tab mein data nahi mila."
    header_lower = [h.strip().lower() for h in rows[0]]
    status_idx = next((i for i, h in enumerate(header_lower) if "status" in h), None)
    name_idx = next((i for i, h in enumerate(header_lower) if "user name" in h or h == "name" or "assign" in h), None)
    desc_idx = next((i for i, h in enumerate(header_lower) if "description" in h or "task" in h), None)
    if status_idx is None or name_idx is None:
        return "Tasks tab mein 'Status' ya assignee/name column nahi mil paya."

    sent, failed = [], []
    for r in rows[1:]:
        if len(r) > status_idx and r[status_idx].strip().lower() == "pending":
            name = r[name_idx].strip() if len(r) > name_idx else ""
            desc = r[desc_idx].strip() if desc_idx is not None and len(r) > desc_idx else "aapka task"
            if not name:
                continue
            phone, matched_name = find_phone_number(name)
            if not phone:
                failed.append(f"{name} (phone nahi mila)")
                continue
            msg = f"Hi {matched_name}, yeh reminder hai ki aapka task '{desc}' abhi PENDING hai. Kripya jald complete karein."
            try:
                send_whatsapp_text(normalize_phone(phone), msg)
                sent.append(matched_name)
            except Exception as e:
                failed.append(f"{name} ({e})")

    parts = []
    if sent:
        parts.append("✅ Reminder bhej diya: " + ", ".join(sent))
    if failed:
        parts.append("⚠️ Yeh nahi bhej paya: " + ", ".join(failed))
    return "\n\n".join(parts) if parts else "Koi PENDING task nahi mila Tasks tab mein."


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True)
    question = (data or {}).get("question", "").strip()
    session_id = (data or {}).get("session_id") or "default"
    # Frontend pichle 3-4 user messages bhejta hai (context ke liye) -- taaki
    # "after 9:30" jaisa follow-up sawaal bhi pichli date-range yaad rakh sake
    recent_context = (data or {}).get("recent_context", "").strip()
    if not question:
        return jsonify({"error": "Sawaal khaali nahi ho sakta"}), 400
    try:
        # Agar "add task" wali multi-turn conversation chal rahi hai, use continue karo
        if session_id in TASK_SESSIONS:
            answer = continue_task_flow(session_id, question)
            return jsonify({"answer": answer, "sources": []})

        action = detect_action(question)
        act = action.get("action", "qa")

        if act == "add_task":
            return jsonify({"answer": start_task_flow(session_id), "sources": []})
        elif act == "send_whatsapp":
            return jsonify({"answer": handle_send_whatsapp(action.get("to_name"), action.get("message")), "sources": []})
        elif act == "pending_reminder":
            return jsonify({"answer": handle_pending_reminder(), "sources": []})
        else:
            return jsonify(answer_question(question, recent_context))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
