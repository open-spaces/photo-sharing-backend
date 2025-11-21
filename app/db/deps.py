from typing import Generator
from app.db.database import get_db_session


def get_db() -> Generator:
    yield from get_db_session()

