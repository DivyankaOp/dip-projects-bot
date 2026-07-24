"""
PMS Sheet Q&A Bot
-----------------
Ek chat-bot jo tumhare Google Sheets (PMS 3.2 aur 1 june) ka LIVE data padh kar
Gemini AI se accurate answers deta hai.

SETUP (README.md mein detail hai):
1. pip install -r requirements.txt
2. GEMINI_API_KEY environment variable set karo
3. python app.py
4. Browser mein http://localhost:5000 kholo
"""

import os
import csv
import io
import json
import time
import urllib.parse
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# ---------------------------------------------------------------------------
# 1. SPREADSHEET CONFIG
#    Yahan apne saare Google Sheets aur unke tabs (sheet names) daalo.
#    Sheet ko "Anyone with the link" -> "Viewer" access diya hona chahiye,
#    tabhi yeh bina login ke data padh payega.
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

MAX_ROWS_PER_TAB = 400  # ek tab se max kitni rows Gemini ko context me bhejni hain


def fetch_tab_csv(spreadsheet_id: str, sheet_name: str) -> str:
    """Google Sheet ke ek tab ka data CSV text ke roop mein laata hai (public link access)."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def csv_to_trimmed_text(csv_text: str, max_rows: int = MAX_ROWS_PER_TAB) -> str:
    """CSV ko readable text table mein badalta hai aur bahut lambi sheets ko trim karta hai.

    IMPORTANT: Zyadatar log-style sheets (Attendance, Tasks, Site Tasks, Sessions,
    Daily Checklist Log, etc.) mein NAYI entries sabse NEECHE add hoti hain. Isliye
    agar trim karna pade to hum sheet ki AAKHIRI (latest) rows rakhte hain, shuru ki
    purani rows nahi -- warna "aaj ka data" jaise sawaalon ka jawab galat/purana aa
    jaata hai.
    """
    reader = list(csv.reader(io.StringIO(csv_text)))
    if not reader:
        return "(khaali sheet / data nahi mila)"
    header, rows = reader[0], reader[1:]
    rows = [r for r in rows if any(cell.strip() for cell in r)]  # empty rows hatao
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[-max_rows:]  # sabse LATEST rows rakho, purani nahi
    lines = [" | ".join(header)]
    lines += [" | ".join(r) for r in rows]
    text = "\n".join(lines)
    if truncated:
        text += (
            f"\n\n[NOTE: is tab mein aur bhi (purani) rows hain, sirf sabse "
            f"RECENT {max_rows} rows dikhayi gayi hain]"
        )
    return text


def today_context() -> str:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    return (
        f"Aaj ki date hai: {now.strftime('%Y-%m-%d')} "
        f"({now.strftime('%A')}), time zone: India (IST)."
    )


def call_gemini(prompt: str, max_retries: int = 3) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY set nahi hai. README dekho.")
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                GEMINI_URL,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=60,
            )
            # 503 / 429 = Gemini abhi busy/overloaded hai -> thodi der ruk ke retry karo
            if resp.status_code in (429, 500, 503):
                last_error = f"{resp.status_code} Server busy"
                time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s backoff
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


def pick_relevant_tabs(question: str) -> list:
    """Step 1: Gemini se poochho ki is sawaal ka jawab kis tab/tabs mein milega."""
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
    raw = call_gemini(prompt)
    raw = raw.strip().strip("`")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    try:
        picks = json.loads(raw)
        valid = []
        for p in picks:
            s, t = p.get("spreadsheet"), p.get("tab")
            if s in SPREADSHEETS and t in SPREADSHEETS[s]["tabs"]:
                valid.append((s, t))
        return valid[:3]
    except Exception:
        return []


def answer_question(question: str) -> dict:
    picks = pick_relevant_tabs(question)
    if not picks:
        return {
            "answer": "Mujhe pakka nahi laga ki yeh sawaal kis sheet/tab se sambandhit hai. "
                      "Thoda aur specific pooch sakte ho? (jaise project ka naam ya tab ka naam)",
            "sources": [],
        }

    context_blocks = []
    sources = []
    for spreadsheet_label, tab in picks:
        sid = SPREADSHEETS[spreadsheet_label]["id"]
        try:
            csv_text = fetch_tab_csv(sid, tab)
            table_text = csv_to_trimmed_text(csv_text)
        except Exception as e:
            table_text = f"(is tab ka data fetch nahi ho paya: {e})"
        context_blocks.append(
            f"### Spreadsheet: {spreadsheet_label} | Tab: {tab}\n{table_text}"
        )
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
to jawab ek CLEAN TABLE (markdown table: | column | column |) format mein do,
jaise: Sr.no, Name, Check In, Check Out, Status/Remarks -- exactly us tarah
jaise ek proper attendance/report sheet dikhti hai. Agar kisi employee ka
Check Out time khaali hai, to "PENDING" likho. Agar Status column mein
"Leave" hai, to "LEAVE" likho.
"""
    answer = call_gemini(final_prompt)
    return {"answer": answer, "sources": sources}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True)
    question = (data or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Sawaal khaali nahi ho sakta"}), 400
    try:
        result = answer_question(question)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
