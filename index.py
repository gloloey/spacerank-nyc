# Vercel entrypoint (flat layout — the repo root IS the app).
# Vercel's Python builder finds the ASGI `app` object re-exported here.
from app import app  # noqa: F401
