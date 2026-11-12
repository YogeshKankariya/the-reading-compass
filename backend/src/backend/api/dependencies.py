from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, get_settings
from backend.db.session import get_db
from backend.services.retrieval.candidate_retrieval_service import (
    CandidateRetrievalService,
)


def get_candidate_retrieval_service(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CandidateRetrievalService:
    """Internal FastAPI dependency provider for request-scoped CandidateRetrievalService.

    Reuses the request-scoped AsyncSession from get_db and Settings from get_settings
    without creating secondary engines, sessions, or public HTTP routes.
    """
    return CandidateRetrievalService(session=session, settings=settings)
