import os
import re
import smtplib
import sqlite3
import pandas as pd
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class ToolFilteredJobAggregator:
    def __init__(
        self, required_tools=None, required_titles=None, db_path="jobs_history.db"
    ):
        self.required_tools = [t.lower() for t in (required_tools or [])]
        self.required_titles = [t.lower() for t in (required_titles or [])]

        self.tool_patterns = {
            "Klaviyo": r"\bklaviyo\b",
            "HubSpot": r"\bhubspot\b",
            "Mailchimp": r"\bmailchimp\b",
            "ActiveCampaign": r"\bactive\s*campaign\b",
            "Marketo": r"\bmarketo\b",
            "Salesforce Marketing Cloud": (
                r"\b(sfmc|salesforce marketing cloud|exacttarget)\b"
            ),
            "Pardot": r"\bpardot\b",
            "Omnisend": r"\bomnisend\b",
            "ConvertKit": r"\bconvertkit\b",
        }

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ToolScraper/1.0"
        }
        self.jobs = []
        self.db_path = db_path

    def _detect_tools(self, text):
        detected = []
        text_lower = text.lower()
        for tool_name, pattern in self.tool_patterns.items():
            if re.search(pattern, text_lower):
                detected.append(tool_name)
        return detected

    def _matches_tool_filter(self, detected_tools):
        if not self.required_tools:
            return True
        detected_lower = [t.lower() for t in detected_tools]
        return any(req_tool in detected_lower for req_tool in self.required_tools)

    def _matches_title_filter(self, title):
        if not self.required_titles:
            return True
        title_lower = title.lower()
        return any(t in title_lower for t in self.required_titles)

    def fetch_remote_ok(self):
        try:
            res = requests.get(
                "https://remoteok.com/api", headers=self.headers, timeout=10
            )
            res.raise_for_status()
            data = res.json()

            for item in data[1:]:
                title = item.get("position", "")
                tags = " ".join(item.get("tags", []))
                description = item.get("description", "")
                combined_text = f"{title} {tags} {description}"

                detected = self._detect_tools(combined_text)

                if self._matches_tool_filter(detected) and self._matches_title_filter(title):
                    self.jobs.append({
                        "source": "RemoteOK",
                        "title": title,
                        "company": item.get("company", "N/A"),
                        "tools_found": ", ".join(detected) if detected else "General Email",
                        "url": item.get("url", "N/A"),
                    })
        except Exception as e:
            print(f"Error fetching RemoteOK: {e}")

    def fetch_jobicy(self):
        try:
            res = requests.get(
                "https://jobicy.com/api/v2/remote-jobs?count=50",
                headers=self.headers,
                timeout=10,
            )
            res.raise_for_status()

            for item in res.json().get("jobs", []):
                title = item.get("jobTitle", "")
                description = item.get("jobDescription", "")
                combined_text = f"{title} {description}"

                detected = self._detect_tools(combined_text)

                if self._matches_tool_filter(detected) and self._matches_title_filter(title):
                    self.jobs.append({
                        "source": "Jobicy",
                        "title": title,
                        "company": item.get("companyName", "N/A"),
                        "tools_found": ", ".join(detected) if detected else "General Email",
                        "url": item.get("url", "N/A"),
                    })
        except Exception as e:
            print(f"Error fetching Jobicy: {e}")

    def get_filtered_jobs(self):
        self.fetch_remote_ok()
        self.fetch_jobicy()

        df = pd.DataFrame(self.jobs)
        if df.empty:
            print("No listings found matching specified tools/titles.")
            return []

        df.drop_duplicates(subset=["url"], inplace=True)
        return df.to_dict("records")

    # ------------------------------------------------------------------
    # NEW-JOBS TRACKING — keeps a small SQLite file of URLs already sent
    # so the daily email only contains listings you haven't seen before.
    # ------------------------------------------------------------------
    def filter_new_jobs(self, jobs):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sent_jobs (url TEXT PRIMARY KEY)"
        )
        conn.commit()

        new_jobs = []
        for job in jobs:
            url = job.get("url", "")
            if not url:
                continue
            cur = conn.execute(
                "SELECT 1 FROM sent_jobs WHERE url = ?", (url,)
            )
            if cur.fetchone() is None:
                new_jobs.append(job)
                conn.execute(
                    "INSERT OR IGNORE INTO sent_jobs (url) VALUES (?)", (url,)
                )

        conn.commit()
        conn.close()
        return new_jobs


