# Google Maps Lead Scraper

A Python tool that extracts business leads from Google Maps, finds email addresses from business websites, and saves everything to a formatted Google Sheet automatically.

## Features
- **Browser Automation:** Playwright (Chromium) to scrape Google Maps results
- **Email Finder:** BeautifulSoup4 scans business websites for email addresses
- **Google Sheets Integration:** Data is saved in a formatted table with borders and styled headers
- **Manual Trigger:** Interactive CLI — just enter a business type and location to start
- **Sponsored Ad Filtering:** Automatically skips promoted/sponsored listings

## Output Columns

| Column | Description |
|--------|-------------|
| Business Name | Name of the business |
| Business Category / Type | Type of business (e.g. Cafe, Hotel) |
| Phone Number | Contact phone number |
| Website URL | Business website |
| Website Link | Clickable "Visit Website" hyperlink |
| Email Address | Email found on the business website |
| Full Physical Address | Street address |
| Maps | Clickable "View on Maps" link |

## Prerequisites

1. **Google Cloud Platform**:
   - Enable the **Google Sheets API** and **Google Drive API**
   - Create a **Service Account** and download the JSON key as `credentials.json`
   - Share your target Google Sheet with the Service Account email (give it **Editor** access)

2. **Python 3.9+** installed

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
   cd CRM_leads
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install flask playwright beautifulsoup4 gspread gspread-formatting requests gunicorn
   playwright install chromium
   ```

3. **Add your `credentials.json`** (Google Service Account key) to the project root.

## Running the Scraper

**Step 1 — Start the Flask server** in one terminal:
```powershell
$GCP_CRED_JSON = Get-Content -Raw "credentials.json"
$env:GOOGLE_APPLICATION_CREDENTIALS_JSON = $GCP_CRED_JSON
$env:GOOGLE_SHEET_NAME = "YOUR_SHEET_NAME"
python main.py
```

**Step 2 — Run the interactive trigger** in a second terminal:
```bash
python run_scraper.py
```

You will be prompted for:
- **Business Type** (e.g. `hotels`, `cafes`, `plumbers`)
- **Location** (e.g. `Mysore`, `Manhattan, NY`)

Results are saved automatically to your Google Sheet.

## Notes
- Scrapes up to **15 organic results** per run
- If the Google Sheet is empty, a formatted header row is created automatically
- `credentials.json` is excluded from git — never commit it
