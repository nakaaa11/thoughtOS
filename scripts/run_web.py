"""Thought OS Web Dashboard 起動スクリプト"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=8080,
        reload=True,
        reload_dirs=[str(Path(__file__).parent.parent / "web")],
    )
