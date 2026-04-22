# REDCap → Jira Data Intake Integration

This project automatically sends requests from REDCap into Jira so the Data & Analytics team can track work in one place.
Instead of people emailing requests (which can get messy or forgotten), requests are submitted through a REDCap form and automatically show up as Jira tickets.

---

## Overview

- Polls REDCap for new survey records using the REDCap API
- Maps REDCap fields to Jira issue fields
- Creates or updates Jira issues programmatically
- Keeps Jira in sync when REDCap records change
- Avoids duplicate Jira issues

This integration is designed to be simple, transparent, and easy to extend.

---

## How It Works

1. The script polls REDCap at a fixed interval (every 60 seconds)
2. All REDCap records are fetched
3. For each record, the script creates a “fingerprint” (hash) based on important fields
4. The current fingerprint is compared to the previous one stored locally
5. If the fingerprint has changed:
    - The Jira issue is created or
    - The existing Jira issue is updated
6,The Jira issue key is written back into REDCap to keep the systems linked

## Hash-Based Change Detection

REDCap does not reliably report when records are edited, especially for surveys or certain types of saves.
To solve this, the integration uses a hash‑based comparison:

- A hash is a short value that represents the contents of a record
- If the record changes, the hash changes
- If the hash changes, the Jira issue is updated
- If the hash stays the same, nothing happens

This ensures that Jira always reflects the latest REDCap data, changes are never missed and Jira issues are not updated unnecessarily

## Field Mapping

| REDCap Field | Jira Field | Notes |
|-------------|-----------|-------|
| `fname`, `lname` | Issue Summary | Combined into a readable title |
| `request_describ` | Description | Included as structured ADF content |
| `priority` | Priority | Normalized to Jira priority names |
| `duedate` | Due Date | Applied directly to the Jira issue |
| Full REDCap record | Description | Included for traceability |


## Environment Variables

Create a `.env` file in the project root with the following variables:

```env
REDCAP_API_URL=your_redcap_api_url
REDCAP_API_TOKEN=your_redcap_api_token

JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your_email@example.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=PROJECTKEY

DEPARTMENT_FIELD_ID = customfield_xxxxx
REQUEST_TYPE_FIELD_ID = customfield_xxxxx
REPORTER_NAME_FIELD_ID = customfield_xxxxx
REQUESTER_EMAIL_FIELD_ID = customfield_xxxxx
