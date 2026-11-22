"""Open a short-lived admin session from the local OS account; no default password."""

import sys
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.store import KnowledgeStore
from app.workspace_accounts import Accounts

if __name__ == "__main__":
    try:
        with urlopen("http://127.0.0.1:8765/api/health", timeout=3) as response:
            if response.status != 200:
                raise OSError
    except OSError:
        sys.exit("请先启动通用知识库平台。")
    store = KnowledgeStore(ROOT / "data" / "knowledge.db")
    try:
        ticket = Accounts(store).local_ticket()
    finally:
        store.conn.close()
    webbrowser.open("http://127.0.0.1:8765/workspace#ticket=" + ticket)
