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
from datetime import datetime
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
}

# Naya task yahan (kaunsi spreadsheet + tab) add hoga
TASK_SHEET = {"spreadsheet": "PMS 3.2", "tab": "Tasks"}

# Phone number dhundhne ke liye kin-kin tabs mein "Employees" data hai
EMPLOYEE_TABS = [("PMS 3.2", "Employees"), ("1 june", "Employees")]

MAX_ROWS_PER_TAB = 400
TODAY_KEYWORDS = ["aaj", "today", "abhi", "current"]

# In-memory session store: multi-turn "add task" conversation ka state yahan
# rakha jaata hai (session_id -> state). NOTE: yeh sirf ek server-instance ke
# liye kaam karta hai; agar Vercel jaise serverless pe multiple instances
# chalte hain to yeh state kabhi-kabhi reset ho sakta hai -- production ke
# liye ise Redis/DB mein move karna better hoga.
TASK_SESSIONS = {}


# ---------------------------------------------------------------------------
# GOOGLE SHEETS: READ (public CSV export, "Anyone with link" access)
# ---------------------------------------------------------------------------
def fetch_tab_csv(spreadsheet_id: str, sheet_name: str) -> str:
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def get_tab_rows(spreadsheet_id: str, sheet_name: str):
    """CSV ko list-of-lists mein parse karta hai. rows[0] = header."""
    csv_text = fetch_tab_csv(spreadsheet_id, sheet_name)
    return list(csv.reader(io.StringIO(csv_text)))


def csv_to_trimmed_text(csv_text: str, max_rows: int = MAX_ROWS_PER_TAB, today_filter: bool = False) -> str:
    """CSV ko readable text table mein badalta hai aur bahut lambi sheets ko trim karta hai.

    today_filter=True hone par sirf AAJ ki date wali rows Python se hi filter
    karke bheji jaati hain (Gemini pe depend nahi karte) -- isse koi row
    miss/drop nahi hoti.
    """
    reader = list(csv.reader(io.StringIO(csv_text)))
    if not reader:
        return "(khaali sheet / data nahi mila)"
    header, rows = reader[0], reader[1:]
    rows = [r for r in rows if any(cell.strip() for cell in r)]

    if today_filter:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        date_variants = [
            now.strftime("%Y-%m-%d"),
            now.strftime("%d-%m-%Y"),
            now.strftime("%d/%m/%Y"),
            now.strftime("%m/%d/%Y"),
        ]
        matched = [r for r in rows if any(any(dv in cell for dv in date_variants) for cell in r)]
        if matched:
            lines = [" | ".join(header)] + [" | ".join(r) for r in matched]
            return "\n".join(lines) + f"\n\n[NOTE: yeh sirf AAJ ({date_variants[0]}) ki poori list hai, koi row skip nahi ki gayi]"

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


def pick_relevant_tabs(question: str) -> list:
    catalog_lines = []
    for sheet_label, cfg in SPREADSHEETS.items():
        for tab in cfg["tabs"]:
            catalog_lines.append(f"- Spreadsheet: \"{sheet_label}\" | Tab: \"{tab}\"")
    catalog = "\n".join(catalog_lines)
    prompt = f"""Tum ek routing assistant ho. Neeche ek Project Management System ke
saare available spreadsheet tabs ki list hai:

{catalog}

User ka sawaal: "{question}"

Bataao is sawaal ka jawab dhoondhne ke liye kaunse 1-3 tabs sabse zyada
relevant hain. SIRF ek JSON array return karo, is exact format mein, kuch
aur text mat likho:
[{{"spreadsheet": "<spreadsheet name>", "tab": "<tab name>"}}, ...]
"""
    try:
        picks = _parse_json_block(call_gemini(prompt, max_output_tokens=500))
        valid = []
        for p in picks:
            s, t = p.get("spreadsheet"), p.get("tab")
            if s in SPREADSHEETS and t in SPREADSHEETS[s]["tabs"]:
                valid.append((s, t))
        return valid[:3]
    except Exception:
        return []


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
def answer_question(question: str) -> dict:
    picks = pick_relevant_tabs(question)
    if not picks:
        return {
            "answer": "Mujhe pakka nahi laga ki yeh sawaal kis sheet/tab se sambandhit hai. "
                      "Thoda aur specific pooch sakte ho? (jaise project ka naam ya tab ka naam)",
            "sources": [],
        }

    wants_today = any(kw in question.lower() for kw in TODAY_KEYWORDS)
    context_blocks, sources = [], []
    for spreadsheet_label, tab in picks:
        sid = SPREADSHEETS[spreadsheet_label]["id"]
        try:
            csv_text = fetch_tab_csv(sid, tab)
            table_text = csv_to_trimmed_text(csv_text, today_filter=wants_today)
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

BAHUT ZAROORI: SHEET DATA mein jitni bhi rows di gayi hain, un SABKO table
mein daalo -- EK BHI row skip/summarize/drop mat karo.
"""
    answer = call_gemini(final_prompt, max_output_tokens=8192)
    return {"answer": answer, "sources": sources}


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
            return jsonify(answer_question(question))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
