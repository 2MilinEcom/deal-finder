# Deal Finder

Native iOS deal discovery and receipt workflows backed by a Python API, caching, and offer verification.

## Technology

SwiftUI, MapKit, FastAPI, SQLite.

## Run locally

Create a Python virtual environment and install `DEAL FINDER/requirements.txt`. Start the backend with `DEAL FINDER/start_server.sh`, then open `DEAL SERVICE/DEAL SERVICE.xcodeproj` in Xcode. Configure your own signing team and backend address in the app. The database is created locally; no saved receipts or personal offers are included.

## Portfolio snapshot

This repository contains a source snapshot of a personal project. Personal records, credentials, browser sessions, local databases, and generated output are excluded. Use your own configuration and fictional data when evaluating it. Some integrations require external accounts or additional setup. This is a development project, with feature limitations documented in the source and guides.

See [the project guide](PROJECT_GUIDE.md) for implementation details and earlier development notes. Verification claims in that guide describe the original working copy; they are not a fresh end-to-end test of this portfolio snapshot.
