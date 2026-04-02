import os
import base64
import json
import time
import requests
from dotenv import load_dotenv
from datetime import datetime

# grabs environment variables from .env file
load_dotenv()

REDCAP_API_URL = os.getenv("REDCAP_API_URL")
REDCAP_API_TOKEN = os.getenv("REDCAP_API_TOKEN")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "TDI")


DEPARTMENT_FIELD_ID = os.getenv("DEPARTMENT_FIELD_ID")
REQUEST_TYPE_FIELD_ID = os.getenv("REQUEST_TYPE_FIELD_ID")
REQUESTER_NAME_FIELD_ID = os.getenv("REPORTER_NAME_FIELD_ID")
# Track processed records so we donyt store duplicates (in-memory for demo; we shopuld use a DB/file for production)
PROCESSED_RECORDS = set()

# gets the auth header for Jira API using email and API token, encoded in base64 for Basic Auth
def get_jira_auth_header():
    token = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    b64 = base64.b64encode(token.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {b64}"}

# Converts plain text to Atlassian Document Format (ADF) for Jira Cloud
def build_adf_description(text):
    """Convert plain text to Atlassian Document Format (ADF) for Jira Cloud."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": text}
                ]
            }
        ]
    }

# Builds Jira issue payload from REDCap data, mapping fields and formatting description
def build_jira_payload_from_redcap(redcap_data: dict) -> dict:
    try:
        fname = redcap_data.get("fname", "").strip()
        lname = redcap_data.get("lname", "").strip()

        participant_name = f"{fname} {lname}".strip() or "Unknown participant"
        requester_name = f"{fname} {lname}".strip() or "Unknown requester"

        reason = redcap_data.get("request_describ", "No reason provided").strip()

        department_raw = redcap_data.get("team", "").strip()
        request_type_raw = redcap_data.get("requesttype", "").strip()
        request_type_other = redcap_data.get("requesttype_other", "").strip()

        # Priority mapping
        priority_raw = redcap_data.get("priority", "").strip()
        priority_map = {
            "1": "High",
            "2": "Medium",
            "3": "Low",
            "High": "High",
            "Medium": "Medium",
            "Low": "Low",
            "": ""
        }
        priority = priority_map.get(priority_raw, priority_raw)

        # Due date
        due_date_raw = redcap_data.get("duedate", "").strip()
        due_date = None
        if due_date_raw:
            try:
                parsed_date = datetime.strptime(due_date_raw, "%Y-%m-%d")
                due_date = parsed_date.strftime("%Y-%m-%d")
            except ValueError:
                print(f"Invalid due date format from REDCap: {due_date_raw}")

        # Build description text (IMPORTANT: add "Other" details BEFORE ADF conversion)
        description_text = (
            f"Participant Name: {participant_name}\n"
            f"Reason: {reason}\n"
            f"Priority (from survey): {priority}\n"
        )

        if request_type_raw.lower() == "other" and request_type_other:
            description_text += f"\nRequest Type (Other details): {request_type_other}\n"

        description_text += f"\nFull REDCap payload:\n{json.dumps(redcap_data, indent=2)}"

        # Now create fields dict (must exist before fields[...] usage)
        fields = {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": f"REDCap Intake: {participant_name}",
            "description": build_adf_description(description_text),
            "issuetype": {"name": "Task"}
        }

        # Set custom Requester Name text field
        if REQUESTER_NAME_FIELD_ID:
            fields[REQUESTER_NAME_FIELD_ID] = requester_name
        else:
            print("WARNING: REQUESTER_NAME_FIELD_ID is not set (check .env).")

        # Optional fields
        if priority:
            fields["priority"] = {"name": priority}

        if due_date:
            fields["duedate"] = due_date

        if department_raw and DEPARTMENT_FIELD_ID:
            fields[DEPARTMENT_FIELD_ID] = {"value": department_raw}

        if request_type_raw and REQUEST_TYPE_FIELD_ID:
            fields[REQUEST_TYPE_FIELD_ID] = {"value": request_type_raw}

        payload = {"fields": fields}
        print("Jira payload to be returned:", json.dumps(payload, indent=2))
        return payload

    except Exception as e:
        print("Error building Jira payload:", e)
        return {}
    

# Fetches records from REDCap using the API, returning JSON data
def fetch_redcap_records():
    payload = {
        'token': REDCAP_API_TOKEN,
        'content': 'record',
        'format': 'json',
        'type': 'flat',
        'rawOrLabel': 'label',       
        'exportCheckboxLabel': 'true'

    }
    resp = requests.post(REDCAP_API_URL, data=payload) 
    resp.raise_for_status()
    return resp.json()

# API call to create a Jira issue
def create_jira_issue(issue_payload: dict) -> dict:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    headers = {
        "Content-Type": "application/json",
        **get_jira_auth_header()
    }
    print("Payload being sent to Jira:", json.dumps(issue_payload, indent=2))
    resp = requests.post(url, headers=headers, json=issue_payload)
    if not resp.ok:
        print("Jira API error response:", resp.text)
    resp.raise_for_status()
    return resp.json()

# ---------------- Main loop ----------------------

def main():
    print("JIRA_PROJECT_KEY:", JIRA_PROJECT_KEY)
    print("Polling REDCap for new records...")
    while True:
        try:
            records = fetch_redcap_records()
            print(f"Fetched {len(records)} records from REDCap.")
            for record in records:
                record_id = record.get("record_id") or record.get("id") or str(record)
                if record_id in PROCESSED_RECORDS:
                    continue
                print(f"Processing new record: {record_id}")
                issue_payload = build_jira_payload_from_redcap(record)
                if not issue_payload or not issue_payload.get("fields"):
                    print("Skipping record due to empty or invalid payload.")
                    continue
                jira_response = create_jira_issue(issue_payload)
                print(f"Created Jira issue: {jira_response.get('key')}")
                PROCESSED_RECORDS.add(record_id)
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(60)  # Poll every 60 seconds

if __name__ == "__main__":
    main()