# Deal Finder — completed project handoff

This is an edited copy of the uploaded project. The original ZIP, existing Desktop project, and synced ChatGPT sources were not overwritten. Open `DEAL SERVICE/DEAL SERVICE.xcodeproj` and select the shared **DEAL SERVICE** scheme. The app display name is **Deal Finder**.

## Architecture

The native SwiftUI app retains Home, Deals, Arrival, Receipts and Household. SwiftData stores receipts, reviewed line items, benefits, images, household members and legacy local deals on the iPhone. A shared deal repository persists remote deal history and an offline change queue to the app's Application Support folder. Home, Deals and Arrival use this same repository.

One backend client handles the configurable Mac URL, optional Keychain access token, timeouts and readable errors. The FastAPI API owns SQLite deal lifecycle, feedback, recommendation scoring and cached Tavily discovery. Receipt summaries are sent to `/for-you` when the Mac is available; raw receipt images remain on the phone. Offline recommendations use local receipt history and cached deals. The existing Streamlit companion remains in `DEAL FINDER/app.py`.

## Implemented behavior

- Receipts: camera/document scan, Photos import, multi-page images, on-device Vision OCR, conservative merchant/date/item/quantity/price/total parsing, editable review, saved item/category/benefit history, correction and confirmed deletion.
- Deals: active, saved, used, dismissed and expired views; redemption timestamp; source links and confidence; household label; persistent history; interested/not-for-me feedback; offline changes queued for the next successful sync.
- Home: up to eight recommendations with short explanations, receipt/category/store affinity, redemption and dismissal history, proximity, value, minimum spend, freshness, expiration and source confidence. Automatic discovery checks up to four nearby merchant chains plus DoorDash after location updates. A cached AI shortlist surfaces the strongest public opportunities with reasons and restrictions.
- Nearby: actual MapKit businesses, broad business categories and major chains including Costco, deduplication, distance sorting, bounded radius/results and caching. Nearby deal search is cached for 45 minutes, deduplicates URLs and distinguishes official-source, delivery, social and web candidates. Manual refresh has a 60-second minimum interval.
- Possible stacks: coupon/reward components for the same merchant, presented as possibilities requiring term verification. Component values are not added into guaranteed savings.
- Arrival: one explicitly selected monitored store; notification permission and Always location checks; cached high-confidence/saved offers; quiet hours from 9 p.m. to 8 a.m., a one-hour global cooldown and six-hour merchant cooldown. Preview controls are tucked into a disclosure.
- Native UI: consistent cards, navigation stacks, loading/empty/offline states, adaptable text and accessibility labels. Backend settings are accessible through the gear on Home.

See `CHANGED_FILES.txt` for the archive comparison and `WORK_LOG.md` for the audit and implementation record.

## Verification

Fourteen backend tests pass, covering safe/idempotent migration, preserved rows, lifecycle/events, expired offers, domain spoofing, discovery/cache/deduplication/saving, recommendation feedback, invalid requests, missing search key and actual FastAPI startup/HTTP responses. Automated tests use controlled Tavily and AI fixtures. Separate live checks verified public Domino’s/DoorDash discovery, Tavily connection, and an OpenAI shortlist using public offer details.

The app, unit-test and UI-test targets build for the iOS simulator under Xcode 27 with an iOS 18 deployment target. Xcode's only build warnings are informational App Intents metadata extraction warnings; this app does not use App Intents. Final simulator runtime results are recorded in `verification/RESULTS.md`.

### Repeat tests

Backend, from this project's root folder:

```sh
"/Users/shopify/Desktop/DEAL FINDER/.venv/bin/python3" -m unittest discover -s tests -v
```

iOS, from the project root with an available iPhone 17 simulator:

```sh
./tests/run_ios_tests.sh
```

Set `DEAL_FINDER_PYTHON` for a different Python environment and `DEAL_FINDER_TEST_DESTINATION` for a different simulator. The iOS helper creates a disposable database and local server on port 18012; it does not edit your production database. Port 18012 must be free. Do not run concurrent installs/tests on that simulator.

## Exact Mac startup

