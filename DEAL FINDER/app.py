import hashlib
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components


APP_DIR = Path(__file__).resolve().parent
DB_FILE = os.getenv("DEAL_FINDER_DB", str(APP_DIR / "deals.db"))
RECEIPT_FOLDER = APP_DIR / "receipts"
RECEIPT_FOLDER.mkdir(exist_ok=True)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

STORE_OPTIONS = [
    "CVS",
    "Walgreens",
    "Jack in the Box",
    "Target",
    "Walmart",
    "Safeway",
    "Costco",
    "Other",
]

STORE_SEARCH_CONFIG = {
    "Costco": {
        "domain": "costco.com",
        "terms": '"warehouse savings" member promotions coupon savings',
    },
    "CVS": {
        "domain": "cvs.com",
        "terms": '"ExtraBucks" coupon weekly ad spend get save offer',
    },
    "Walgreens": {
        "domain": "walgreens.com",
        "terms": '"Walgreens Cash" coupon weekly ad spend get save offer',
    },
    "Jack in the Box": {
        "domain": "jackinthebox.com",
        "terms": '"Jack Pack" rewards offer coupon free app deal',
    },
    "Target": {
        "domain": "target.com",
        "terms": '"Target Circle" deal offer coupon save',
    },
    "Walmart": {
        "domain": "walmart.com",
        "terms": 'rollback deal sale save offer',
    },
    "Safeway": {
        "domain": "safeway.com",
        "terms": '"for U" digital coupon weekly ad deal save',
    },
}


# -----------------------------
# Database
# -----------------------------

def get_connection():
    return sqlite3.connect(DB_FILE)


def create_database():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS household_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO household_members (
            name,
            created_at
        )
        VALUES (?, ?)
    """, (
        "Me",
        datetime.now().isoformat(),
    ))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL,
            title TEXT NOT NULL,
            deal_type TEXT,
            discount_value REAL DEFAULT 0,
            minimum_spend REAL DEFAULT 0,
            promo_code TEXT,
            expiration_date TEXT,
            source TEXT,
            notes TEXT,
            used INTEGER DEFAULT 0,
            household_member TEXT DEFAULT 'Me',
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path TEXT NOT NULL,
            store TEXT,
            purchase_date TEXT,
            total REAL DEFAULT 0,
            notes TEXT,
            ocr_text TEXT,
            household_member TEXT DEFAULT 'Me',
            created_at TEXT NOT NULL
        )
    """)

    # Existing databases from earlier versions do not have ocr_text yet.
    # Add it without deleting or recreating the user's receipt data.
    receipt_columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(receipts)"
        ).fetchall()
    }

    if "ocr_text" not in receipt_columns:
        conn.execute(
            "ALTER TABLE receipts ADD COLUMN ocr_text TEXT"
        )

    if "household_member" not in receipt_columns:
        conn.execute(
            "ALTER TABLE receipts "
            "ADD COLUMN household_member TEXT DEFAULT 'Me'"
        )

    deal_columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(deals)"
        ).fetchall()
    }

    if "household_member" not in deal_columns:
        conn.execute(
            "ALTER TABLE deals "
            "ADD COLUMN household_member TEXT DEFAULT 'Me'"
        )

    if "created_at" not in deal_columns:
        conn.execute(
            "ALTER TABLE deals "
            "ADD COLUMN created_at TEXT"
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            price REAL DEFAULT 0,
            quantity REAL DEFAULT 1,
            raw_line TEXT,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_benefits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            benefit_type TEXT,
            value REAL DEFAULT 0,
            percent_value REAL DEFAULT 0,
            description TEXT,
            expiration_date TEXT,
            raw_line TEXT,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS deal_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL UNIQUE,
            source_domain TEXT,
            found_at TEXT NOT NULL,
            last_checked TEXT NOT NULL,
            expiration_date TEXT,
            discount_value REAL DEFAULT 0,
            minimum_spend REAL DEFAULT 0,
            promo_code TEXT,
            deal_type TEXT,
            official_source INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            search_query TEXT,
            household_member TEXT DEFAULT 'Household'
        )
    """)

    candidate_columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(deal_candidates)"
        ).fetchall()
    }

    if "household_member" not in candidate_columns:
        conn.execute(
            "ALTER TABLE deal_candidates "
            "ADD COLUMN household_member TEXT DEFAULT 'Household'"
        )

    conn.commit()
    conn.close()


def get_household_members(include_household=False):
    conn = get_connection()

    rows = conn.execute("""
        SELECT name
        FROM household_members
        ORDER BY
            CASE WHEN name = 'Me' THEN 0 ELSE 1 END,
            name COLLATE NOCASE ASC
    """).fetchall()

    conn.close()

    members = [
        row[0]
        for row in rows
    ]

    if include_household:
        return [
            "Household",
            *members,
        ]

    return members


def add_household_member(name):
    name = " ".join(
        str(name or "").split()
    ).strip()

    if not name:
        return False, "Enter a name."

    if name.lower() == "household":
        return False, "'Household' is reserved for shared offers."

    conn = get_connection()

    try:
        conn.execute("""
            INSERT INTO household_members (
                name,
                created_at
            )
            VALUES (?, ?)
        """, (
            name,
            datetime.now().isoformat(),
        ))

        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"{name} is already in the household."

    conn.close()
    return True, f"Added {name}."


def get_household_summary():
    conn = get_connection()

    summary = pd.read_sql_query("""
        SELECT
            hm.name,
            (
                SELECT COUNT(*)
                FROM deals d
                WHERE d.household_member = hm.name
                  AND d.used = 0
            ) AS active_deals,
            (
                SELECT COUNT(*)
                FROM receipts r
                WHERE r.household_member = hm.name
            ) AS receipts
        FROM household_members hm
        ORDER BY
            CASE WHEN hm.name = 'Me' THEN 0 ELSE 1 END,
            hm.name COLLATE NOCASE ASC
    """, conn)

    conn.close()
    return summary


def remove_household_member(name):
    if name == "Me":
        return False, "The default 'Me' profile cannot be removed."

    conn = get_connection()

    # Preserve all historical data by moving it to Me instead of deleting it.
    conn.execute(
        "UPDATE deals SET household_member = 'Me' WHERE household_member = ?",
        (name,),
    )

    conn.execute(
        "UPDATE receipts SET household_member = 'Me' WHERE household_member = ?",
        (name,),
    )

    conn.execute(
        "UPDATE deal_candidates SET household_member = 'Me' "
        "WHERE household_member = ?",
        (name,),
    )

    conn.execute(
        "DELETE FROM household_members WHERE name = ?",
        (name,),
    )

    conn.commit()
    conn.close()

    return True, (
        f"Removed {name}. Existing deals and receipts were reassigned to Me."
    )


def add_deal(
    store,
    title,
    deal_type,
    discount_value,
    minimum_spend,
    promo_code,
    expiration_date,
    source,
    notes,
    household_member="Me",
):
    conn = get_connection()

    conn.execute("""
        INSERT INTO deals (
            store,
            title,
            deal_type,
            discount_value,
            minimum_spend,
            promo_code,
            expiration_date,
            source,
            notes,
            household_member,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        store,
        title,
        deal_type,
        discount_value,
        minimum_spend,
        promo_code,
        expiration_date,
        source,
        notes,
        household_member,
        datetime.now().isoformat(),
    ))

    conn.commit()
    conn.close()


def get_deals():
    conn = get_connection()

    deals = pd.read_sql_query("""
        SELECT *
        FROM deals
        ORDER BY
            CASE
                WHEN expiration_date IS NULL OR expiration_date = '' THEN 1
                ELSE 0
            END,
            expiration_date ASC
    """, conn)

    conn.close()
    return deals


def get_saved_deals_for_store(store):
    conn = get_connection()

    deals = pd.read_sql_query("""
        SELECT *
        FROM deals
        WHERE store = ?
          AND used = 0
        ORDER BY
            CASE
                WHEN expiration_date IS NULL OR expiration_date = '' THEN 1
                ELSE 0
            END,
            expiration_date ASC,
            id DESC
    """, conn, params=(store,))

    conn.close()
    return deals


def deal_already_saved(store, title, source):
    conn = get_connection()

    row = conn.execute("""
        SELECT id
        FROM deals
        WHERE store = ?
          AND (
                LOWER(title) = LOWER(?)
                OR (
                    source IS NOT NULL
                    AND source != ''
                    AND source = ?
                )
              )
        LIMIT 1
    """, (store, title, source)).fetchone()

    conn.close()
    return row is not None


def mark_used(deal_id):
    conn = get_connection()

    conn.execute(
        "UPDATE deals SET used = 1 WHERE id = ?",
        (deal_id,),
    )

    conn.commit()
    conn.close()


def delete_deal(deal_id):
    conn = get_connection()

    conn.execute(
        "DELETE FROM deals WHERE id = ?",
        (deal_id,),
    )

    conn.commit()
    conn.close()


