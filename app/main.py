from fastapi import FastAPI

from app.routers.jarchive import router as jarchive_router


app = FastAPI(title="jarchive-verifier")


# Routers (router has its own prefix/tags)
app.include_router(jarchive_router)


@app.get("/")
def root():
    return {"service": "jarchive-verifier", "status": "ok"}


