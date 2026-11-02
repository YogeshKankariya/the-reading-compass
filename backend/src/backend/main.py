from fastapi import FastAPI

from backend.api.routes.health import router as health_router

app = FastAPI(
    title="The Reading Compass API",
    description="An AI-powered personalized reading companion.",
    version="0.1.0",
)

app.include_router(health_router)


@app.get("/")
async def root():
    return {
        "message": "Welcome to The Reading Compass API",
        "status": "running",
    }