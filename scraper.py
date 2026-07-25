import os
import time
import re
import html
import datetime
import requests
from urllib.parse import urlparse
import gspread

# Dual-import support for DuckDuckGo library variants
try:
    from duckduckgo_search import DDGS
except ImportError:
    from ddgs import DDGS

# ==============================================================================
# --- CONFIGURATION ---
# ==============================================================================
SPREADSHEET_NAME = "Opportunity Spreadsheet"
PENDING_WORKSHEET_NAME = "Pending"
LIVE_WORKSHEET_NAME = "Live Tracker"
CREDENTIALS_FILE = "credentials.json"

# Optional Discord Alerting (Set env var or paste URL directly if desired)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Focused, DuckDuckGo-friendly search queries
SEARCH_QUERIES = [
    'degree apprenticeship STEM Wales',
    'cyber security degree apprenticeship Wales',
    'software engineering apprenticeship Wales',
    'engineering degree apprenticeship South Wales',
    'STEM work experience placement Wales students',
    'STEM bursary scholarship Wales students',
    'higher apprenticeship computing Wales'
]

MAX_RESULTS_PER_QUERY = 8

# Aggregators, social media, ad networks, and generic news sites to block
BLOCKED_DOMAINS = [
    "indeed.com", "reed.co.uk", "totaljobs.com", "glassdoor.co.uk", "cv-library.co.uk",
    "jobsite.co.uk", "simplyhired.co.uk", "adzuna.co.uk", "gradcracker.com", "eventbrite.co.uk",
    "bing.com", "doubleclick.net", "tiktok.com", "instagram.com", "facebook.com", "twitter.com", 
    "x.com", "linkedin.com", "youtube.com", "reddit.com", "medium.com", "wordpress.com", 
    "blogspot.com", "wikipedia.org", "bbc.co.uk", "walesonline.co.uk", "wales247.co.uk",
    "businessnewswales.com", "quora.com", "educanada.ca", "globalscholarships.com",
    "universitycompare.com", "sciencefix.co.uk"
]

JUNK_KEYWORDS_TITLE = [
    "news", "blog", "article", "press-release", "shortlist", "announced", "winners", 
    "calendar", "lesson plan", "jobs available", "internship jobs", "events in", 
    "best engineering apprenticeship jobs", "search vacancies"
]

# Explicit out-of-region location blocklist
EXCLUDED_LOCATIONS = [
    "croydon", "australia", "australian", "north east", "north west", "lancashire", 
    "canada", "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh", 
    "bristol", "newcastle"
]

STANDARD_HEADERS = ["Category", "Title", "Description", "Deadline", "Link", "Colour", "Region", "YearGroup", "Logo"]


# ==============================================================================
# --- DEADLINE EXTRACTION ENGINE ---
# ==============================================================================
def parse_matched_date(m, pattern_type, months_map, current_year):
    """Formats regex match groups into standard DD/MM/YYYY string."""
    groups = m.groups()
    if pattern_type == 'iso':
        year, month, day = groups[0], groups[1], groups[2]
        return f"{int(day):02d}/{int(month):02d}/{year}"
    elif pattern_type == 'uk_num':
        day, month, year = groups[0], groups[1], groups[2]
        if len(year) == 2:
            year = f"20{year}"
        return f"{int(day):02d}/{int(month):02d}/{year}"
    elif pattern_type == 'dmy':
        day = int(groups[0])
        month_str = groups[1][:3].lower()
        month = months_map.get(month_str, '01')
        year = groups[2] if groups[2] else str(current_year)
        return f"{day:02d}/{month}/{year}"
    elif pattern_type == 'mdy':
        month_str = groups[0][:3].lower()
        month = months_map.get(month_str, '01')
        day = int(groups[1])
        year = groups[2] if groups[2] else str(current_year)
        return f"{day:02d}/{month}/{year}"
    return "Rolling"


