"""Relation naming rules.

In Frostlake, unquoted identifiers are case-insensitive and resolve uppercase, so dbt
should not quote them by default. Quoting everything (dbt's cross-adapter default)
would make `my_model` a distinct, lowercase relation.
"""

from dataclasses import dataclass
from dataclasses import field

from dbt.adapters.base.relation import BaseRelation
from dbt.adapters.base.relation import Policy


@dataclass
class FrostlakeQuotePolicy(Policy):
    database: bool = False
    schema: bool = False
    identifier: bool = False


@dataclass
class FrostlakeIncludePolicy(Policy):
    database: bool = True
    schema: bool = True
    identifier: bool = True


@dataclass(frozen=True, eq=False, repr=False)
class FrostlakeRelation(BaseRelation):
    quote_policy: FrostlakeQuotePolicy = field(default_factory=lambda: FrostlakeQuotePolicy())
    include_policy: FrostlakeIncludePolicy = field(
        default_factory=lambda: FrostlakeIncludePolicy()
    )
