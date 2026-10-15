#!/bin/zsh
set -euo pipefail
project_root="${0:A:h:h}"
server_python="${DEAL_FINDER_PYTHON:-$project_root/DEAL FINDER/.venv/bin/python3}"
if [[ ! -x "$server_python" ]]; then server_python='/Users/shopify/Desktop/DEAL FINDER/.venv/bin/python3'; fi
qa_directory="$(mktemp -d /tmp/dealfinder-tests.XXXXXX)"
export DEAL_FINDER_QA_DIR="$qa_directory"
export DEAL_FINDER_PROJECT_ROOT="$project_root"
"$server_python" - <<'PY'
import os, socket, sqlite3, shutil
from pathlib import Path
with socket.socket() as s:
    s.bind(('127.0.0.1',18012))
root=Path(os.environ['DEAL_FINDER_PROJECT_ROOT'])
db=Path(os.environ['DEAL_FINDER_QA_DIR'])/'deals.db'
shutil.copy2(root/'DEAL FINDER/deals.db',db)
import sys
sys.path.insert(0,str(root/'DEAL FINDER'))
import deal_finder_api as api
api.DB_PATH=db
api.migrate_database()
with sqlite3.connect(db) as c:
    c.execute('DELETE FROM deal_events')
    c.execute('DELETE FROM deals')
    c.execute("INSERT INTO deals(store,title,deal_type,discount_value,used,status,source_type) VALUES('Costco','Laundry detergent offer','$ Off',6,0,'active','saved')")
    c.execute("INSERT INTO deals(store,title,deal_type,discount_value,used,status,source_type) VALUES('CVS','Personal care coupon','$ Off',5,0,'active','saved')")
PY
cd -- "$project_root/DEAL FINDER"
DEAL_FINDER_DB="$qa_directory/deals.db" DEAL_FINDER_API_TOKEN='' "$server_python" -m uvicorn deal_finder_api:app --host 127.0.0.1 --port 18012 > "$qa_directory/server.log" 2>&1 &
qa_server_pid=$!
trap 'kill "$qa_server_pid" 2>/dev/null || true' EXIT
"$server_python" - <<'PY'
import time,urllib.request
for _ in range(300):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18012/health',timeout=1) as response:
            if response.status==200:break
    except OSError:time.sleep(.1)
else:raise RuntimeError('Test server did not start')
PY
cd -- "$project_root"
xcodebuild -project 'DEAL SERVICE/DEAL SERVICE.xcodeproj' -scheme 'DEAL SERVICE' -destination "${DEAL_FINDER_TEST_DESTINATION:-platform=iOS Simulator,name=iPhone 17}" -parallel-testing-enabled NO CODE_SIGNING_ALLOWED=NO test
