"""FastAPI main application."""
from src.api.compat import patch_pydantic_forward_refs

patch_pydantic_forward_refs()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from src.api.routes import router  # noqa: E402

app = FastAPI(
    title="EV Charging Time Series API",
    description="API for EV charging time series analysis",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@router.get("/")
async def root():
    """Root endpoint."""
    return {"message": "EV Charging Time Series Analysis API"}
