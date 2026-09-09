# Job Watcher

Checks a handful of legitimate, free job APIs once a day for postings that
match your keywords, and pushes new matches to your phone as notifications.

It deliberately does **not** scrape LinkedIn or Indeed — both explicitly
forbid automated scraping in their terms of service and use anti-bot systems
that would require credential handling and CAPTCHA-solving to defeat. Instead
it pulls from sources that offer open, ToS-friendly APIs:

- **RemoteOK** — remote tech jobs, no API key needed
- **Arbeitnow** — tech jobs (remote + on-site), no API key needed
- **The Muse** — broad job board, filtered to Software Engineering, no key needed
- **Adzuna** *(optional)* — aggregates many boards including some that overlap
  with Indeed listings; needs a free API key, off by default

Between these you'll get solid tech-role coverage. If you want, I can add
more sources later (e.g. Greenhouse/Lever boards for specific companies you
care about, or your city's civic-tech job board).

## 1. Set up notifications (5 minutes)

1. Install the **ntfy** app: [iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
2. In the app, subscribe to a topic name you make up — something unique and
   hard to guess, e.g. `jsmith-jobwatch-8271` (topics on ntfy.sh are public by
   name, so don't use something guessable if the content is sensitive).
3. Open `config.json` and set `"ntfy_topic"` to that same name.

That's it — no account, no API key, for this part.

## 2. Configure your search

Edit `config.json`:

```json
{
  "keywords": ["software engineer", "backend developer"],
  "exclude_keywords": ["senior staff", "principal engineer"],
  "remote_only": false,
  "ntfy_topic": "your-topic-here"
}
```

- `keywords`: matched against job title/company/tags, case-insensitive. A job
  needs to match at least one.
- `exclude_keywords`: if any of these appear, the job is skipped even if a
  keyword also matches (handy for filtering out seniority levels you're not
  targeting).
- `remote_only`: set `true` to drop on-site listings where the source tells us
  location.

### Optional: turn on Adzuna for broader coverage

1. Get a free key at https://developer.adzuna.com/
2. In `config.json`, set:
   ```json
   "adzuna": {
     "enabled": true,
     "app_id": "your_id",
     "app_key": "your_key",
     "country": "us",
     "where": "Austin"
   }
   ```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Run it manually once to test

```bash
python3 job_watcher.py
```

Check `job_watcher.log` for what happened, and your phone for notifications.
The first run will likely notify you about a bunch of jobs at once since
nothing has been "seen" yet — that's expected. After that, you'll only be
notified about postings that are new since the last run.

## 5. Schedule it to run daily

**macOS / Linux (cron):**

```bash
crontab -e
```

Add a line to run it every day at 8:00 AM (adjust the path to match where
you saved this folder):

```
0 8 * * * cd /path/to/job-watcher && /usr/bin/python3 job_watcher.py >> cron.log 2>&1
```

**Windows (Task Scheduler):**

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, pick a time
3. Action: Start a program
   - Program: `python`
   - Arguments: `job_watcher.py`
   - Start in: the full path to this folder

## Files

- `job_watcher.py` — the script
- `config.json` — your keywords and notification settings
- `seen_jobs.json` — auto-created; tracks what's already been shown to you
  (entries auto-expire after 30 days)
- `job_watcher.log` — run history/errors, useful for debugging cron issues

## Notes & limits

- Rate/volume: capped at 15 notifications per run by default
  (`max_notifications_per_run` in config) so a big first run or a busy day
  doesn't flood your phone. Anything over the cap is still marked "seen" so
  it won't repeat tomorrow — raise the cap if you'd rather see everything.
- ntfy.sh topics are unauthenticated by design — anyone who knows your exact
  topic name can read or post to it. Pick something long and non-obvious. For
  stronger privacy you can self-host ntfy or use Pushover instead (let me
  know if you'd like that swapped in).
- If a source's API changes shape or goes down, that one source is skipped
  and logged — it won't crash the whole run.
