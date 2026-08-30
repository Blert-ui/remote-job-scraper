import os
import re
import sqlite3
import pandas as pd
import requests


class ToolFilteredJobAggregator:

  def __init__(
      self, required_tools=None, db_path="jobs_history.db"
  ):
    # Standardize required tools list (e.g., ['klaviyo', 'hubspot'])
    self.required_tools = [t.lower() for t in (required_tools or [])]

    # Map target platforms to regex variations to capture typos or phrasing
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
    """Scans job text using regex and returns a list of detected tools."""
    detected = []
    text_lower = text.lower()
    for tool_name, pattern in self.tool_patterns.items():
      if re.search(pattern, text_lower):
        detected.append(tool_name)
    return detected

  def _matches_tool_filter(self, detected_tools):
    """Verifies if job contains at least one of the user's required tools."""
    if not self.required_tools:
      return True  # If no filter specified, keep all
    detected_lower = [t.lower() for t in detected_tools]
    return any(
        req_tool in detected_lower for req_tool in self.required_tools
    )

  def fetch_remote_ok(self):
    """Fetches jobs and filters by required ESP tools."""
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

        # Apply ESP filtering check
        if self._matches_tool_filter(detected):
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
    """Fetches listings from Jobicy and checks for tool matches."""
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

        if self._matches_tool_filter(detected):
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
      print("No listings found matching specified tools.")
      return []

    # Deduplicate based on URL
    df.drop_duplicates(subset=["url"], inplace=True)
    return df.to_dict("records")


# Usage Example
if __name__ == "__main__":
  # Specify exact ESP tools you want to filter for
  my_target_tools = ["Klaviyo", "HubSpot"]

  scraper = ToolFilteredJobAggregator(required_tools=my_target_tools)
  matched_jobs = scraper.get_filtered_jobs()

  print(
      f"\nFound {len(matched_jobs)} jobs matching {my_target_tools}:"
  )
  for job in matched_jobs:
    print(
        f"- {job['title']} at {job['company']} [{job['source']}]"
        f" | Tools: {job['tools_found']}"
    )
    
