import os
import time
import subprocess
import traceback

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))  # 1 min default

def run_once():
    # Runs the same thing that runs manually
    subprocess.run(
    ["python", "redcap_to_jira/redcap_to_jira.py"],
    check=True
)


if __name__ == "__main__":
    print(f"[startup] polling every {POLL_SECONDS} seconds", flush=True)
    while True:
        try:
            run_once()
            print("[ok] cycle complete", flush=True)
        except Exception as e:
            print(f"[error] {e}", flush=True)
            traceback.print_exc()
        time.sleep(POLL_SECONDS)
