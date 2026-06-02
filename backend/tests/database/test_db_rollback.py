import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base
from backend.repositories.flow_repository import FlowRepository
from backend.repositories.flow_run_repository import FlowRunRepository
from backend.repositories.api_key_repository import ApiKeyRepository
from backend.models.schemas import FlowRunStatus


@pytest.fixture
def db_session():
    # Use in-memory SQLite for isolated tests
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_flow_repository_create_rollback(db_session):
    repo = FlowRepository(db_session)
    db_session.rollback = MagicMock(side_effect=db_session.rollback)

    with patch.object(db_session, "commit", side_effect=Exception("Database write error")):
        with pytest.raises(Exception, match="Database write error"):
            repo.create_flow(
                name="Test Flow",
                nodes={"node1": "data"},
                edges={},
            )
        
        # Assert rollback was called
        assert db_session.rollback.called


def test_flow_repository_update_rollback(db_session):
    repo = FlowRepository(db_session)
    
    # First, create a valid flow
    flow = repo.create_flow(name="Initial Flow", nodes={}, edges={})
    db_session.rollback = MagicMock(side_effect=db_session.rollback)

    with patch.object(db_session, "commit", side_effect=Exception("Update commit error")):
        with pytest.raises(Exception, match="Update commit error"):
            repo.update_flow(flow_id=flow.id, name="Updated Flow")
        
        # Assert rollback was called
        assert db_session.rollback.called


def test_flow_repository_delete_rollback(db_session):
    repo = FlowRepository(db_session)
    flow = repo.create_flow(name="Flow to delete", nodes={}, edges={})
    db_session.rollback = MagicMock(side_effect=db_session.rollback)

    with patch.object(db_session, "commit", side_effect=Exception("Delete commit error")):
        with pytest.raises(Exception, match="Delete commit error"):
            repo.delete_flow(flow_id=flow.id)
        
        # Assert rollback was called
        assert db_session.rollback.called


def test_flow_run_repository_create_rollback(db_session):
    repo = FlowRunRepository(db_session)
    db_session.rollback = MagicMock(side_effect=db_session.rollback)

    # Need a parent flow first in SQLite (due to foreign key constraint, though memory SQLite doesn't enforce by default, let's keep it correct)
    flow_repo = FlowRepository(db_session)
    flow = flow_repo.create_flow(name="Flow", nodes={}, edges={})

    with patch.object(db_session, "commit", side_effect=Exception("FlowRun insert error")):
        with pytest.raises(Exception, match="FlowRun insert error"):
            repo.create_flow_run(flow_id=flow.id)
        
        # Assert rollback was called
        assert db_session.rollback.called


def test_api_key_repository_create_rollback(db_session):
    repo = ApiKeyRepository(db_session)
    db_session.rollback = MagicMock(side_effect=db_session.rollback)

    with patch.object(db_session, "commit", side_effect=Exception("ApiKey create error")):
        with pytest.raises(Exception, match="ApiKey create error"):
            repo.create_or_update_api_key(provider="TEST_PROVIDER", key_value="xyz")
        
        # Assert rollback was called
        assert db_session.rollback.called