Run the edited backend from this copy, not an old Desktop `deal_finder_api.py`:

```sh
cd "/Users/shopify/.codex/.chatgpt-projects/g-p-6ac082271d488191a21fcf38dc18deac/DealFinder/DEAL FINDER"
DEAL_FINDER_HOST=127.0.0.1 ./start_server.sh
```

The helper uses this folder's `.venv` when present, otherwise your existing `/Users/shopify/Desktop/DEAL FINDER/.venv`. It sets `SSL_CERT_FILE` from certifi and uses port 8001. If your Mac's Tailscale IP changes, change `DEAL_FINDER_HOST` and the Home → Settings server address on the iPhone. Keep both devices signed into your Tailscale network and the Mac awake with the server running.

Open `http://127.0.0.1:8001/health` on the iPhone to check connectivity. If you configure authentication, Safari without an Authorization header will return 401; the app uses the token entered in Settings. Health reports whether discovery is configured. The helper defaults to loopback for local use when `DEAL_FINDER_HOST` is omitted.

For a fresh Python environment, use Python 3.11 or later:

```sh
python3 -m venv .venv
./.venv/bin/python3 -m pip install -r requirements.txt
DEAL_FINDER_HOST=127.0.0.1 ./start_server.sh
```

New backend dependencies are FastAPI, Pydantic, uvicorn and certifi. Existing Streamlit, pandas and requests dependencies are retained. No third-party iOS dependency was added.

## Database and existing data

Startup creates a consistent `deals.db.pre-v3.bak` snapshot once, then adds deal lifecycle/source/confidence/category/date columns and three tables: `deal_events`, `discovery_cache`, and `discovery_feedback`. Schema version becomes 3. Original deal column values and the other original SQLite tables were compared against the uploaded database and preserved. Expiration remains derived from the existing expiration date. The delivered database contains the archive's records, not your current Desktop changes made since that archive.

To use your current Desktop database with this edited server, first stop other writers and make your own backup. Then set `DEAL_FINDER_DB` to that database's absolute path before startup. The additive migration applies to the selected database. Do not replace a newer live database with the archive copy. Streamlit also respects `DEAL_FINDER_DB`.

The iPhone model adds optional subtotal, tax and additional-image fields. Existing SwiftData models and original fields remain. Upgrade preservation on your existing installed app still requires a physical-device check; do not uninstall the old app or reset its data for that check.

## Secrets and configuration

- `TAVILY_API_KEY`: Mac-only environment variable, or a supported Tavily key entry in `DEAL FINDER/.streamlit/secrets.toml` or `~/.streamlit/secrets.toml`. No search key was available during verification. Restart the server after changing environment configuration.
- `DEAL_FINDER_API_TOKEN`: optional Mac server access token. Enter the same value in Home → Settings; it is stored in the iPhone Keychain per server. It is distinct from the Tavily key.
- `DEAL_FINDER_DB`: optional SQLite path; default is the database beside the API.
- `DEAL_FINDER_HOST`, `DEAL_FINDER_PORT`, `DEAL_FINDER_PYTHON`, `SSL_CERT_FILE`: optional startup overrides.

Keep secrets outside the delivered archive. Never put Tavily credentials in Swift. HTTP is permitted for private/Tailscale addresses; use HTTPS for a public server rather than expanding those exceptions.

## Xcode privacy and signing

The existing bundle identifier and development team are preserved. Select your signing team if necessary for your iPhone. Camera, local network, When In Use location and Always location purpose strings are configured in the target. PhotosPicker uses system-selected photo access. Notifications are requested when enabling an arrival reminder.

ATS permits local/private network and Tailscale address ranges rather than arbitrary HTTP everywhere. Region monitoring uses explicit Always authorization; continuous GPS/background-location tracking and push notifications were not added. Do not add background capabilities merely to make foreground nearby search run continuously.

## Physical iPhone acceptance checklist

