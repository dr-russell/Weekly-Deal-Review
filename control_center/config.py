from pathlib import Path
import os
import json

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DATA = ROOT / "data"
DB = DATA / "deals.sqlite"
ARTIFACTS = DATA / "artifacts"
MANDATE = ROOT / "references/mandate.md"
NEWSLETTER_CONFIG = ROOT / "references/newsletters.json"
GOOGLE_TOKEN = DATA / "secrets/google_write_token.json"
GOOGLE_CLIENT = DATA / "secrets/gmail_credentials.json"
GMAIL_TOKEN = DATA / "secrets/gmail_token.pickle"
MODEL = os.environ.get("DEAL_REVIEW_MODEL", "gpt-6-sol")
EXTRACT_MODEL = os.environ.get("DEAL_EXTRACT_MODEL", MODEL)
VERIFY_MODEL = os.environ.get("DEAL_VERIFY_MODEL", MODEL)
MAX_WORKERS = 3
REVIEW_WORKERS = max(1, min(8, int(os.environ.get("DEAL_REVIEW_WORKERS", "6"))))
BATCH_SIZE = max(1, int(os.environ.get("DEAL_REVIEW_BATCH_SIZE", "22")))
REVIEW_SCHEDULE = os.environ.get("DEAL_REVIEW_SCHEDULE", "streaming")
if REVIEW_SCHEDULE not in {"phased", "streaming"}:
    raise ValueError("DEAL_REVIEW_SCHEDULE must be phased or streaming")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/documents"]
def load_google_settings(path=None):
    path = Path(path) if path is not None else ROOT / 'google.local.json'
    if not path.exists():
        return {'account':'', 'targets':{name:{'sheet':'','doc':''} for name in ['test','production']}}
    settings = json.loads(path.read_text())
    account = settings.get('account', '')
    targets = settings.get('targets', {})
    if not isinstance(account, str) or '@' not in account or account.endswith('@example.com'):
        raise ValueError('Set a real permitted account in control_center/google.local.json')
    for name in ['test', 'production']:
        target = targets.get(name)
        if not isinstance(target, dict) or any(not isinstance(target.get(key), str) for key in ['sheet','doc']):
            raise ValueError('Google local settings require test and production sheet/doc strings')
        if any(value.startswith('YOUR_') for value in target.values()):
            raise ValueError('Replace Google target placeholders in control_center/google.local.json')
    return {'account':account.strip().casefold(), 'targets':targets}


_GOOGLE_SETTINGS = load_google_settings()
GOOGLE_ACCOUNT = _GOOGLE_SETTINGS['account']
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
TARGETS = _GOOGLE_SETTINGS['targets']
HEADERS = ["Current Week?", "Growth or Early", "Deal Date", "Company", "Website", "Round", "Amount Raised", "Investors", "Sector", "Description", "Geography", "Source", "Announcement Link", "Fit?"]
TAXONOMY = ["PropTech", "Construction Tech", "Built World Software", "Real Estate Fintech", "Climate / Building Efficiency", "Smart Buildings", "Infrastructure / Facilities", "AEC Software", "Home Services", "Insurance / Risk for Real Estate", "Logistics / Industrial Real Estate", "Adjacent Built World", "Outside Mandate"]
FIELDS = ["growth_stage", "deal_date", "website", "round", "amount", "investors", "sector", "description", "geography", "announcement_link"]
