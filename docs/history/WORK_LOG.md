# Deal Finder work log

The uploaded archive is unchanged. Work takes place in this extracted project. Synced project sources are unchanged. The bundled virtual environment, user-specific Xcode state, macOS resource forks, and secret files were not copied into the deliverable.

## Baseline

- Source of truth: `DEAL SERVICE/DEAL SERVICE.xcodeproj`, its synchronized `DealFinderIOS_ReceiptScanner_Fixed` source folder, and `DEAL FINDER/deal_finder_api.py`.
- `DEAL FINDER/app.py` is the existing Streamlit companion, not the iOS API.
- Original iOS simulator build: passed.
- SQLite contained three deal rows and existing receipt, item, benefit, candidate, and household tables.
- Original API included health, deal listing/detail, used/unused, and nearby search. It did not include `/for-you`.
- Main gaps: disconnected local/remote Home data, no parsed receipt review, no discovery cache, duplicated server address, missing tab navigation containers, broad ATS exception, and incomplete backend requirements.

## Implemented

- Shared backend client, editable URL, optional Keychain server token, and readable errors.
- Atomic persistent remote deal cache and offline outbox, shared by Home, Deals and Arrival. Used/saved/dismissed history and expired filtering.
- SQLite backup and additive migration. Existing Streamlit tables and deal rows retained.
- Explainable recommendation endpoint and local fallback using reviewed receipts, history, feedback, distance, freshness, confidence and expiration.
- Home feed capped at eight cards; one nearby business discovery per location update, cached for 45 minutes.
- Conservative possible-stack components, with no guaranteed or summed savings claim.
- Native photo import, Vision OCR orientation handling, receipt parsing, editable review, item/benefit persistence, corrections, multi-page images and confirmed cascade deletion.
- Costco recognition and discovery in both clients, plus membership/warehouse caveats.
- Cached MapKit search, distance sorting, stable business IDs, deduplication, recent-location validation and visible failures.
- One persistent geofence manager, one monitored store, offline notification data, permission checks, quiet hours and cooldowns.
- SwiftUI navigation containers, shared deal cards, empty/loading states, Dynamic Type-compatible controls and accessibility labels.
- Shared Xcode test scheme, Swift persistence/OCR/network tests, simulator navigation tests, backend tests and startup helper.

## Final verification

- Python syntax/import checks: passed.
- Seven backend tests, including actual uvicorn startup and HTTP validation: passed.
- Updated app and test targets: build passed. Xcode emits informational App Intents extraction warnings because this app does not use App Intents.
- Simulator behavioral tests: five unit/integration cases passed in the final rerun; one UI case passed, covering five tabs, receipt saving, persistence after relaunch and correction navigation. The first integration attempt failed because its temporary server was unavailable; the restarted-server rerun passed. See verification/RESULTS.md and retained logs.
- Home light/dark screenshots visually checked at standard text size; included in verification.
- Live Tavily search: not performed; no key was found in the accessible Mac configuration. Controlled search fixtures cover caching, source classification, malformed URLs, expired snippets and saving candidates.

## Physical iPhone acceptance still required

Camera capture and photo permission states; real receipt layouts and orientation; existing-install SwiftData migration; Tailscale on cellular/Wi-Fi; precise/approximate and denied location states; real warehouse/restaurant MapKit results; geofence entry with app suspended and Mac offline; notification permissions, Focus modes and cooldowns; VoiceOver and large text on the actual device.

## Mac and iPhone installation

- Finished source copied to Desktop/Deal Finder Ready, preserving the old Desktop project.
- Current Desktop database snapshotted into the installation; runtime uses ~/Library/Application Support/Deal Finder/deals.db.
- Dedicated backend virtual environment installed in Application Support.
- Authenticated Tailscale-only login service configured; real health returned 200 with token and 401 without it.
- Signed physical iPhone build and installation passed. Development-only launch provisioning saves the generated Mac access token in Keychain; no token is embedded in source or printed in chat.
- Native app launched successfully on the paired iPhone. After relaunch without provisioning variables, the phone fetched /deals and /for-you through Tailscale with HTTP 200, confirming persisted Keychain authentication. Real scanning and arrival verification still require user interaction.
- Live discovery key remains unconfigured. Desktop Configure Search.command enters it privately on the Mac.

## Store accounts expansion

- Added store-account browser entry points for Target, CVS, Safeway, Costco, Walmart, Walgreens and Kroger, with explicit import of a selected displayed receipt and on-device sign-in clearing.
- Added PDF/image/text file import and explicit pasted-text import, with bounded file/page/text limits.
- Receipt footer offers preserve printed terms, dollar/percent distinction, expiration and an explicit unused/available confirmation.
- Added authenticated receipt-offer saving, duplicate prevention and expired-offer rejection. No new SQLite schema migration.
- Live account sign-in and website extraction still require user account testing; no automatic receipt-history synchronization is claimed.
- Store-account tests passed: eight backend cases, three native checks, one new navigation/review UI case and one WebKit privacy fixture. Signed iPhone update built successfully.


## Foreground store-history batch importer

