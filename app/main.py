from fastapi import FastAPI
from app.routers import jarchive

app = FastAPI(title="J! Archive Verifier API")

# Mount our router at /ja
app.include_router(jarchive.router)

# Root handlers so Render’s primary URL returns JSON instead of 404
@app.get("/", include_in_schema=False)
async def root():
    return {"ok": True, "service": "jarchive-verifier"}

@app.head("/", include_in_schema=False)
async def root_head():
    # Respond 200 to Render/ELB health probes
    return {}

# Simple health endpoint (optional but useful)
@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