def add_receipt(
    image_path,
    store,
    purchase_date,
    total,
    notes,
    ocr_text="",
    household_member="Me",
):
    conn = get_connection()

    cursor = conn.execute("""
        INSERT INTO receipts (
            image_path,
            store,
            purchase_date,
            total,
            notes,
            ocr_text,
            household_member,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        image_path,
        store,
        purchase_date,
        total,
        notes,
        ocr_text,
        household_member,
        datetime.now().isoformat(),
    ))

    receipt_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return receipt_id


def get_receipts():
    conn = get_connection()

    receipts = pd.read_sql_query("""
        SELECT *
        FROM receipts
        ORDER BY created_at DESC
    """, conn)

    conn.close()
    return receipts


def get_purchase_history():
    conn = get_connection()

    history = pd.read_sql_query("""
        SELECT
            ri.item_name,
            ri.price,
            ri.quantity,
            r.store,
            r.purchase_date,
            r.household_member,
            r.id AS receipt_id
        FROM receipt_items ri
        JOIN receipts r
          ON r.id = ri.receipt_id
        ORDER BY r.purchase_date DESC, ri.id DESC
    """, conn)

    conn.close()
    return history


def get_receipt_store_history():
    conn = get_connection()

    stores = pd.read_sql_query("""
        SELECT
            store,
            COUNT(*) AS receipt_count,
            MAX(purchase_date) AS last_purchase_date
        FROM receipts
        WHERE store IS NOT NULL
          AND store != ''
          AND store != 'Other'
        GROUP BY store
        ORDER BY receipt_count DESC, last_purchase_date DESC
    """, conn)

    conn.close()
    return stores


def add_receipt_items(receipt_id, items):
    if not items:
        return

    conn = get_connection()

    rows = []

    for item in items:
        name = str(
            item.get("Item", "")
        ).strip()

        if not name:
            continue

        try:
            price = float(
                item.get("Price", 0) or 0
            )
        except (TypeError, ValueError):
            price = 0.0

        try:
            quantity = float(
                item.get("Qty", 1) or 1
            )
        except (TypeError, ValueError):
            quantity = 1.0

        raw_line = str(
            item.get("Raw Line", "")
        ).strip()

        rows.append(
            (
                receipt_id,
                name,
                price,
                quantity,
                raw_line,
            )
        )

    if rows:
        conn.executemany("""
            INSERT INTO receipt_items (
                receipt_id,
                item_name,
                price,
                quantity,
                raw_line
            )
            VALUES (?, ?, ?, ?, ?)
        """, rows)

    conn.commit()
    conn.close()


def get_receipt_items(receipt_id):
    conn = get_connection()

    items = pd.read_sql_query("""
        SELECT
            item_name,
            price,
            quantity,
            raw_line
        FROM receipt_items
        WHERE receipt_id = ?
        ORDER BY id ASC
    """, conn, params=(receipt_id,))

    conn.close()
    return items


def add_receipt_benefits(receipt_id, benefits):
    if not benefits:
        return

    conn = get_connection()
    rows = []

    for benefit in benefits:
        direction = str(
            benefit.get("Direction", "Detected")
        ).strip() or "Detected"

        benefit_type = str(
            benefit.get("Type", "Other")
        ).strip() or "Other"

        try:
            value = float(
                benefit.get("Value", 0) or 0
            )
        except (TypeError, ValueError):
            value = 0.0

        try:
            percent_value = float(
                benefit.get("Percent", 0) or 0
            )
        except (TypeError, ValueError):
            percent_value = 0.0

        description = str(
            benefit.get("Description", "")
        ).strip()

        expiration_date = str(
            benefit.get("Expires", "")
            if benefit.get("Expires", "") is not None
            else ""
        ).strip()

        raw_line = str(
            benefit.get("Raw Line", "")
        ).strip()

        rows.append(
            (
                receipt_id,
                direction,
                benefit_type,
                value,
                percent_value,
                description,
                expiration_date,
                raw_line,
            )
        )

    if rows:
        conn.executemany("""
            INSERT INTO receipt_benefits (
                receipt_id,
                direction,
                benefit_type,
                value,
                percent_value,
                description,
                expiration_date,
                raw_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    conn.commit()
    conn.close()


def get_receipt_benefits(receipt_id):
    conn = get_connection()

    benefits = pd.read_sql_query("""
        SELECT
            direction,
            benefit_type,
            value,
            percent_value,
            description,
            expiration_date,
            raw_line
        FROM receipt_benefits
        WHERE receipt_id = ?
        ORDER BY id ASC
    """, conn, params=(receipt_id,))

    conn.close()
    return benefits


def delete_receipt(receipt_id):
    conn = get_connection()

    row = conn.execute(
        "SELECT image_path FROM receipts WHERE id = ?",
        (receipt_id,),
    ).fetchone()

    conn.execute(
        "DELETE FROM receipt_items WHERE receipt_id = ?",
        (receipt_id,),
    )

    conn.execute(
        "DELETE FROM receipt_benefits WHERE receipt_id = ?",
        (receipt_id,),
    )

    conn.execute(
        "DELETE FROM deal_candidates WHERE url LIKE ?",
        (f"receipt://{receipt_id}/%",),
    )

    conn.execute(
        "DELETE FROM receipts WHERE id = ?",
        (receipt_id,),
    )

    conn.commit()
    conn.close()

    if row:
        image_path = Path(row[0])

        if image_path.exists():
            try:
                image_path.unlink()
            except OSError:
                pass


def upsert_candidate(candidate):
    conn = get_connection()

    conn.execute("""
        INSERT INTO deal_candidates (
            store,
            title,
            description,
            url,
            source_domain,
            found_at,
            last_checked,
            expiration_date,
            discount_value,
            minimum_spend,
            promo_code,
            deal_type,
            official_source,
            status,
            search_query,
            household_member
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            store = excluded.store,
            title = excluded.title,
            description = excluded.description,
            source_domain = excluded.source_domain,
            last_checked = excluded.last_checked,
            expiration_date = excluded.expiration_date,
            discount_value = excluded.discount_value,
            minimum_spend = excluded.minimum_spend,
            promo_code = excluded.promo_code,
            deal_type = excluded.deal_type,
            official_source = excluded.official_source,
            search_query = excluded.search_query,
            household_member = excluded.household_member
    """, (
        candidate["store"],
        candidate["title"],
        candidate["description"],
        candidate["url"],
        candidate["source_domain"],
        candidate["found_at"],
        candidate["last_checked"],
        candidate["expiration_date"],
        candidate["discount_value"],
        candidate["minimum_spend"],
        candidate["promo_code"],
        candidate["deal_type"],
        int(candidate["official_source"]),
        candidate["search_query"],
        candidate.get(
            "household_member",
            "Household",
        ),
    ))

    conn.commit()
    conn.close()


def get_candidates(status=None, stores=None):
    conn = get_connection()

    query = """
        SELECT *
        FROM deal_candidates
        WHERE 1 = 1
    """
    params = []

    if status and status != "all":
        query += " AND status = ?"
        params.append(status)

    if stores:
        placeholders = ",".join("?" for _ in stores)
        query += f" AND store IN ({placeholders})"
        params.extend(stores)

    query += """
        ORDER BY
            official_source DESC,
            last_checked DESC,
            store ASC,
            title ASC
    """

    candidates = pd.read_sql_query(
        query,
        conn,
        params=params,
    )

    conn.close()
    return candidates


def set_candidate_status(candidate_id, status):
    conn = get_connection()

    conn.execute(
        "UPDATE deal_candidates SET status = ? WHERE id = ?",
        (status, candidate_id),
    )

    conn.commit()
    conn.close()


def clear_ignored_candidates():
    conn = get_connection()

    conn.execute(
        "DELETE FROM deal_candidates WHERE status = 'ignored'"
    )

    conn.commit()
    conn.close()


# -----------------------------
# Receipt helpers
# -----------------------------

MERCHANT_KEYWORDS = {
    "CVS": [
        "cvs/pharmacy",
        "cvs pharmacy",
        "cvspharmacy",
        "extracare",
        "cvs",
    ],
    "Walgreens": [
        "walgreens",
        "walgreen",
        "mywalgreens",
    ],
    "Jack in the Box": [
        "jack in the box",
        "jackinthebox",
        "jack pack",
    ],
    "Target": [
        "target",
        "target circle",
    ],
    "Walmart": [
        "walmart",
        "wal-mart",
    ],
    "Safeway": [
        "safeway",
        "safeway for u",
    ],
}


def detect_merchant_from_text(text):
    cleaned = re.sub(
        r"\s+",
        " ",
        text or "",
    ).lower()

    if not cleaned:
        return "Other", 0

    scores = {}

    for store, keywords in MERCHANT_KEYWORDS.items():
        score = 0

        for keyword in keywords:
            occurrences = cleaned.count(
                keyword.lower()
            )

            if not occurrences:
                continue

            # Longer phrases are much stronger evidence than a short
            # standalone word.
            weight = 4 if " " in keyword or "/" in keyword else 2

            score += occurrences * weight

        scores[store] = score

    best_store = max(
        scores,
        key=scores.get,
    )

    best_score = scores[best_store]

    if best_score <= 0:
        return "Other", 0

    return best_store, best_score


def parse_receipt_date_value(value):
    value = (
        value.strip()
        .replace(",", "")
        .replace(".", "/")
    )

    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y/%m/%d",
        "%b/%d/%Y",
        "%B/%d/%Y",
        "%b %d %Y",
        "%B %d %Y",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(
                value,
                fmt,
            ).date()

            # Receipts should not be wildly in the future.
            if parsed > date.today() + timedelta(days=2):
                continue

            # Ignore implausibly old OCR mistakes.
            if parsed < date(2000, 1, 1):
                continue

            return parsed
        except ValueError:
            pass

    return None


def extract_receipt_date(text):
    """
    Find the most likely purchase date from OCR text.

    Dates near the top of the receipt and dates accompanied by a time
    receive a higher score. This helps avoid coupon expiration dates
    printed near the bottom.
    """
    lines = [
        line.strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    date_patterns = [
        r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
        r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",
        (
            r"\b("
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
            r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+\d{1,2}(?:,\s*|\s+)\d{2,4}"
            r")\b"
        ),
    ]

    candidates = []

    for index, line in enumerate(lines):
        lower = line.lower()

        # Expiration / offer lines are unlikely to be the transaction date.
        if any(
            phrase in lower
            for phrase in [
                "expire",
                "valid thru",
                "valid through",
                "offer ends",
                "coupon",
            ]
        ):
            continue

        for pattern in date_patterns:
            for match in re.finditer(
                pattern,
                line,
                flags=re.IGNORECASE,
            ):
                raw = match.group(1)

                normalized = re.sub(
                    r"(?i)(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
                    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
                    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r"\s+(\d{1,2})(?:,\s*|\s+)(\d{2,4})",
                    r"\1 \2 \3",
                    raw,
                )

                parsed = parse_receipt_date_value(
                    normalized
                )

                if not parsed:
                    continue

                score = 0

                # Transaction dates are commonly near the top.
                position_ratio = index / max(
                    len(lines) - 1,
                    1,
                )

                if position_ratio <= 0.25:
                    score += 5
                elif position_ratio <= 0.50:
                    score += 2

                # Date and time printed together is a strong transaction signal.
                if re.search(
                    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?\b",
                    line,
                    flags=re.IGNORECASE,
                ):
                    score += 5

                if any(
                    phrase in lower
                    for phrase in [
                        "date",
                        "purchase",
                        "transaction",
                        "trans",
                    ]
                ):
                    score += 3

                # Prefer dates closest to today when scores tie.
                days_ago = abs(
                    (date.today() - parsed).days
                )

                candidates.append(
                    (
                        score,
                        -days_ago,
                        -index,
                        parsed,
                    )
                )

    if not candidates:
        return None

    candidates.sort(
        reverse=True
    )

    return candidates[0][3]


def extract_money_values(line):
    """
    Return plausible monetary values from one OCR line.
    Requires cents so receipt IDs, phone numbers, dates, etc. are ignored.
    """
    matches = re.findall(
        r"(?<!\d)(?:\$?\s*)"
        r"(\d{1,4}(?:,\d{3})*\.\d{2})"
        r"(?!\d)",
        line,
    )

    values = []

    for value in matches:
        try:
            number = float(
                value.replace(",", "")
            )
        except ValueError:
            continue

        if 0 <= number <= 10000:
            values.append(
                number
            )

    return values


def extract_receipt_total(text):
    """
    Find the amount actually paid / due.

    Strong total labels beat subtotal, tax, savings, tender and change lines.
    When several total-like lines exist, the best label and later receipt
    position are used.
    """
    lines = [
        line.strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    candidates = []

    strong_labels = [
        "grand total",
        "amount due",
        "balance due",
        "total due",
        "order total",
        "sale total",
        "net total",
        "total",
    ]

    reject_labels = [
        "subtotal",
        "sub total",
        "total savings",
        "savings total",
        "tax",
        "change",
        "cash back",
        "cashback",
        "tender",
        "payment",
        "visa",
        "mastercard",
        "amex",
        "discover",
        "gift card",
        "you saved",
        "rewards earned",
        "reward earned",
    ]

    for index, line in enumerate(lines):
        lower = re.sub(
            r"\s+",
            " ",
            line.lower(),
        )

        values = extract_money_values(
            line
        )

        if not values:
            continue

        if any(
            label in lower
            for label in reject_labels
        ):
            # A plain "TOTAL" line containing "tax" is usually not the amount paid.
            continue

        score = 0

        if "grand total" in lower:
            score += 15
        elif "amount due" in lower:
            score += 14
        elif "balance due" in lower:
            score += 14
        elif "total due" in lower:
            score += 13
        elif "order total" in lower:
            score += 12
        elif "sale total" in lower:
            score += 12
        elif "net total" in lower:
            score += 11
        elif re.search(
            r"\btotal\b",
            lower,
        ):
            score += 10
        else:
            continue

        # Totals are commonly in the lower half of a receipt.
        position_ratio = index / max(
            len(lines) - 1,
            1,
        )

        if position_ratio >= 0.45:
            score += 2

        # The final monetary value on a total line is usually the one we want.
        value = values[-1]

        candidates.append(
            (
                score,
                index,
                value,
            )
        )

    if candidates:
        candidates.sort(
            reverse=True
        )

        return candidates[0][2]

    # Conservative fallback: look for "TOTAL" on one line and the value on
    # the immediately following line. OCR sometimes splits them.
    for index, line in enumerate(lines[:-1]):
        lower = line.lower()

        if (
            re.search(r"\btotal\b", lower)
            and "subtotal" not in lower
            and "savings" not in lower
        ):
            next_values = extract_money_values(
                lines[index + 1]
            )

            if next_values:
                return next_values[-1]

    return None


def looks_like_product_name(text):
    cleaned = re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip(" -*#")

    if len(cleaned) < 2:
        return False

    if not re.search(
        r"[A-Za-z]",
        cleaned,
    ):
        return False

    lower = cleaned.lower()

    blocked_phrases = [
        "subtotal",
        "sub total",
        "grand total",
        "amount due",
        "balance due",
        "total due",
        "total savings",
        "you saved",
        "tax",
        "change",
        "tender",
        "payment",
        "visa",
        "mastercard",
        "amex",
        "discover",
        "debit",
        "credit",
        "cash",
        "coupon",
        "promo",
        "discount",
        "extrabucks",
        "walgreens cash",
        "rewards earned",
        "reward earned",
        "points earned",
        "member savings",
        "loyalty",
        "receipt",
        "transaction",
        "approval",
        "approved",
        "auth code",
        "reference",
        "store #",
        "register",
        "cashier",
        "thank you",
        "survey",
        "www.",
        "http",
        "phone",
    ]

    if any(
        phrase in lower
        for phrase in blocked_phrases
    ):
        return False

    # Avoid lines that are mostly identifiers / phone numbers / addresses.
    letters = len(
        re.findall(
            r"[A-Za-z]",
            cleaned,
        )
    )

    digits = len(
        re.findall(
            r"\d",
            cleaned,
        )
    )

    if letters < 2:
        return False

    if digits > letters * 2 and letters < 5:
        return False

    return True


def clean_item_name(text):
    cleaned = re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip(" -*#")

    # Remove common trailing register flags that OCR may include.
    cleaned = re.sub(
        r"\s+[FTNX]\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    # Remove obvious SKU-like prefixes while keeping real product numbers
    # that are part of the product name.
    cleaned = re.sub(
        r"^\d{6,}\s+",
        "",
        cleaned,
    ).strip()

    return cleaned[:160]


def parse_quantity_from_line(line):
    patterns = [
        r"\b(\d+(?:\.\d+)?)\s*@\s*\$?\d+(?:\.\d{2})",
        r"\bqty\s*[:x]?\s*(\d+(?:\.\d+)?)\b",
        r"^\s*(\d+(?:\.\d+)?)\s+x\s+",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            line,
            flags=re.IGNORECASE,
        )

        if match:
            try:
                value = float(
                    match.group(1)
                )

                if 0 < value <= 99:
                    return value
            except ValueError:
                pass

    return 1.0


def extract_receipt_items(text):
    """
    Convert OCR lines into candidate purchased items.

    This intentionally favors precision over recall: it is better to miss
    an unusual line than to save tax, totals, coupon values, or card data
    as purchased products.
    """
    lines = [
        re.sub(
            r"\s+",
            " ",
            line or "",
        ).strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]

    items = []
    seen = set()

    # A price at the end of the OCR line is the strongest generic signal.
    price_at_end = re.compile(
        r"^(.*?)"
        r"\s+\$?\s*"
        r"(\d{1,4}(?:,\d{3})*\.\d{2})"
        r"\s*[FTNX]?\s*$",
        flags=re.IGNORECASE,
    )

    only_price = re.compile(
        r"^\$?\s*"
        r"(\d{1,4}(?:,\d{3})*\.\d{2})"
        r"\s*[FTNX]?\s*$",
        flags=re.IGNORECASE,
    )

    for index, line in enumerate(lines):
        match = price_at_end.match(
            line
        )

        item_name = ""
        raw_price = ""
        raw_line = line

        if match:
            item_name = clean_item_name(
                match.group(1)
            )

            raw_price = match.group(2)

            # Some OCR lines are "2 @ 3.99 7.98" and the real product
            # description is directly above.
            if not looks_like_product_name(
                item_name
            ):
                if index > 0 and looks_like_product_name(
                    lines[index - 1]
                ):
                    item_name = clean_item_name(
                        lines[index - 1]
                    )
                else:
                    continue

        else:
            price_match = only_price.match(
                line
            )

            if (
                price_match
                and index > 0
                and looks_like_product_name(
                    lines[index - 1]
                )
            ):
                item_name = clean_item_name(
                    lines[index - 1]
                )

                raw_price = price_match.group(1)

                raw_line = (
                    f"{lines[index - 1]} | {line}"
                )
            else:
                continue

        if not looks_like_product_name(
            item_name
        ):
            continue

        try:
            price = float(
                raw_price.replace(",", "")
            )
        except ValueError:
            continue

        # Zero-priced lines are commonly coupons, rewards, or voided items.
        if price <= 0 or price > 5000:
            continue

        quantity = parse_quantity_from_line(
            line
        )

        normalized_key = (
            item_name.lower(),
            round(price, 2),
            round(quantity, 3),
        )

        if normalized_key in seen:
            continue

        seen.add(
            normalized_key
        )

        items.append(
            {
                "Include": True,
                "Item": item_name,
                "Price": round(price, 2),
                "Qty": quantity,
                "Raw Line": raw_line,
            }
        )

    return items


def extract_benefit_money(text):
    patterns = [
        r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)",
        r"(?<!\d)(\d{1,4}\.\d{2})\s*-?\s*(?:coupon|reward|rewards|off|savings|cash)\b",
        r"(?:coupon|reward|rewards|off|savings|cash)\D{0,12}(\d{1,4}\.\d{2})\s*-?",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            try:
                value = float(
                    match.group(1).replace(",", "")
                )

                if 0 < value <= 10000:
                    return value
            except ValueError:
                pass

    return 0.0


def extract_benefit_percent(text):
    match = re.search(
        r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%\s*(?:off|discount|savings?)?",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return 0.0

    try:
        value = float(
            match.group(1)
        )
    except ValueError:
        return 0.0

    if 0 < value <= 100:
        return value

    return 0.0


def benefit_type_from_text(text):
    lower = text.lower()

    if (
        "extrabucks" in lower
        or "walgreens cash" in lower
        or "cash rewards" in lower
        or "reward" in lower
        or "rewards" in lower
        or "target circle earnings" in lower
        or "jack pack" in lower
    ):
        return "Store Reward"

    if (
        "promo code" in lower
        or "coupon code" in lower
        or "use code" in lower
    ):
        return "Promo Code"

    if "coupon" in lower or "mfr" in lower or "manufacturer" in lower:
        return "Coupon"

    if "cashback" in lower or "cash back" in lower:
        return "Cashback"

    if "free" in lower:
        return "Free Item"

    if (
        "discount" in lower
        or "savings" in lower
        or "% off" in lower
        or " off " in f" {lower} "
    ):
        return "Discount"

    return "Other"


def benefit_direction_from_text(text, benefit_type, expiration_date=""):
    lower = text.lower()

    used_cues = [
        "redeemed",
        "redemption",
        "applied",
        "used",
        "coupon savings",
        "manufacturer coupon",
        "mfr coupon",
        "extrabucks used",
        "extrabucks redeemed",
        "rewards redeemed",
        "reward redeemed",
        "walgreens cash redeemed",
        "walgreens cash used",
    ]

    earned_cues = [
        "earned",
        "you earned",
        "reward earned",
        "rewards earned",
        "cash earned",
        "issued",
        "you received",
        "reward received",
        "available on your next",
        "next purchase",
        "use on your next",
        "redeem by",
    ]

    if any(
        cue in lower
        for cue in earned_cues
    ):
        return "Earned"

    if any(
        cue in lower
        for cue in used_cues
    ):
        return "Used"

    # A minus sign at either side of an amount commonly means the coupon
    # or reward was applied to this transaction.
    if re.search(
        r"(?:-\s*\$?\s*\d+(?:\.\d{1,2})?|\$?\s*\d+(?:\.\d{1,2})?\s*-)",
        text,
    ):
        return "Used"

    # Printed rewards/coupons with a future expiration date are usually
    # something the shopper can use later, as long as the line does not
    # explicitly say it was redeemed.
    if (
        expiration_date
        and benefit_type in {
            "Store Reward",
            "Coupon",
            "Promo Code",
            "Free Item",
        }
    ):
        return "Earned"

    # Generic coupon lines on the transaction section are generally used.
    if benefit_type == "Coupon":
        return "Used"

    return "Detected"


def make_benefit_description(store, text, benefit_type, value, percent_value):
    lower = text.lower()

    if "extrabucks" in lower:
        return (
            f"{money_text(value)} CVS ExtraBucks"
            if value
            else "CVS ExtraBucks"
        )

    if "walgreens cash" in lower:
        return (
            f"{money_text(value)} Walgreens Cash"
            if value
            else "Walgreens Cash"
        )

    if "target circle" in lower:
        return (
            f"{money_text(value)} Target Circle reward"
            if value
            else "Target Circle reward"
        )

    if "jack pack" in lower:
        return (
            f"{money_text(value)} Jack Pack reward"
            if value
            else "Jack Pack reward"
        )

    if benefit_type == "Coupon":
        if percent_value:
            return f"{percent_value:g}% off coupon"

        if value:
            return f"{money_text(value)} coupon"

        return f"{store} coupon"

    if benefit_type == "Discount":
        if percent_value:
            return f"{percent_value:g}% discount"

        if value:
            return f"{money_text(value)} discount"

    if benefit_type == "Cashback":
        return (
            f"{money_text(value)} cashback"
            if value
            else "Cashback"
        )

    if benefit_type == "Free Item":
        return "Free item offer"

    concise = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return concise[:140]


def extract_receipt_benefits(text, store="Other"):
    """
    Detect coupons, rewards, savings and future-use benefits from OCR text.

    The parser uses a small context window around each matching line so a
    reward amount and its expiration date can be joined even when OCR places
    them on separate lines.
    """
    lines = [
        re.sub(
            r"\s+",
            " ",
            line or "",
        ).strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]

    if not lines:
        return []

    cue_pattern = re.compile(
        r"\b("
        r"coupon|coupons|mfr|manufacturer|discount|savings?|"
        r"extrabucks|extra bucks|walgreens cash|cash rewards?|"
        r"reward|rewards|cashback|cash back|target circle|"
        r"jack pack|promo code|coupon code|free item|"
        r"you earned|earned this visit|redeemed"
        r")\b",
        flags=re.IGNORECASE,
    )

    benefits = []
    seen = set()

    for index, line in enumerate(lines):
        if not cue_pattern.search(line):
            continue

        window_start = max(
            0,
            index - 1,
        )

        window_end = min(
            len(lines),
            index + 3,
        )

        context = " | ".join(
            lines[window_start:window_end]
        )

        benefit_type = benefit_type_from_text(
            context
        )

        value = extract_benefit_money(
            context
        )

        percent_value = extract_benefit_percent(
            context
        )

        expiration_date = extract_expiration_date(
            context
        )

        direction = benefit_direction_from_text(
            context,
            benefit_type,
            expiration_date,
        )

        # Avoid low-information "savings" headings unless an amount,
        # percentage, or specific reward/coupon type is present.
        if (
            value <= 0
            and percent_value <= 0
            and benefit_type in {
                "Other",
                "Discount",
            }
        ):
            continue

        description = make_benefit_description(
            store,
            context,
            benefit_type,
            value,
            percent_value,
        )

        key = (
            direction.lower(),
            benefit_type.lower(),
            round(value, 2),
            round(percent_value, 2),
            expiration_date,
            description.lower(),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        benefits.append(
            {
                "Include": True,
                "Direction": direction,
                "Type": benefit_type,
                "Value": round(value, 2),
                "Percent": round(percent_value, 2),
                "Description": description,
                "Expires": expiration_date,
                "Raw Line": line,
            }
        )

    return benefits


def normalize_benefit_expiration(value):
    value = str(
        value or ""
    ).strip()

    if not value:
        return ""

    try:
        return datetime.fromisoformat(
            value
        ).date().isoformat()
    except ValueError:
        pass

    parsed = parse_date_candidate(
        value
    )

    return (
        parsed.isoformat()
        if parsed
        else value
    )


def receipt_candidate_title(store, benefit):
    value = float(
        benefit.get("Value", 0) or 0
    )

    percent_value = float(
        benefit.get("Percent", 0) or 0
    )

    benefit_type = str(
        benefit.get("Type", "Other")
    ).strip()

    description = str(
        benefit.get("Description", "")
    ).strip()

    if description:
        return description[:300]

    if percent_value:
        return (
            f"{percent_value:g}% off {benefit_type.lower()} at {store}"
        )[:300]

    if value:
        return (
            f"{money_text(value)} {benefit_type.lower()} at {store}"
        )[:300]

    return f"{store} {benefit_type}"[:300]


def create_receipt_deal_candidates(
    receipt_id,
    store,
    benefits,
    household_member="Me",
):
    created = 0
    now = datetime.now().isoformat()

    for index, benefit in enumerate(benefits):
        direction = str(
            benefit.get("Direction", "")
        ).strip()

        if direction != "Earned":
            continue

        value = float(
            benefit.get("Value", 0) or 0
        )

        percent_value = float(
            benefit.get("Percent", 0) or 0
        )

        description = str(
            benefit.get("Description", "")
        ).strip()

        # Do not create a future offer from a completely empty/ambiguous row.
        if not description and value <= 0 and percent_value <= 0:
            continue

        benefit_type = str(
            benefit.get("Type", "Other")
        ).strip() or "Other"

        expiration_date = normalize_benefit_expiration(
            benefit.get("Expires", "")
        )

        raw_line = str(
            benefit.get("Raw Line", "")
        ).strip()

        details = (
            f"Detected from scanned {store} receipt #{receipt_id}. "
            f"{description}"
        ).strip()

        if percent_value:
            details += (
                f" Detected percentage: {percent_value:g}%."
            )

        if raw_line:
            details += (
                f" Receipt text: {raw_line}"
            )

        candidate = {
            "store": store,
            "title": receipt_candidate_title(
                store,
                benefit,
            ),
            "description": details[:2000],
            "url": (
                f"receipt://{receipt_id}/benefit/{index}"
            ),
            "source_domain": "Scanned receipt",
            "found_at": now,
            "last_checked": now,
            "expiration_date": expiration_date,
            "discount_value": value,
            "minimum_spend": 0.0,
            "promo_code": extract_promo_code(
                raw_line
            ),
            "deal_type": (
                "Store Reward"
                if benefit_type == "Store Reward"
                else (
                    "Coupon"
                    if benefit_type in {
                        "Coupon",
                        "Discount",
                    }
                    else benefit_type
                )
            ),
            "official_source": False,
            "search_query": "receipt scan",
            "household_member": household_member,
        }

        upsert_candidate(
            candidate
        )

        created += 1

    return created


def run_apple_vision_ocr(image_bytes, suffix=".jpg"):
    """
    Read receipt text locally using Apple's Vision framework.

    The Python app invokes a tiny temporary Swift program. This keeps
    receipt images on the Mac and does not send them to an OCR service.
    """
    swift_path = shutil.which(
        "swift"
    )

    if not swift_path:
        return "", (
            "Swift was not found. Open Xcode once, or install/select "
            "the Xcode Command Line Tools, then try again."
        )

    swift_program = r"""
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count >= 2 else {
    fputs("Missing image path\n", stderr)
    exit(2)
}

let imagePath = CommandLine.arguments[1]

guard let image = NSImage(contentsOfFile: imagePath) else {
    fputs("Could not open receipt image\n", stderr)
    exit(3)
}

var proposedRect = NSRect(
    origin: .zero,
    size: image.size
)

guard let cgImage = image.cgImage(
    forProposedRect: &proposedRect,
    context: nil,
    hints: nil
) else {
    fputs("Could not convert receipt image\n", stderr)
    exit(4)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["en-US"]

if #available(macOS 13.0, *) {
    request.automaticallyDetectsLanguage = true
}

let handler = VNImageRequestHandler(
    cgImage: cgImage,
    options: [:]
)

do {
    try handler.perform([request])
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(5)
}

guard let observations = request.results else {
    print("")
    exit(0)
}

let sorted = observations.sorted { left, right in
    let leftY = left.boundingBox.midY
    let rightY = right.boundingBox.midY

    if abs(leftY - rightY) > 0.02 {
        return leftY > rightY
    }

    return left.boundingBox.minX < right.boundingBox.minX
}

let lines = sorted.compactMap { observation in
    observation.topCandidates(1).first?.string
}

print(lines.joined(separator: "\n"))
"""

    with tempfile.TemporaryDirectory(
        prefix="deal_finder_ocr_"
    ) as temp_dir:
        temp_dir = Path(temp_dir)

        image_path = temp_dir / (
            "receipt" + suffix
        )

        script_path = temp_dir / "receipt_ocr.swift"

        image_path.write_bytes(
            image_bytes
        )

        script_path.write_text(
            swift_program,
            encoding="utf-8",
        )

        try:
            result = subprocess.run(
                [
                    swift_path,
                    str(script_path),
                    str(image_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return "", (
                "Receipt reading timed out. Try a clearer, "
                "closer photo of the receipt."
            )
        except OSError as exc:
            return "", (
                f"Could not start Apple Vision OCR: {exc}"
            )

    if result.returncode != 0:
        error_message = (
            result.stderr.strip()
            or "Apple Vision could not read this receipt."
        )

        return "", error_message

    text = result.stdout.strip()

    if not text:
        return "", (
            "No text was recognized. Try retaking the photo "
            "with the receipt flat, well lit, and filling the frame."
        )

    return text, ""


def receipt_image_suffix(uploaded_file):
    mime_type = (
        getattr(
            uploaded_file,
            "type",
            "",
        )
        or ""
    ).lower()

    if "png" in mime_type:
        return ".png"

    if "heic" in mime_type:
        return ".heic"

    return ".jpg"


def receipt_fingerprint(uploaded_file):
    return hashlib.sha256(
        uploaded_file.getvalue()
    ).hexdigest()


def save_receipt_image(uploaded_file):
    extension = receipt_image_suffix(
        uploaded_file
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = RECEIPT_FOLDER / f"receipt_{timestamp}{extension}"

    with open(filename, "wb") as file:
        file.write(uploaded_file.getbuffer())

    return str(filename)


# -----------------------------
# Deal search helpers
# -----------------------------

def get_source_domain(url):
    try:
        domain = urlparse(url).netloc.lower()
    except ValueError:
        return ""

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def is_official_domain(result_domain, official_domain):
    result_domain = (result_domain or "").lower()
    official_domain = (official_domain or "").lower()

    return (
        result_domain == official_domain
        or result_domain.endswith("." + official_domain)
    )


def build_search_query(
    store,
    official_only,
    custom_keywords,
):
    config = STORE_SEARCH_CONFIG[store]
    terms = config["terms"]

    if custom_keywords.strip():
        terms = (
            f"{custom_keywords.strip()} "
            f"{terms}"
        )

    # Tavily can restrict results by domain separately, so the query
    # itself stays natural rather than relying on site: syntax.
    return f'"{store}" {terms}'


def tavily_web_search(
    api_key,
    query,
    count=10,
    time_range="month",
    include_domains=None,
):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": count,
        "topic": "general",
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "country": "united states",
        "language": "en",
        "safe_search": True,
    }

    if time_range:
        payload["time_range"] = time_range

    if include_domains:
        payload["include_domains"] = include_domains
        payload["include_domains_mode"] = "restrict"

    response = requests.post(
        TAVILY_SEARCH_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Tavily rejected the API key. Check that the key is correct."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Tavily rate limit reached. Wait a little and try again."
        )

    if response.status_code in (432, 433):
        raise RuntimeError(
            "Tavily says the account has reached its usage limit."
        )

    if not response.ok:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None

        if detail:
            raise RuntimeError(
                f"Tavily search failed: {detail}"
            )

        response.raise_for_status()

    data = response.json()
    return data.get("results", [])


def clean_number(number_text):
    try:
        return float(
            str(number_text)
            .replace(",", "")
            .replace("$", "")
            .strip()
        )
    except (AttributeError, ValueError):
        return 0.0


def money_text(value):
    value = float(value or 0)

    if value.is_integer():
        return f"${int(value)}"

    return f"${value:.2f}"


def normalize_space(text):
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()


def extract_discount_value(text):
    # Retailer snippets sometimes omit the dollar sign:
    # "10 ExtraBucks Rewards for spending 30".
    patterns = [
        r"\$(\d+(?:\.\d{1,2})?)\s*(?:off|discount|back|reward|rewards|cash)",
        r"(?:save|get|earn|receive)\s+\$(\d+(?:\.\d{1,2})?)",
        r"\$?(\d+(?:\.\d{1,2})?)\s+ExtraBucks(?:\s+Rewards?)?",
        r"\$?(\d+(?:\.\d{1,2})?)\s+Walgreens\s+Cash(?:\s+rewards?)?",
        r"(?:get|earn|receive)\s+\$?(\d+(?:\.\d{1,2})?)\s+(?:in\s+)?(?:rewards?|cash)",
        r"(?:coupon|reward)\s+(?:for|of)\s+\$(\d+(?:\.\d{1,2})?)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return clean_number(
                match.group(1)
            )

    return 0.0


def extract_minimum_spend(text):
    # Handles both "$30" and retailer snippets like "spending 30".
    patterns = [
        r"\$\d+(?:\.\d{1,2})?\s+off\s+(?:a\s+)?\$(\d+(?:\.\d{1,2})?)",
        r"(?:when\s+you\s+spend|spend|spending)\s+\$?(\d+(?:\.\d{1,2})?)",
        r"(?:on\s+)?purchases?\s+(?:of|over)\s+\$?(\d+(?:\.\d{1,2})?)",
        r"\$(\d+(?:\.\d{1,2})?)\s+(?:purchase|required|min(?:imum)?)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return clean_number(
                match.group(1)
            )

    return 0.0


def extract_percent_discount(text):
    match = re.search(
        r"(\d{1,2}(?:\.\d+)?)\s*%\s*off",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return 0.0

    return clean_number(
        match.group(1)
    )


def extract_promo_code(text):
    patterns = [
        r"(?:promo\s+code|coupon\s+code|use\s+code|code)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{3,19})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            promo = match.group(1).upper()

            blocked_words = {
                "SAVE",
                "DEAL",
                "SALE",
                "COUPON",
                "OFFER",
                "REWARD",
                "REWARDS",
                "DETAILS",
            }

            if promo not in blocked_words:
                return promo

    return ""


def parse_date_candidate(date_text):
    date_text = date_text.strip().replace(",", "")

    formats_with_year = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%B %d %Y",
        "%b %d %Y",
    ]

    for fmt in formats_with_year:
        try:
            return datetime.strptime(
                date_text,
                fmt,
            ).date()
        except ValueError:
            pass

    formats_without_year = [
        "%m/%d",
        "%m-%d",
        "%B %d",
        "%b %d",
    ]

    current_year = date.today().year

    for fmt in formats_without_year:
        try:
            parsed = datetime.strptime(
                date_text,
                fmt,
            ).date()

            candidate = parsed.replace(
                year=current_year
            )

            if candidate < date.today() - timedelta(days=60):
                candidate = candidate.replace(
                    year=current_year + 1
                )

            return candidate
        except ValueError:
            pass

    return None


def extract_expiration_date(text):
    trigger_pattern = (
        r"(?:expires?|exp\.?|ends?|valid\s+(?:through|until)|"
        r"through|until)\s*[:\-]?\s*"
    )

    date_pattern = (
        r"("
        r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
        r"|"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
        r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2}(?:,\s*\d{4})?"
        r")"
    )

    match = re.search(
        trigger_pattern + date_pattern,
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return ""

    parsed = parse_date_candidate(
        match.group(1)
    )

    return parsed.isoformat() if parsed else ""


def infer_deal_type(text):
    lower = text.lower()

    if (
        "promo code" in lower
        or "coupon code" in lower
        or "use code" in lower
    ):
        return "Promo Code"

    if (
        "extrabucks" in lower
        or "walgreens cash" in lower
        or re.search(
            r"\b(?:earn|get|receive)\b.{0,30}\brewards?\b",
            lower,
        )
    ):
        return "Store Reward"

    if (
        "cashback" in lower
        or "cash back" in lower
    ):
        return "Cashback"

    if re.search(
        r"\bfree\b",
        lower,
    ):
        return "Free Item"

    if "coupon" in lower:
        return "Coupon"

    if (
        "sale" in lower
        or "discount" in lower
        or "% off" in lower
        or "save" in lower
        or "rollback" in lower
        or "buy one" in lower
    ):
        return "Sale"

    return "Other"


def deal_signal_score(text):
    lower = normalize_space(text).lower()
    score = 0

    strong_patterns = [
        r"\$\d+(?:\.\d{1,2})?\s+off\b",
        r"\d{1,2}(?:\.\d+)?\s*%\s*off\b",
        r"\b(?:save|get|earn|receive)\s+\$\d+",
        r"\bbuy\s+\d+\s+get\s+\d+",
        r"\bbuy\s+one\s+get\s+one",
        r"\bbogo\b",
        r"\bfree\b.{0,35}\b(?:purchase|order|buy|with)\b",
        r"\b(?:promo|coupon)\s+code\b",
        r"\b(?:spend|spending|when you spend)\s+\$?\d+",
    ]

    for pattern in strong_patterns:
        if re.search(
            pattern,
            lower,
            flags=re.IGNORECASE,
        ):
            score += 3

    if re.search(
        r"\$?\d+(?:\.\d{1,2})?\s+extrabucks",
        lower,
        flags=re.IGNORECASE,
    ):
        score += 4

    if re.search(
        r"\$?\d+(?:\.\d{1,2})?\s+walgreens\s+cash",
        lower,
        flags=re.IGNORECASE,
    ):
        score += 4

    if "coupon" in lower:
        score += 1

    if "weekly ad" in lower:
        score += 1

    if "offer" in lower:
        score += 1

    if "reward" in lower:
        score += 1

    if "sale" in lower or "rollback" in lower:
        score += 1

    return score


def looks_like_deal(text):
    # Requiring a real deal signal keeps ordinary product pages out.
    return deal_signal_score(
        text
    ) >= 3


def split_sentences(text):
    text = normalize_space(text)

    if not text:
        return []

    pieces = re.split(
        r"(?<=[.!?])\s+|\s+[|•]\s+",
        text,
    )

    return [
        piece.strip(" -–—")
        for piece in pieces
        if piece.strip(" -–—")
    ]


def best_deal_sentence(title, description):
    candidates = (
        split_sentences(description)
        + split_sentences(title)
    )

    if not candidates:
        return normalize_space(
            title or description
        )

    best = max(
        candidates,
        key=deal_signal_score,
    )

    if deal_signal_score(best) < 3:
        return normalize_space(
            description or title
        )

    return best


def extract_item_after_spend(text):
    patterns = [
        r"(?:spend|spending|when you spend)\s+\$?\d+(?:\.\d{1,2})?\s+on\s+(.+?)(?:\.|;|$)",
        r"(?:purchase|buy)\s+(.+?)\s+(?:and\s+)?(?:get|earn|receive)\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            item = normalize_space(
                match.group(1)
            )

            item = re.sub(
                r"\b(?:see eligible products|details|shop now)\b.*$",
                "",
                item,
                flags=re.IGNORECASE,
            ).strip(" ,.-")

            if 2 <= len(item) <= 110:
                return item

    return ""


def make_candidate_title(
    store,
    original_title,
    description,
    discount_value,
    minimum_spend,
):
    combined = normalize_space(
        f"{original_title}. {description}"
    )

    item = extract_item_after_spend(
        description
    )

    if "extrabucks" in combined.lower() and discount_value:
        if minimum_spend:
            title = (
                f"Spend {money_text(minimum_spend)}"
            )

            if item:
                title += f" on {item}"

            title += (
                f" — get {money_text(discount_value)} ExtraBucks"
            )

            return title[:300]

        return (
            f"Get {money_text(discount_value)} ExtraBucks"
        )[:300]

    if "walgreens cash" in combined.lower() and discount_value:
        if minimum_spend:
            return (
                f"Spend {money_text(minimum_spend)}"
                f" — get {money_text(discount_value)} Walgreens Cash"
            )[:300]

        return (
            f"Get {money_text(discount_value)} Walgreens Cash"
        )[:300]

    percent = extract_percent_discount(
        combined
    )

    if percent:
        sentence = best_deal_sentence(
            original_title,
            description,
        )

        if sentence and len(sentence) <= 180:
            return sentence[:300]

        return (
            f"{percent:g}% off at {store}"
        )[:300]

    sentence = best_deal_sentence(
        original_title,
        description,
    )

    if (
        sentence
        and deal_signal_score(sentence) >= 3
    ):
        return sentence[:300]

    return normalize_space(
        original_title
    )[:300]


def result_to_candidate(
    result,
    store,
    query,
):
    original_title = (
        result.get("title")
        or "Untitled result"
    ).strip()

    description = (
        result.get("content")
        or result.get("description")
        or ""
    ).strip()

    url = (
        result.get("url")
        or ""
    ).strip()

    if not url:
        return None

    # Retailer search results often use a product name as the page title,
    # while the useful promotion lives in the result snippet.
    combined = normalize_space(
        f"{description}. {original_title}"
    )

    if not looks_like_deal(
        combined
    ):
        return None

    config = STORE_SEARCH_CONFIG[store]
    source_domain = get_source_domain(
        url
    )

    discount_value = extract_discount_value(
        combined
    )

    minimum_spend = extract_minimum_spend(
        combined
    )

    deal_type = infer_deal_type(
        combined
    )

    candidate_title = make_candidate_title(
        store=store,
        original_title=original_title,
        description=description,
        discount_value=discount_value,
        minimum_spend=minimum_spend,
    )

    now = datetime.now().isoformat()

    return {
        "store": store,
        "title": candidate_title,
        "description": description[:2000],
        "url": url,
        "source_domain": source_domain,
        "found_at": now,
        "last_checked": now,
        "expiration_date": extract_expiration_date(
            combined
        ),
        "discount_value": discount_value,
        "minimum_spend": minimum_spend,
        "promo_code": extract_promo_code(
            combined
        ),
        "deal_type": deal_type,
        "official_source": is_official_domain(
            source_domain,
            config["domain"],
        ),
        "search_query": query,
        "household_member": "Household",
    }



def search_for_deals(
    api_key,
    stores,
    official_only,
    custom_keywords,
    freshness,
    results_per_store,
):
    found_candidates = []
    errors = []

    for store in stores:
        query = build_search_query(
            store,
            official_only,
            custom_keywords,
        )

        config = STORE_SEARCH_CONFIG[store]

        include_domains = (
            [config["domain"]]
            if official_only
            else None
        )

        try:
            results = tavily_web_search(
                api_key=api_key,
                query=query,
                count=results_per_store,
                time_range=freshness,
                include_domains=include_domains,
            )
        except requests.RequestException as exc:
            errors.append(
                f"{store}: network/search error — {exc}"
            )
            continue
        except RuntimeError as exc:
            errors.append(
                f"{store}: {exc}"
            )
            continue

        for result in results:
            candidate = result_to_candidate(
                result,
                store,
                query,
            )

            if candidate is None:
                continue

            upsert_candidate(candidate)
            found_candidates.append(candidate)

    return found_candidates, errors


def reprocess_existing_candidates():
    conn = get_connection()

    rows = conn.execute("""
        SELECT
            id,
            store,
            title,
            description,
            url,
            source_domain,
            found_at,
            last_checked,
            expiration_date,
            discount_value,
            minimum_spend,
            promo_code,
            deal_type,
            official_source,
            status,
            search_query,
            household_member
        FROM deal_candidates
    """).fetchall()

    columns = [
        "id",
        "store",
        "title",
        "description",
        "url",
        "source_domain",
        "found_at",
        "last_checked",
        "expiration_date",
        "discount_value",
        "minimum_spend",
        "promo_code",
        "deal_type",
        "official_source",
        "status",
        "search_query",
        "household_member",
    ]

    updated = 0
    ignored = 0

    for row in rows:
        old = dict(
            zip(columns, row)
        )

        if str(
            old.get("url") or ""
        ).startswith("receipt://"):
            continue

        parsed = result_to_candidate(
            {
                "title": old["title"],
                "content": old["description"],
                "url": old["url"],
            },
            old["store"],
            old["search_query"] or "",
        )

        if parsed is not None:
            parsed["household_member"] = old.get(
                "household_member",
                "Household",
            )

        if parsed is None:
            if old["status"] == "new":
                conn.execute(
                    """
                    UPDATE deal_candidates
                    SET status = 'ignored'
                    WHERE id = ?
                    """,
                    (old["id"],),
                )
                ignored += 1

            continue

        conn.execute("""
            UPDATE deal_candidates
            SET
                title = ?,
                description = ?,
                source_domain = ?,
                last_checked = ?,
                expiration_date = ?,
                discount_value = ?,
                minimum_spend = ?,
                promo_code = ?,
                deal_type = ?,
                official_source = ?
            WHERE id = ?
        """, (
            parsed["title"],
            parsed["description"],
            parsed["source_domain"],
            parsed["last_checked"],
            parsed["expiration_date"],
            parsed["discount_value"],
            parsed["minimum_spend"],
            parsed["promo_code"],
            parsed["deal_type"],
            int(parsed["official_source"]),
            old["id"],
        ))

        updated += 1

    conn.commit()
    conn.close()

    return updated, ignored


def get_api_key_from_app():
    env_key = os.getenv(
        "TAVILY_API_KEY",
        "",
    ).strip()

    if env_key:
        return env_key

    return st.session_state.get(
        "tavily_api_key",
        "",
    ).strip()


def format_candidate_expiration(value):
    if not value:
        return "Not detected"

    try:
        return datetime.fromisoformat(
            value
        ).date().strftime(
            "%b %d, %Y"
        )
    except ValueError:
        return value


def save_candidate_as_deal(candidate):
    source_url = candidate["url"] or ""
    is_receipt_source = str(
        source_url
    ).startswith("receipt://")

    source_for_deal = (
        ""
        if is_receipt_source
        else source_url
    )

    if deal_already_saved(
        candidate["store"],
        candidate["title"],
        source_for_deal,
    ):
        set_candidate_status(
            candidate["id"],
            "saved",
        )

        return False

    notes_parts = [
        "Found automatically by Deal Finder.",
    ]

    if is_receipt_source:
        notes_parts.append(
            "Source: scanned receipt."
        )
    elif candidate["source_domain"]:
        notes_parts.append(
            f"Source: {candidate['source_domain']}."
        )

    if candidate["description"]:
        notes_parts.append(
            candidate["description"]
        )

    add_deal(
        store=candidate["store"],
        title=candidate["title"],
        deal_type=candidate["deal_type"] or "Other",
        discount_value=float(
            candidate["discount_value"] or 0
        ),
        minimum_spend=float(
            candidate["minimum_spend"] or 0
        ),
        promo_code=candidate["promo_code"] or "",
        expiration_date=candidate["expiration_date"] or "",
        source=source_for_deal,
        notes=" ".join(notes_parts),
        household_member=(
            candidate.get("household_member", "Household")
            if hasattr(candidate, "get")
            else "Household"
        ),
    )

    set_candidate_status(
        candidate["id"],
        "saved",
    )

    return True


# -----------------------------
# Missed Savings helpers
# -----------------------------

def get_receipt_record(receipt_id):
    conn = get_connection()

    row = pd.read_sql_query("""
        SELECT *
        FROM receipts
        WHERE id = ?
        LIMIT 1
    """, conn, params=(receipt_id,))

    conn.close()

    if row.empty:
        return None

    return row.iloc[0]


def receipt_used_benefits(receipt_id):
    benefits = get_receipt_benefits(
        receipt_id
    )

    if benefits.empty:
        return benefits

    return benefits[
        benefits["direction"].astype(str).str.lower()
        == "used"
    ].copy()


def tokenize_match_text(text):
    words = re.findall(
        r"[a-z0-9]+",
        str(text or "").lower(),
    )

    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "off",
        "save",
        "deal",
        "coupon",
        "reward",
        "rewards",
        "sale",
        "offer",
        "offers",
        "purchase",
        "spend",
        "when",
        "your",
        "you",
        "get",
        "extra",
        "cash",
        "store",
        "app",
        "eligible",
        "products",
        "product",
    }

    return {
        word
        for word in words
        if len(word) >= 3
        and word not in stop_words
    }


def is_broad_purchase_offer(text):
    lower = normalize_space(
        text
    ).lower()

    broad_phrases = [
        "entire purchase",
        "off purchase",
        "off your purchase",
        "extrabucks",
        "walgreens cash",
        "store reward",
        "rewards",
        "reward",
        "spend $",
        "spend ",
        "minimum purchase",
        "promo code",
    ]

    return any(
        phrase in lower
        for phrase in broad_phrases
    )


def deal_likely_used_on_receipt(
    deal,
    used_benefits,
    ocr_text,
):
    promo_code = str(
        deal.get(
            "promo_code",
            "",
        )
        or ""
    ).strip()

    if (
        promo_code
        and promo_code.lower()
        in str(
            ocr_text or ""
        ).lower()
    ):
        return True, (
            f"Promo code {promo_code} appears in the receipt text."
        )

    deal_value = float(
        deal.get(
            "discount_value",
            0,
        )
        or 0
    )

    if used_benefits.empty:
        return False, ""

    deal_type = str(
        deal.get(
            "deal_type",
            "",
        )
        or ""
    ).lower()

    title_text = str(
        deal.get(
            "title",
            "",
        )
        or ""
    ).lower()

    for _, benefit in used_benefits.iterrows():
        benefit_value = float(
            benefit.get(
                "value",
                0,
            )
            or 0
        )

        benefit_type = str(
            benefit.get(
                "benefit_type",
                "",
            )
            or ""
        ).lower()

        benefit_description = str(
            benefit.get(
                "description",
                "",
            )
            or ""
        ).lower()

        same_value = (
            deal_value > 0
            and benefit_value > 0
            and abs(
                deal_value
                - benefit_value
            )
            <= 0.01
        )

        type_overlap = (
            deal_type
            and benefit_type
            and (
                deal_type in benefit_type
                or benefit_type in deal_type
                or (
                    "reward" in deal_type
                    and "reward" in benefit_type
                )
                or (
                    "coupon" in deal_type
                    and "coupon" in benefit_type
                )
            )
        )

        keyword_overlap = False

        if benefit_description:
            for keyword in [
                "extrabucks",
                "walgreens cash",
                "coupon",
                "reward",
            ]:
                if (
                    keyword in title_text
                    and keyword in benefit_description
                ):
                    keyword_overlap = True
                    break

        if (
            same_value
            and (
                type_overlap
                or keyword_overlap
            )
        ):
            return True, (
                "A used coupon/reward with the same value and similar "
                "type was detected on this receipt."
            )

    return False, ""


def analyze_missed_savings_for_receipt(receipt_id):
    receipt = get_receipt_record(
        receipt_id
    )

    if receipt is None:
        return []

    store = str(
        receipt.get(
            "store",
            "",
        )
        or ""
    )

    member = str(
        receipt.get(
            "household_member",
            "Me",
        )
        or "Me"
    )

    purchase_date = pd.to_datetime(
        receipt.get(
            "purchase_date",
            "",
        ),
        errors="coerce",
    )

    if pd.isna(
        purchase_date
    ):
        return []

    purchase_date = purchase_date.normalize()

    total = float(
        receipt.get(
            "total",
            0,
        )
        or 0
    )

    ocr_text = str(
        receipt.get(
            "ocr_text",
            "",
        )
        or ""
    )

    conn = get_connection()

    deals = pd.read_sql_query("""
        SELECT *
        FROM deals
        WHERE store = ?
          AND used = 0
          AND household_member IN (?, 'Household')
        ORDER BY id DESC
    """, conn, params=(
        store,
        member,
    ))

    conn.close()

    if deals.empty:
        return []

    items = get_receipt_items(
        receipt_id
    )

    used_benefits = receipt_used_benefits(
        receipt_id
    )

    item_text = " ".join(
        items["item_name"].astype(str).tolist()
        if not items.empty
        else []
    )

    purchased_categories = set(
        detect_categories(
            item_text
        )
    )

    item_tokens = tokenize_match_text(
        item_text
    )

    results = []

    for _, deal in deals.iterrows():
        expiration_dt = pd.to_datetime(
            deal.get(
                "expiration_date",
                "",
            ),
            errors="coerce",
        )

        if (
            pd.notna(
                expiration_dt
            )
            and expiration_dt.normalize()
            < purchase_date
        ):
            continue

        created_at = pd.to_datetime(
            deal.get(
                "created_at",
                "",
            ),
            errors="coerce",
        )

        # For deals saved by newer versions, only compare them to receipts
        # created after the deal was already known to Deal Finder.
        if (
            pd.notna(
                created_at
            )
            and created_at.normalize()
            > purchase_date
        ):
            continue

        minimum_spend = float(
            deal.get(
                "minimum_spend",
                0,
            )
            or 0
        )

        if (
            minimum_spend > 0
            and total + 0.001
            < minimum_spend
        ):
            continue

        likely_used, used_reason = deal_likely_used_on_receipt(
            deal,
            used_benefits,
            ocr_text,
        )

        if likely_used:
            continue

        combined_text = " ".join(
            [
                str(
                    deal.get(
                        "title",
                        "",
                    )
                    or ""
                ),
                str(
                    deal.get(
                        "notes",
                        "",
                    )
                    or ""
                ),
                str(
                    deal.get(
                        "deal_type",
                        "",
                    )
                    or ""
                ),
            ]
        )

        deal_categories = set(
            detect_categories(
                combined_text
            )
        )

        category_overlap = (
            purchased_categories
            & deal_categories
        )

        deal_tokens = tokenize_match_text(
            combined_text
        )

        token_overlap = (
            item_tokens
            & deal_tokens
        )

        broad_offer = is_broad_purchase_offer(
            combined_text
        )

        if not (
            category_overlap
            or token_overlap
            or broad_offer
        ):
            continue

        reasons = []

        if minimum_spend > 0:
            reasons.append(
                f"Receipt total ${total:.2f} met the "
                f"${minimum_spend:.2f} spend requirement"
            )
        else:
            reasons.append(
                "No minimum spend was recorded"
            )

        if category_overlap:
            reasons.append(
                "matched receipt category: "
                + ", ".join(
                    sorted(
                        category_overlap
                    )
                )
            )
        elif token_overlap:
            reasons.append(
                "matched receipt item wording: "
                + ", ".join(
                    sorted(
                        list(
                            token_overlap
                        )[:4]
                    )
                )
            )
        elif broad_offer:
            reasons.append(
                "looks like a store-wide or purchase-level offer"
            )

        if pd.isna(
            expiration_dt
        ):
            reasons.append(
                "expiration was unknown"
            )
        else:
            reasons.append(
                "deal had not expired by the purchase date"
            )

        if pd.isna(
            created_at
        ):
            reasons.append(
                "older deal record has no saved-on date, so availability "
                "at the exact purchase time cannot be confirmed"
            )

        results.append(
            {
                "deal_id": int(
                    deal["id"]
                ),
                "title": deal[
                    "title"
                ],
                "store": deal[
                    "store"
                ],
                "household_member": deal.get(
                    "household_member",
                    "Me",
                ),
                "deal_type": deal.get(
                    "deal_type",
                    "Other",
                ),
                "discount_value": float(
                    deal.get(
                        "discount_value",
                        0,
                    )
                    or 0
                ),
                "minimum_spend": minimum_spend,
                "promo_code": deal.get(
                    "promo_code",
                    "",
                )
                or "",
                "expiration_date": (
                    ""
                    if pd.isna(
                        expiration_dt
                    )
                    else expiration_dt.date().isoformat()
                ),
                "source": deal.get(
                    "source",
                    "",
                )
                or "",
                "notes": deal.get(
                    "notes",
                    "",
                )
                or "",
                "reasons": reasons,
            }
        )

    results.sort(
        key=lambda row: (
            -float(
                row[
                    "discount_value"
                ]
            ),
            row[
                "expiration_date"
            ]
            or "9999-12-31",
        )
    )

    return results


# -----------------------------
# Location / Arrival Mode helpers
# -----------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

SUPPORTED_STORE_ALIASES = {
    "CVS": [
        "cvs",
        "cvs pharmacy",
        "cvs/pharmacy",
    ],
    "Walgreens": [
        "walgreens",
        "walgreen",
    ],
    "Jack in the Box": [
        "jack in the box",
        "jackinthebox",
    ],
    "Target": [
        "target",
    ],
    "Walmart": [
        "walmart",
        "wal-mart",
    ],
    "Safeway": [
        "safeway",
    ],
}


def normalize_supported_store(name):
    cleaned = re.sub(
        r"\s+",
        " ",
        str(name or "").strip().lower(),
    )

    for canonical, aliases in SUPPORTED_STORE_ALIASES.items():
        for alias in aliases:
            if alias in cleaned:
                return canonical

    return None


def haversine_meters(lat1, lon1, lat2, lon2):
    radius_m = 6371000.0

    phi1 = math.radians(
        float(lat1)
    )

    phi2 = math.radians(
        float(lat2)
    )

    d_phi = math.radians(
        float(lat2) - float(lat1)
    )

    d_lambda = math.radians(
        float(lon2) - float(lon1)
    )

    a = (
        math.sin(
            d_phi / 2
        ) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(
            d_lambda / 2
        ) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return radius_m * c


def find_nearby_supported_stores(
    latitude,
    longitude,
    radius_m=700,
):
    """
    Look for supported chains around the phone's current coordinates
    using OpenStreetMap's public Overpass data.

    No location is written to Deal Finder's database.
    """
    name_regex = (
        "CVS|Walgreens|Walgreen|Jack in the Box|"
        "Jackinthebox|Target|Walmart|Wal-Mart|Safeway"
    )

    query = f"""
    [out:json][timeout:20];
    (
      nwr(around:{int(radius_m)},{float(latitude)},{float(longitude)})
        ["name"~"{name_regex}",i];
    );
    out center tags;
    """

    headers = {
        "User-Agent": (
            "DealFinder/1.0 "
            "(personal local shopping assistant)"
        )
    }

    response = requests.post(
        OVERPASS_URL,
        data={
            "data": query,
        },
        headers=headers,
        timeout=30,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "The nearby-store lookup service is busy. "
            "Wait a moment and try again."
        )

    response.raise_for_status()

    data = response.json()

    found = []

    for element in data.get(
        "elements",
        [],
    ):
        tags = element.get(
            "tags",
            {},
        )

        name = tags.get(
            "name",
            "",
        )

        store = normalize_supported_store(
            name
        )

        if not store:
            continue

        store_lat = element.get(
            "lat"
        )

        store_lon = element.get(
            "lon"
        )

        if (
            store_lat is None
            or store_lon is None
        ):
            center = element.get(
                "center",
                {},
            )

            store_lat = center.get(
                "lat"
            )

            store_lon = center.get(
                "lon"
            )

        if (
            store_lat is None
            or store_lon is None
        ):
            continue

        distance_m = haversine_meters(
            latitude,
            longitude,
            store_lat,
            store_lon,
        )

        found.append(
            {
                "store": store,
                "name": name or store,
                "distance_m": distance_m,
                "lat": store_lat,
                "lon": store_lon,
                "address": " ".join(
                    part
                    for part in [
                        tags.get(
                            "addr:housenumber",
                            "",
                        ),
                        tags.get(
                            "addr:street",
                            "",
                        ),
                    ]
                    if part
                ).strip(),
            }
        )

    # Deduplicate multiple OSM objects describing the same nearby location.
    deduped = []

    for item in sorted(
        found,
        key=lambda row: row[
            "distance_m"
        ],
    ):
        duplicate = False

        for existing in deduped:
            if (
                item["store"]
                == existing["store"]
                and abs(
                    item["distance_m"]
                    - existing["distance_m"]
                )
                < 20
            ):
                duplicate = True
                break

        if not duplicate:
            deduped.append(
                item
            )

    return deduped[:10]


def location_capture_html():
    """
    A small foreground-only location control.

    The browser asks the user for permission, then reloads the Streamlit
    app with the coordinates in query parameters. The coordinates are
    used for the current nearby-store lookup and are not stored in SQLite.
    """
    return r"""
    <div style="
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        padding: 4px 0 10px 0;
    ">
      <button
        id="deal-finder-location-button"
        style="
          width: 100%;
          min-height: 44px;
          border: 0;
          border-radius: 8px;
          font-size: 16px;
          font-weight: 600;
          cursor: pointer;
          background: #ff4b4b;
          color: white;
        "
      >
        📍 Check My Current Location
      </button>
      <div
        id="deal-finder-location-status"
        style="
          margin-top: 8px;
          font-size: 13px;
          color: #666;
        "
      ></div>
    </div>

    <script>
      const button = document.getElementById(
        "deal-finder-location-button"
      );

      const status = document.getElementById(
        "deal-finder-location-status"
      );

      button.addEventListener("click", () => {
        if (!navigator.geolocation) {
          status.textContent =
            "Location is not supported by this browser.";
          return;
        }

        status.textContent =
          "Requesting your phone's location…";

        navigator.geolocation.getCurrentPosition(
          (position) => {
            const lat =
              position.coords.latitude.toFixed(7);

            const lon =
              position.coords.longitude.toFixed(7);

            const accuracy =
              Math.round(position.coords.accuracy || 0);

            const params =
              new URLSearchParams();

            params.set(
              "arrival",
              "1"
            );

            params.set(
              "lat",
              lat
            );

            params.set(
              "lon",
              lon
            );

            params.set(
              "accuracy",
              accuracy
            );

            params.set(
              "location_ts",
              Date.now().toString()
            );

            window.parent.location.href =
              window.parent.location.pathname
              + "?"
              + params.toString();
          },
          (error) => {
            let message =
              "Could not access your location.";

            if (error.code === 1) {
              message =
                "Location permission was denied. "
                + "Enable location access for this site in Safari and try again.";
            } else if (error.code === 2) {
              message =
                "Your device could not determine a location.";
            } else if (error.code === 3) {
              message =
                "Location request timed out.";
            }

            status.textContent = message;
          },
          {
            enableHighAccuracy: true,
            timeout: 15000,
            maximumAge: 30000
          }
        );
      });
    </script>
    """


def get_arrival_mode_coordinates():
    try:
        latitude = float(
            st.query_params.get(
                "lat",
                "",
            )
        )

        longitude = float(
            st.query_params.get(
                "lon",
                "",
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not (
        -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None

    try:
        accuracy = float(
            st.query_params.get(
                "accuracy",
                0,
            )
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        accuracy = 0

    return {
        "lat": latitude,
        "lon": longitude,
        "accuracy": accuracy,
    }


def filter_deals_for_member(
    deals,
    member,
):
    if deals.empty:
        return deals

    if member == "Everyone":
        return deals

    if member == "Household":
        return deals[
            deals[
                "household_member"
            ]
            == "Household"
        ].copy()

    return deals[
        deals[
            "household_member"
        ].isin(
            [
                "Household",
                member,
            ]
        )
    ].copy()


# -----------------------------
# Expiration dashboard helpers
# -----------------------------

def get_expiration_dashboard_deals(
    stores=None,
    members=None,
):
    deals = get_deals()

    if deals.empty:
        return deals

    deals = deals[
        deals["used"] == 0
    ].copy()

    if stores:
        deals = deals[
            deals["store"].isin(
                stores
            )
        ]

    if members:
        deals = deals[
            deals["household_member"].isin(
                members
            )
        ]

    deals["expiration_dt"] = pd.to_datetime(
        deals["expiration_date"],
        errors="coerce",
    )

    today = pd.Timestamp(
        date.today()
    )

    deals["days_left"] = (
        deals["expiration_dt"]
        - today
    ).dt.days

    deals["expired"] = (
        deals["expiration_dt"].notna()
        & (deals["days_left"] < 0)
    )

    deals["discount_value"] = pd.to_numeric(
        deals["discount_value"],
        errors="coerce",
    ).fillna(0)

    return deals


def expiration_bucket(days_left, expiration_dt):
    if pd.isna(
        expiration_dt
    ):
        return "Unknown"

    days_left = int(
        days_left
    )

    if days_left < 0:
        return "Expired"

    if days_left == 0:
        return "Today"

    if days_left <= 3:
        return "Next 3 Days"

    if days_left <= 7:
        return "Next 7 Days"

    if days_left <= 30:
        return "Next 30 Days"

    return "Later"


def expiration_sort_key(bucket):
    order = {
        "Today": 0,
        "Next 3 Days": 1,
        "Next 7 Days": 2,
        "Next 30 Days": 3,
        "Later": 4,
        "Unknown": 5,
        "Expired": 6,
    }

    return order.get(
        bucket,
        99,
    )


# -----------------------------
# For You recommendation helpers
# -----------------------------

CATEGORY_KEYWORDS = {
    "Oral Care": [
        "toothpaste",
        "tooth brush",
        "toothbrush",
        "mouthwash",
        "mouth rinse",
        "floss",
        "crest",
        "colgate",
        "sensodyne",
        "oral-b",
        "oral b",
        "listerine",
        "scope",
    ],
    "Hair Care": [
        "shampoo",
        "conditioner",
        "hair mask",
        "hair spray",
        "hairspray",
        "dry shampoo",
        "dove hair",
        "pantene",
        "tresemme",
        "head & shoulders",
        "head and shoulders",
        "garnier",
        "herbal essences",
    ],
    "Skin Care": [
        "moisturizer",
        "lotion",
        "face wash",
        "facial cleanser",
        "sunscreen",
        "spf",
        "cerave",
        "cetaphil",
        "neutrogena",
        "eucerin",
        "aquaphor",
        "lip balm",
        "chapstick",
    ],
    "Personal Care": [
        "deodorant",
        "body wash",
        "soap",
        "razor",
        "shave",
        "shaving",
        "body lotion",
        "old spice",
        "secret",
        "degree",
        "axe",
        "gillette",
        "native",
    ],
    "Pain Relief": [
        "advil",
        "tylenol",
        "aleve",
        "ibuprofen",
        "acetaminophen",
        "naproxen",
        "pain relief",
        "pain reliever",
        "aspercreme",
        "icy hot",
    ],
    "Cold & Allergy": [
        "allergy",
        "zyrtec",
        "claritin",
        "allegra",
        "benadryl",
        "cold & flu",
        "cold and flu",
        "dayquil",
        "nyquil",
        "mucinex",
        "cough",
        "nasal",
        "sinus",
    ],
    "Vitamins & Wellness": [
        "vitamin",
        "gummy vitamin",
        "multivitamin",
        "supplement",
        "probiotic",
        "goli",
        "nature made",
        "centrum",
        "emergen-c",
    ],
    "Paper Products": [
        "paper towel",
        "paper towels",
        "toilet paper",
        "bath tissue",
        "facial tissue",
        "kleenex",
        "bounty",
        "charmin",
        "cottonelle",
        "scott tissue",
        "napkin",
    ],
    "Cleaning": [
        "cleaner",
        "cleaning",
        "disinfect",
        "lysol",
        "clorox",
        "windex",
        "dish soap",
        "dawn",
        "sponges",
        "swiffer",
        "trash bag",
        "garbage bag",
        "ziploc",
    ],
    "Laundry": [
        "laundry",
        "detergent",
        "tide",
        "gain",
        "persil",
        "fabric softener",
        "dryer sheet",
        "downy",
    ],
    "Beverages": [
        "water",
        "soda",
        "cola",
        "pepsi",
        "coke",
        "coca cola",
        "sprite",
        "gatorade",
        "powerade",
        "juice",
        "coffee",
        "tea",
        "energy drink",
        "red bull",
        "monster",
        "sparkling water",
        "la croix",
        "lacroix",
    ],
    "Snacks": [
        "chips",
        "crackers",
        "cookies",
        "candy",
        "chocolate",
        "granola",
        "protein bar",
        "snack",
        "doritos",
        "lays",
        "cheetos",
        "oreo",
        "kind bar",
    ],
    "Breakfast": [
        "cereal",
        "oatmeal",
        "breakfast",
        "granola",
        "pancake",
        "waffle",
        "coffee",
    ],
    "Pet Care": [
        "dog food",
        "cat food",
        "pet food",
        "dog treat",
        "cat treat",
        "litter",
        "purina",
        "pedigree",
        "temptations",
        "iams",
        "blue buffalo",
    ],
    "Baby": [
        "diaper",
        "diapers",
        "wipes",
        "baby formula",
        "baby food",
        "pampers",
        "huggies",
        "gerber",
    ],
    "Fast Food": [
        "burger",
        "cheeseburger",
        "fries",
        "taco",
        "sandwich",
        "combo",
        "meal",
        "shake",
        "jack in the box",
        "jack pack",
    ],
}

CATEGORY_RELATED = {
    "Oral Care": [
        "Personal Care",
        "Vitamins & Wellness",
    ],
    "Hair Care": [
        "Personal Care",
        "Skin Care",
    ],
    "Skin Care": [
        "Personal Care",
        "Vitamins & Wellness",
    ],
    "Personal Care": [
        "Skin Care",
        "Hair Care",
        "Oral Care",
    ],
    "Pain Relief": [
        "Cold & Allergy",
        "Vitamins & Wellness",
    ],
    "Cold & Allergy": [
        "Pain Relief",
        "Vitamins & Wellness",
    ],
    "Vitamins & Wellness": [
        "Personal Care",
        "Skin Care",
    ],
    "Paper Products": [
        "Cleaning",
        "Laundry",
    ],
    "Cleaning": [
        "Paper Products",
        "Laundry",
    ],
    "Laundry": [
        "Cleaning",
        "Paper Products",
    ],
    "Beverages": [
        "Snacks",
        "Breakfast",
    ],
    "Snacks": [
        "Beverages",
        "Breakfast",
    ],
    "Breakfast": [
        "Beverages",
        "Snacks",
    ],
    "Pet Care": [
        "Cleaning",
    ],
    "Baby": [
        "Paper Products",
        "Cleaning",
    ],
    "Fast Food": [
        "Beverages",
        "Snacks",
    ],
}

TRY_SUGGESTIONS = {
    "Oral Care": [
        "mouthwash",
        "floss picks",
        "electric toothbrush heads",
    ],
    "Hair Care": [
        "conditioner",
        "hair mask",
        "dry shampoo",
    ],
    "Skin Care": [
        "sunscreen",
        "lip balm",
        "gentle moisturizer",
    ],
    "Personal Care": [
        "body wash",
        "deodorant",
        "razor refills",
    ],
    "Pain Relief": [
        "topical pain relief",
        "heat wraps",
    ],
    "Cold & Allergy": [
        "saline nasal spray",
        "facial tissues",
    ],
    "Vitamins & Wellness": [
        "multivitamins",
        "probiotics",
    ],
    "Paper Products": [
        "facial tissues",
        "napkins",
    ],
    "Cleaning": [
        "disinfecting wipes",
        "trash bags",
    ],
    "Laundry": [
        "fabric softener",
        "dryer sheets",
    ],
    "Beverages": [
        "sparkling water",
        "tea",
    ],
    "Snacks": [
        "nuts",
        "protein bars",
    ],
    "Breakfast": [
        "oatmeal",
        "granola",
    ],
    "Pet Care": [
        "pet treats",
        "litter supplies",
    ],
    "Baby": [
        "baby wipes",
        "gentle bath products",
    ],
    "Fast Food": [
        "new side items",
        "limited-time menu items",
    ],
}


def detect_categories(text):
    lower = normalize_space(
        text
    ).lower()

    if not lower:
        return []

    matches = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0

        for keyword in keywords:
            if keyword.lower() in lower:
                score += 2 if " " in keyword else 1

        if score:
            matches.append(
                (
                    score,
                    category,
                )
            )

    matches.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        category
        for _, category in matches
    ]


def primary_category(text):
    categories = detect_categories(
        text
    )

    return (
        categories[0]
        if categories
        else "Other"
    )


def build_purchase_profile():
    history = get_purchase_history()

    if history.empty:
        return (
            history,
            pd.DataFrame(),
        )

    history = history.copy()

    history["purchase_dt"] = pd.to_datetime(
        history["purchase_date"],
        errors="coerce",
    )

    history["category"] = history["item_name"].apply(
        primary_category
    )

    history["quantity"] = pd.to_numeric(
        history["quantity"],
        errors="coerce",
    ).fillna(1)

    history["price"] = pd.to_numeric(
        history["price"],
        errors="coerce",
    ).fillna(0)

    profile_rows = []

    for category, group in history.groupby(
        "category"
    ):
        if category == "Other":
            continue

        purchase_events = (
            group[
                [
                    "receipt_id",
                    "purchase_dt",
                ]
            ]
            .dropna(
                subset=[
                    "purchase_dt",
                ]
            )
            .drop_duplicates()
            .sort_values(
                "purchase_dt"
            )
        )

        purchase_count = len(
            purchase_events
        )

        item_count = int(
            len(group)
        )

        last_purchase = (
            purchase_events[
                "purchase_dt"
            ].max()
            if not purchase_events.empty
            else pd.NaT
        )

        average_gap = None

        if purchase_count >= 2:
            gaps = (
                purchase_events[
                    "purchase_dt"
                ]
                .diff()
                .dt.days
                .dropna()
            )

            if not gaps.empty:
                average_gap = float(
                    gaps.mean()
                )

        days_since_last = None

        if not pd.isna(
            last_purchase
        ):
            days_since_last = int(
                (
                    pd.Timestamp(
                        date.today()
                    )
                    - last_purchase.normalize()
                ).days
            )

        typical_items = (
            group["item_name"]
            .astype(str)
            .value_counts()
            .head(3)
            .index
            .tolist()
        )

        stores = (
            group["store"]
            .dropna()
            .astype(str)
            .value_counts()
            .head(3)
            .index
            .tolist()
        )

        due_score = 0.0

        if (
            average_gap
            and average_gap > 0
            and days_since_last is not None
        ):
            due_score = (
                days_since_last
                / average_gap
            )

        profile_rows.append(
            {
                "category": category,
                "purchase_count": purchase_count,
                "item_count": item_count,
                "last_purchase": last_purchase,
                "average_gap_days": average_gap,
                "days_since_last": days_since_last,
                "due_score": due_score,
                "typical_items": typical_items,
                "stores": stores,
            }
        )

    profile = pd.DataFrame(
        profile_rows
    )

    if not profile.empty:
        profile = profile.sort_values(
            by=[
                "purchase_count",
                "item_count",
                "due_score",
            ],
            ascending=[
                False,
                False,
                False,
            ],
        ).reset_index(
            drop=True
        )

    return (
        history,
        profile,
    )


def profile_reason(profile_row):
    purchase_count = int(
        profile_row.get(
            "purchase_count",
            0,
        )
    )

    days_since_last = profile_row.get(
        "days_since_last"
    )

    average_gap = profile_row.get(
        "average_gap_days"
    )

    parts = []

    if purchase_count:
        parts.append(
            f"bought on {purchase_count} receipt"
            f"{'s' if purchase_count != 1 else ''}"
        )

    if days_since_last is not None:
        parts.append(
            f"last seen {int(days_since_last)} days ago"
        )

    if (
        average_gap
        and average_gap > 0
        and purchase_count >= 2
    ):
        parts.append(
            f"typical gap ~{int(round(average_gap))} days"
        )

    return " · ".join(
        parts
    )


def category_due_label(profile_row):
    days_since_last = profile_row.get(
        "days_since_last"
    )

    average_gap = profile_row.get(
        "average_gap_days"
    )

    if (
        average_gap
        and average_gap > 0
        and days_since_last is not None
    ):
        ratio = (
            days_since_last
            / average_gap
        )

        if ratio >= 1.1:
            return "Likely due"

        if ratio >= 0.75:
            return "May be due soon"

    return "Recurring purchase"


def get_current_recommendation_offers():
    today = pd.Timestamp(
        date.today()
    )

    frames = []

    deals = get_deals()

    if not deals.empty:
        saved = deals[
            deals["used"] == 0
        ].copy()

        saved["expiration_dt"] = pd.to_datetime(
            saved["expiration_date"],
            errors="coerce",
        )

        saved = saved[
            saved["expiration_dt"].isna()
            | (saved["expiration_dt"] >= today)
        ].copy()

        if not saved.empty:
            saved["description"] = (
                saved["notes"]
                .fillna("")
            )

            saved["source_kind"] = "Saved"
            saved["candidate_id"] = None
            saved["offer_id"] = saved["id"]

            frames.append(
                saved[
                    [
                        "store",
                        "title",
                        "description",
                        "expiration_date",
                        "expiration_dt",
                        "discount_value",
                        "minimum_spend",
                        "promo_code",
                        "deal_type",
                        "source",
                        "source_kind",
                        "offer_id",
                        "candidate_id",
                    ]
                ]
            )

    candidates = get_candidates(
        status="new"
    )

    if not candidates.empty:
        candidates = candidates.copy()

        candidates["expiration_dt"] = pd.to_datetime(
            candidates["expiration_date"],
            errors="coerce",
        )

        candidates = candidates[
            candidates["expiration_dt"].isna()
            | (
                candidates[
                    "expiration_dt"
                ]
                >= today
            )
        ].copy()

        if not candidates.empty:
            candidates["source_kind"] = "Review"
            candidates["source"] = candidates["url"]
            candidates["offer_id"] = None
            candidates["candidate_id"] = candidates["id"]

            frames.append(
                candidates[
                    [
                        "store",
                        "title",
                        "description",
                        "expiration_date",
                        "expiration_dt",
                        "discount_value",
                        "minimum_spend",
                        "promo_code",
                        "deal_type",
                        "source",
                        "source_kind",
                        "offer_id",
                        "candidate_id",
                    ]
                ]
            )

    if not frames:
        return pd.DataFrame()

    offers = pd.concat(
        frames,
        ignore_index=True,
    )

    offers["combined_text"] = (
        offers["title"].fillna("")
        + " "
        + offers["description"].fillna("")
    )

    offers["categories"] = offers[
        "combined_text"
    ].apply(
        detect_categories
    )

    offers["days_left"] = (
        offers["expiration_dt"]
        - today
    ).dt.days

    offers["known_value"] = pd.to_numeric(
        offers["discount_value"],
        errors="coerce",
    ).fillna(0)

    return offers


def find_profile_matches(profile, offers):
    if (
        profile.empty
        or offers.empty
    ):
        return (
            [],
            [],
        )

    profile_map = {
        row["category"]: row
        for _, row in profile.iterrows()
    }

    purchased_categories = set(
        profile_map
    )

    relevant = []
    explore = []

    for _, offer in offers.iterrows():
        offer_categories = set(
            offer["categories"]
            if isinstance(
                offer["categories"],
                list,
            )
            else []
        )

        if not offer_categories:
            continue

        direct = (
            offer_categories
            & purchased_categories
        )

        if direct:
            best_category = max(
                direct,
                key=lambda category:
                    profile_map[
                        category
                    ]["purchase_count"],
            )

            profile_row = profile_map[
                best_category
            ]

            score = (
                int(
                    profile_row[
                        "purchase_count"
                    ]
                )
                * 10
                + min(
                    int(
                        profile_row[
                            "item_count"
                        ]
                    ),
                    10,
                )
            )

            if offer["source_kind"] == "Saved":
                score += 8

            if (
                pd.notna(
                    offer["days_left"]
                )
                and offer["days_left"] <= 7
            ):
                score += 5

            score += min(
                float(
                    offer["known_value"]
                ),
                20,
            ) / 2

            relevant.append(
                {
                    "offer": offer,
                    "category": best_category,
                    "score": score,
                    "reason": (
                        f"Matches {best_category}. "
                        + profile_reason(
                            profile_row
                        )
                    ),
                }
            )

            continue

        related_sources = []

        for purchased_category in purchased_categories:
            related = set(
                CATEGORY_RELATED.get(
                    purchased_category,
                    [],
                )
            )

            if offer_categories & related:
                related_sources.append(
                    purchased_category
                )

        if related_sources:
            source_category = max(
                related_sources,
                key=lambda category:
                    profile_map[
                        category
                    ]["purchase_count"],
            )

            source_row = profile_map[
                source_category
            ]

            new_category = sorted(
                offer_categories
            )[0]

            score = (
                int(
                    source_row[
                        "purchase_count"
                    ]
                )
                * 5
            )

            if offer["source_kind"] == "Saved":
                score += 5

            if (
                pd.notna(
                    offer["days_left"]
                )
                and offer["days_left"] <= 7
            ):
                score += 3

            explore.append(
                {
                    "offer": offer,
                    "category": new_category,
                    "score": score,
                    "reason": (
                        f"Something different in {new_category}, "
                        f"related to your {source_category} purchases."
                    ),
                }
            )

    relevant.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    explore.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return (
        relevant,
        explore,
    )


def personalized_search(
    api_key,
    categories,
    stores,
    official_only=True,
    freshness="month",
    results_per_search=5,
):
    found = []
    errors = []

    for category in categories:
        category_results, category_errors = search_for_deals(
            api_key=api_key,
            stores=stores,
            official_only=official_only,
            custom_keywords=category,
            freshness=freshness,
            results_per_store=results_per_search,
        )

        found.extend(
            category_results
        )

        errors.extend(
            category_errors
        )

    return (
        found,
        errors,
    )


def default_personalized_stores():
    store_history = get_receipt_store_history()

    if not store_history.empty:
        stores = [
            store
            for store in store_history[
                "store"
            ].tolist()
            if store in STORE_SEARCH_CONFIG
        ]

        if stores:
            return stores[:3]

    return [
        "CVS",
        "Walgreens",
        "Jack in the Box",
    ]


def recommendation_expiration_text(offer):
    expiration_dt = offer.get(
        "expiration_dt"
    )

    if pd.isna(
        expiration_dt
    ):
        return "Expiration not detected"

    days_left = offer.get(
        "days_left"
    )

    date_text = expiration_dt.date().strftime(
        "%b %d, %Y"
    )

    if pd.isna(
        days_left
    ):
        return f"Expires {date_text}"

    days_left = int(
        days_left
    )

    if days_left == 0:
        return f"Expires today · {date_text}"

    if days_left == 1:
        return f"Expires tomorrow · {date_text}"

    if days_left <= 7:
        return f"Expires in {days_left} days · {date_text}"

    return f"Expires {date_text}"


# -----------------------------
# Shop Here helpers
# -----------------------------

def prepare_shop_deals(store):
    deals = get_saved_deals_for_store(
        store
    )

    if deals.empty:
        return deals

    deals = deals.copy()

    deals["expiration_dt"] = pd.to_datetime(
        deals["expiration_date"],
        errors="coerce",
    )

    today = pd.Timestamp(
        date.today()
    )

    # Keep unknown-expiration deals and known deals that have not expired.
    deals = deals[
        deals["expiration_dt"].isna()
        | (deals["expiration_dt"] >= today)
    ].copy()

    if deals.empty:
        return deals

    deals["days_left"] = (
        deals["expiration_dt"] - today
    ).dt.days

    # Known expiration dates first, soonest first. Unknown dates last.
    deals["unknown_expiration"] = (
        deals["expiration_dt"].isna()
    )

    deals = deals.sort_values(
        by=[
            "unknown_expiration",
            "expiration_dt",
            "discount_value",
        ],
        ascending=[
            True,
            True,
            False,
        ],
        na_position="last",
    )

    return deals


def shop_deal_expiration_text(deal):
    expiration_dt = deal.get(
        "expiration_dt"
    )

    if pd.isna(
        expiration_dt
    ):
        return "Expiration not detected"

    days_left = int(
        deal.get(
            "days_left",
            0,
        )
    )

    date_text = expiration_dt.date().strftime(
        "%b %d, %Y"
    )

    if days_left == 0:
        return f"Expires today · {date_text}"

    if days_left == 1:
        return f"Expires tomorrow · {date_text}"

    if days_left <= 7:
        return f"Expires in {days_left} days · {date_text}"

    return f"Expires {date_text}"


def shop_spend_status(minimum_spend, planned_spend):
    minimum_spend = float(
        minimum_spend or 0
    )

    planned_spend = float(
        planned_spend or 0
    )

    if minimum_spend <= 0:
        return (
            "No minimum spend detected",
            True,
        )

    if planned_spend <= 0:
        return (
            f"Requires at least ${minimum_spend:.2f}",
            None,
        )

    if planned_spend >= minimum_spend:
        return (
            f"Planned spend meets the ${minimum_spend:.2f} minimum",
            True,
        )

    remaining = (
        minimum_spend
        - planned_spend
    )

    return (
        f"${remaining:.2f} more needed to reach the ${minimum_spend:.2f} minimum",
        False,
    )


# -----------------------------
# App setup
# -----------------------------

create_database()

st.set_page_config(
    page_title="Deal Finder",
    page_icon="💰",
    layout="wide",
)

st.title("💰 Deal Finder")
st.caption(
    "Keep track of coupons, rewards, promo codes, store deals, and receipts."
)

PAGES = [
    "Deals",
    "⏰ Expiring",
    "🔎 Find Deals",
    "✨ For You",
    "🛒 Shop Here",
    "🕵️ Missed Savings",
    "📍 Arrival Mode",
    "👥 Household",
    "Scan Receipt",
    "Receipts",
]

arrival_requested = (
    st.query_params.get(
        "arrival",
        ""
    )
    == "1"
)

default_page = (
    "📍 Arrival Mode"
    if arrival_requested
    else "Deals"
)

page = st.sidebar.radio(
    "Go to",
    PAGES,
    index=PAGES.index(
        default_page
    ),
)


# -----------------------------
# Deals page
# -----------------------------

if page == "Deals":
    st.header("Add a Deal")

    with st.form("add_deal_form"):
        col1, col2 = st.columns(2)

        with col1:
            store = st.selectbox(
                "Store",
                STORE_OPTIONS,
            )

            title = st.text_input(
                "Deal",
                placeholder="$5 off a $20 purchase",
            )

            deal_type = st.selectbox(
                "Deal Type",
                [
                    "Coupon",
                    "Store Reward",
                    "Promo Code",
                    "Sale",
                    "Free Item",
                    "Cashback",
                    "Other",
                ],
            )

            discount_value = st.number_input(
                "Approximate Savings ($)",
                min_value=0.0,
                step=1.0,
            )

        with col2:
            minimum_spend = st.number_input(
                "Minimum Purchase ($)",
                min_value=0.0,
                step=1.0,
            )

            promo_code = st.text_input(
                "Promo Code",
                placeholder="Optional",
            )

            expiration_date = st.date_input(
                "Expiration Date",
                value=date.today() + timedelta(days=7),
            )

            source = st.text_input(
                "Source / Link",
                placeholder="CVS app, email, website...",
            )

        household_member = st.selectbox(
            "Who can use this deal?",
            get_household_members(
                include_household=True
            ),
            help=(
                "Use Household for a general/public offer. "
                "Choose a person for account-specific coupons or rewards."
            ),
        )

        notes = st.text_area(
            "Notes",
            placeholder="Anything important about using this deal...",
        )

        submitted = st.form_submit_button("Save Deal")

        if submitted:
            if not title.strip():
                st.error(
                    "Enter a description for the deal."
                )
            else:
                add_deal(
                    store,
                    title.strip(),
                    deal_type,
                    discount_value,
                    minimum_spend,
                    promo_code.strip(),
                    expiration_date.isoformat(),
                    source.strip(),
                    notes.strip(),
                    household_member=household_member,
                )

                st.success("Deal saved!")
                st.rerun()

    st.divider()
    st.header("Your Deals")

    deals = get_deals()

    if deals.empty:
        st.info(
            "You don't have any deals yet. "
            "Add one manually or use Find Deals."
        )

    else:
        deals["expiration_dt"] = pd.to_datetime(
            deals["expiration_date"],
            errors="coerce",
        )

        today = pd.Timestamp(date.today())

        active = deals[
            (deals["used"] == 0)
            & (
                deals["expiration_dt"].isna()
                | (deals["expiration_dt"] >= today)
            )
        ].copy()

        expiring_soon = active[
            active["expiration_dt"].notna()
            & (
                active["expiration_dt"]
                <= today + pd.Timedelta(days=7)
            )
        ].copy()

        expired = deals[
            (deals["used"] == 0)
            & deals["expiration_dt"].notna()
            & (deals["expiration_dt"] < today)
        ].copy()

        unknown_expiration = active[
            active["expiration_dt"].isna()
        ]

        potential_savings = (
            active["discount_value"]
            .fillna(0)
            .sum()
        )

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "Active Deals",
            len(active),
        )

        col2.metric(
            "Expiring in 7 Days",
            len(expiring_soon),
        )

        col3.metric(
            "Potential Savings",
            f"${potential_savings:.2f}",
        )

        col4.metric(
            "Unknown Expiration",
            len(unknown_expiration),
        )

        st.subheader("🔥 Expiring Soon")

        if expiring_soon.empty:
            st.success(
                "Nothing with a known expiration date "
                "expires in the next 7 days."
            )

        else:
            for _, deal in expiring_soon.iterrows():
                days_left = (
                    deal["expiration_dt"] - today
                ).days

                if days_left == 0:
                    time_left = "expires today"
                elif days_left == 1:
                    time_left = "1 day left"
                else:
                    time_left = f"{days_left} days left"

                with st.expander(
                    f"{deal['store']} — "
                    f"{deal['title']} ({time_left})"
                ):
                    st.write(
                        f"**Type:** "
                        f"{deal['deal_type']}"
                    )

                    st.write(
                        f"**Savings:** "
                        f"${float(deal['discount_value'] or 0):.2f}"
                    )

                    st.write(
                        f"**Minimum purchase:** "
                        f"${float(deal['minimum_spend'] or 0):.2f}"
                    )

                    if deal["promo_code"]:
                        st.write(
                            f"**Promo code:** "
                            f"`{deal['promo_code']}`"
                        )

                    st.write(
                        f"**Expires:** "
                        f"{deal['expiration_dt'].date()}"
                    )

                    if deal["source"]:
                        st.link_button(
                            "Open Source",
                            deal["source"],
                        )

                    if deal["notes"]:
                        st.write(
                            f"**Notes:** "
                            f"{deal['notes']}"
                        )

                    col_a, col_b = st.columns(2)

                    with col_a:
                        if st.button(
                            "✅ Mark Used",
                            key=f"used_{deal['id']}",
                        ):
                            mark_used(
                                deal["id"]
                            )
                            st.rerun()

                    with col_b:
                        if st.button(
                            "🗑 Delete",
                            key=f"delete_{deal['id']}",
                        ):
                            delete_deal(
                                deal["id"]
                            )
                            st.rerun()

        st.subheader("All Active Deals")

        if active.empty:
            st.info("No active deals.")
        else:
            display = active[
                [
                    "store",
                    "title",
                    "deal_type",
                    "discount_value",
                    "minimum_spend",
                    "promo_code",
                    "expiration_dt",
                    "household_member",
                ]
            ].copy()

            display.columns = [
                "Store",
                "Deal",
                "Type",
                "Savings ($)",
                "Minimum Purchase ($)",
                "Promo Code",
                "Expiration Date",
                "Who",
            ]

            display["Expiration Date"] = (
                display["Expiration Date"]
                .apply(
                    lambda value:
                        "Unknown"
                        if pd.isna(value)
                        else value.date()
                )
            )

            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
            )

        if not expired.empty:
            with st.expander(
                f"Expired Deals ({len(expired)})"
            ):
                expired_display = expired[
                    [
                        "store",
                        "title",
                        "expiration_dt",
                    ]
                ].copy()

                expired_display.columns = [
                    "Store",
                    "Deal",
                    "Expired",
                ]

                expired_display["Expired"] = (
                    expired_display["Expired"]
                    .dt.date
                )

                st.dataframe(
                    expired_display,
                    use_container_width=True,
                    hide_index=True,
                )


# -----------------------------
# Expiration Dashboard page
# -----------------------------

elif page == "⏰ Expiring":
    st.header("⏰ Expiration Dashboard")

    st.write(
        "See what is about to expire across the household so rewards and "
        "coupons don't disappear unused."
    )

    all_deals_for_filters = get_deals()

    available_stores = (
        sorted(
            all_deals_for_filters[
                "store"
            ]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        if not all_deals_for_filters.empty
        else []
    )

    available_members = get_household_members(
        include_household=True
    )

    filter_col1, filter_col2 = st.columns(2)

    with filter_col1:
        expiration_stores = st.multiselect(
            "Stores",
            available_stores,
            default=[],
            placeholder="All stores",
            key="expiration_stores",
        )

    with filter_col2:
        expiration_members = st.multiselect(
            "Household members",
            available_members,
            default=[],
            placeholder="Everyone",
            key="expiration_members",
        )

    expiration_deals = get_expiration_dashboard_deals(
        stores=expiration_stores or None,
        members=expiration_members or None,
    )

    if expiration_deals.empty:
        st.info(
            "No unused deals match these filters."
        )

    else:
        expiration_deals["bucket"] = expiration_deals.apply(
            lambda row: expiration_bucket(
                row["days_left"],
                row["expiration_dt"],
            ),
            axis=1,
        )

        active_for_value = expiration_deals[
            ~expiration_deals["expired"]
        ].copy()

        expiring_7 = active_for_value[
            active_for_value["days_left"].notna()
            & (active_for_value["days_left"] <= 7)
        ].copy()

        expiring_3 = active_for_value[
            active_for_value["days_left"].notna()
            & (active_for_value["days_left"] <= 3)
        ].copy()

        value_at_risk = (
            expiring_7["discount_value"]
            .fillna(0)
            .sum()
        )

        metric1, metric2, metric3, metric4 = st.columns(4)

        metric1.metric(
            "Expires ≤ 3 Days",
            len(expiring_3),
        )

        metric2.metric(
            "Expires ≤ 7 Days",
            len(expiring_7),
        )

        metric3.metric(
            "$ Value ≤ 7 Days",
            f"${value_at_risk:.2f}",
        )

        metric4.metric(
            "Unknown Expiration",
            int(
                active_for_value[
                    "expiration_dt"
                ].isna().sum()
            ),
        )

        st.caption(
            "The dollar value is the sum of listed savings/reward values. "
            "It does not assume offers can be stacked."
        )

        st.divider()

        bucket_order = [
            "Today",
            "Next 3 Days",
            "Next 7 Days",
            "Next 30 Days",
            "Later",
            "Unknown",
            "Expired",
        ]

        bucket_titles = {
            "Today": "🚨 Expires Today",
            "Next 3 Days": "⚠️ Next 3 Days",
            "Next 7 Days": "📅 Next 7 Days",
            "Next 30 Days": "🗓️ Next 30 Days",
            "Later": "Later",
            "Unknown": "❓ Expiration Unknown",
            "Expired": "Expired",
        }

        for bucket in bucket_order:
            group = expiration_deals[
                expiration_deals["bucket"] == bucket
            ].copy()

            if group.empty:
                continue

            group = group.sort_values(
                by=[
                    "expiration_dt",
                    "discount_value",
                ],
                ascending=[
                    True,
                    False,
                ],
                na_position="last",
            )

            st.subheader(
                f"{bucket_titles[bucket]} ({len(group)})"
            )

            for _, deal in group.iterrows():
                if pd.isna(
                    deal["expiration_dt"]
                ):
                    expiration_text = "Unknown"
                else:
                    expiration_text = deal[
                        "expiration_dt"
                    ].date().strftime(
                        "%b %d, %Y"
                    )

                value = float(
                    deal["discount_value"] or 0
                )

                title = (
                    f"{deal['store']} — {deal['title']} "
                    f"· {deal.get('household_member', 'Me')}"
                )

                with st.expander(
                    title,
                    expanded=(
                        bucket
                        in {
                            "Today",
                            "Next 3 Days",
                        }
                    ),
                ):
                    detail_col1, detail_col2 = st.columns(2)

                    with detail_col1:
                        st.write(
                            f"**For:** "
                            f"{deal.get('household_member', 'Me')}"
                        )

                        st.write(
                            f"**Type:** "
                            f"{deal['deal_type'] or 'Other'}"
                        )

                        st.write(
                            f"**Expires:** "
                            f"{expiration_text}"
                        )

                    with detail_col2:
                        if value > 0:
                            st.write(
                                f"**Listed value:** "
                                f"${value:.2f}"
                            )

                        minimum_spend = float(
                            deal["minimum_spend"] or 0
                        )

                        if minimum_spend > 0:
                            st.write(
                                f"**Minimum spend:** "
                                f"${minimum_spend:.2f}"
                            )

                        if deal["promo_code"]:
                            st.write(
                                f"**Promo code:** "
                                f"`{deal['promo_code']}`"
                            )

                    if deal["notes"]:
                        st.write(
                            f"**Notes:** "
                            f"{deal['notes']}"
                        )

                    if deal["source"]:
                        st.link_button(
                            "Open Source",
                            deal["source"],
                        )

                    action_col1, action_col2 = st.columns(2)

                    with action_col1:
                        if st.button(
                            "✅ Mark Used",
                            key=f"expiration_used_{deal['id']}",
                            use_container_width=True,
                        ):
                            mark_used(
                                deal["id"]
                            )
                            st.rerun()

                    with action_col2:
                        if st.button(
                            "🗑 Delete",
                            key=f"expiration_delete_{deal['id']}",
                            use_container_width=True,
                        ):
                            delete_deal(
                                deal["id"]
                            )
                            st.rerun()


# -----------------------------
# Find Deals page
# -----------------------------

elif page == "🔎 Find Deals":
    st.header("🔎 Find Deals Online")

    st.write(
        "Search the web for coupons, promotions, rewards, "
        "weekly ads, and other offers. Deal Finder now filters "
        "ordinary product pages more aggressively and tries to "
        "turn retailer snippets into useful offer titles."
    )

    st.warning(
        "A search result is not proof that a coupon still works. "
        "Check the source and offer terms before relying on it."
    )

    env_api_key = os.getenv(
        "TAVILY_API_KEY",
        "",
    ).strip()

    if env_api_key:
        st.success(
            "Tavily API key found in "
            "TAVILY_API_KEY."
        )
    else:
        st.text_input(
            "Tavily API Key",
            type="password",
            key="tavily_api_key",
            placeholder="Paste your tvly-... API key here",
            help=(
                "The key stays in this Streamlit session. "
                "For a permanent setup, set the "
                "TAVILY_API_KEY environment variable."
            ),
        )

        st.link_button(
            "Open Tavily dashboard",
            "https://app.tavily.com/",
        )

    st.divider()

    with st.form("deal_search_form"):
        st.subheader("What should I search?")

        selected_stores = st.multiselect(
            "Stores",
            list(STORE_SEARCH_CONFIG.keys()),
            default=[
                "CVS",
                "Walgreens",
                "Jack in the Box",
            ],
        )

        col1, col2 = st.columns(2)

        with col1:
            official_only = st.checkbox(
                "Official store websites only",
                value=True,
                help=(
                    "Recommended for the first pass. "
                    "Turn this off to search the broader web, "
                    "including coupon and deal sites."
                ),
            )

            freshness_label = st.selectbox(
                "How recent?",
                [
                    "Past week",
                    "Past month",
                    "Past year",
                    "Any time",
                ],
                index=1,
            )

        with col2:
            results_per_store = st.slider(
                "Results per store",
                min_value=3,
                max_value=20,
                value=8,
            )

            custom_keywords = st.text_input(
                "Optional product / keyword",
                placeholder=(
                    "toothpaste, shampoo, burgers..."
                ),
            )

        search_submitted = st.form_submit_button(
            "🔎 Search for New Deals",
            use_container_width=True,
        )

    freshness_map = {
        "Past week": "week",
        "Past month": "month",
        "Past year": "year",
        "Any time": None,
    }

    if search_submitted:
        api_key = get_api_key_from_app()

        if not api_key:
            st.error(
                "Add your Tavily API key first."
            )
        elif not selected_stores:
            st.error(
                "Choose at least one store."
            )
        else:
            with st.spinner(
                "Searching for deals..."
            ):
                found, errors = search_for_deals(
                    api_key=api_key,
                    stores=selected_stores,
                    official_only=official_only,
                    custom_keywords=custom_keywords,
                    freshness=freshness_map[
                        freshness_label
                    ],
                    results_per_store=results_per_store,
                )

            if found:
                st.success(
                    f"Search complete. "
                    f"Processed {len(found)} deal-like results."
                )
            else:
                st.info(
                    "Search completed, but no new deal-like "
                    "results were detected."
                )

            for error in errors:
                st.error(error)

    st.divider()
    st.subheader("Deal Candidates")

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(
        [1, 2, 1, 1]
    )

    with filter_col1:
        candidate_status_label = st.selectbox(
            "Status",
            [
                "New",
                "Saved",
                "Ignored",
                "All",
            ],
        )

    with filter_col2:
        candidate_store_filter = st.multiselect(
            "Filter stores",
            list(STORE_SEARCH_CONFIG.keys()),
            default=[],
            key="candidate_store_filter",
        )

    with filter_col3:
        if st.button(
            "Re-analyze",
            use_container_width=True,
            help=(
                "Re-run the improved parser on candidates already "
                "in your database. This uses no Tavily credits."
            ),
        ):
            updated, ignored = reprocess_existing_candidates()
            st.session_state["reprocess_message"] = (
                f"Re-analyzed {updated} candidates. "
                f"Moved {ignored} weak product-page results to Ignored."
            )
            st.rerun()

    with filter_col4:
        if st.button(
            "Clear Ignored",
            use_container_width=True,
        ):
            clear_ignored_candidates()
            st.rerun()

    if "reprocess_message" in st.session_state:
        st.success(
            st.session_state.pop(
                "reprocess_message"
            )
        )

    status_map = {
        "New": "new",
        "Saved": "saved",
        "Ignored": "ignored",
        "All": "all",
    }

    candidates = get_candidates(
        status=status_map[
            candidate_status_label
        ],
        stores=candidate_store_filter or None,
    )

    if candidates.empty:
        st.info(
            "No candidates in this view yet. "
            "Run a search above."
        )

    else:
        official_count = int(
            candidates["official_source"]
            .fillna(0)
            .sum()
        )

        detected_expiration_count = int(
            candidates["expiration_date"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )

        stat1, stat2, stat3 = st.columns(3)

        stat1.metric(
            "Candidates",
            len(candidates),
        )

        stat2.metric(
            "Official Sources",
            official_count,
        )

        stat3.metric(
            "Expiration Detected",
            detected_expiration_count,
        )

        for _, candidate in candidates.iterrows():
            is_receipt_candidate = str(
                candidate["url"] or ""
            ).startswith("receipt://")

            if is_receipt_candidate:
                official_badge = "🧾 Receipt"
            else:
                official_badge = (
                    "✅ Official"
                    if candidate["official_source"]
                    else "🌐 Web"
                )

            expander_title = (
                f"{candidate['store']} — "
                f"{candidate['title']} — "
                f"{official_badge}"
            )

            with st.expander(expander_title):
                if candidate["description"]:
                    st.write(
                        candidate["description"]
                    )

                info_col1, info_col2 = st.columns(2)

                with info_col1:
                    st.write(
                        f"**Type detected:** "
                        f"{candidate['deal_type'] or 'Other'}"
                    )

                    st.write(
                        f"**Savings detected:** "
                        f"${float(candidate['discount_value'] or 0):.2f}"
                    )

                    st.write(
                        f"**Minimum spend detected:** "
                        f"${float(candidate['minimum_spend'] or 0):.2f}"
                    )

                    if candidate["promo_code"]:
                        st.write(
                            f"**Promo code detected:** "
                            f"`{candidate['promo_code']}`"
                        )

                with info_col2:
                    st.write(
                        f"**Expiration detected:** "
                        f"{format_candidate_expiration(candidate['expiration_date'])}"
                    )

                    st.write(
                        f"**Source:** "
                        f"{'Scanned receipt' if is_receipt_candidate else (candidate['source_domain'] or 'Unknown')}"
                    )

                    st.write(
                        f"**Status:** "
                        f"{candidate['status'].title()}"
                    )

                    st.write(
                        f"**For:** "
                        f"{candidate.get('household_member', 'Household')}"
                    )

                    try:
                        checked_at = datetime.fromisoformat(
                            candidate["last_checked"]
                        ).strftime(
                            "%b %d, %Y %I:%M %p"
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        checked_at = (
                            candidate["last_checked"]
                            or "Unknown"
                        )

                    st.caption(
                        f"Last checked: {checked_at}"
                    )

                if (
                    candidate["url"]
                    and not is_receipt_candidate
                ):
                    st.link_button(
                        "Open Source",
                        candidate["url"],
                    )

                action_col1, action_col2, action_col3 = (
                    st.columns(3)
                )

                with action_col1:
                    if candidate["status"] != "saved":
                        if st.button(
                            "💾 Save Deal",
                            key=f"save_candidate_{candidate['id']}",
                            use_container_width=True,
                        ):
                            saved = save_candidate_as_deal(
                                candidate
                            )

                            if saved:
                                st.success(
                                    "Saved to your deals."
                                )
                            else:
                                st.info(
                                    "That deal was already "
                                    "in your saved deals."
                                )

                            st.rerun()

                with action_col2:
                    if candidate["status"] != "ignored":
                        if st.button(
                            "🙈 Ignore",
                            key=f"ignore_candidate_{candidate['id']}",
                            use_container_width=True,
                        ):
                            set_candidate_status(
                                candidate["id"],
                                "ignored",
                            )
                            st.rerun()

                with action_col3:
                    if candidate["status"] != "new":
                        if st.button(
                            "↩️ Mark New",
                            key=f"new_candidate_{candidate['id']}",
                            use_container_width=True,
                        ):
                            set_candidate_status(
                                candidate["id"],
                                "new",
                            )
                            st.rerun()


# -----------------------------
# For You page
# -----------------------------

elif page == "✨ For You":
    st.header("✨ For You")

    st.write(
        "Deal Finder uses the items saved from your past receipts to learn "
        "which shopping categories recur, match them against current saved "
        "and discovered offers, and suggest a few related things you might "
        "want to try."
    )

    purchase_history, purchase_profile = build_purchase_profile()

    if purchase_profile.empty:
        st.info(
            "I need some receipt item history first. Scan and save a few "
            "receipts with detected items, then this page can start learning "
            "what you buy."
        )

    else:
        total_items = int(
            len(
                purchase_history
            )
        )

        total_receipts = int(
            purchase_history[
                "receipt_id"
            ].nunique()
        )

        tracked_categories = int(
            len(
                purchase_profile
            )
        )

        profile_metric1, profile_metric2, profile_metric3 = (
            st.columns(3)
        )

        profile_metric1.metric(
            "Receipts Learned From",
            total_receipts,
        )

        profile_metric2.metric(
            "Items Learned From",
            total_items,
        )

        profile_metric3.metric(
            "Shopping Categories",
            tracked_categories,
        )

        st.subheader("Your Purchase Patterns")

        top_profile = purchase_profile.head(
            8
        )

        for _, profile_row in top_profile.iterrows():
            category = profile_row[
                "category"
            ]

            due_label = category_due_label(
                profile_row
            )

            typical_items = (
                profile_row[
                    "typical_items"
                ]
                if isinstance(
                    profile_row[
                        "typical_items"
                    ],
                    list,
                )
                else []
            )

            item_text = (
                ", ".join(
                    typical_items
                )
                if typical_items
                else "No examples"
            )

            with st.expander(
                f"{category} · {due_label}",
                expanded=False,
            ):
                st.write(
                    f"**Why it's here:** "
                    f"{profile_reason(profile_row)}"
                )

                st.write(
                    f"**Typical items:** "
                    f"{item_text}"
                )

                if (
                    profile_row[
                        "average_gap_days"
                    ]
                    and profile_row[
                        "purchase_count"
                    ] >= 2
                ):
                    average_gap = int(
                        round(
                            profile_row[
                                "average_gap_days"
                            ]
                        )
                    )

                    days_since = int(
                        profile_row[
                            "days_since_last"
                        ]
                    )

                    st.write(
                        f"**Pattern:** about every {average_gap} days; "
                        f"last purchase was {days_since} days ago."
                    )

        st.divider()

        offers = get_current_recommendation_offers()

        relevant_matches, explore_matches = find_profile_matches(
            purchase_profile,
            offers,
        )

        st.subheader("🎯 Relevant Right Now")

        if relevant_matches:
            st.caption(
                "These match categories you have actually bought before. "
                "Saved offers are ready for your Shop Here screen; Review "
                "offers are still candidates and should be checked first."
            )

            for index, match in enumerate(
                relevant_matches[:12]
            ):
                offer = match[
                    "offer"
                ]

                source_kind = offer[
                    "source_kind"
                ]

                badge = (
                    "✅ Saved"
                    if source_kind == "Saved"
                    else "🔎 Review"
                )

                title = (
                    f"{offer['store']} — "
                    f"{offer['title']} — {badge}"
                )

                with st.expander(
                    title,
                    expanded=(index < 3),
                ):
                    st.write(
                        f"**Why recommended:** "
                        f"{match['reason']}"
                    )

                    st.write(
                        f"**Expiration:** "
                        f"{recommendation_expiration_text(offer)}"
                    )

                    value = float(
                        offer[
                            "discount_value"
                        ]
                        or 0
                    )

                    if value > 0:
                        st.write(
                            f"**Known value:** "
                            f"${value:.2f}"
                        )

                    minimum_spend = float(
                        offer[
                            "minimum_spend"
                        ]
                        or 0
                    )

                    if minimum_spend > 0:
                        st.write(
                            f"**Minimum spend:** "
                            f"${minimum_spend:.2f}"
                        )

                    if offer[
                        "promo_code"
                    ]:
                        st.write(
                            f"**Promo code:** "
                            f"`{offer['promo_code']}`"
                        )

                    if offer[
                        "description"
                    ]:
                        st.write(
                            offer[
                                "description"
                            ]
                        )

                    if (
                        offer[
                            "source"
                        ]
                        and not str(
                            offer[
                                "source"
                            ]
                        ).startswith(
                            "receipt://"
                        )
                    ):
                        st.link_button(
                            "Open Source",
                            offer[
                                "source"
                            ],
                        )

                    if (
                        source_kind == "Review"
                        and pd.notna(
                            offer[
                                "candidate_id"
                            ]
                        )
                    ):
                        if st.button(
                            "💾 Save This Deal",
                            key=(
                                "for_you_save_"
                                + str(
                                    int(
                                        offer[
                                            "candidate_id"
                                        ]
                                    )
                                )
                            ),
                        ):
                            candidate_id = int(
                                offer[
                                    "candidate_id"
                                ]
                            )

                            candidate_rows = get_candidates(
                                status="new"
                            )

                            selected = candidate_rows[
                                candidate_rows[
                                    "id"
                                ] == candidate_id
                            ]

                            if not selected.empty:
                                save_candidate_as_deal(
                                    selected.iloc[0]
                                )

                                st.rerun()
        else:
            st.info(
                "No current saved or discovered offers match your receipt "
                "history yet. Use the personalized search below to look for some."
            )

        st.subheader("🌱 Something New to Try")

        if explore_matches:
            st.caption(
                "These are not exact repeats. They are current offers in "
                "categories related to things you already buy."
            )

            for index, match in enumerate(
                explore_matches[:8]
            ):
                offer = match[
                    "offer"
                ]

                source_kind = offer[
                    "source_kind"
                ]

                badge = (
                    "✅ Saved"
                    if source_kind == "Saved"
                    else "🔎 Review"
                )

                with st.expander(
                    f"{offer['store']} — {offer['title']} — {badge}",
                    expanded=(index < 2),
                ):
                    st.write(
                        f"**Why it might be worth trying:** "
                        f"{match['reason']}"
                    )

                    st.write(
                        f"**Expiration:** "
                        f"{recommendation_expiration_text(offer)}"
                    )

                    if offer[
                        "description"
                    ]:
                        st.write(
                            offer[
                                "description"
                            ]
                        )

                    if (
                        offer[
                            "source"
                        ]
                        and not str(
                            offer[
                                "source"
                            ]
                        ).startswith(
                            "receipt://"
                        )
                    ):
                        st.link_button(
                            "Open Source",
                            offer[
                                "source"
                            ],
                        )
        else:
            # Even without a live deal, provide a small explainable
            # exploration list from the strongest purchase categories.
            suggestion_rows = []

            for _, profile_row in purchase_profile.head(
                4
            ).iterrows():
                category = profile_row[
                    "category"
                ]

                suggestions = TRY_SUGGESTIONS.get(
                    category,
                    [],
                )

                if suggestions:
                    suggestion_rows.append(
                        {
                            "Based on": category,
                            "Ideas to try": ", ".join(
                                suggestions[:3]
                            ),
                            "Why": (
                                f"You buy {category.lower()} products."
                            ),
                        }
                    )

            if suggestion_rows:
                st.caption(
                    "I don't currently have matching live offers for adjacent "
                    "categories, so these are low-pressure ideas based only on "
                    "what you already buy."
                )

                st.dataframe(
                    pd.DataFrame(
                        suggestion_rows
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info(
                    "Once your receipt history covers a few more categories, "
                    "I can make better adjacent-category suggestions."
                )

        st.divider()

        st.subheader("🌐 Refresh Personalized Deals")

        st.write(
            "Search current retailer offers using the strongest categories "
            "from your receipt history. Results go into Find Deals first so "
            "you can review them."
        )

        env_api_key = os.getenv(
            "TAVILY_API_KEY",
            "",
        ).strip()

        if not env_api_key:
            st.text_input(
                "Tavily API Key",
                type="password",
                key="tavily_api_key",
                placeholder="Paste your tvly-... API key here",
                help=(
                    "This uses the same key as Find Deals. "
                    "The key stays in this Streamlit session."
                ),
            )
        else:
            st.success(
                "Tavily API key found in TAVILY_API_KEY."
            )

        available_categories = purchase_profile[
            "category"
        ].tolist()

        default_categories = available_categories[
            : min(
                3,
                len(
                    available_categories
                ),
            )
        ]

        search_col1, search_col2 = st.columns(2)

        with search_col1:
            personalized_categories = st.multiselect(
                "Receipt categories to search",
                available_categories,
                default=default_categories,
                key="personalized_categories",
            )

        with search_col2:
            personalized_stores = st.multiselect(
                "Stores to check",
                list(
                    STORE_SEARCH_CONFIG.keys()
                ),
                default=default_personalized_stores(),
                key="personalized_stores",
            )

        personalize_options1, personalize_options2 = st.columns(2)

        with personalize_options1:
            personalized_official_only = st.checkbox(
                "Official store websites only",
                value=True,
                key="personalized_official_only",
            )

        with personalize_options2:
            personalized_results = st.slider(
                "Results per category/store",
                min_value=2,
                max_value=8,
                value=4,
                key="personalized_results",
            )

        estimated_searches = (
            len(
                personalized_categories
            )
            * len(
                personalized_stores
            )
        )

        st.caption(
            f"This refresh can make up to {estimated_searches} Tavily "
            f"search request{'s' if estimated_searches != 1 else ''}."
        )

        if st.button(
            "✨ Find Fresh Deals for Me",
            type="primary",
            use_container_width=True,
        ):
            api_key = get_api_key_from_app()

            if not api_key:
                st.error(
                    "Add your Tavily API key first."
                )
            elif not personalized_categories:
                st.error(
                    "Choose at least one receipt category."
                )
            elif not personalized_stores:
                st.error(
                    "Choose at least one store."
                )
            else:
                with st.spinner(
                    "Searching current offers that match your receipt history..."
                ):
                    personalized_found, personalized_errors = (
                        personalized_search(
                            api_key=api_key,
                            categories=personalized_categories,
                            stores=personalized_stores,
                            official_only=personalized_official_only,
                            freshness="month",
                            results_per_search=personalized_results,
                        )
                    )

                if personalized_found:
                    st.success(
                        f"Processed {len(personalized_found)} deal-like result"
                        f"{'s' if len(personalized_found) != 1 else ''}. "
                        "They are now available for recommendation matching "
                        "and review in Find Deals."
                    )
                else:
                    st.info(
                        "The search completed, but no new deal-like results "
                        "passed the current filters."
                    )

                for error in personalized_errors:
                    st.error(
                        error
                    )

                if personalized_found:
                    st.rerun()


# -----------------------------
# Shop Here page
# -----------------------------

elif page == "🛒 Shop Here":
    st.header("🛒 Shop Here")

    st.write(
        "Choose where you're shopping and Deal Finder will gather the "
        "saved coupons, rewards, and offers you can check before paying."
    )

    st.caption(
        "This screen only treats deals you already saved to My Deals as "
        "ready to use. Unreviewed web and receipt candidates stay in Find Deals."
    )

    shop_store_options = [
        store
        for store in STORE_OPTIONS
        if store != "Other"
    ]

    control_col1, control_col2, control_col3 = st.columns(
        [1, 1, 1]
    )

    with control_col1:
        shop_store = st.selectbox(
            "Where are you shopping?",
            shop_store_options,
            key="shop_here_store",
        )

    with control_col2:
        shop_member = st.selectbox(
            "Whose offers?",
            [
                "Everyone",
                *get_household_members(
                    include_household=True
                ),
            ],
            key="shop_here_member",
            help=(
                "Everyone combines household-wide offers with "
                "each person's saved account-specific offers."
            ),
        )

    with control_col3:
        planned_spend = st.number_input(
            "Planned purchase amount ($)",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            help=(
                "Optional. Enter roughly what you expect to spend so "
                "Deal Finder can check minimum-purchase requirements."
            ),
        )

    shop_deals = prepare_shop_deals(
        shop_store
    )

    if (
        not shop_deals.empty
        and shop_member != "Everyone"
    ):
        if shop_member == "Household":
            shop_deals = shop_deals[
                shop_deals["household_member"] == "Household"
            ].copy()
        else:
            shop_deals = shop_deals[
                shop_deals["household_member"].isin(
                    [
                        "Household",
                        shop_member,
                    ]
                )
            ].copy()

    if shop_deals.empty:
        st.info(
            f"You don't currently have any active saved deals for {shop_store}. "
            "Use Find Deals or scan a receipt, then save a useful candidate "
            "to My Deals."
        )

    else:
        known_dollar_savings = (
            shop_deals["discount_value"]
            .fillna(0)
            .sum()
        )

        expiring_soon_count = int(
            (
                shop_deals["days_left"]
                .notna()
                & (shop_deals["days_left"] <= 3)
            ).sum()
        )

        threshold_met_count = 0
        threshold_pending_count = 0

        for _, row in shop_deals.iterrows():
            _, status = shop_spend_status(
                row["minimum_spend"],
                planned_spend,
            )

            if status is True:
                threshold_met_count += 1
            elif status is None:
                threshold_pending_count += 1

        metric1, metric2, metric3, metric4 = st.columns(4)

        metric1.metric(
            "Saved Offers",
            len(shop_deals),
        )

        metric2.metric(
            "Expiring ≤ 3 Days",
            expiring_soon_count,
        )

        metric3.metric(
            "Known $ Value",
            f"${known_dollar_savings:.2f}",
        )

        metric4.metric(
            "Spend Rules Met",
            (
                str(threshold_met_count)
                if planned_spend > 0
                else "—"
            ),
        )

        st.caption(
            "Known $ Value is simply the sum of listed dollar values. "
            "Offers may not stack, and percentage/free-item offers are not "
            "included in that dollar total."
        )

        st.divider()

        urgent = shop_deals[
            shop_deals["days_left"]
            .notna()
            & (shop_deals["days_left"] <= 3)
        ]

        if not urgent.empty:
            st.subheader("⚠️ Use Soon")

            for _, deal in urgent.iterrows():
                st.write(
                    f"**{deal['title']}** — "
                    f"{shop_deal_expiration_text(deal)}"
                )

        st.subheader(
            f"Available at {shop_store}"
        )

        for _, deal in shop_deals.iterrows():
            expiration_text = shop_deal_expiration_text(
                deal
            )

            spend_text, spend_status = shop_spend_status(
                deal["minimum_spend"],
                planned_spend,
            )

            savings_value = float(
                deal["discount_value"] or 0
            )

            title_bits = [
                str(
                    deal["title"]
                )
            ]

            if savings_value > 0:
                title_bits.append(
                    f"${savings_value:.2f} value"
                )

            expander_title = " · ".join(
                title_bits
            )

            with st.expander(
                expander_title,
                expanded=False,
            ):
                info_col1, info_col2 = st.columns(2)

                with info_col1:
                    st.write(
                        f"**Type:** "
                        f"{deal['deal_type'] or 'Other'}"
                    )

                    st.write(
                        f"**For:** "
                        f"{deal.get('household_member', 'Me')}"
                    )

                    st.write(
                        f"**Expiration:** "
                        f"{expiration_text}"
                    )

                    if deal["promo_code"]:
                        st.write(
                            f"**Promo code:** "
                            f"`{deal['promo_code']}`"
                        )

                with info_col2:
                    if spend_status is True:
                        st.success(
                            spend_text
                        )
                    elif spend_status is False:
                        st.warning(
                            spend_text
                        )
                    else:
                        st.info(
                            spend_text
                        )

                    if savings_value > 0:
                        st.write(
                            f"**Listed savings:** "
                            f"${savings_value:.2f}"
                        )

                if deal["notes"]:
                    st.write(
                        f"**Notes:** "
                        f"{deal['notes']}"
                    )

                if deal["source"]:
                    st.link_button(
                        "Open Source",
                        deal["source"],
                    )

                action_col1, action_col2 = st.columns(
                    [1, 1]
                )

                with action_col1:
                    if st.button(
                        "✅ Mark Used",
                        key=f"shop_used_{deal['id']}",
                        use_container_width=True,
                    ):
                        mark_used(
                            deal["id"]
                        )

                        st.rerun()

                with action_col2:
                    if st.button(
                        "🗑 Delete",
                        key=f"shop_delete_{deal['id']}",
                        use_container_width=True,
                    ):
                        delete_deal(
                            deal["id"]
                        )

                        st.rerun()

        st.divider()

        st.subheader("Quick Checkout Checklist")

        checklist_rows = []

        for _, deal in shop_deals.iterrows():
            spend_text, spend_status = shop_spend_status(
                deal["minimum_spend"],
                planned_spend,
            )

            if spend_status is True:
                threshold_label = "Ready by spend rule"
            elif spend_status is False:
                threshold_label = "Spend threshold not met"
            else:
                threshold_label = spend_text

            checklist_rows.append(
                {
                    "Deal": deal["title"],
                    "Who": deal.get(
                        "household_member",
                        "Me",
                    ),
                    "Expires": shop_deal_expiration_text(
                        deal
                    ),
                    "Spend Check": threshold_label,
                    "Promo Code": (
                        deal["promo_code"]
                        if deal["promo_code"]
                        else ""
                    ),
                }
            )

        checklist_df = pd.DataFrame(
            checklist_rows
        )

        st.dataframe(
            checklist_df,
            use_container_width=True,
            hide_index=True,
        )


# -----------------------------
# Missed Savings page
# -----------------------------

elif page == "🕵️ Missed Savings":
    st.header("🕵️ Missed Savings")

    st.write(
        "Compare a saved receipt with the unused deals Deal Finder knew about "
        "for that store and household member. This is a screening tool: it "
        "flags offers that may have applied, not guaranteed missed savings."
    )

    st.caption(
        "Stacking rules, excluded products, account eligibility, and offers "
        "saved after an older receipt can all affect whether a deal truly applied."
    )

    receipts_for_review = get_receipts()

    if receipts_for_review.empty:
        st.info(
            "No saved receipts yet. Scan and save a receipt first."
        )

    else:
        receipt_choices = []

        for _, receipt in receipts_for_review.iterrows():
            receipt_id = int(
                receipt[
                    "id"
                ]
            )

            store = (
                receipt[
                    "store"
                ]
                or "Unknown Store"
            )

            purchase_date = (
                receipt[
                    "purchase_date"
                ]
                or "Unknown date"
            )

            member = (
                receipt.get(
                    "household_member",
                    "Me",
                )
                or "Me"
            )

            total = float(
                receipt[
                    "total"
                ]
                or 0
            )

            receipt_choices.append(
                {
                    "id": receipt_id,
                    "label": (
                        f"#{receipt_id} · {store} · {purchase_date} · "
                        f"{member} · ${total:.2f}"
                    ),
                }
            )

        label_to_id = {
            row[
                "label"
            ]: row[
                "id"
            ]
            for row in receipt_choices
        }

        selected_receipt_label = st.selectbox(
            "Receipt to review",
            list(
                label_to_id.keys()
            ),
            key="missed_savings_receipt",
        )

        selected_receipt_id = label_to_id[
            selected_receipt_label
        ]

        selected_receipt = get_receipt_record(
            selected_receipt_id
        )

        if selected_receipt is not None:
            receipt_col1, receipt_col2, receipt_col3, receipt_col4 = (
                st.columns(4)
            )

            receipt_col1.metric(
                "Store",
                selected_receipt.get(
                    "store",
                    "Unknown",
                )
                or "Unknown",
            )

            receipt_col2.metric(
                "Receipt Total",
                f"${float(selected_receipt.get('total', 0) or 0):.2f}",
            )

            receipt_col3.metric(
                "Purchase Date",
                selected_receipt.get(
                    "purchase_date",
                    "Unknown",
                )
                or "Unknown",
            )

            receipt_col4.metric(
                "For",
                selected_receipt.get(
                    "household_member",
                    "Me",
                )
                or "Me",
            )

            saved_items = get_receipt_items(
                selected_receipt_id
            )

            if not saved_items.empty:
                with st.expander(
                    "Purchased items"
                ):
                    item_display = saved_items[
                        [
                            "item_name",
                            "price",
                            "quantity",
                        ]
                    ].copy()

                    item_display.columns = [
                        "Item",
                        "Price",
                        "Qty",
                    ]

                    st.dataframe(
                        item_display,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Price": st.column_config.NumberColumn(
                                "Price",
                                format="$%.2f",
                            ),
                            "Qty": st.column_config.NumberColumn(
                                "Qty",
                                format="%.2f",
                            ),
                        },
                    )

            possible_misses = analyze_missed_savings_for_receipt(
                selected_receipt_id
            )

            st.divider()

            if not possible_misses:
                st.success(
                    "No obvious unused saved offers matched this receipt."
                )

            else:
                known_value = sum(
                    row[
                        "discount_value"
                    ]
                    for row in possible_misses
                )

                metric1, metric2 = st.columns(2)

                metric1.metric(
                    "Possible Matches",
                    len(
                        possible_misses
                    ),
                )

                metric2.metric(
                    "Listed $ Value",
                    f"${known_value:.2f}",
                )

                st.caption(
                    "Listed $ Value is not a claim that you definitely missed "
                    "that amount. Offers may not stack and some may have product "
                    "or account restrictions."
                )

                st.subheader(
                    "Offers That May Have Applied"
                )

                for index, miss in enumerate(
                    possible_misses
                ):
                    value = float(
                        miss[
                            "discount_value"
                        ]
                        or 0
                    )

                    title = miss[
                        "title"
                    ]

                    if value > 0:
                        title += (
                            f" · ${value:.2f} listed value"
                        )

                    with st.expander(
                        title,
                        expanded=(
                            index < 3
                        ),
                    ):
                        st.write(
                            f"**For:** "
                            f"{miss['household_member']}"
                        )

                        st.write(
                            f"**Type:** "
                            f"{miss['deal_type']}"
                        )

                        if miss[
                            "expiration_date"
                        ]:
                            st.write(
                                f"**Expiration:** "
                                f"{miss['expiration_date']}"
                            )
                        else:
                            st.write(
                                "**Expiration:** Unknown"
                            )

                        if miss[
                            "minimum_spend"
                        ] > 0:
                            st.write(
                                f"**Minimum spend:** "
                                f"${miss['minimum_spend']:.2f}"
                            )

                        if miss[
                            "promo_code"
                        ]:
                            st.write(
                                f"**Promo code:** "
                                f"`{miss['promo_code']}`"
                            )

                        st.write(
                            "**Why it was flagged:**"
                        )

                        for reason in miss[
                            "reasons"
                        ]:
                            st.write(
                                f"- {reason}"
                            )

                        if miss[
                            "notes"
                        ]:
                            st.write(
                                f"**Deal notes:** "
                                f"{miss['notes']}"
                            )

                        if miss[
                            "source"
                        ]:
                            st.link_button(
                                "Open Deal Source",
                                miss[
                                    "source"
                                ],
                            )

                st.info(
                    "If one of these really was used, mark the saved deal as "
                    "used from Deals/Shop Here. If it was not applicable, leave "
                    "it alone; the detector is intentionally conservative."
                )


# -----------------------------
# Arrival Mode page
# -----------------------------

elif page == "📍 Arrival Mode":
    st.header("📍 Store Arrival Mode")

    st.write(
        "Use your phone's current location to recognize a nearby supported "
        "store and surface the saved offers you may want before checkout."
    )

    st.info(
        "This version works while this page is open. Your browser asks for "
        "location permission when you tap the button below. Deal Finder uses "
        "the coordinates for the nearby lookup but does not save them in "
        "deals.db."
    )

    components.html(
        location_capture_html(),
        height=95,
    )

    arrival_coords = get_arrival_mode_coordinates()

    if arrival_coords is None:
        st.caption(
            "Tap Check My Current Location on your phone when you arrive "
            "at a store. Safari may ask you to allow location access."
        )

    else:
        accuracy = arrival_coords[
            "accuracy"
        ]

        if accuracy > 0:
            st.caption(
                f"Phone-reported location accuracy: about {accuracy:.0f} meters."
            )

        radius_options = {
            "Very close (250 m)": 250,
            "Nearby (500 m)": 500,
            "Wider search (1 km)": 1000,
        }

        radius_label = st.selectbox(
            "Nearby search radius",
            list(
                radius_options.keys()
            ),
            index=1,
            key="arrival_radius",
        )

        radius_m = radius_options[
            radius_label
        ]

        try:
            with st.spinner(
                "Checking for nearby supported stores..."
            ):
                nearby_stores = find_nearby_supported_stores(
                    arrival_coords[
                        "lat"
                    ],
                    arrival_coords[
                        "lon"
                    ],
                    radius_m=radius_m,
                )
        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
        ) as exc:
            nearby_stores = []

            st.error(
                f"Nearby-store lookup failed: {exc}"
            )

        if not nearby_stores:
            st.warning(
                "I couldn't find CVS, Walgreens, Jack in the Box, Target, "
                "Walmart, or Safeway within this radius. You can increase "
                "the radius or refresh your location."
            )

        else:
            nearest = nearby_stores[0]

            distance_m = float(
                nearest[
                    "distance_m"
                ]
            )

            if distance_m <= 150:
                st.success(
                    f"You appear to be at or very near "
                    f"{nearest['name']} ({distance_m:.0f} m away)."
                )
            elif distance_m <= 400:
                st.info(
                    f"Closest supported store: "
                    f"{nearest['name']} ({distance_m:.0f} m away)."
                )
            else:
                st.info(
                    f"Nearest supported store found: "
                    f"{nearest['name']} ({distance_m:.0f} m away)."
                )

            if len(
                nearby_stores
            ) > 1:
                with st.expander(
                    "Other nearby supported stores"
                ):
                    nearby_rows = []

                    for store in nearby_stores:
                        nearby_rows.append(
                            {
                                "Store": store[
                                    "name"
                                ],
                                "Chain": store[
                                    "store"
                                ],
                                "Distance (m)": round(
                                    store[
                                        "distance_m"
                                    ]
                                ),
                                "Address": store[
                                    "address"
                                ],
                            }
                        )

                    st.dataframe(
                        pd.DataFrame(
                            nearby_rows
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

            arrival_member = st.selectbox(
                "Whose offers should I show?",
                [
                    "Everyone",
                    *get_household_members(
                        include_household=True
                    ),
                ],
                key="arrival_member",
            )

            arrival_store = nearest[
                "store"
            ]

            arrival_deals = prepare_shop_deals(
                arrival_store
            )

            arrival_deals = filter_deals_for_member(
                arrival_deals,
                arrival_member,
            )

            st.divider()

            st.subheader(
                f"Offers for {arrival_store}"
            )

            if arrival_deals.empty:
                st.info(
                    f"No active saved deals for {arrival_store} match "
                    f"this household filter right now."
                )

                st.caption(
                    "Try Find Deals or For You to discover more offers, then "
                    "save the useful ones so they appear here on arrival."
                )

            else:
                known_value = (
                    arrival_deals[
                        "discount_value"
                    ]
                    .fillna(0)
                    .sum()
                )

                urgent_count = int(
                    (
                        arrival_deals[
                            "days_left"
                        ]
                        .notna()
                        & (
                            arrival_deals[
                                "days_left"
                            ]
                            <= 3
                        )
                    ).sum()
                )

                metric1, metric2, metric3 = st.columns(3)

                metric1.metric(
                    "Saved Offers",
                    len(
                        arrival_deals
                    ),
                )

                metric2.metric(
                    "Expiring ≤ 3 Days",
                    urgent_count,
                )

                metric3.metric(
                    "Known $ Value",
                    f"${known_value:.2f}",
                )

                st.caption(
                    "Known $ Value is the sum of explicit dollar values only. "
                    "It does not assume offers can be stacked."
                )

                for _, deal in arrival_deals.iterrows():
                    expiration_text = shop_deal_expiration_text(
                        deal
                    )

                    value = float(
                        deal[
                            "discount_value"
                        ]
                        or 0
                    )

                    with st.expander(
                        (
                            f"{deal['title']} · "
                            f"{deal.get('household_member', 'Me')}"
                        ),
                        expanded=(
                            pd.notna(
                                deal[
                                    "days_left"
                                ]
                            )
                            and deal[
                                "days_left"
                            ]
                            <= 3
                        ),
                    ):
                        detail_col1, detail_col2 = st.columns(2)

                        with detail_col1:
                            st.write(
                                f"**For:** "
                                f"{deal.get('household_member', 'Me')}"
                            )

                            st.write(
                                f"**Type:** "
                                f"{deal['deal_type'] or 'Other'}"
                            )

                            st.write(
                                f"**Expiration:** "
                                f"{expiration_text}"
                            )

                        with detail_col2:
                            if value > 0:
                                st.write(
                                    f"**Listed value:** "
                                    f"${value:.2f}"
                                )

                            minimum_spend = float(
                                deal[
                                    "minimum_spend"
                                ]
                                or 0
                            )

                            if minimum_spend > 0:
                                st.write(
                                    f"**Minimum spend:** "
                                    f"${minimum_spend:.2f}"
                                )

                            if deal[
                                "promo_code"
                            ]:
                                st.write(
                                    f"**Promo code:** "
                                    f"`{deal['promo_code']}`"
                                )

                        if deal[
                            "notes"
                        ]:
                            st.write(
                                f"**Notes:** "
                                f"{deal['notes']}"
                            )

                        if deal[
                            "source"
                        ]:
                            st.link_button(
                                "Open Source",
                                deal[
                                    "source"
                                ],
                            )

                        if st.button(
                            "✅ Mark Used",
                            key=(
                                f"arrival_used_"
                                f"{deal['id']}"
                            ),
                            use_container_width=True,
                        ):
                            mark_used(
                                deal[
                                    "id"
                                ]
                            )

                            st.rerun()

            st.divider()

            st.subheader(
                "Foreground Arrival Mode"
            )

            st.write(
                "Leave this page open while you're shopping and tap "
                "**Check My Current Location** again whenever you want to "
                "refresh where you are."
            )

            st.caption(
                "True background geofencing and automatic lock-screen "
                "notifications require a native iPhone app. That would be "
                "a later Xcode feature once the Deal Finder logic is stable."
            )


# -----------------------------
# Household page
# -----------------------------

elif page == "👥 Household":
    st.header("👥 Household")

    st.write(
        "Keep account-specific coupons and rewards separate by person, "
        "while still seeing the household's combined opportunities."
    )

    st.caption(
        "Use the special Household owner for public offers anyone can use. "
        "Use a person's name for CVS/Walgreens account rewards, receipt rewards, "
        "or other offers tied to one person's account."
    )

    summary = get_household_summary()

    if not summary.empty:
        summary_display = summary.copy()

        summary_display.columns = [
            "Member",
            "Active Saved Deals",
            "Saved Receipts",
        ]

        st.dataframe(
            summary_display,
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Add Household Member")

    with st.form(
        "add_household_member_form"
    ):
        new_member_name = st.text_input(
            "Name",
            placeholder="Alex",
        )

        add_member_submit = st.form_submit_button(
            "Add Member"
        )

        if add_member_submit:
            success, message = add_household_member(
                new_member_name
            )

            if success:
                st.success(
                    message
                )
                st.rerun()
            else:
                st.error(
                    message
                )

    removable_members = [
        member
        for member in get_household_members()
        if member != "Me"
    ]

    if removable_members:
        st.subheader("Remove Member")

        st.warning(
            "Removing a member does not delete their history. "
            "Their existing deals, candidates, and receipts are reassigned to Me."
        )

        remove_member_name = st.selectbox(
            "Member to remove",
            removable_members,
            key="remove_household_member",
        )

        if st.button(
            "Remove Member",
            key="remove_household_member_button",
        ):
            success, message = remove_household_member(
                remove_member_name
            )

            if success:
                st.success(
                    message
                )
                st.rerun()
            else:
                st.error(
                    message
                )

    st.divider()

    st.subheader("Combined Active Deals")

    active_household_deals = get_deals()

    if active_household_deals.empty:
        st.info(
            "No saved deals yet."
        )
    else:
        active_household_deals = active_household_deals[
            active_household_deals["used"] == 0
        ].copy()

        active_household_deals["expiration_dt"] = pd.to_datetime(
            active_household_deals["expiration_date"],
            errors="coerce",
        )

        today = pd.Timestamp(
            date.today()
        )

        active_household_deals = active_household_deals[
            active_household_deals["expiration_dt"].isna()
            | (
                active_household_deals["expiration_dt"]
                >= today
            )
        ].copy()

        if active_household_deals.empty:
            st.info(
                "No active household deals."
            )
        else:
            household_display = active_household_deals[
                [
                    "household_member",
                    "store",
                    "title",
                    "discount_value",
                    "minimum_spend",
                    "expiration_dt",
                ]
            ].copy()

            household_display.columns = [
                "Who",
                "Store",
                "Deal",
                "Value ($)",
                "Min Spend ($)",
                "Expires",
            ]

            household_display["Expires"] = (
                household_display["Expires"]
                .apply(
                    lambda value:
                        "Unknown"
                        if pd.isna(value)
                        else value.date()
                )
            )

            st.dataframe(
                household_display,
                use_container_width=True,
                hide_index=True,
            )


# -----------------------------
# Scan Receipt page
# -----------------------------

elif page == "Scan Receipt":
    st.header("🧾 Scan a Receipt")

    st.write(
        "Take a picture with your phone, or upload a receipt "
        "photo you already have."
    )

    st.info(
        "Deal Finder reads receipt text locally with Apple's Vision, "
        "identifies the merchant, date, total and purchased items, and now "
        "detects coupons and rewards. Future-use rewards can be sent to "
        "Find Deals for review. The receipt image is not sent to an OCR service."
    )

    camera_tab, upload_tab = st.tabs(
        [
            "📷 Camera",
            "🖼️ Upload Photo",
        ]
    )

    receipt_photo = None

    with camera_tab:
        camera_photo = st.camera_input(
            "Take a picture of your receipt"
        )

        if camera_photo is not None:
            receipt_photo = camera_photo

    with upload_tab:
        uploaded_photo = st.file_uploader(
            "Choose a receipt image",
            type=[
                "jpg",
                "jpeg",
                "png",
                "heic",
            ],
        )

        if uploaded_photo is not None:
            receipt_photo = uploaded_photo

    if receipt_photo is not None:
        current_receipt_hash = receipt_fingerprint(
            receipt_photo
        )

        if (
            st.session_state.get(
                "active_receipt_hash"
            )
            != current_receipt_hash
        ):
            st.session_state[
                "active_receipt_hash"
            ] = current_receipt_hash

            st.session_state.pop(
                "receipt_ocr_text",
                None,
            )

            st.session_state.pop(
                "receipt_detected_store",
                None,
            )

            st.session_state.pop(
                "receipt_store",
                None,
            )

            st.session_state.pop(
                "receipt_ocr_error",
                None,
            )

            st.session_state.pop(
                "receipt_detected_date",
                None,
            )

            st.session_state.pop(
                "receipt_detected_total",
                None,
            )

            st.session_state.pop(
                "receipt_detected_items",
                None,
            )

            st.session_state.pop(
                "receipt_detected_benefits",
                None,
            )

        st.subheader("Receipt Preview")

        st.image(
            receipt_photo,
            use_container_width=True,
        )

        read_col, privacy_col = st.columns(
            [1, 2]
        )

        with read_col:
            read_receipt = st.button(
                "✨ Read Receipt",
                type="primary",
                use_container_width=True,
            )

        with privacy_col:
            st.caption(
                "OCR runs on your Mac using Apple Vision. "
                "The image stays local."
            )

        if read_receipt:
            with st.spinner(
                "Reading receipt with Apple Vision..."
            ):
                ocr_text, ocr_error = run_apple_vision_ocr(
                    receipt_photo.getvalue(),
                    suffix=receipt_image_suffix(
                        receipt_photo
                    ),
                )

            if ocr_error:
                st.session_state[
                    "receipt_ocr_error"
                ] = ocr_error

                st.session_state[
                    "receipt_ocr_text"
                ] = ""
            else:
                detected_store, merchant_score = (
                    detect_merchant_from_text(
                        ocr_text
                    )
                )

                detected_date = extract_receipt_date(
                    ocr_text
                )

                detected_total = extract_receipt_total(
                    ocr_text
                )

                detected_items = extract_receipt_items(
                    ocr_text
                )

                detected_benefits = extract_receipt_benefits(
                    ocr_text,
                    detected_store,
                )

                st.session_state[
                    "receipt_ocr_text"
                ] = ocr_text

                st.session_state[
                    "receipt_detected_store"
                ] = detected_store

                st.session_state[
                    "receipt_merchant_score"
                ] = merchant_score

                st.session_state[
                    "receipt_detected_date"
                ] = detected_date

                st.session_state[
                    "receipt_detected_total"
                ] = detected_total

                st.session_state[
                    "receipt_detected_items"
                ] = detected_items

                st.session_state[
                    "receipt_detected_benefits"
                ] = detected_benefits

                st.session_state[
                    "receipt_store"
                ] = detected_store

                st.session_state.pop(
                    "receipt_ocr_error",
                    None,
                )

        ocr_error = st.session_state.get(
            "receipt_ocr_error"
        )

        ocr_text = st.session_state.get(
            "receipt_ocr_text",
            "",
        )

        detected_store = st.session_state.get(
            "receipt_detected_store"
        )

        detected_date = st.session_state.get(
            "receipt_detected_date"
        )

        detected_total = st.session_state.get(
            "receipt_detected_total"
        )

        detected_items = st.session_state.get(
            "receipt_detected_items",
            [],
        )

        detected_benefits = st.session_state.get(
            "receipt_detected_benefits",
            [],
        )

        if ocr_error:
            st.error(
                ocr_error
            )

            if "Swift was not found" in ocr_error:
                st.code(
                    "xcode-select --install",
                    language="bash",
                )

        if ocr_text:
            summary_col1, summary_col2, summary_col3 = st.columns(3)

            with summary_col1:
                if (
                    detected_store
                    and detected_store != "Other"
                ):
                    st.metric(
                        "Merchant",
                        detected_store,
                    )
                else:
                    st.metric(
                        "Merchant",
                        "Not detected",
                    )

            with summary_col2:
                st.metric(
                    "Purchase Date",
                    (
                        detected_date.strftime("%b %d, %Y")
                        if detected_date
                        else "Not detected"
                    ),
                )

            with summary_col3:
                st.metric(
                    "Receipt Total",
                    (
                        f"${detected_total:.2f}"
                        if detected_total is not None
                        else "Not detected"
                    ),
                )

            if (
                not detected_store
                or detected_store == "Other"
            ):
                st.warning(
                    "I read the receipt, but could not identify "
                    "the merchant automatically. You can choose it below."
                )

            with st.expander(
                "Recognized receipt text",
                expanded=False,
            ):
                st.text_area(
                    "OCR text",
                    value=ocr_text,
                    height=300,
                    disabled=True,
                    label_visibility="collapsed",
                )

        edited_items = pd.DataFrame(
            columns=[
                "Include",
                "Item",
                "Price",
                "Qty",
                "Raw Line",
            ]
        )

        if ocr_text:
            st.subheader("🛒 Detected Items")

            if detected_items:
                st.caption(
                    "Review these before saving. You can edit names, prices, "
                    "quantities, or uncheck anything that is not actually a product."
                )

                items_df = pd.DataFrame(
                    detected_items
                )

                edited_items = st.data_editor(
                    items_df,
                    use_container_width=True,
                    hide_index=True,
                    key=(
                        "receipt_items_editor_"
                        + current_receipt_hash[:12]
                    ),
                    column_config={
                        "Include": st.column_config.CheckboxColumn(
                            "Keep",
                            help="Uncheck lines that are not purchased items.",
                            default=True,
                        ),
                        "Item": st.column_config.TextColumn(
                            "Item",
                            required=True,
                        ),
                        "Price": st.column_config.NumberColumn(
                            "Price",
                            min_value=0.0,
                            format="$%.2f",
                        ),
                        "Qty": st.column_config.NumberColumn(
                            "Qty",
                            min_value=0.01,
                            format="%.2f",
                        ),
                        "Raw Line": st.column_config.TextColumn(
                            "OCR Source",
                            disabled=True,
                        ),
                    },
                    disabled=[
                        "Raw Line",
                    ],
                )

                included_items = edited_items[
                    edited_items["Include"] == True
                ]

                detected_item_total = (
                    included_items["Price"]
                    .fillna(0)
                    .sum()
                    if not included_items.empty
                    else 0
                )

                item_metric1, item_metric2 = st.columns(2)

                item_metric1.metric(
                    "Detected Products",
                    len(included_items),
                )

                item_metric2.metric(
                    "Detected Item Sum",
                    f"${detected_item_total:.2f}",
                )

                st.caption(
                    "The item sum may differ from the receipt total because of "
                    "taxes, coupons, rewards, deposits, or OCR lines that still "
                    "need correction."
                )
            else:
                st.warning(
                    "No individual product lines were detected automatically. "
                    "The receipt can still be saved."
                )

        edited_benefits = pd.DataFrame(
            columns=[
                "Include",
                "Direction",
                "Type",
                "Value",
                "Percent",
                "Description",
                "Expires",
                "Raw Line",
            ]
        )

        if ocr_text:
            st.subheader("💸 Coupons & Rewards")

            if detected_benefits:
                st.caption(
                    "Review what Deal Finder found. 'Used' means it reduced "
                    "this purchase. 'Earned' means it looks usable later. "
                    "Earned rows become reviewable candidates in Find Deals "
                    "after you save the receipt."
                )

                benefits_df = pd.DataFrame(
                    detected_benefits
                )

                edited_benefits = st.data_editor(
                    benefits_df,
                    use_container_width=True,
                    hide_index=True,
                    key=(
                        "receipt_benefits_editor_"
                        + current_receipt_hash[:12]
                    ),
                    column_config={
                        "Include": st.column_config.CheckboxColumn(
                            "Keep",
                            default=True,
                        ),
                        "Direction": st.column_config.SelectboxColumn(
                            "Status",
                            options=[
                                "Used",
                                "Earned",
                                "Detected",
                            ],
                            required=True,
                        ),
                        "Type": st.column_config.SelectboxColumn(
                            "Type",
                            options=[
                                "Store Reward",
                                "Coupon",
                                "Promo Code",
                                "Discount",
                                "Cashback",
                                "Free Item",
                                "Other",
                            ],
                            required=True,
                        ),
                        "Value": st.column_config.NumberColumn(
                            "Value",
                            min_value=0.0,
                            format="$%.2f",
                        ),
                        "Percent": st.column_config.NumberColumn(
                            "%",
                            min_value=0.0,
                            max_value=100.0,
                            format="%.1f%%",
                        ),
                        "Description": st.column_config.TextColumn(
                            "Description",
                        ),
                        "Expires": st.column_config.TextColumn(
                            "Expires",
                            help="YYYY-MM-DD when detected. You can correct it.",
                        ),
                        "Raw Line": st.column_config.TextColumn(
                            "OCR Source",
                            disabled=True,
                        ),
                    },
                    disabled=[
                        "Raw Line",
                    ],
                )

                kept_benefits = edited_benefits[
                    edited_benefits["Include"] == True
                ]

                used_rows = kept_benefits[
                    kept_benefits["Direction"] == "Used"
                ]

                earned_rows = kept_benefits[
                    kept_benefits["Direction"] == "Earned"
                ]

                used_value = (
                    used_rows["Value"]
                    .fillna(0)
                    .sum()
                    if not used_rows.empty
                    else 0
                )

                earned_value = (
                    earned_rows["Value"]
                    .fillna(0)
                    .sum()
                    if not earned_rows.empty
                    else 0
                )

                benefit_metric1, benefit_metric2, benefit_metric3 = (
                    st.columns(3)
                )

                benefit_metric1.metric(
                    "Savings Used",
                    f"${used_value:.2f}",
                )

                benefit_metric2.metric(
                    "Future Benefits",
                    len(earned_rows),
                )

                benefit_metric3.metric(
                    "Future $ Value",
                    f"${earned_value:.2f}",
                )
            else:
                st.info(
                    "No coupons or rewards were confidently detected on "
                    "this receipt. You can still save the receipt normally."
                )

        default_store = (
            detected_store
            if detected_store in STORE_OPTIONS
            else "Other"
        )

        default_store_index = (
            STORE_OPTIONS.index(
                default_store
            )
        )

        receipt_household_member = st.selectbox(
            "Whose receipt is this?",
            get_household_members(),
            key="receipt_household_member",
            help=(
                "Account-specific rewards found on this receipt "
                "will stay attached to this household member."
            ),
        )

        with st.form("receipt_details_form"):
            col1, col2 = st.columns(2)

            with col1:
                receipt_store = st.selectbox(
                    "Store",
                    STORE_OPTIONS,
                    index=default_store_index,
                    key="receipt_store",
                    help=(
                        "Deal Finder fills this automatically "
                        "after Read Receipt, but you can override it."
                    ),
                )

                purchase_date = st.date_input(
                    "Purchase Date",
                    value=(
                        detected_date
                        if detected_date
                        else date.today()
                    ),
                    help=(
                        "Filled automatically when Deal Finder can "
                        "recognize the transaction date."
                    ),
                )

            with col2:
                receipt_total = st.number_input(
                    "Receipt Total ($)",
                    min_value=0.0,
                    value=(
                        float(detected_total)
                        if detected_total is not None
                        else 0.0
                    ),
                    step=0.01,
                    format="%.2f",
                    help=(
                        "Filled automatically when Deal Finder can "
                        "recognize the final total."
                    ),
                )

            receipt_notes = st.text_area(
                "Notes",
                placeholder=(
                    "Example: earned $5 ExtraBucks, "
                    "used a $3 coupon, etc."
                ),
            )

            save_receipt = st.form_submit_button(
                "Save Receipt"
            )

            if save_receipt:
                image_path = save_receipt_image(
                    receipt_photo
                )

                receipt_id = add_receipt(
                    image_path,
                    receipt_store,
                    purchase_date.isoformat(),
                    receipt_total,
                    receipt_notes.strip(),
                    ocr_text=ocr_text,
                    household_member=receipt_household_member,
                )

                items_to_save = []

                if not edited_items.empty:
                    included = edited_items[
                        edited_items["Include"] == True
                    ]

                    items_to_save = (
                        included[
                            [
                                "Item",
                                "Price",
                                "Qty",
                                "Raw Line",
                            ]
                        ]
                        .to_dict(
                            orient="records"
                        )
                    )

                add_receipt_items(
                    receipt_id,
                    items_to_save,
                )

                benefits_to_save = []

                if not edited_benefits.empty:
                    included_benefits = edited_benefits[
                        edited_benefits["Include"] == True
                    ]

                    benefits_to_save = (
                        included_benefits[
                            [
                                "Direction",
                                "Type",
                                "Value",
                                "Percent",
                                "Description",
                                "Expires",
                                "Raw Line",
                            ]
                        ]
                        .to_dict(
                            orient="records"
                        )
                    )

                add_receipt_benefits(
                    receipt_id,
                    benefits_to_save,
                )

                receipt_candidate_count = create_receipt_deal_candidates(
                    receipt_id,
                    receipt_store,
                    benefits_to_save,
                    household_member=receipt_household_member,
                )

                message = (
                    f"Receipt saved with {len(items_to_save)} item"
                    f"{'s' if len(items_to_save) != 1 else ''} and "
                    f"{len(benefits_to_save)} coupon/reward record"
                    f"{'s' if len(benefits_to_save) != 1 else ''}."
                )

                if receipt_candidate_count:
                    message += (
                        f" Added {receipt_candidate_count} future benefit"
                        f"{'s' if receipt_candidate_count != 1 else ''} "
                        "to Find Deals for review."
                    )

                st.success(
                    message
                )
                st.rerun()


# -----------------------------
# Receipts page
# -----------------------------

elif page == "Receipts":
    st.header("🧾 Saved Receipts")

    receipts = get_receipts()

    if receipts.empty:
        st.info(
            "No receipts saved yet. "
            "Use Scan Receipt to add one."
        )

    else:
        total_spent = (
            receipts["total"]
            .fillna(0)
            .sum()
        )

        col1, col2 = st.columns(2)

        col1.metric(
            "Saved Receipts",
            len(receipts),
        )

        col2.metric(
            "Recorded Spending",
            f"${total_spent:.2f}",
        )

        st.divider()

        for _, receipt in receipts.iterrows():
            purchase_date = (
                receipt["purchase_date"]
                if receipt["purchase_date"]
                else "Unknown date"
            )

            title = (
                f"{receipt['store'] or 'Unknown Store'} "
                f"— {purchase_date}"
            )

            with st.expander(title):
                image_path = Path(
                    receipt["image_path"]
                )

                col_image, col_details = st.columns(
                    [1, 1]
                )

                with col_image:
                    if image_path.exists():
                        st.image(
                            str(image_path),
                            use_container_width=True,
                        )
                    else:
                        st.warning(
                            "Receipt image file is missing."
                        )

                with col_details:
                    st.write(
                        f"**Store:** "
                        f"{receipt['store'] or 'Unknown'}"
                    )

                    st.write(
                        f"**Household member:** "
                        f"{receipt.get('household_member', 'Me')}"
                    )

                    st.write(
                        f"**Purchase date:** "
                        f"{purchase_date}"
                    )

                    st.write(
                        f"**Total:** "
                        f"${float(receipt['total'] or 0):.2f}"
                    )

                    if receipt["notes"]:
                        st.write(
                            f"**Notes:** "
                            f"{receipt['notes']}"
                        )

                    ocr_text = (
                        receipt["ocr_text"]
                        if "ocr_text" in receipt.index
                        else ""
                    )

                    saved_items = get_receipt_items(
                        receipt["id"]
                    )

                    if not saved_items.empty:
                        st.write(
                            f"**Detected items:** {len(saved_items)}"
                        )

                        item_display = saved_items[
                            [
                                "item_name",
                                "price",
                                "quantity",
                            ]
                        ].copy()

                        item_display.columns = [
                            "Item",
                            "Price",
                            "Qty",
                        ]

                        st.dataframe(
                            item_display,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Price": st.column_config.NumberColumn(
                                    "Price",
                                    format="$%.2f",
                                ),
                                "Qty": st.column_config.NumberColumn(
                                    "Qty",
                                    format="%.2f",
                                ),
                            },
                        )

                    saved_benefits = get_receipt_benefits(
                        receipt["id"]
                    )

                    if not saved_benefits.empty:
                        st.write(
                            f"**Coupons & rewards:** {len(saved_benefits)}"
                        )

                        benefit_display = saved_benefits[
                            [
                                "direction",
                                "benefit_type",
                                "value",
                                "percent_value",
                                "description",
                                "expiration_date",
                            ]
                        ].copy()

                        benefit_display.columns = [
                            "Status",
                            "Type",
                            "Value",
                            "%",
                            "Description",
                            "Expires",
                        ]

                        st.dataframe(
                            benefit_display,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Value": st.column_config.NumberColumn(
                                    "Value",
                                    format="$%.2f",
                                ),
                                "%": st.column_config.NumberColumn(
                                    "%",
                                    format="%.1f%%",
                                ),
                            },
                        )

                    if ocr_text:
                        with st.expander(
                            "Recognized receipt text"
                        ):
                            st.text(
                                ocr_text
                            )

                    created_at = receipt["created_at"]

                    if created_at:
                        try:
                            saved_at = datetime.fromisoformat(
                                created_at
                            ).strftime(
                                "%b %d, %Y at %I:%M %p"
                            )

                            st.caption(
                                f"Saved {saved_at}"
                            )
                        except ValueError:
                            pass

                    st.caption(
                        "Use the 🕵️ Missed Savings page to compare this receipt "
                        "against saved offers that may have applied."
                    )

                    if st.button(
                        "🗑 Delete Receipt",
                        key=(
                            f"delete_receipt_"
                            f"{receipt['id']}"
                        ),
                    ):
                        delete_receipt(
                            receipt["id"]
                        )
                        st.rerun()
