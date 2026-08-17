"""The Frostlake adapter, built on dbt-adapters' SQL layer."""

from typing import TYPE_CHECKING, List, Optional

from dbt.adapters.sql import SQLAdapter
from dbt_common.exceptions import DbtRuntimeError

from dbt.adapters.frostlake.connections import FrostlakeConnectionManager
from dbt.adapters.frostlake.relation import FrostlakeRelation

if TYPE_CHECKING:
    import agate


class FrostlakeAdapter(SQLAdapter):
    ConnectionManager = FrostlakeConnectionManager
    Relation = FrostlakeRelation

    @classmethod
    def date_function(cls) -> str:
        return "CURRENT_TIMESTAMP()"

    @classmethod
    def quote(cls, identifier: str) -> str:
        return '"{}"'.format(identifier)

    # -- seed type inference -------------------------------------------------
    # Which SQL type a seed column becomes, in the engine's own spellings.

    @classmethod
    def convert_text_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "VARCHAR"

    @classmethod
    def convert_number_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        import agate

        decimals = agate_table.aggregate(agate.MaxPrecision(col_idx))
        return "NUMBER(38,{})".format(decimals) if decimals else "NUMBER(38,0)"

    @classmethod
    def convert_integer_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "NUMBER(38,0)"

    @classmethod
    def convert_boolean_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "BOOLEAN"

    @classmethod
    def convert_datetime_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "TIMESTAMP_NTZ"

    @classmethod
    def convert_date_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "DATE"

    @classmethod
    def convert_time_type(cls, agate_table: "agate.Table", col_idx: int) -> str:
        return "TIME"

    # -- identifier handling -------------------------------------------------

    def quote_seed_column(self, column: str, quote_config: Optional[bool]) -> str:
        """Seed columns are unquoted unless the project asks otherwise.

        dbt's cross-adapter default quotes them, which on an engine that resolves
        unquoted names uppercase would create lowercase columns that unquoted model
        SQL then cannot find.
        """
        quote_columns = False
        if isinstance(quote_config, bool):
            quote_columns = quote_config
        elif quote_config is None:
            pass
        else:
            raise DbtRuntimeError(
                'The seed configuration value of "quote_columns" has an invalid type {}'.format(
                    type(quote_config)
                )
            )
        if quote_columns:
            return self.quote(column)
        return column

    def _make_match_kwargs(self, database: str, schema: str, identifier: str) -> dict:
        """Unquoted names resolve uppercase, so compare them that way."""
        quoting = self.config.quoting
        if identifier is not None and not quoting["identifier"]:
            identifier = identifier.upper()
        if schema is not None and not quoting["schema"]:
            schema = schema.upper()
        if database is not None and not quoting["database"]:
            database = database.upper()
        return {"database": database, "identifier": identifier, "schema": schema}

    def timestamp_add_sql(self, add_to: str, number: int = 1, interval: str = "hour") -> str:
        return "DATEADD({interval}, {number}, {add_to})".format(
            interval=interval, number=number, add_to=add_to
        )

    def valid_incremental_strategies(self) -> List[str]:
        """A plain method, not a property — dbt calls it."""
        return ["append", "merge", "delete+insert"]

    def debug_query(self) -> None:
        """What `dbt debug` runs to prove the connection works."""
        self.execute("SELECT 1 AS id")
