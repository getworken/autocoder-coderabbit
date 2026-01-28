"""
Feature Repository
==================

Repository pattern for Feature database operations.
Centralizes all Feature-related queries in one place.

Retry Logic:
- Database operations that involve commits include retry logic
- Uses exponential backoff to handle transient errors (lock contention, etc.)
- Raises original exception after max retries exceeded
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .database import Feature

# Module logger
logger = logging.getLogger(__name__)

# Retry configuration
MAX_COMMIT_RETRIES = 3
INITIAL_RETRY_DELAY_MS = 100


def _utc_now() -> datetime:
    """
    Return the current UTC datetime with timezone information.
    
    Returns:
        datetime: Current UTC datetime with tzinfo set to UTC.
    """
    return datetime.now(timezone.utc)


def _commit_with_retry(session: Session, max_retries: int = MAX_COMMIT_RETRIES) -> None:
    """
    Commit the given SQLAlchemy session, retrying transient database lock/busy errors with exponential backoff.
    
    This function attempts to commit the session and, on transient OperationalError messages containing "locked" or "busy", retries the commit up to `max_retries` times with an exponentially increasing delay, rolling back the session between attempts. Logs warnings on intermediate retries and logs an error before re-raising the final error if all attempts fail.
    
    Parameters:
        session (Session): SQLAlchemy session to commit.
        max_retries (int): Maximum number of retry attempts (default defined by module constant).
    
    Raises:
        OperationalError: If the commit fails after all retry attempts.
    """
    delay_ms = INITIAL_RETRY_DELAY_MS
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            session.commit()
            return
        except OperationalError as e:
            error_msg = str(e).lower()
            # Retry on lock/busy errors
            if "locked" in error_msg or "busy" in error_msg:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"Database commit failed (attempt {attempt + 1}/{max_retries + 1}), "
                        f"retrying in {delay_ms}ms: {e}"
                    )
                    time.sleep(delay_ms / 1000)
                    delay_ms *= 2  # Exponential backoff
                    session.rollback()  # Reset session state before retry
                    continue
            raise

    # If we get here, all retries failed
    if last_error:
        logger.error(f"Database commit failed after {max_retries + 1} attempts")
        raise last_error


class FeatureRepository:
    """Repository for Feature CRUD operations.

    Provides a centralized interface for all Feature database operations,
    reducing code duplication and ensuring consistent query patterns.

    Usage:
        repo = FeatureRepository(session)
        feature = repo.get_by_id(1)
        ready_features = repo.get_ready()
    """

    def __init__(self, session: Session):
        """Initialize repository with a database session."""
        self.session = session

    # ========================================================================
    # Basic CRUD Operations
    # ========================================================================

    def get_by_id(self, feature_id: int) -> Optional[Feature]:
        """Get a feature by its ID.

        Args:
            feature_id: The feature ID to look up.

        Returns:
            The Feature object or None if not found.
        """
        return self.session.query(Feature).filter(Feature.id == feature_id).first()

    def get_all(self) -> list[Feature]:
        """Get all features.

        Returns:
            List of all Feature objects.
        """
        return self.session.query(Feature).all()

    def get_all_ordered_by_priority(self) -> list[Feature]:
        """
        Return all Feature records ordered by priority (lowest first).
        
        Returns:
            List of Feature objects ordered by ascending priority.
        """
        return self.session.query(Feature).order_by(Feature.priority).all()

    def count(self) -> int:
        """Get total count of features.

        Returns:
            Total number of features.
        """
        return self.session.query(Feature).count()

    # ========================================================================
    # Status-Based Queries
    # ========================================================================

    def get_passing_ids(self) -> set[int]:
        """Get set of IDs for all passing features.

        Returns:
            Set of feature IDs that are passing.
        """
        return {
            f.id for f in self.session.query(Feature.id).filter(Feature.passes == True).all()
        }

    def get_passing(self) -> list[Feature]:
        """Get all passing features.

        Returns:
            List of Feature objects that are passing.
        """
        return self.session.query(Feature).filter(Feature.passes == True).all()

    def get_passing_count(self) -> int:
        """
        Return the number of features with `passes` set to True.
        
        Returns:
            count (int): The number of features where `passes` is True.
        """
        return self.session.query(Feature).filter(Feature.passes == True).count()

    def get_in_progress(self) -> list[Feature]:
        """
        Retrieve all features that are currently marked as in progress.
        
        Returns:
            list[Feature]: Features whose `in_progress` attribute is set to True.
        """
        return self.session.query(Feature).filter(Feature.in_progress == True).all()

    def get_pending(self) -> list[Feature]:
        """
        Return features that are neither passing nor in progress.
        
        Returns:
            list[Feature]: Features with `passes` == False and `in_progress` == False.
        """
        return self.session.query(Feature).filter(
            Feature.passes == False,
            Feature.in_progress == False
        ).all()

    def get_non_passing(self) -> list[Feature]:
        """
        List features that are not passing.
        
        Returns:
            list[Feature]: Feature objects whose `passes` attribute is False.
        """
        return self.session.query(Feature).filter(Feature.passes == False).all()

    def get_max_priority(self) -> Optional[int]:
        """
        Return the highest priority value among all Feature records.
        
        Returns:
            The highest priority value as an int, or `None` if no features exist.
        """
        feature = self.session.query(Feature).order_by(Feature.priority.desc()).first()
        return feature.priority if feature else None

    # ========================================================================
    # Status Updates
    # ========================================================================

    def mark_in_progress(self, feature_id: int) -> Optional[Feature]:
        """
        Mark the specified feature as in progress.
        
        Parameters:
            feature_id (int): ID of the feature to mark as in progress.
        
        Returns:
            Feature | None: The updated Feature instance, or `None` if no feature with the given ID exists.
        
        Notes:
            Commits use retry logic to handle transient database errors.
        """
        feature = self.get_by_id(feature_id)
        if feature and not feature.passes and not feature.in_progress:
            feature.in_progress = True
            feature.started_at = _utc_now()
            _commit_with_retry(self.session)
            self.session.refresh(feature)
        return feature

    def mark_passing(self, feature_id: int) -> Optional[Feature]:
        """
        Mark the feature identified by `feature_id` as passing and persist the change.
        
        This sets `passes` to `True`, clears `in_progress`, and updates `completed_at` to the current UTC time; the change is persisted (with retry on transient database errors) and the returned object is refreshed from the session.
        
        Parameters:
            feature_id (int): ID of the Feature to mark as passing.
        
        Returns:
            Feature | None: The updated Feature instance if found, `None` if no Feature with `feature_id` exists.
        """
        feature = self.get_by_id(feature_id)
        if feature:
            feature.passes = True
            feature.in_progress = False
            feature.completed_at = _utc_now()
            _commit_with_retry(self.session)
            self.session.refresh(feature)
        return feature

    def mark_failing(self, feature_id: int) -> Optional[Feature]:
        """Mark a feature as failing.

        Args:
            feature_id: The feature ID to update.

        Returns:
            Updated Feature or None if not found.

        Note:
            Uses retry logic to handle transient database errors.
        """
        feature = self.get_by_id(feature_id)
        if feature:
            feature.passes = False
            feature.in_progress = False
            feature.last_failed_at = _utc_now()
            _commit_with_retry(self.session)
            self.session.refresh(feature)
        return feature

    def clear_in_progress(self, feature_id: int) -> Optional[Feature]:
        """
        Clear the in_progress flag for the feature with the given id.
        
        If the feature exists, sets its `in_progress` attribute to False, commits the change using the repository's retry logic, and refreshes the instance.
        
        Parameters:
            feature_id (int): ID of the feature to update.
        
        Returns:
            The updated `Feature` instance if found, otherwise `None`.
        """
        feature = self.get_by_id(feature_id)
        if feature:
            feature.in_progress = False
            _commit_with_retry(self.session)
            self.session.refresh(feature)
        return feature

    # ========================================================================
    # Dependency Queries
    # ========================================================================

    def get_ready_features(self) -> list[Feature]:
        """
        Return features that are ready to implement.
        
        A feature is ready when it is not passing, not in progress, and every dependency id is in the set of passing feature ids.
        
        Returns:
            list[Feature]: Features that meet the readiness criteria.
        """
        passing_ids = self.get_passing_ids()
        candidates = self.get_pending()

        ready = []
        for f in candidates:
            deps = f.dependencies or []
            if all(dep_id in passing_ids for dep_id in deps):
                ready.append(f)

        return ready

    def get_blocked_features(self) -> list[tuple[Feature, list[int]]]:
        """
        Return features that are currently blocked due to unmet dependencies.
        
        Each item is a tuple (feature, blocking_ids) where blocking_ids is a list of dependency feature IDs that are not passing.
        
        Returns:
            blocked (list[tuple[Feature, list[int]]]): Features with at least one unmet dependency and the IDs of those blocking dependencies.
        """
        passing_ids = self.get_passing_ids()
        candidates = self.get_non_passing()

        blocked = []
        for f in candidates:
            deps = f.dependencies or []
            blocking = [d for d in deps if d not in passing_ids]
            if blocking:
                blocked.append((f, blocking))

        return blocked