1. Install over the existing app; verify previous households, receipts, images and local deals survive.
2. Scan several real receipts, including rotated/multi-page and column-based layouts; import a photo; check parsed values before saving; edit and delete a disposable receipt.
3. Test Tailscale over Wi-Fi and cellular; change the server URL; stop the Mac and verify cached deals/receipt history; mark a deal used offline and verify it syncs after reconnecting.
4. Test denied/approximate/precise location and current nearby business distances, including Costco. Check failed search and unavailable Mac messages.
5. Enable one arrival reminder, grant notifications and Always location, then physically leave/enter its region with the app suspended. Verify cooldowns, Focus behavior and Mac-offline behavior. No system privacy reset is necessary.
6. Check VoiceOver, largest Dynamic Type sizes, light/dark appearance, keyboard scrolling and iPad layout.
7. Configure the Mac Tavily key; perform one live nearby search, verify cache reuse, source/expiry labels and source website terms before using an offer.

## Known limitations and next improvements

OCR requires review: unusual receipt columns, handwritten text, abbreviated SKUs, ambiguous dates and nonmonetary points may remain unknown. Categories are conservative heuristics. The server does not maintain a backup of iPhone receipt images or household data. Local cache persistence is not a substitute for device backup.

Web snippets are candidates, including official-domain results; terms are not automatically verified and unknown expiry is visible. Delivery promotions may be account/location specific. Costco offers may require membership or vary by warehouse. There is no personalized delivery-account integration, automatic redemption, guaranteed stacking or background web crawling. One search call returns a limited candidate set for one business, not exhaustive coverage.

Arrival depends on iOS region delivery and permissions and cannot guarantee timing. Search caches are time-based; there is no remote push or continuous background refresh. The optional server token is not a multi-user account system. Newer production data, physical-device upgrade behavior and live search must be checked before treating this as a production release.

Recommended next work: a broader real-receipt fixture corpus, explicit verified offer terms/structured merchant feeds, household-specific recommendation controls, encrypted receipt export/backup, launch-on-login for the Mac server, and device-tested notification refinements. Keep paid-search budgeting visible as usage grows.

## Store accounts and digital receipts

Receipts → Store accounts lists Target, CVS, Safeway, Costco, Walmart, Walgreens and Kroger. Sign in on the merchant's own website, open one receipt or offer, and choose **Import this page**. Only rendered text is imported; form values, passwords and cookies are not extracted. Store sessions remain in on-device WebKit. HTTPS imports are restricted to the selected official domain and subdomains. Clear store sign-ins is available with confirmation. Some cross-site sign-in flows require Safari and a receipt-file import.

**Target store-purchase history importer:** the on-device report identified `/orders`, individual `/orders/stores/<purchase-id>` links labeled **View purchase**, and a button labeled **Load more purchases**. Target now has **Import Target store receipts**: it opens store purchase history, loads additional purchases, and reads/saves each compatible purchase using the corrected Target reader. Keep the app foreground while importing; **Stop import** preserves saved receipts. Counts distinguish saved, duplicate and unreadable purchases. Store sessions remain on-device. This connector covers the reported in-store purchase layout; online-order layouts and other merchants are not yet supported by bulk import. Other stores retain reviewed single-page/file imports.

The collector checks at most 200 purchases and 20 history expansions per run. If a limit is reached, the app explicitly says older purchases may remain. A stalled pagination control or renewed sign-in produces a specific error with the saved count. Stable hashed purchase IDs deduplicate repeated imports and existing manual Target imports even when page headers change. Saved receipts are not silently overwritten. Fixture tests verify the reported controls and receipt reader; a real account run still requires user confirmation before claiming live compatibility.

Target's purchase-detail reader recognizes the layout reported in the user's screenshots: written purchase date, separate subtotal/tax/total labels and amounts, and product name/price/Qty 1 cards. The screenshot fixture produces September 25, 2026; $45.48 subtotal; $2.58 tax; $48.06 total; and $21.49/$23.99 items. Multi-unit price meaning remains uncertain and is flagged without guessing a line total. This is screenshot-based fixture validation; re-import from the live website still requires checking. Missing or conflicting totals require explicit entry before saving. Generic imports also recognize split summary labels and no longer let conflicting totals overwrite each other or classify charge/regular-price/quantity rows as items.

