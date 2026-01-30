#!/usr/bin/env python3
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
import urllib3

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---- Config ----
OUTPUT_ICS = Path("cga.ics")
TZID = "America/New_York"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

session = requests.Session()

@dataclass(frozen=True)
class Event:
    dt_start: datetime
    title: str
    location: str = ""
    
    @property
    def uid(self) -> str:
        key = f"{self.dt_start.isoformat()}|{self.title.lower()}"
        h = hashlib.sha1(key.encode()).hexdigest()
        return f"cga-{h}@cga.ct.gov"

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_dt_line(date_str: str, time_str: str) -> Optional[datetime]:
    try:
        clean_dt = normalize_ws(f"{date_str} {time_str}")
        pattern = re.compile(
            r"(?P<mon>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s+"
            r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>AM|PM)",
            re.IGNORECASE
        )
        m = pattern.search(clean_dt)
        if not m: return None
        
        h = int(m.group("h"))
        if m.group("ampm").upper() == "PM" and h != 12: h += 12
        elif m.group("ampm").upper() == "AM" and h == 12: h = 0
        
        return datetime(int(m.group("year")), int(m.group("mon")), int(m.group("day")), h, int(m.group("m")))
    except Exception:
        return None

def parse_events_from_html(html_content: str) -> List[Event]:
    soup = BeautifulSoup(html_content, "html.parser")
    events = []
    
    # The CGA webapp results are strictly inside table rows (tr)
    rows = soup.find_all("tr")
    
    for row in rows:
        cols = row.find_all("td")
        # Valid event rows on this ASP page typically have 4+ columns:
        # 0: Date, 1: Time, 2: Usually an ID or Duration, 3: The Description/Title, 4: Location
        if len(cols) < 4:
            continue
            
        date_raw = cols[0].get_text(strip=True)
        time_raw = cols[1].get_text(strip=True)
        
        # Test if the first column is actually a date (mm/dd/yyyy)
        dt = parse_dt_line(date_raw, time_raw)
        if not dt:
            continue

        # We need to find which column contains the actual text description.
        # We iterate through the remaining columns and pick the longest string 
        # that isn't just a number or "CGA Event".
        potential_titles = []
        for col in cols[2:]:
            text = normalize_ws(col.get_text(strip=True))
            if text and not text.isdigit() and text.lower() != "cga event":
                potential_titles.append(text)
        
        # Use the first valid text as the title and the second (if exists) as location
        title = potential_titles[0] if potential_titles else "Unknown CGA Meeting"
        location = potential_titles[1] if len(potential_titles) > 1 else ""

        # If the title is too short, it might be a code; keep looking
        if len(title) < 3 and len(potential_titles) > 1:
            title = potential_titles[1]
            location = potential_titles[2] if len(potential_titles) > 2 else ""

        events.append(Event(dt_start=dt, title=title, location=location))
        print(f"    [FOUND] {dt.strftime('%m/%d %H:%M')} -> {title}")
            
    return events

def fetch_events_for_day(target_date: date) -> str:
    search_url = "https://www.cga.ct.gov/webapps/cgaevents.asp"
    xhr_url = "https://www.cga.ct.gov/webapps/in-events1x.asp"
    
    if not hasattr(session, 'asp_tokens'):
        r = session.get(search_url, verify=False, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        session.asp_tokens = {
            f: soup.find("input", attrs={"name": f}).get("value", "")
            for f in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]
            if soup.find("input", attrs={"name": f})
        }

    date_str = target_date.strftime("%m/%d/%Y")
    data = {
        "sDate": date_str, "eDate": date_str,
        "btnSubmit": "Search", "List": "1",
        **session.asp_tokens
    }

    headers = {"User-Agent": USER_AGENT, "Referer": search_url}
    response = session.post(xhr_url, headers=headers, data=data, verify=False, timeout=20)
    return response.text

def build_ics(events: List[Event]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CGA//EN", "METHOD:PUBLISH"]
    for e in events:
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{e.uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID={TZID}:{e.dt_start.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{e.title.replace(',', r'\,')}",
            f"LOCATION:{e.location.replace(',', r'\,')}",
            "END:VEVENT"
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def main():
    start_date = datetime.now().date()
    days_to_pull = 14
    all_events = []

    print(f"[*] Starting CGA Scraper for {days_to_pull} days...")

    for i in range(days_to_pull):
        current = start_date + timedelta(days=i)
        try:
            html = fetch_events_for_day(current)
            day_events = parse_events_from_html(html)
            if day_events:
                all_events.extend(day_events)
            time.sleep(0.5)
        except Exception as e:
            print(f"  [!] Error on {current}: {e}")

    if all_events:
        # Unique events based on UID
        final = sorted({e.uid: e for e in all_events}.values(), key=lambda x: x.dt_start)
        OUTPUT_ICS.write_text(build_ics(final), "utf-8")
        print(f"\n[SUCCESS] Wrote {len(final)} events to {OUTPUT_ICS}")
    else:
        print("\n[FAIL] No events found. Please check if the CGA website is up.")

if __name__ == "__main__":
    main()