def send_email_digest(jobs, recipient, sender, app_password):
    """Sends a daily digest email of matched jobs via Gmail SMTP."""
    if not jobs:
        print("No new jobs — skipping email.")
        return

    subject = f"Job Digest: {len(jobs)} new listing(s) found"

    html_rows = ""
    for job in jobs:
        html_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{job['title']}</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{job['company']}</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{job['source']}</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">{job['tools_found']}</td>
            <td style="padding:8px;border-bottom:1px solid #ddd;">
                <a href="{job['url']}">View</a>
            </td>
        </tr>"""

    html_body = f"""
    <html><body>
    <h2>{len(jobs)} new job(s) matching your search</h2>
    <table style="border-collapse:collapse;width:100%;font-family:sans-serif;">
        <tr style="background:#f0f0f0;text-align:left;">
            <th style="padding:8px;">Title</th>
            <th style="padding:8px;">Company</th>
            <th style="padding:8px;">Source</th>
            <th style="padding:8px;">Tools Found</th>
            <th style="padding:8px;">Link</th>
        </tr>
        {html_rows}
    </table>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, recipient, msg.as_string())

    print(f"Email sent to {recipient} with {len(jobs)} job(s).")


if __name__ == "__main__":
    my_target_tools = ["Klaviyo", "HubSpot", "Mailchimp", "ActiveCampaign",
                        "Marketo", "Salesforce Marketing Cloud", "Pardot",
                        "Omnisend", "ConvertKit"]

    my_target_titles = [
        "email marketing", "email marketer", "email campaign",
        "email specialist", "email coordinator", "email associate",
        "email assistant", "email intern",
        "lifecycle marketing", "lifecycle marketer", "retention marketing",
        "crm marketing", "crm coordinator", "crm specialist", "crm assistant",
        "marketing automation", "automation specialist", "automation coordinator",
        "newsletter", "content marketing coordinator", "content marketing assistant",
        "digital marketing coordinator", "digital marketing assistant",
        "digital marketing specialist", "digital marketing intern",
        "growth marketing coordinator", "growth marketing assistant",
        "growth marketing specialist", "ecommerce marketing", "e-commerce marketing",
        "marketing coordinator", "marketing assistant", "marketing specialist",
        "marketing associate", "marketing intern", "junior marketer",
        "junior digital marketer", "marketing analyst",
        "social media and email", "social media & email",
    ]

    scraper = ToolFilteredJobAggregator(
        required_tools=my_target_tools,
        required_titles=my_target_titles,
    )
    matched_jobs = scraper.get_filtered_jobs()
    print(f"\nFound {len(matched_jobs)} total matching job(s) this run.")

    # Only email jobs we haven't already sent before
    new_jobs = scraper.filter_new_jobs(matched_jobs)
    print(f"{len(new_jobs)} of those are new since last run.")

    for job in new_jobs:
        print(f"- {job['title']} at {job['company']} [{job['source']}] | Tools: {job['tools_found']}")

    # Email sending — reads credentials from environment variables
    # (set as GitHub Actions secrets — see workflow file)
    EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
    EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
    EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")

    if EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECIPIENT:
        send_email_digest(new_jobs, EMAIL_RECIPIENT, EMAIL_SENDER, EMAIL_APP_PASSWORD)
    else:
        print("Email credentials not set — skipping email send (set EMAIL_SENDER, "
              "EMAIL_APP_PASSWORD, EMAIL_RECIPIENT as env vars / GitHub secrets).")
        
      
