import pytest

from tests.direct.flightattest_support import reset_environment


@pytest.fixture(autouse=True)
def _clean_world():
    reset_environment()
    yield
    reset_environment()
