"""Credentials and connection management for Frostlake.

Built directly on dbt-adapters' SQL layer: the transport is `frostlake_connector`,
which speaks Frostlake's HTTP protocol, so no other database client is involved.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional, Tuple, Union

import frostlake_connector
from frostlake_connector import constants  # noqa: F401  (registers the submodule)

from dbt.adapters.contracts.connection import AdapterResponse
from dbt.adapters.contracts.connection import Connection
from dbt.adapters.contracts.connection import Credentials
from dbt.adapters.events.logging import AdapterLogger
from dbt.adapters.sql import SQLConnectionManager
from dbt_common.exceptions import DbtDatabaseError
from dbt_common.exceptions import DbtRuntimeError

logger = AdapterLogger("Frostlake")


@dataclass
class FrostlakeCredentials(Credentials):
    """A Frostlake profile.

    `host`/`port` say where the Frostlake HTTP server listens. `account`, `user` and
    `password` are accepted so that a profile carried over from another warehouse still
    loads, and are ignored — the server has no login.
    """

    host: str = "localhost"
    port: int = 18082
    warehouse: Optional[str] = None
    role: Optional[str] = None
    query_tag: Optional[str] = None
    connect_retries: int = 0
    connect_timeout: Optional[int] = None
    retry_on_database_errors: bool = False
    account: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None

    _ALIASES = {"dbname": "database", "passwd": "password"}

    @property
    def type(self) -> str:
        return "frostlake"

    @property
    def unique_field(self) -> str:
        """Keys dbt's connection cache — two servers must never share an entry."""
        return "{}:{}".format(self.host or "localhost", self.port or 18082)

    def _connection_keys(self) -> Tuple[str, ...]:
        """What `dbt debug` prints back at the user."""
        return ("host", "port", "database", "schema", "warehouse", "role", "query_tag")


class FrostlakeConnectionManager(SQLConnectionManager):
    TYPE = "frostlake"

    @contextmanager
    def exception_handler(self, sql: str):
        try:
            yield
        except frostlake_connector.errors.ProgrammingError as e:
            self.release()
            raise DbtDatabaseError(str(e)) from e
        except frostlake_connector.errors.Error as e:
            self.release()
            raise DbtDatabaseError(str(e)) from e
        except Exception as e:
            logger.debug("Error while running:\n{}".format(sql))
            self.release()
            if isinstance(e, DbtRuntimeError):
                raise
            raise DbtRuntimeError(str(e)) from e

    @classmethod
    def open(cls, connection: Connection) -> Connection:
        if connection.state == "open":
            logger.debug("Connection is already open, skipping open.")
            return connection

        credentials = connection.credentials

        def connect():
            session_parameters = {}
            if credentials.query_tag:
                session_parameters["QUERY_TAG"] = credentials.query_tag
            return frostlake_connector.connect(
                host=credentials.host or "localhost",
                port=credentials.port or 18082,
                database=credentials.database,
                schema=credentials.schema,
                warehouse=credentials.warehouse,
                role=credentials.role,
                autocommit=True,
                application="dbt",
                session_parameters=session_parameters,
            )

        def exponential_backoff(attempt: int):
            return attempt * attempt

        return cls.retry_connection(
            connection,
            connect=connect,
            logger=logger,
            retry_limit=credentials.connect_retries,
            retry_timeout=exponential_backoff,
            retryable_exceptions=[frostlake_connector.errors.OperationalError],
        )

    def cancel(self, connection: Connection) -> None:
        """Frostlake's HTTP protocol has no cancel endpoint, so the best available
        answer is to drop the connection and let the statement finish server-side."""
        logger.debug("Frostlake cannot cancel a running query; closing the connection.")
        try:
            connection.handle.close()
        except Exception as e:  # pragma: no cover - best effort teardown
            logger.debug("Error while closing a cancelled connection: {}".format(e))

    @classmethod
    def data_type_code_to_name(cls, type_code: Union[int, str]) -> str:
        """Name the type behind a cursor description code — snapshots need it.

        `frostlake_connector` reports numeric type codes rather than names, so its own
        table is the authority.
        """
        if isinstance(type_code, str):
            return type_code.upper()
        return frostlake_connector.constants.FIELD_ID_TO_NAME.get(type_code, "TEXT")

    @classmethod
    def get_response(cls, cursor: Any) -> AdapterResponse:
        rowcount = getattr(cursor, "rowcount", None)
        rows = rowcount if isinstance(rowcount, int) and rowcount >= 0 else 0
        return AdapterResponse(
            _message="SUCCESS {}".format(rows),
            rows_affected=rows,
            query_id=getattr(cursor, "query_id", None),
            code="SUCCESS",
        )
