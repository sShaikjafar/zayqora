import os
import sys
from pathlib import Path

# Must be set before the app is imported.
os.environ["DATABASE_URL"] = "sqlite:///./test_zayqora.db"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["RATE_LIMIT_DISABLED"] = "1"  # the rate-limit test switches it back on
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
