import os
import base64
import json
import time
import hashlib
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv


# ------------------- Config -------------------
POLL_SECONDS = 60

# Ensure we always load the correct .env from repo root
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

STATE_PATH = BASE_DIR / ".redcap_jira_hash_state.json"

REDCAP_API_URL = os.getenv("REDCAP_API_URL")
REDCAP_API_TOKEN = os.getenv("REDCAP_API_TOKEN")

JIRA_BASE_URL = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "TEST")

DEPARTMENT_FIELD_ID = os.getenv("DEPARTMENT_FIELD_ID")
REQUEST_TYPE_FIELD_ID = os.getenv("REQUEST_TYPE_FIELD_ID")
REQUESTER_NAME_FIELD_ID = os.getenv("REQUESTER_NAME_FIELD_ID")
REQUESTER_EMAIL_FIELD_ID = os.getenv("REQUESTER_EMAIL_FIELD_ID")


# ------------------- State (hashes) -------------------
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"records": {}}

def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

STATE = load_state()


# ------------------- Jira -------------------
def get_jira_auth_header():
    token = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    b64 = base64.b64encode(token.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {b64}"}

def jira_project_access_ok() -> bool:
    """Avoid duplicate spam if API user can't browse the project."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}"
    headers = {"Accept": "application/json", **get_jira_auth_header()}
    r = requests.get(url, headers=headers)
    return r.status_code == 200

def jira_issue_exists(issue_key: str) -> bool:
    """True only if Jira issue exists AND API user can access it."""
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    headers = {"Accept": "application/json", **get_jira_auth_header()}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    # anything else is a real error
    print("Jira issue_exists check failed:", r.status_code, r.text)
    r.raise_for_status()
    return False

def build_adf_description(text: str):
    return {
        "type": "doc",
        "version": 1,
        "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": text}]
        }]
    }

def create_jira_issue(issue_payload: dict) -> dict:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    headers = {"Content-Type": "application/json", **get_jira_auth_header()}
    resp = requests.post(url, headers=headers, json=issue_payload)
    if not resp.ok:
        print("Jira CREATE error:", resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()

def update_jira_issue(issue_key: str, issue_payload: dict):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    headers = {"Content-Type": "application/json", **get_jira_auth_header()}

    fields = dict(issue_payload.get("fields", {}))
    fields.pop("project", None)
    fields.pop("issuetype", None)

    resp = requests.put(url, headers=headers, json={"fields": fields})
    if not resp.ok:
        print("Jira UPDATE error:", resp.status_code, resp.text)
    resp.raise_for_status()


# ------------------- REDCap -------------------
def fetch_redcap_records():
    payload = {
        "token": REDCAP_API_TOKEN,
        "content": "record",
        "format": "json",
        "type": "flat",
        "rawOrLabel": "label",
        "exportCheckboxLabel": "true"
    }
    resp = requests.post(REDCAP_API_URL, data=payload)
    resp.raise_for_status()
    return resp.json()

def write_jira_key_back_to_redcap(record_id: str, jira_key: str):
    payload = {
        "token": REDCAP_API_TOKEN,
        "content": "record",
        "format": "json",
        "type": "flat",
        "data": json.dumps([{
            "record_id": record_id,
            "jira_issue_key": jira_key
        }])
    }
    resp = requests.post(REDCAP_API_URL, data=payload)
    resp.raise_for_status()
    return resp.json()


# ------------------- Hashing -------------------
def record_hash(redcap_data: dict) -> str:
    """
    Hash only the fields that should trigger Jira changes.
    Excludes jira_issue_key so writing back the key doesn't trigger a loop.
    """
    relevant = {
        "record_id": redcap_data.get("record_id"),
        "fname": redcap_data.get("fname"),
        "lname": redcap_data.get("lname"),
        "email": redcap_data.get("email"),
        "request_title": redcap_data.get("request_title"),
        "request_describ": redcap_data.get("request_describ"),
        "team": redcap_data.get("team"),
        "team_other": redcap_data.get("team_other"),
        "requesttype": redcap_data.get("requesttype"),
        "requesttype_other": redcap_data.get("requesttype_other"),
        "priority": redcap_data.get("priority"),
        "due": redcap_data.get("due"),
        "duedate": redcap_data.get("duedate"),
        "source": redcap_data.get("source"),
        "source_other": redcap_data.get("source_other"),
        "data_access": redcap_data.get("data_access"),
        "data_refresh": redcap_data.get("data_refresh"),
        "automation": redcap_data.get("automation"),
        "update_freq": redcap_data.get("update_freq"),
        "pref_tool": redcap_data.get("pref_tool"),
        "tool_other": redcap_data.get("tool_other"),
        # add/remove fields as you like
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------- Mapping REDCap -> Jira -------------------
def build_jira_payload_from_redcap(redcap_data: dict) -> dict:
    fname = (redcap_data.get("fname") or "").strip()
    lname = (redcap_data.get("lname") or "").strip()

    participant_name = f"{fname} {lname}".strip() or "Unknown participant"
    requester_name = participant_name or "Unknown requester"

    title = (redcap_data.get("request_title") or "No title provided").strip()
    department_raw = (redcap_data.get("team") or "").strip()
    request_type_raw = (redcap_data.get("requesttype") or "").strip()
    request_type_other = (redcap_data.get("requesttype_other") or "").strip()
    requester_email = (redcap_data.get("email") or "").strip()

    priority_raw = (redcap_data.get("priority") or "").strip()
    priority_map = {"1": "High", "2": "Medium", "3": "Low", "High": "High", "Medium": "Medium", "Low": "Low", "": ""}
    priority = priority_map.get(priority_raw, priority_raw)

    due_date_raw = (redcap_data.get("duedate") or "").strip()
    due_date = None
    if due_date_raw:
        try:
            due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            print(f"Invalid due date format from REDCap: {due_date_raw}")

    description_text = (
        f"Participant Name: {participant_name}\n"
        f"Request: {title}\n"
        f"Priority (from survey): {priority}\n"
    )
    if request_type_raw.lower() == "other" and request_type_other:
        description_text += f"\nRequest Type (Other details): {request_type_other}\n"

    description_text += f"\nFull REDCap payload:\n{json.dumps(redcap_data, indent=2)}"

    fields = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": f"{participant_name}: {title}",
        "description": build_adf_description(description_text),
        "issuetype": {"name": "Task"},
    }

    if REQUESTER_NAME_FIELD_ID:
        fields[REQUESTER_NAME_FIELD_ID] = requester_name

    if priority:
        fields["priority"] = {"name": priority}

    if due_date:
        fields["duedate"] = due_date

    if department_raw and DEPARTMENT_FIELD_ID:
        fields[DEPARTMENT_FIELD_ID] = {"value": department_raw}

    if request_type_raw and REQUEST_TYPE_FIELD_ID:
        fields[REQUEST_TYPE_FIELD_ID] = {"value": request_type_raw}

    if requester_email and REQUESTER_EMAIL_FIELD_ID:
        fields[REQUESTER_EMAIL_FIELD_ID] = requester_email

    return {"fields": fields}


# ------------------- Upsert -------------------
def upsert_record(record: dict, project_access_ok: bool):
    record_id = record.get("record_id") or record.get("id") or str(record)
    jira_key = (record.get("jira_issue_key") or "").strip()

    if not project_access_ok:
        print(f"SKIP {record_id}: no access to Jira project {JIRA_PROJECT_KEY} (won't create duplicates).")
        return None

    payload = build_jira_payload_from_redcap(record)

    # Update if possible
    if jira_key and jira_issue_exists(jira_key):
        update_jira_issue(jira_key, payload)
        print(f"UPDATED {jira_key} from REDCap record {record_id}")
        return jira_key

    # Otherwise create new
    if jira_key:
        print(f"INFO: jira_issue_key '{jira_key}' not found/accessible -> creating new for {record_id}")

    resp = create_jira_issue(payload)
    new_key = resp.get("key")
    print(f"CREATED {new_key} for REDCap record {record_id}")

    if new_key:
        write_jira_key_back_to_redcap(record_id, new_key)
        print(f"WROTE BACK jira_issue_key={new_key} to REDCap record {record_id}")

    return new_key


# ------------------- Main loop -------------------
def main():
    print("JIRA_PROJECT_KEY:", JIRA_PROJECT_KEY)
    print(f"Hash-sync polling every {POLL_SECONDS} seconds...")

    while True:
        try:
            records = fetch_redcap_records()
            print(f"Fetched {len(records)} records from REDCap.")

            project_access_ok = jira_project_access_ok()
            any_change = False

            for record in records:
                record_id = record.get("record_id") or record.get("id") or str(record)
                h = record_hash(record)

                prev = STATE["records"].get(str(record_id), {})
                prev_hash = prev.get("hash")

                if prev_hash == h:
                    continue  # unchanged

                # changed or first time seen
                any_change = True
                print(f"CHANGE DETECTED for {record_id}")

                new_key = upsert_record(record, project_access_ok)

                # update state
                STATE["records"][str(record_id)] = {
                    "hash": h,
                    "jira_key": new_key or (record.get("jira_issue_key") or "").strip()
                }

                save_state(STATE)

            if not any_change:
                print("No changes detected (hash match).")

        except Exception as e:
            print("Error:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
