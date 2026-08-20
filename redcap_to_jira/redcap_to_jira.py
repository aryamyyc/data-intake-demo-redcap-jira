import os
import re
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
ATTACHMENT_FIELD_NAME = "attachments"

# Ensure we always load the correct .env from repo root
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

STATE_PATH = BASE_DIR / ".redcap_jira_hash_state.json"
TEMP_ATTACHMENT_DIR = BASE_DIR / ".temp_redcap_attachments"
TEMP_ATTACHMENT_DIR.mkdir(exist_ok=True)

REDCAP_API_URL = os.getenv("REDCAP_API_URL")
REDCAP_API_TOKEN = os.getenv("REDCAP_API_TOKEN")

JIRA_BASE_URL = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")

DEPARTMENT_FIELD_ID = os.getenv("DEPARTMENT_FIELD_ID")
REQUEST_TYPE_FIELD_ID = os.getenv("REQUEST_TYPE_FIELD_ID")
REQUESTER_NAME_FIELD_ID = os.getenv("REQUESTER_NAME_FIELD_ID")
REQUESTER_EMAIL_FIELD_ID = os.getenv("REQUESTER_EMAIL_FIELD_ID")
REQUEST_SOURCE_FIELD_ID = os.getenv("REQUEST_SOURCE_FIELD_ID")


# ------------------- State (hashes) -------------------
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"records": {}}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


STATE = load_state()


# ------------------- Helpers -------------------
def norm(v):
    return str(v).strip() if v else ""


def safe_filename(file_name: str) -> str:
    """
    Removes unsafe characters from a file name.
    """
    file_name = file_name.strip().strip('"')
    file_name = re.sub(r'[<>:"/\\|?*]', "_", file_name)
    return file_name or "redcap_attachment"


def file_sha256(file_path: Path) -> str:
    """
    Creates a hash of the file content so we do not keep uploading duplicates.
    """
    h = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)

    return h.hexdigest()


def get_record_id(record: dict) -> str:
    return str(record.get("record_id") or record.get("id") or "")


# ------------------- Jira -------------------
def get_jira_auth_header():
    token = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    b64 = base64.b64encode(token.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {b64}"}


def jira_project_access_ok() -> bool:
    # Avoiding duplicate spam
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}"
    headers = {"Accept": "application/json", **get_jira_auth_header()}

    r = requests.get(url, headers=headers)
    return r.status_code == 200


def jira_issue_exists(issue_key: str) -> bool:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    headers = {"Accept": "application/json", **get_jira_auth_header()}

    r = requests.get(url, headers=headers)

    if r.status_code == 200:
        return True

    if r.status_code == 404:
        return False

    print("Jira issue_exists check failed:", r.status_code, r.text)
    r.raise_for_status()
    return False


def build_adf_description(text: str):
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        ]
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


def upload_attachment_to_jira(issue_key: str, file_path: Path, file_name: str):
    """
    Uploads a local file to the Jira issue's attachments section.
    """

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"

    headers = {
        "Accept": "application/json",
        "X-Atlassian-Token": "no-check",
        **get_jira_auth_header()
    }

    with open(file_path, "rb") as f:
        files = {
            "file": (file_name, f)
        }

        resp = requests.post(url, headers=headers, files=files)

    if not resp.ok:
        print("Jira ATTACHMENT UPLOAD error:", resp.status_code, resp.text)

    resp.raise_for_status()
    return resp.json()


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
        "overwriteBehavior": "normal",
        "returnContent": "count",
        "returnFormat": "json",
        "data": json.dumps([
            {
                "record_id": str(record_id),
                "jira_issue_key": jira_key
            }
        ])
    }

    resp = requests.post(REDCAP_API_URL, data=payload)

    if not resp.ok:
        print("REDCAP WRITEBACK error:", resp.status_code, resp.text)

    resp.raise_for_status()
    return resp.json() if resp.text else None


def get_redcap_attachment_filename(resp, record_id: str) -> str:
    """
    Attempts to get the original filename from REDCap response headers.
    If REDCap does not provide one, creates a fallback filename.
    """

    content_disposition = resp.headers.get("Content-Disposition", "")

    match = re.search(r'filename="?([^"]+)"?', content_disposition)

    if match:
        return safe_filename(match.group(1))

    return safe_filename(f"redcap_attachment_record_{record_id}")


