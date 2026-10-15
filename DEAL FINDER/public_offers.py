"""Fetch a small allowlist of public merchant pages. Never invent offers from links."""
from html.parser import HTMLParser
from datetime import date
import hashlib
import re
import ssl
import urllib.request
from urllib.parse import urlparse
import certifi

SOURCES = {
    "domino's": ('https://www.dominos.com/deals', 'dominos.com'),
    'doordash': ('https://www.doordash.com/promos/', 'doordash.com'),
    'cvs': ('https://www.cvs.com/shop/beauty/hair-care/shampoo-conditioner/q/All_Deals/pr', 'cvs.com'),
}

def brand_key(brand):
    text = brand.casefold().replace('’', "'").strip()
    if 'domino' in text: return "domino's"
    return text

class OfferHeadings(HTMLParser):
    def __init__(self):
        super().__init__(); self.headings = []; self.details = {}; self.previous = None; self.current = None; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.skip += 1
        if not self.skip and tag == 'h2': self.current = []; self.previous = None
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.skip = max(0, self.skip - 1)
        if tag == 'h2' and self.current is not None:
            title = re.sub(r'\s+', ' ', ''.join(self.current)).strip()
            self.headings.append(title); self.details.setdefault(title, []); self.previous = title; self.current = None
    def handle_data(self, data):
        if not self.skip and self.current is not None: self.current.append(data)
        elif not self.skip and self.previous is not None: self.details[self.previous].append(data)

class MerchantRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, domain): self.domain = domain
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl).hostname or ''
        if urlparse(newurl).scheme != 'https' or not (host == self.domain or host.endswith('.' + self.domain)):
            raise ValueError('Merchant redirect left official domain')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_public_page(url, domain):
    # URLs originate only in SOURCES. Caller input is never fetched.
    opener = urllib.request.build_opener(MerchantRedirect(domain), urllib.request.HTTPSHandler(
        context=ssl.create_default_context(cafile=certifi.where())))
    req = urllib.request.Request(url, headers={'User-Agent': 'DealFinder/1.0 (public offer discovery)', 'Accept': 'text/html'})
    with opener.open(req, timeout=8) as response:
        data = response.read(2_000_001)
        if len(data) > 2_000_000: raise ValueError('Merchant page too large')
        return data.decode('utf-8', errors='replace')

def extract_public_offers(brand, html, today=None):
    key = brand_key(brand); source = SOURCES.get(key)
    if not source: return []
    today = today or date.today(); parser = OfferHeadings(); parser.feed(html)
    results = []; seen = set()
    for title in parser.headings:
        # Concrete prices/discounts only. General promo/category headings are not offers.
        if not re.search(r'\$\d|\d+\s*%\s*off|buy\s+\d.*get', title, re.I): continue
        if not 8 <= len(title) <= 240 or title in seen: continue
        end = None
        full = re.search(r'(?:expires?|through|thru|ends?)\s+(\d{4}-\d{2}-\d{2})', title, re.I)
        span = re.search(r'(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})/(\d{1,2})', title)
        try:
            if full: end = date.fromisoformat(full.group(1))
            elif span:
                start = date(today.year, int(span[1]), int(span[2]))
                end = date(today.year + (int(span[3]) < int(span[1])), int(span[3]), int(span[4]))
                if start > today: continue
            if end and end < today: continue
        except ValueError: continue
        seen.add(title)
        caveat = ('Public national offer; participating locations and prices vary. Choose your local store to confirm.'
                  if key == "domino's" else 'Delivery address, account eligibility, minimum order and fees must be checked in DoorDash.'
                  if key == 'doordash' else 'ExtraCare, selected products and store availability may be required. Confirm current terms.')
        results.append({'id': hashlib.sha256((source[0] + '|' + title).encode()).hexdigest()[:24],
            'title': title, 'summary': re.sub(r'\s+', ' ', ' '.join(parser.details.get(title, []))).strip()[:900] + ' ' + caveat, 'url': source[0], 'source_type': 'delivery' if key == 'doordash' else 'official',
            'source_label': 'Official public offer · local eligibility unconfirmed', 'score': 1.2,
            'published_date': None, 'expiration_date': end.isoformat() if end else None,
            'verification_status': 'unverified', 'caveat': caveat})
    return results[:8]

def public_offers(brand):
    key = brand_key(brand); source = SOURCES.get(key)
    if not source: return [], None
    try:
        result = extract_public_offers(key, fetch_public_page(*source))
        return result, None if result else 'No concrete public offers could be read from this merchant.'
    except Exception:
        return [], 'The merchant’s public offers could not be read. Its site may require sign-in or block automated access.'
