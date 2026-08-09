import pytest

from photolib.db import catalog


@pytest.fixture
def conn(tmp_path):
    connection = catalog.connect(tmp_path / "test.db")
    yield connection
    connection.close()