def download_redcap_attachment(record_id: str, field_name: str = ATTACHMENT_FIELD_NAME):
    """
    Downloads the file uploaded to a REDCap File Upload field.

    Returns:
        file_path, file_name if a file exists
        None, None if no file exists
    """

    payload = {
        "token": REDCAP_API_TOKEN,
        "content": "file",
        "action": "export",
        "record": str(record_id),
        "field": field_name,
        "returnFormat": "json"
    }

    resp = requests.post(REDCAP_API_URL, data=payload)

    # REDCap may return 400 if there is no file uploaded for that field/record
    if resp.status_code == 400:
        print(f"No REDCap attachment found for record {record_id}.")
        return None, None

    if not resp.ok:
        print("REDCAP FILE DOWNLOAD error:", resp.status_code, resp.text)
        resp.raise_for_status()

    if not resp.content:
        print(f"REDCap attachment field is empty for record {record_id}.")
        return None, None

    file_name = get_redcap_attachment_filename(resp, record_id)
    file_path = TEMP_ATTACHMENT_DIR / file_name

    file_path.write_bytes(resp.content)

    return file_path, file_name


# ------------------- Attachment Sync -------------------
def sync_redcap_attachment_to_jira(record: dict, issue_key: str):
    """
    Downloads the REDCap attachment from the 'attachments' field
    and uploads it to Jira if it has not already been uploaded.
    """

    record_id = get_record_id(record)

    if not record_id or not issue_key:
        return None

    file_path, file_name = download_redcap_attachment(record_id, ATTACHMENT_FIELD_NAME)

    if not file_path:
        return None

    try:
        current_attachment_hash = file_sha256(file_path)

        record_state = STATE["records"].get(str(record_id), {})
        previous_attachment_hash = record_state.get("attachment_hash")

        if previous_attachment_hash == current_attachment_hash:
            print(f"Attachment already synced for record {record_id}; skipping upload.")
            return current_attachment_hash

        upload_attachment_to_jira(issue_key, file_path, file_name)
        print(f"ATTACHED {file_name} to {issue_key}")

        return current_attachment_hash

    finally:
        try:
            file_path.unlink()
        except Exception:
            pass


# ------------------- Mapping REDCap to Jira -------------------
def build_jira_payload_from_redcap(redcap_data: dict) -> dict:
    # ------------------- Basic fields -------------------
    requester = norm(redcap_data.get("requestor_name")) or "Unknown"
    email = norm(redcap_data.get("contact_email_phone"))
    department = norm(redcap_data.get("team_department"))
    title = norm(redcap_data.get("request_title")) or "No title"
    due = norm(redcap_data.get("desired_completion_date"))

    business_objective = norm(redcap_data.get("business_objective"))
    strategic = norm(redcap_data.get("strategic_pillars"))
    request_type = norm(redcap_data.get("request_type"))
    deliverables = norm(redcap_data.get("requested_deliverables"))

    attachment_marker = norm(redcap_data.get(ATTACHMENT_FIELD_NAME))
    attachment_text = attachment_marker if attachment_marker else "No attachment uploaded"

    # ------------------- Description -------------------
    description_text = f"""

Request submitted by: {requester} ({department})

Business Objective:
{business_objective}

Requested Deliverables:
{deliverables}

Desired Completion Date: {due}
Request Type: {request_type}
Strategic Pillars: {strategic}

"""

    # ------------------- Format date -------------------
    def format_date(d):
        if not d:
            return None

        # Try common REDCap/date formats
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass

        # If it is already some other string, do not send it to Jira as duedate
        return None

    due_formatted = format_date(due)

    # ------------------- Jira fields -------------------
    fields = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": f"{requester}: {title}",
        "description": build_adf_description(description_text),
        "issuetype": {"name": "Task"},
    }

    if due_formatted:
        fields["duedate"] = due_formatted

    if requester and REQUESTER_NAME_FIELD_ID:
        fields[REQUESTER_NAME_FIELD_ID] = requester

    if email and REQUESTER_EMAIL_FIELD_ID:
        fields[REQUESTER_EMAIL_FIELD_ID] = email

    if department and DEPARTMENT_FIELD_ID:
        fields[DEPARTMENT_FIELD_ID] = {"value": department}

    if request_type and REQUEST_TYPE_FIELD_ID:
        fields[REQUEST_TYPE_FIELD_ID] = {"value": request_type}

    if REQUEST_SOURCE_FIELD_ID:
        fields[REQUEST_SOURCE_FIELD_ID] = {"value": "Online Form"}


    return {"fields": fields}


