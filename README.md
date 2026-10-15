# Deal Finder

I built Deal Finder to make nearby offers easier to compare and keep track of. I worked on a native iOS interface and a Python service for discovery, saved offers, and receipt workflows.

I included map based discovery, cached search results, offer dates and restrictions, saved deals, and optional arrival reminders. I keep possible coupon combinations separate from guaranteed savings because the merchant terms still need to be checked.

## Built with

I used SwiftUI and MapKit for iOS, with FastAPI and SQLite on the backend.

## Inside the project

* `DEAL SERVICE/`: the iOS app and Xcode project.
* `DEAL FINDER/`: the Python service and local dashboard.
* `tests/`: backend and iOS test helpers.
* `docs/history/`: implementation notes.

## Running it

I install `DEAL FINDER/requirements.txt` in a Python environment and use `DEAL FINDER/start_server.sh` to start the service. I open `DEAL SERVICE/DEAL SERVICE.xcodeproj` in Xcode, select my own signing team, and configure the app with my backend address.

I keep receipt records and local databases out of this repository. Live discovery requires my own search configuration.

## More detail

* [Setup and feature details](PROJECT_GUIDE.md)
* [Development notes](docs/history/README.md)