def extract_deadline(text):
    """
    Context-aware deadline extractor. Prioritizes explicit date triggers
    ('deadline:', 'closing date:') before checking standalone snippet dates.
    """
    if not text:
        return "Rolling"

    text_lower = text.lower()
    current_year = datetime.date.today().year

    months_map = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
        'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
    }

    # Contextual triggers preceding dates
    context_prefix = r'(?:deadline|closing date|apply by|closes|due date|expiry|due|applications close)\s*(?:is|on|by)?\s*:?\s*'

    # Base Regex Patterns
    iso_pattern = r'\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12][0-9]|3[01])\b'
    uk_num_pattern = r'\b(0?[1-9]|[12][0-9]|3[01])[./-](0?[1-9]|1[012])[./-]((?:20)?\d{2})\b'
    
    # Matches "15th November 2026", "15 Nov 2026", "15 November"
    dmy_pattern = r'\b(0?[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s*,?\s*(20\d{2})?\b'
    
    # Matches "November 15th 2026", "Nov 15 2026", "November 15"
    mdy_pattern = r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(0?[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?\s*,?\s*(20\d{2})?\b'

    # --- TIER 1: Check Contextual Trigger Phrases First ---
    for pattern_type, pattern in [
        ('dmy', context_prefix + dmy_pattern),
        ('mdy', context_prefix + mdy_pattern),
        ('uk_num', context_prefix + uk_num_pattern),
        ('iso', context_prefix + iso_pattern),
    ]:
        match = re.search(pattern, text_lower)
        if match:
            return parse_matched_date(match, pattern_type, months_map, current_year)

    # --- TIER 2: Check Standalone Date Expressions Across Full Text ---
    for pattern_type, pattern in [
        ('dmy', dmy_pattern),
        ('mdy', mdy_pattern),
        ('uk_num', uk_num_pattern),
        ('iso', iso_pattern),
    ]:
        match = re.search(pattern, text_lower)
        if match:
            return parse_matched_date(match, pattern_type, months_map, current_year)

    return "Rolling"


# ==============================================================================
# --- SANITIZATION & WELSH RELEVANCE ENGINE ---
# ==============================================================================
def normalize_url(url):
    """Strips protocols, tracking flags, and trailing slashes for duplicate checking."""
    if not url: 
        return ""
    clean = url.split('?')[0].split('#')[0]
    clean = re.sub(r'^https?://', '', clean.lower())
    clean = re.sub(r'^www\.', '', clean)
    return clean.rstrip('/')


def extract_company_logo(url):
    """Generates a high-res favicon image link based on domain."""
    try:
        domain = urlparse(url).netloc
        domain = re.sub(r'^www\.', '', domain)
        if domain:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    except Exception:
        pass
    return ""


def sanitize_text(text):
    """Removes HTML entities and publisher timestamp prefixes."""
    if not text: 
        return ""
    cleaned = html.unescape(text)
    cleaned = re.sub(r'\b(last updated|posted on|published|updated)\s+\b(20\d{2}[-/]\d{2}[-/]\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b', '', cleaned, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', cleaned).strip()


def is_quality_welsh_opportunity(url, title, snippet):
    """Verifies that result is a direct provider link located in Wales or Remote."""
    url_lower = url.lower()
    title_lower = title.lower()
    snippet_lower = snippet.lower()
    combined = f"{title_lower} {snippet_lower}"

    # 1. Block Domain Check
    if any(domain in url_lower for domain in BLOCKED_DOMAINS):
        return False

    # 2. Block Aggregator/Junk Title Check
    if any(junk in title_lower for junk in JUNK_KEYWORDS_TITLE):
        return False

    # 3. Explicit Non-Welsh Location Check
    if any(loc in combined for loc in EXCLUDED_LOCATIONS):
        if not any(w in combined for w in ["wales", "cymru", "remote"]):
            return False

    # 4. Mandatory Welsh / Remote Region Tag Check
    has_welsh_identifier = any(w in combined for w in [
        "wales", "cymru", "cardiff", "swansea", "wrexham", "deeside", "newport", 
        "bangor", "coleg", "usw", "cardiffmet", "uwtsd", "techniquest", "remote"
    ])

    return has_welsh_identifier


def enrich_opportunity_data(title, snippet, url):
    """Categorizes, geotags, extracts deadline, and assigns badge attributes."""
    combined_text = f"{title} {snippet}".lower()

    # Category & Colour Assignment
    if any(k in combined_text for k in ["apprentice", "degree apprenticeship", "earn while you learn"]):
        category, colour = "Apprenticeship", "red"
    elif any(k in combined_text for k in ["insight", "taster", "experience day", "placement", "work experience"]):
        category, colour = "Insight Programme", "blue"
    elif any(k in combined_text for k in ["competition", "hackathon", "challenge"]):
        category, colour = "Competition", "indigo"
    elif any(k in combined_text for k in ["scholarship", "bursary", "grant", "funding"]):
        category, colour = "Scholarship", "amber"
    else:
        category, colour = "Apprenticeship", "red"

    # Region Assignment
    if any(loc in combined_text for loc in ["north wales", "deeside", "wrexham", "bangor", "flintshire"]):
        region = "North Wales"
    elif any(loc in combined_text for loc in ["south wales", "cardiff", "swansea", "newport", "usw", "bridgend"]):
        region = "South Wales"
    elif "remote" in combined_text:
        region = "Remote"
    else:
        region = "Wales"

    # Year Group Target
    if "year 11" in combined_text and "year 12" not in combined_text:
        year_group = "11"
    elif "year 12" in combined_text or "year 13" in combined_text or "a-level" in combined_text:
        year_group = "12, 13"
    else:
        year_group = "11, 12, 13"

    deadline = extract_deadline(combined_text)
    logo_url = extract_company_logo(url)

    return {
        "category": category, "colour": colour, "region": region,
        "year_group": year_group, "deadline": deadline, "logo": logo_url
    }


# ==============================================================================
# --- GOOGLE SHEETS & PIPELINE ENGINE ---
# ==============================================================================
def get_urls_from_sheet(spreadsheet, sheet_name):
    """Loads existing URLs from specified sheet tab to prevent duplicates."""
    urls = set()
    try:
        sheet = spreadsheet.worksheet(sheet_name)
        all_rows = sheet.get_all_values()
        if all_rows:
            header_idx = next((i for i, r in enumerate(all_rows) if r and r[0].strip().lower() == "category"), -1)
            if header_idx != -1:
                headers = [h.lower().strip() for h in all_rows[header_idx]]
                if 'link' in headers:
                    link_idx = headers.index('link')
                    for row in all_rows[header_idx + 1:]:
                        if len(row) > link_idx and row[link_idx].strip():
                            urls.add(normalize_url(row[link_idx].strip()))
    except Exception as e:
        print(f"⚠️ Could not load '{sheet_name}': {e}")
    return urls


def fetch_stem_opportunities(queries, known_urls, max_results=8):
    """Executes search queries, filters results, and enriches data."""
    all_results = []
    seen_urls = set(known_urls)

    print("\n🚀 Starting Precision Search Pipeline...\n")

    for attempt in range(3):
        try:
            with DDGS() as ddgs:
                for query in queries:
                    print(f"🔍 Searching: {query}")
                    try:
                        ddg_results = list(ddgs.text(query, max_results=max_results))
                        added_count = 0
                        for item in ddg_results:
                            url = item.get('href', '').strip()
                            title = sanitize_text(item.get('title', ''))
                            snippet = sanitize_text(item.get('body', ''))
                            norm_url = normalize_url(url)

                            if not norm_url or norm_url in seen_urls:
                                continue

                            if not is_quality_welsh_opportunity(url, title, snippet):
                                print(f"   🚫 Filtered out: {title[:48]}...")
                                continue

                            seen_urls.add(norm_url)
                            enriched = enrich_opportunity_data(title, snippet, url)
                            
                            all_results.append({
                                'category': enriched['category'],
                                'title': title,
                                'description': snippet,
                                'deadline': enriched['deadline'],
                                'link': url,
                                'colour': enriched['colour'],
                                'region': enriched['region'],
                                'year_group': enriched['year_group'],
                                'logo': enriched['logo']
                            })
                            added_count += 1
                        print(f"   ↳ Retained {added_count} verified opportunities.\n")
                    except Exception as e:
                        print(f"   ❌ Query Error: {e}\n")
                    time.sleep(1.2)
            break
        except Exception as network_err:
            print(f"⚠️ Network issue: {network_err}. Retrying... ({attempt+1}/3)")
            time.sleep(5)

    return all_results


def update_pending_sheet(spreadsheet, new_opportunities, live_urls_normalized):
    """Appends verified items to the Pending worksheet without overwriting header layout."""
    try:
        pending_sheet = spreadsheet.worksheet(PENDING_WORKSHEET_NAME)
    except Exception as e:
        print(f"❌ Failed to connect to Pending tab: {e}")
        return

    all_rows = pending_sheet.get_all_values()
    headers = STANDARD_HEADERS
    valid_existing_data = []

    if all_rows:
        header_idx = next((i for i, r in enumerate(all_rows) if r and r[0].strip().lower() == "category"), -1)
        if header_idx != -1:
            headers = all_rows[header_idx]
            if "Logo" not in headers:
                headers.append("Logo")

            link_idx = [h.lower().strip() for h in headers].index('link') if 'link' in [h.lower().strip() for h in headers] else 4
            for row in all_rows[header_idx + 1:]:
                if any(cell.strip() for cell in row):
                    url = normalize_url(row[link_idx].strip()) if len(row) > link_idx else ""
                    if url not in live_urls_normalized:
                        valid_existing_data.append(row)

    new_rows = []
    for item in new_opportunities:
        new_rows.append([
            item['category'], item['title'], item['description'],
            item['deadline'], item['link'], item['colour'],
            item['region'], item['year_group'], item['logo']
        ])

    table_matrix = [headers] + valid_existing_data + new_rows

    pending_sheet.clear()
    try:
        pending_sheet.update(range_name='A1', values=table_matrix)
    except TypeError:
        pending_sheet.update('A1', table_matrix)

    print(f"✨ Success! Saved {len(new_opportunities)} verified items directly to Pending sheet.")


def send_discord_alert(webhook_url, new_opportunities):
    """Sends a Discord embed summary if a webhook URL is configured."""
    if not webhook_url or len(new_opportunities) == 0:
        return

    new_count = len(new_opportunities)
    preview_text = "\n".join([f"• **[{i['title']}]({i['link']})** ({i['category']} - {i['region']})" for i in new_opportunities[:5]])
    if new_count > 5:
        preview_text += f"\n*...and {new_count - 5} more waiting in Pending!*"

    embed = {
        "title": "🏴󠁧󠁢󠁷󠁬󠁳󠁿 STEM Opportunity Pipeline Update",
        "description": f"Found **{new_count} new verified opportunities** requiring review!",
        "color": 3066993,
        "fields": [{"name": "📋 Recent Discoveries", "value": preview_text, "inline": False}]
    }

    try:
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=5)
        print("🔔 Discord notification sent successfully!")
    except Exception as e:
        print(f"⚠️ Failed to send Discord alert: {e}")


# ==============================================================================
# --- MAIN SCRIPT ENTRY ---
# ==============================================================================
if __name__ == "__main__":
    try:
        print("Connecting to Google Sheets...")
        gc = gspread.service_account(filename=CREDENTIALS_FILE)
        spreadsheet = gc.open(SPREADSHEET_NAME)
        print(f"✅ Authenticated: '{SPREADSHEET_NAME}'\n")
        
        live_urls = get_urls_from_sheet(spreadsheet, LIVE_WORKSHEET_NAME)
        pending_urls = get_urls_from_sheet(spreadsheet, PENDING_WORKSHEET_NAME)
        known_urls = live_urls.union(pending_urls)
        
        print(f"📊 Blocklist Status: {len(live_urls)} Live URLs and {len(pending_urls)} Pending URLs.")
        
        new_opps = fetch_stem_opportunities(SEARCH_QUERIES, known_urls, MAX_RESULTS_PER_QUERY)
        update_pending_sheet(spreadsheet, new_opps, live_urls)
        send_discord_alert(DISCORD_WEBHOOK_URL, new_opps)

    except Exception as e:
        print(f"❌ Execution Failed: {e}")
