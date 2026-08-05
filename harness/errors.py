"""하니스 자체 예외. 드라이버(PyMySQL/psycopg) 예외를 이 타입으로 번역해,
Runner가 드라이버를 몰라도 stage를 분류할 수 있게 한다.
"""

from __future__ import annotations


class HarnessError(Exception):
    """하니스 실행 중 발생하는 모든 예외의 베이스."""


class StatementTimeout(HarnessError):
    """쿼리 statement timeout 초과. 항상 infrastructure/error로 분류."""


class ConnectionFailure(HarnessError):
    """DB 연결·인증 단절. 항상 infrastructure/error로 분류."""


class InfrastructureFailure(HarnessError):
    """쿼리 본체가 아닌 DB 호출(timeout SET·commit/rollback·cursor·fetch·close 등)의
    실패. 항상 infrastructure/error로 분류. SqlExecutionFailure로 번역하면 PG에서
    pg.statement/fail로 오분류되므로 별도 타입으로 가른다.
    """


class SqlExecutionFailure(HarnessError):
    """피검증/제어 쿼리 '본체'의 SQL 실행 실패(구문·제약 위반 등).
    stage는 호출 문맥(피검증/제어)이 결정한다.
    """