User requested sign-in followed by automatic collection across stores, with Target as the first live login. Added a shared experimental browser batch engine across all seven listed merchants: discover visible history/receipt/detail/pagination links on official HTTPS domains, bounded 25-page traversal, cancellable progress, automatic local saving of conservative parsed receipts, repeat-content SHA256 deduplication, unconfirmed offers and honest unsupported-layout/partial-result messages. Added optional SwiftData importFingerprint for additive migration. No credentials, form values or cookies extracted. No authenticated merchant batch flow is verified yet; generic link discovery must not be presented as complete universal store integration.

Validation: simulator build succeeded; nine native cases passed, and both new batch cases passed after final filtering changes. No backend change. Signed device build and deployment recorded below when complete.
Signed physical-iPhone build from the updated Desktop source succeeded. Live merchant batch imports remain pending.

Final batch update installed and launched on the paired iPhone. No authenticated merchant batch result is claimed.


## Target live failure and receipt reader correction

User reported failed bulk import and incorrect manual totals/items, then supplied screenshots. Visible receipt text showed split summary labels and a written purchase date; the old generic parser returned no total/date/items and the review displayed missing total as zero. Added a Target purchase-detail parser with month-name date, exact split-label amounts, Qty 1 product cards, recommendation section exclusion, and explicit ambiguity for multi-unit prices. Generic summaries now accept split label/value and reject conflicting totals, payment metadata and price/quantity metadata as products. Missing totals require explicit correction before save. Added user-triggered re-reading for existing Target receipts without automatic saved-data edits.

Removed the broken/unverified batch-import button across stores. Added explicit Copy import report to expose sanitized visible link/button metadata for debugging real history navigation; it excludes credential fields/cookies/query values/full receipt text and remains in clipboard until user shares it. Bulk import is unfinished and must not be called working.

Eleven native cases passed, including screenshot-based $48.06/$45.48/$2.58 and $21.49/$23.99 fixture, written date and monetary ambiguity regression. Existing local API/outbox case excluded because no fixture server for this run. Final report-privacy check and signed device update are recorded below.

Final report privacy and Target fixture tests passed; signed iPhone build succeeded. Bulk remains disabled awaiting real history navigation report.

Final Target receipt correction and import-report build installed and launched on paired iPhone. Live re-import and bulk navigation diagnosis remain pending user feedback.


## Target connector from real on-device navigation report

User supplied sanitized Purchase History report: `/orders`, ten anchors labeled View purchase with paths `/orders/stores/<four hyphen-separated numeric groups>`, and Load more purchases button. The old generic discovery missed both labels and button pagination. Replaced active history import with a Target-specific collector that uses those observed controls and paths, loads more before visiting purchases, validates each individual purchase, waits for rendered data, saves corrected Target receipt data, exposes progress/cancellation and bounded-limit counts. Other stores' bulk controls remain unavailable. Added stable hashed Target purchase identity and checks against manually saved original text to avoid duplicates without overwriting edited data. Native connector fixture and WebKit pagination-control tests are being verified before installation. Live account run remains pending.

Validation: 14-case native suite passed; all three focused connector/pagination checks passed after transaction identity validation. WebKit fixture exercised the production load-more script and left payment-card action untouched. Fifteen distinct native cases passed across runs. First signed connector build passed; final scope-label/warning build pending installation.

Final Target store-history connector build succeeded and installed on the paired iPhone. Live account import remains pending user acceptance; no other-store bulk support claimed.


### Physical launch regression under investigation

After connector installation, devicectl launch returned CoreDevice error 10004 / timeouts and the user reported a black screen. No Deal Finder process appeared in CoreDevice or LLDB process listings; no matching system crash report was available. Signing profile is unexpired, includes the device, and has debug entitlement; on-disk deep signature verified. Device is paired, developer mode enabled, DDI services available, and unlocked since boot. Built cleanly in /tmp/DealFinderTargetRecovery with ENABLE_DEBUG_DYLIB=NO, signed and installed without uninstalling or clearing user data; launch still timed out. Asked user to restart/unlock iPhone and reopen app. This is unresolved until user reports successful startup. The connector fixture results do not imply successful physical launch. No credentials or app databases have been modified for this diagnosis.


2026-10-10 — Phone recovery and proactive deal selection
- User confirmed phone responsiveness after force restart. App registration was missing; reinstall restored it and the user confirmed the app opened normally.
- Added automatic discovery across four distinct nearby chains plus DoorDash, persistent fresh feed, significant-location monitoring, arrival regions, opt-in alerts and correctly registered background refresh. A startup registration error was caught and fixed on the QA simulator before phone deployment.
- Connected the existing Tavily account locally. User explicitly approved sending nearby business names/addresses and interests to Tavily. One initial real test was rejected by automatic review until that approval was received; no bypass occurred.
- A live CVS search exposed written September 26, 2026 expiration text. Added written/numeric expiry checks and new discovery cache version so those promotions cannot reach the shortlist or be saved.
- Applied OpenAI key and official-docs skills. User chose reuse of the existing key and approved the exact private runtime destination. No new key was created. AI assesses public evidence only; interests/distances are applied on the Mac.
- Added AI shortlist endpoint/UI, bounded cached calls, server-resolved IDs, claim checks, conditional-offer label caps and explicit non-AI fallback. Alerts prefer strong shortlisted picks.
- Fourteen backend checks and four focused native workflow checks passed. Live public-to-AI API returned five picks in 3.3 seconds; repeat used the cached assessment. Signed physical build succeeded. Physical UI/background behavior requires normal user acceptance, and iOS scheduling cannot be guaranteed.
