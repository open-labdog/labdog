"""Run a callback once the session's transaction has committed (BUG-37 class).

A Celery task dispatched before the row it reads is committed can start
first and find nothing. Every dispatch that follows a ``flush`` should go
through :func:`run_after_commit` instead of calling ``.delay`` directly,
so a helper that leaves the commit to its caller (BUG-121) cannot get the
order wrong. A rollback drops the callbacks: nothing was committed for the
task to read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)

_KEY = "labdog_after_commit"


def run_after_commit(db: AsyncSession | Session, callback: Callable[[], object]) -> None:
    """Call *callback* after the outermost transaction of *db* commits."""
    session = db.sync_session if isinstance(db, AsyncSession) else db
    session.info.setdefault(_KEY, []).append(callback)


@event.listens_for(Session, "after_commit")
def _run_callbacks(session: Session) -> None:
    for callback in session.info.pop(_KEY, []):
        try:
            callback()
        except Exception:
            # The commit has happened; a broker hiccup must not turn it
            # into an error the caller would report as a failed request.
            # The sweepers revive a pending row nobody dispatched.
            logger.exception("after-commit callback failed")


@event.listens_for(Session, "after_transaction_end")
def _drop_on_rollback(session: Session, transaction: SessionTransaction) -> None:
    # after_commit has already run and emptied the list for a commit, so
    # anything left when the outermost transaction ends was rolled back.
    if transaction.parent is None:
        session.info.pop(_KEY, None)
