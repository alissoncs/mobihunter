"""Executar servidor: python -m mobihunter.web"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("MOBIHUNTER_UI_PORT", "9090"))
    host = os.environ.get("MOBIHUNTER_UI_HOST", "127.0.0.1")
    uvicorn.run(
        "mobihunter.web.app:app",
        host=host,
        port=port,
        reload=os.environ.get("MOBIHUNTER_UI_RELOAD", "").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    main()