To repair an existing Target receipt: open it, choose **Correct receipt**, then **Re-read Target receipt text**, check the values and choose **Save corrections**. This updates the review draft and preserves reviewed benefits, notes and household assignment. It does not silently modify saved receipts.

To diagnose the bulk navigation problem, open Target's Purchase History in Store accounts and choose **Copy import report**. Paste that report in this chat. It includes visible receipt/history navigation labels, element types, test attributes and redacted route paths; form values, passwords, cookies, URL query values and full page/receipt text are excluded. Email addresses, long numeric identifiers, UUIDs and long path identifiers are redacted. It stays in the clipboard until the user chooses to share it.

Add receipt also accepts PDF, image and UTF-8 text files (up to 20 MB; PDFs up to 20 pages), photo imports and explicitly pasted text. PDF text is read directly when present, otherwise Vision reads the rendered page on the iPhone. Imported images and text are retained in the reviewed receipt; original PDF files are not separately archived.

Printed future offers are separated from transaction discounts where the layout is recognized. Review the dollar/percent value, expiration and printed restrictions. Confirm that an offer is still unused and available, save the receipt, then open its details and choose **Save offer to Deals**. This sends the selected reviewed offer to the authenticated Mac, where it appears in saved deals and recommendation history. Expired offers are rejected, repeat saves do not create duplicates or reactivate used offers, and source confidence is **reviewed by you**, not merchant-verified. Saving an offer requires the Mac connection; failed saves can be retried. Receipt correction preserves offer identifiers but does not silently overwrite a deal already imported.

Official guidance: [Target receipts](https://www.target.com/help/articles/orders-purchases/print-a-receipt), [Costco warehouse receipts](https://customerservice.costco.com/app/answers/detail/a_id/10306), [CVS ExtraCare](https://www.cvs.com/retail/help/help-subtopic-extracare-savings-rewards), [Safeway app purchase history](https://www.safeway.com/about-us/mobile-apps.html).


## Automatic local offers and AI shortlist

Home checks four distinct nearby chains within the map radius plus DoorDash. CVS, Domino’s, Walgreens and Target receive priority when nearby; a real nearby business is required for physical-store distance labels. Public merchant connectors read actual advertised Domino’s and DoorDash offers without inventing deals from category links. Tavily broadens coverage when configured and restricts known merchant searches to official domains. Merchant blocking and missing coverage are reported. Explicit ISO, written-month and numeric promotion expiration dates are excluded before discovery, saving and AI selection. Unknown expiry remains unknown.

The new **Best deals for you** section automatically compares current candidates. The Mac resolves candidate IDs against its fresh search cache, sends only public offer descriptions/terms to OpenAI, validates returned IDs and monetary claims, caps overconfident labels for generic/first-order offers, then applies interest/proximity preferences locally. Top picks include **Potential steal**, **Good deal** or **Worth checking**, a reason and a catch. The label describes a potential advertised saving; local participation, account eligibility and the normal selling price still require checking. No guaranteed price-drop comparison is claimed without a baseline.

AI calls use the Responses API, strict structured output, `store: false` and configurable `DEAL_FINDER_AI_MODEL` (default `gpt-5.4-mini`). Public assessments are cached for 45 minutes and reused across preference changes. A failed AI request is explicitly labeled **Basic value checks**, never presented as an AI result. The iPhone persists the shortlist, can opt out of AI, and suppresses expired or unavailable candidate IDs. Source links and the complete fresh feed remain available below the shortlist.

Always location and notification access are user-controlled. Significant location changes, cached arrival regions and registered background refresh requests support proactive checks. iOS controls background scheduling; checks are not guaranteed hourly. Strong new picks can notify at most once every four hours during 8 AM–9 PM. Your Mac must remain awake and reachable through Tailscale. Receipts and precise location are not sent to OpenAI; nearby business/address and interest searches use Tavily after the user’s explicit approval.

Runtime secrets are private under `~/Library/Application Support/Deal Finder/.config/` and excluded from this handoff. The existing Tavily account is connected. The user approved reusing the existing OpenAI key and its private `openai.env` destination. Never include either key in source, logs, archives or the iPhone app.
