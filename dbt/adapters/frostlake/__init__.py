from dbt.adapters.base import AdapterPlugin
from dbt.include import frostlake

from dbt.adapters.frostlake.connections import FrostlakeConnectionManager  # noqa: F401
from dbt.adapters.frostlake.connections import FrostlakeCredentials
from dbt.adapters.frostlake.impl import FrostlakeAdapter
from dbt.adapters.frostlake.relation import FrostlakeRelation  # noqa: F401

# Standalone: this adapter's own macros plus dbt's global project are the whole
# surface, so no other adapter is needed at build or run time.
Plugin = AdapterPlugin(
    adapter=FrostlakeAdapter,  # type: ignore
    credentials=FrostlakeCredentials,
    include_path=frostlake.PACKAGE_PATH,
)
