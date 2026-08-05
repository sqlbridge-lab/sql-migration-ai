import socket

import pytest


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def mysql_up() -> None:
    if not _reachable("127.0.0.1", 13306):
        pytest.skip("MySQL 컨테이너 미기동 (docker compose up -d)")


@pytest.fixture(scope="session")
def postgres_up() -> None:
    if not _reachable("127.0.0.1", 15432):
        pytest.skip("PostgreSQL 컨테이너 미기동 (docker compose up -d)")