# ------------------- Hashing -------------------
def record_hash(redcap_data: dict) -> str:
    """
    Hashes the Jira field payload and attachment marker.

    Note:
    This helps detect changes when REDCap includes a visible value
    for the file upload field in the exported record.
    """

    payload = build_jira_payload_from_redcap(redcap_data)

    fields = dict(payload.get("fields", {}))

    # Do not include create-only Jira fields in update comparison
    fields.pop("project", None)
    fields.pop("issuetype", None)

    attachment_marker = norm(redcap_data.get(ATTACHMENT_FIELD_NAME))

    blob = json.dumps(
        {
            "fields": fields,
            "attachment_marker": attachment_marker
        },
        sort_keys=True,
        ensure_ascii=False
    )

    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------- Upsert -------------------
def upsert_record(record: dict, project_access_ok: bool):
    """
    Stops duplicates and lets edits in REDCap update the correct Jira ticket.
    Also syncs REDCap attachment to the Jira ticket.
    """

    record_id = get_record_id(record)

    if not record_id:
        print("SKIP: record has no record_id.")
        return None

    state_jira_key = STATE["records"].get(str(record_id), {}).get("jira_key", "")
    jira_key = norm(record.get("jira_issue_key") or state_jira_key)

    if not project_access_ok:
        print(f"SKIP {record_id}: no access to Jira project {JIRA_PROJECT_KEY} won't create duplicates.")
        return jira_key or None

    payload = build_jira_payload_from_redcap(record)

    # Existing Jira issue
    if jira_key and jira_issue_exists(jira_key):
        update_jira_issue(jira_key, payload)
        print(f"UPDATED {jira_key} from REDCap record {record_id}")

        attachment_hash = sync_redcap_attachment_to_jira(record, jira_key)

        if attachment_hash:
            STATE["records"].setdefault(str(record_id), {})["attachment_hash"] = attachment_hash

        return jira_key

    # Jira key exists in REDCap/state but Jira cannot find it
    if jira_key:
        print(f"INFO: jira_issue_key '{jira_key}' not found or not accessible -> creating new ticket for {record_id}")

    # Create new Jira issue
    resp = create_jira_issue(payload)
    new_key = resp.get("key")

    print(f"CREATED {new_key} for REDCap record {record_id}")

    if new_key:
        try:
            write_jira_key_back_to_redcap(record_id, new_key)
            print(f"WROTE BACK jira_issue_key={new_key} to REDCap record {record_id}")
        except Exception as e:
            print(f"WARNING: Jira issue created, but REDCap write-back failed for record {record_id}: {e}")

        attachment_hash = sync_redcap_attachment_to_jira(record, new_key)

        if attachment_hash:
            STATE["records"].setdefault(str(record_id), {})["attachment_hash"] = attachment_hash

    return new_key


# ------------------- Main loop -------------------
def main():
    print("JIRA_PROJECT_KEY:", JIRA_PROJECT_KEY)
    print(f"Hash-sync polling every {POLL_SECONDS} seconds...")
    print(f"REDCap attachment field: {ATTACHMENT_FIELD_NAME}")

    while True:
        try:
            records = fetch_redcap_records()
            print(f"Fetched {len(records)} records from REDCap.")

            project_access_ok = jira_project_access_ok()
            any_change = False

            for record in records:
                try:
                    record_id = get_record_id(record)

                    if not record_id:
                        print("Skipping record with no record_id.")
                        continue

                    h = record_hash(record)

                    prev = STATE["records"].get(str(record_id), {})
                    prev_hash = prev.get("hash")

                    if prev_hash == h:
                        continue

                    any_change = True
                    print(f"CHANGE DETECTED for {record_id}")

                    new_key = upsert_record(record, project_access_ok)

                    current_state = STATE["records"].get(str(record_id), {})

                    STATE["records"][str(record_id)] = {
                        "hash": h,
                        "jira_key": new_key or current_state.get("jira_key") or norm(record.get("jira_issue_key")),
                        "attachment_hash": current_state.get("attachment_hash")
                    }

                    save_state(STATE)

                except Exception as e:
                    print(f"Record-level error for {record.get('record_id', 'unknown')}: {e}")

            if not any_change:
                print("No changes detected hash match.")

        except Exception as e:
            print("Loop-level error:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()