from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.session import AsyncSessionLocal
from backend.services.profile.profile_service import ProfileService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# In-memory deduplication set to avoid redundant background tasks within the process
_active_background_refreshes: set[uuid.UUID] = set()


async def _run_background_profile_refresh(
    user_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    force: bool = False,
) -> None:
    """Execute profile refresh in an isolated, independently managed AsyncSession."""
    try:
        async with session_factory() as session:
            service = ProfileService(session=session)
            await service.refresh_profile(user_id=user_id, force=force)
    except Exception as exc:
        logger.exception(
            "Background profile refresh failed for user %s (force=%s): %s",
            user_id,
            force,
            exc,
        )
    finally:
        _active_background_refreshes.discard(user_id)


def schedule_profile_refresh(
    background_tasks: BackgroundTasks,
    user_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    force: bool = False,
) -> bool:
    """Schedule asynchronous background profile refresh with in-flight deduplication.

    Requirements:
        - Never reuses the request-scoped session.
        - Injects an async_sessionmaker so the runner creates and closes its own session.
        - Best-effort and non-blocking for HTTP endpoints.
        - Cleans up deduplication set in finally block.

    Returns:
        True if the refresh was newly scheduled, False if already in-flight for this user.
    """
    if user_id in _active_background_refreshes:
        return False

    _active_background_refreshes.add(user_id)
    background_tasks.add_task(
        _run_background_profile_refresh,
        user_id=user_id,
        session_factory=session_factory,
        force=force,
    )
    return True
