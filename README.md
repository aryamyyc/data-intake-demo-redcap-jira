# REDCap → Jira Data Intake Integration

This project implements an automated integration between REDCap and Jira to streamline data intake requests. Survey submissions collected in REDCap are periodically fetched, transformed, and created as Jira issues with structured fields such as summary, description, priority, and due date.
The goal is to replace unstructured email-based intake with an organized, automated workflow

---

## Overview

- Polls REDCap for new survey records using the REDCap API
- Maps REDCap fields to Jira issue fields
- Converts plain-text descriptions into Atlassian Document Format (ADF)
- Creates Jira issues programmatically via the Jira Cloud REST API
- Prevents duplicate issue creation during runtime

This integration is designed to be simple, transparent, and easy to extend.

---

## How It Works

1. The script polls REDCap at a fixed interval (every 60 seconds)
2. Each REDCap record is processed exactly once per runtime
3. Relevant fields are extracted and normalized
4. A Jira issue payload is constructed
5. The issue is created in the configured Jira project

## Field Mapping

| REDCap Field | Jira Field | Notes |
|-------------|-----------|-------|
| `fname`, `lname` | Issue Summary | Combined into a readable title |
| `request_describ` | Description | Included as structured ADF content |
| `priority` | Priority | Normalized to Jira priority names |
| `duedate` | Due Date | Applied directly to the Jira issue |
| Full REDCap record | Description | Included for traceability |


## Environment Variables

Sensitive configuration values are loaded from a `.env` file (not committed to version control).

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
