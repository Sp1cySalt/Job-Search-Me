#!/usr/bin/env python3
"""
job_watcher.py

Pulls new job postings from a set of free, ToS-friendly job APIs, filters
them against keywords you configure, remembers what it has already shown
you, and pushes new matches to your phone via ntfy.sh.

Sources used (no scraping of sites that forbid it, blah blah blah):
  - RemoteOK        (https://remoteok.com/api)              - no key needed
  - Arbeitnow        (https://www.arbeitnow.com/api/job-board-api) - no key needed
  - The Muse         (https://www.themuse.com/api/public/jobs)     - no key needed
  - Adzuna (optional, needs free API key from https://developer.adzuna.com/)

Run it once a day via cron (see README.md for setup).
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "seen_jobs.json")
LOG_PATH = os.path.join(BASE_DIR, "job_watcher.log")

SEEN_JOB_TTL_DAYS = 30  # forget jobs older than this so the state file doesn't grow forever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("job_watcher")


# ---------- config / state helpers ----------

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def location_matches(job_location, allowed_locations):
    if not allowed_locations:
        return True
    loc_l = job_location.lower()
    return any(loc.lower() in loc_l for loc in allowed_locations)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("State file unreadable, starting fresh.")
        return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def prune_state(state):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_JOB_TTL_DAYS)
    kept = {}
    for job_id, seen_at in state.items():
        try:
            seen_dt = datetime.fromisoformat(seen_at)
        except ValueError:
            continue
        if seen_dt > cutoff:
            kept[job_id] = seen_at
    return kept

#-------- List of standard European countries & locations to ignore -----------
EUROPE_BLACKLIST = [
    "france", "germany", "uk", "united kingdom", "london", "paris", "berlin", 
    "amsterdam", "netherlands", "spain", "italy", "poland", "sweden", 
    "ireland", "dublin", "switzerland", "portugal", "worldwide"
]

def is_us_or_remote(location_str):
    loc_lower = location_str.lower()
    # Reject if any explicit non-US keyword is present
    if any(country in loc_lower for country in EUROPE_BLACKLIST):
        return False
    return True

# ---------- matching ----------

def text_matches(text, keywords, exclude_keywords):
    text_l = text.lower()
    if any(bad.lower() in text_l for bad in exclude_keywords):
        return False
    return any(kw.lower() in text_l for kw in keywords)


# ---------- job sources ----------
# Each fetch_* function returns a list of dicts:
# {id, title, company, url, location, remote, source}

def fetch_remoteok(config):
    jobs = []
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "job-watcher-script/1.0"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        # First element is a legal/notice blob, not a job
        for item in data[1:]:
            title = item.get("position", "")
            company = item.get("company", "")
            tags = " ".join(item.get("tags", []))
            haystack = f"{title} {company} {tags}"
            if text_matches(haystack, config["keywords"], config["exclude_keywords"]):
                location = item.get("location", "Remote")
                if config.get("location_filter") == "us_only" and not is_us_or_remote(location):
                    continue  # Skip non-US jobs
                
                jobs.append({
                    "id": f"remoteok:{item.get('id')}",
                    "title": title,
                    # ... rest of dict
                    "company": company,
                    "url": item.get("url") or f"https://remoteok.com/l/{item.get('id')}",
                    "location": item.get("location", "Remote"),
                    "remote": True,
                    "source": "RemoteOK",
                })
    except requests.RequestException as e:
        log.warning(f"RemoteOK fetch failed: {e}")
    return jobs



def fetch_themuse(config):
    jobs = []
    try:
        # category filter narrows results server-side; we still keyword-filter locally
        resp = requests.get(
            "https://www.themuse.com/api/public/jobs",
            params={"category": "Software Engineering", "page": 0},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            title = item.get("name", "")
            company = (item.get("company") or {}).get("name", "")
            locations = ", ".join(
                loc.get("name", "") for loc in item.get("locations", [])
            ) or "Unspecified"
            haystack = f"{title} {company}"
            if config.get("remote_only") and "remote" not in locations.lower():
                continue
            if text_matches(haystack, config["keywords"], config["exclude_keywords"]):
                refs = item.get("refs", {})
                jobs.append({
                    "id": f"themuse:{item.get('id')}",
                    "title": title,
                    "company": company,
                    "url": refs.get("landing_page", ""),
                    "location": locations,
                    "remote": "remote" in locations.lower(),
                    "source": "The Muse",
                })
    except requests.RequestException as e:
        log.warning(f"The Muse fetch failed: {e}")
    return jobs


def fetch_adzuna(config):
    jobs = []
    az = config.get("adzuna", {})
    if not az.get("enabled"):
        return jobs
    if not az.get("app_id") or not az.get("app_key"):
        log.warning("Adzuna enabled but app_id/app_key missing in config.json — skipping.")
        return jobs
    try:
        for keyword in config["keywords"]:
            resp = requests.get(
                f"https://api.adzuna.com/v1/api/jobs/{az.get('country', 'us')}/search/1",
                params={
                    "app_id": az["app_id"],
                    "app_key": az["app_key"],
                    "results_per_page": 20,
                    "what": keyword,
                    "where": az.get("where", ""),
                    "content-type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                title = item.get("title", "")
                company = (item.get("company") or {}).get("display_name", "")
                location = (item.get("location") or {}).get("display_name", "")
                haystack = f"{title} {company}"
                if text_matches(haystack, config["keywords"], config["exclude_keywords"]):
                    jobs.append({
                        "id": f"adzuna:{item.get('id')}",
                        "title": title,
                        "company": company,
                        "url": item.get("redirect_url", ""),
                        "location": location,
                        "remote": "remote" in location.lower(),
                        "source": "Adzuna",
                    })
            time.sleep(0.3)  # be polite between keyword queries
    except requests.RequestException as e:
        log.warning(f"Adzuna fetch failed: {e}")
    return jobs


# ---------- notification ----------

def send_ntfy_notification(topic, job):
    if not topic or topic.startswith("CHANGE-ME"):
        log.error("ntfy_topic not configured in config.json — skipping notification. "
                   "Edit config.json and set a unique topic name.")
        return False
    title = f"{job['title']} @ {job['company']}"[:120]
    body = f"{job['source']} · {job['location']}"
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Click": job["url"] or "https://ntfy.sh",
                "Tags": "briefcase",
                "Priority": "default",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"ntfy notification failed for {job['id']}: {e}")
        return False


def send_summary_notification(topic, total_new, shown, extra_count):
    if not topic or topic.startswith("CHANGE-ME"):
        return
    body = f"{shown} shown"
    if extra_count > 0:
        body += f", {extra_count} more matched — check logs/config to raise the daily cap."
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": f"Job watch: {total_new} new matches today",
                "Tags": "mag",
                "Priority": "default",
            },
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning(f"Summary notification failed: {e}")


# ---------- main ----------

def main():
    config = load_config()
    state = prune_state(load_state())

    all_jobs = []
    all_jobs += fetch_remoteok(config)
    all_jobs += fetch_themuse(config)
    all_jobs += fetch_adzuna(config)

    log.info(f"Fetched {len(all_jobs)} matching jobs across all sources before dedup.")

    # de-dupe within this run (in case two sources return the same posting)
    unique_jobs = {}
    for job in all_jobs:
        unique_jobs[job["id"]] = job

    new_jobs = [job for job_id, job in unique_jobs.items() if job_id not in state]
    log.info(f"{len(new_jobs)} of those are new since the last run.")

    if not new_jobs:
        log.info("No new jobs. Done.")
        return

    cap = config.get("max_notifications_per_run", 15)
    to_send = new_jobs[:cap]
    extra = max(0, len(new_jobs) - cap)

    sent = 0
    for job in to_send:
        if send_ntfy_notification(config["ntfy_topic"], job):
            sent += 1
        state[job["id"]] = datetime.now(timezone.utc).isoformat()
        time.sleep(0.5)  # avoid hammering ntfy.sh

    # mark the rest as seen too, even if not notified, so they don't pile up tomorrow
    for job in new_jobs[cap:]:
        state[job["id"]] = datetime.now(timezone.utc).isoformat()

    send_summary_notification(config["ntfy_topic"], len(new_jobs), sent, extra)
    save_state(state)
    log.info(f"Done. Notified {sent} jobs, {extra} additional matches not sent (cap reached).")


if __name__ == "__main__":
    main()
