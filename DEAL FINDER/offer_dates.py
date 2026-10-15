"""Recognize explicit promotion end dates before discovery, saving or ranking."""
from datetime import datetime, date
import re

MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?'

def offer_expiration(text, today=None):
    today = today or date.today()
    dates = []
    pattern = r'(?:expires?|ends?|through|thru|valid\s+until)\s+(?:on\s+)?('
    for match in re.finditer(pattern + r'\d{4}-\d{2}-\d{2})', text, re.I):
        try: dates.append(date.fromisoformat(match[1]))
        except ValueError: pass
    for match in re.finditer(pattern + MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)', text, re.I):
        raw = re.sub(r'(\d)(?:st|nd|rd|th)', r'\1', match[1], flags=re.I).replace('.', '').replace(',', '')
        raw = re.sub(r'\bSept\b', 'Sep', raw, flags=re.I)
        if not re.search(r'\d{4}', raw): raw += ' ' + str(today.year)
        for fmt in ('%B %d %Y', '%b %d %Y'):
            try: dates.append(datetime.strptime(raw, fmt).date()); break
            except ValueError: pass
    for match in re.finditer(pattern + r'\d{1,2}/\d{1,2}(?:/\d{2,4})?)', text, re.I):
        parts = match[1].split('/')
        try:
            year = int(parts[2]) if len(parts) == 3 else today.year
            if year < 100: year += 2000
            dates.append(date(year, int(parts[0]), int(parts[1])))
        except ValueError: pass
    # If a snippet contains mixed terms, use the earliest explicitly stated end.
    return min(dates).isoformat() if dates else None
