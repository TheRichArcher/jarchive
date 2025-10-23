from fastapi import FastAPI

from app.routers.jarchive import router as jarchive_router


app = FastAPI(title="jarchive-verifier")


# Routers
app.include_router(jarchive_router, prefix="/ja", tags=["J! Archive"])


@app.get("/")
def root():
    return {"service": "jarchive-verifier", "status": "ok"}


