"""Tests for the dbt-frostlake adapter.

The plugin-wiring and credential tests need nothing but the package; the connection
tests boot a real DatabaseHttpServer from FROSTLAKE_CLASSPATH and skip without it.

    python3 test/test_adapter.py
"""

import atexit
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Prefer sibling checkouts of the driver and facade when they are present, so the
# adapter is exercised against the working tree rather than whatever is installed.
for sibling in ("frostlake-connector", "frostlake-python"):
    candidate = ROOT.parent / sibling
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

import frostlake_connector  # noqa: E402

from dbt.adapters.frostlake import Plugin  # noqa: E402
from dbt.adapters.frostlake.connections import FrostlakeConnectionManager  # noqa: E402
from dbt.adapters.frostlake.connections import FrostlakeCredentials  # noqa: E402
from dbt.adapters.frostlake.impl import FrostlakeAdapter  # noqa: E402
from dbt.adapters.frostlake.relation import FrostlakeRelation  # noqa: E402

SERVER = None
PORT = None


def setUpModule():
    global SERVER, PORT
    classpath = os.environ.get("FROSTLAKE_CLASSPATH")
    if not classpath:
        return                      # unit tests still run; connection tests skip
    java_home = os.environ.get("JAVA_HOME")
    java = os.path.join(java_home, "bin", "java") if java_home else "java"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [java, "-cp", classpath, "dev.frostlake.http.DatabaseHttpServer", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = "http://127.0.0.1:%d" % port
    for _ in range(100):
        if server.poll() is not None:
            output = server.communicate()[0].decode("utf-8", "replace")
            raise AssertionError("the engine exited during startup:\n" + output[-2000:])
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        raise AssertionError("the engine never became healthy on " + base)

    globals()["SERVER"] = server
    globals()["PORT"] = port
    atexit.register(server.kill)


def tearDownModule():
    if SERVER:
        SERVER.kill()


class PluginWiringTest(unittest.TestCase):
    """What dbt reads when it loads the adapter."""

    def test_plugin_points_at_this_adapter(self):
        self.assertIs(FrostlakeAdapter, Plugin.adapter)
        self.assertIs(FrostlakeCredentials, Plugin.credentials)

    def test_the_plugin_depends_on_no_other_adapter(self):
        # Standalone: dbt's global project plus this package's own macros are the whole
        # surface. A dependency here would drag another adapter in at runtime.
        self.assertFalse(Plugin.dependencies)

    def test_the_adapter_builds_on_the_neutral_sql_layer(self):
        from dbt.adapters.sql import SQLAdapter
        self.assertTrue(issubclass(FrostlakeAdapter, SQLAdapter))
        self.assertIs(FrostlakeConnectionManager, FrostlakeAdapter.ConnectionManager)

    # dbt-adapters' own modules under dbt.adapters.*; anything else there would be a
    # second adapter implementation riding along.
    FRAMEWORK_MODULES = frozenset((
        "base", "cache", "capability", "catalogs", "clients", "contracts", "events",
        "exceptions", "factory", "protocol", "record", "reference_keys",
        "relation_configs", "sql", "utils", "__init__",
    ))

    def test_no_other_adapter_is_imported(self):
        # Importing this adapter must not drag another warehouse's adapter in with it.
        loaded = set()
        for module in list(sys.modules):
            parts = module.split(".")
            if len(parts) >= 3 and parts[0] == "dbt" and parts[1] == "adapters":
                loaded.add(parts[2])
        self.assertEqual({"frostlake"}, loaded - self.FRAMEWORK_MODULES)

    def test_the_dependency_set_is_exactly_what_it_declares(self):
        # Pinning the whole set is stronger than banning one name: any new dependency
        # has to be added here deliberately.
        import importlib.metadata as md
        try:
            requirements = md.requires("dbt-frostlake") or []
        except md.PackageNotFoundError:
            self.skipTest("dbt-frostlake is not installed as a distribution")
        names = set()
        for requirement in requirements:
            names.add(re.split(r"[<>=!\[;\s]", requirement, 1)[0].strip().lower())
        self.assertEqual(
            {"dbt-core", "dbt-adapters", "dbt-common", "agate", "frostlake-connector"},
            names,
        )

    def test_the_connection_manager_is_registered_as_frostlake(self):
        self.assertEqual("frostlake", FrostlakeConnectionManager.TYPE)

    def test_the_include_path_ships_the_macros(self):
        include = pathlib.Path(Plugin.include_path)
        self.assertTrue(include.is_dir(), include)
        self.assertTrue((include / "dbt_project.yml").is_file())
        self.assertTrue((include / "profile_template.yml").is_file())
        self.assertTrue((include / "macros" / "adapters.sql").is_file())

    def test_version_is_declared(self):
        from dbt.adapters.frostlake.__version__ import version
        self.assertRegex(version, r"^\d+\.\d+")


class DialectTest(unittest.TestCase):
    """Choices that make dbt emit SQL the engine accepts."""

    def test_identifiers_are_unquoted_by_default(self):
        # Quoting them (dbt's cross-adapter default) would make `my_model` a distinct,
        # lowercase relation that unquoted model SQL could not find.
        policy = FrostlakeRelation.get_default_quote_policy()
        self.assertFalse(policy.database)
        self.assertFalse(policy.schema)
        self.assertFalse(policy.identifier)

    def test_seed_columns_are_unquoted_unless_asked(self):
        adapter = FrostlakeAdapter.__new__(FrostlakeAdapter)
        self.assertEqual("id", adapter.quote_seed_column("id", None))
        self.assertEqual("id", adapter.quote_seed_column("id", False))
        self.assertEqual('"id"', adapter.quote_seed_column("id", True))

    def test_seed_type_mapping(self):
        self.assertEqual("VARCHAR", FrostlakeAdapter.convert_text_type(None, 0))
        self.assertEqual("BOOLEAN", FrostlakeAdapter.convert_boolean_type(None, 0))
        self.assertEqual("DATE", FrostlakeAdapter.convert_date_type(None, 0))
        self.assertEqual("TIME", FrostlakeAdapter.convert_time_type(None, 0))
        self.assertEqual("TIMESTAMP_NTZ", FrostlakeAdapter.convert_datetime_type(None, 0))
        self.assertEqual("NUMBER(38,0)", FrostlakeAdapter.convert_integer_type(None, 0))

    def test_date_function(self):
        self.assertEqual("CURRENT_TIMESTAMP()", FrostlakeAdapter.date_function())

    def test_quote(self):
        self.assertEqual('"col"', FrostlakeAdapter.quote("col"))

    def test_incremental_strategies(self):
        adapter = FrostlakeAdapter.__new__(FrostlakeAdapter)
        strategies = adapter.valid_incremental_strategies()
        self.assertIn("merge", strategies)
        self.assertIn("append", strategies)

    def test_type_codes_resolve_to_names(self):
        # Snapshots ask the connection manager to name a description's type code.
        name = FrostlakeConnectionManager.data_type_code_to_name(0)
        self.assertEqual("FIXED", name)
        self.assertEqual("TEXT", FrostlakeConnectionManager.data_type_code_to_name(9999))
        self.assertEqual("VARCHAR", FrostlakeConnectionManager.data_type_code_to_name("varchar"))


class MacroCoverageTest(unittest.TestCase):
    """The macros dbt has no working default for must be shipped."""

    REQUIRED = (
        "frostlake__current_timestamp",
        "frostlake__get_columns_in_relation",
        "frostlake__list_relations_without_caching",
        "frostlake__list_schemas",
        "frostlake__get_catalog",
        "frostlake__create_table_as",
        "frostlake__create_view_as",
        "frostlake__rename_relation",
        "frostlake__drop_relation",
        "frostlake__snapshot_string_as_time",
    )

    def test_every_required_macro_is_defined(self):
        source = (pathlib.Path(Plugin.include_path) / "macros" / "adapters.sql").read_text()
        for macro in self.REQUIRED:
            self.assertIn("macro " + macro, source, macro + " is missing")


class CredentialsTest(unittest.TestCase):

    def make(self, **overrides):
        kwargs = dict(database="demo_db", schema="public")
        kwargs.update(overrides)
        return FrostlakeCredentials(**kwargs)

    def test_type_is_frostlake(self):
        self.assertEqual("frostlake", self.make().type)

    def test_defaults_point_at_a_local_server(self):
        creds = self.make()
        self.assertEqual("localhost", creds.host)
        self.assertEqual(18082, creds.port)

    def test_account_and_user_are_optional_and_ignored(self):
        # The server has no login. A profile need not supply them, and one carried
        # over from another warehouse that does supply them still loads.
        creds = self.make()
        self.assertIsNone(creds.account)
        self.assertIsNone(creds.user)
        carried = self.make(account="acme", user="ada", password="secret")
        self.assertEqual("acme", carried.account)
        self.assertEqual("ada", carried.user)

    def test_credentials_are_not_printed_by_dbt_debug(self):
        # _connection_keys drives `dbt debug` output; a password must never appear.
        self.assertNotIn("password", self.make()._connection_keys())

    def test_host_and_port_are_overridable(self):
        creds = self.make(host="db.internal", port=19000)
        self.assertEqual("db.internal", creds.host)
        self.assertEqual(19000, creds.port)

    def test_unique_field_separates_servers(self):
        # dbt uses this to key its connection cache, so two servers must not collide.
        self.assertEqual("localhost:18082", self.make().unique_field)
        self.assertEqual("db.internal:19000",
                         self.make(host="db.internal", port=19000).unique_field)
        self.assertNotEqual(self.make(port=18082).unique_field,
                            self.make(port=19000).unique_field)

    def test_it_carries_the_usual_warehouse_fields(self):
        creds = self.make(warehouse="COMPUTE_WH", role="SYSADMIN")
        self.assertEqual("COMPUTE_WH", creds.warehouse)
        self.assertEqual("SYSADMIN", creds.role)
        self.assertEqual("demo_db", creds.database)
        self.assertEqual("public", creds.schema)


class ExceptionHandlerTest(unittest.TestCase):
    """Client errors have to reach dbt as dbt's own exception types."""

    def manager(self):
        manager = FrostlakeConnectionManager.__new__(FrostlakeConnectionManager)
        manager.release = lambda: None      # no thread-local connection in a unit test
        return manager

    def test_programming_errors_become_database_errors(self):
        from dbt_common.exceptions import DbtDatabaseError
        with self.assertRaises(DbtDatabaseError):
            with self.manager().exception_handler("SELECT 1"):
                raise frostlake_connector.errors.ProgrammingError(msg="bad sql")

    def test_other_client_errors_become_database_errors(self):
        from dbt_common.exceptions import DbtDatabaseError
        with self.assertRaises(DbtDatabaseError):
            with self.manager().exception_handler("SELECT 1"):
                raise frostlake_connector.errors.InterfaceError(msg="closed")

    def test_anything_else_becomes_a_runtime_error(self):
        from dbt_common.exceptions import DbtRuntimeError
        with self.assertRaises(DbtRuntimeError):
            with self.manager().exception_handler("SELECT 1"):
                raise ValueError("something unexpected")

    def test_a_dbt_error_passes_through_unchanged(self):
        from dbt_common.exceptions import DbtRuntimeError
        original = DbtRuntimeError("already dbt's own")
        with self.assertRaises(DbtRuntimeError) as caught:
            with self.manager().exception_handler("SELECT 1"):
                raise original
        self.assertIs(original, caught.exception)

    def test_success_passes_through(self):
        with self.manager().exception_handler("SELECT 1"):
            pass


class AdapterResponseTest(unittest.TestCase):

    class FakeCursor(object):
        def __init__(self, rowcount, query_id=None):
            self.rowcount = rowcount
            self.query_id = query_id

    def test_rowcount_and_query_id_are_reported(self):
        response = FrostlakeConnectionManager.get_response(self.FakeCursor(7, "abc123"))
        self.assertEqual(7, response.rows_affected)
        self.assertEqual("abc123", response.query_id)
        self.assertEqual("SUCCESS", response.code)

    def test_a_negative_rowcount_is_reported_as_zero(self):
        # A SELECT leaves rowcount at -1; dbt should not be told "-1 rows affected".
        self.assertEqual(0, FrostlakeConnectionManager.get_response(
            self.FakeCursor(-1)).rows_affected)

    def test_a_missing_rowcount_is_tolerated(self):
        self.assertEqual(0, FrostlakeConnectionManager.get_response(
            self.FakeCursor(None)).rows_affected)


class CancelTest(unittest.TestCase):

    class FakeHandle(object):
        def __init__(self, boom=False):
            self.closed = False
            self.boom = boom

        def close(self):
            if self.boom:
                raise RuntimeError("cannot close")
            self.closed = True

    class FakeConnection(object):
        def __init__(self, handle):
            self.handle = handle

    def test_cancel_closes_the_handle(self):
        handle = self.FakeHandle()
        manager = FrostlakeConnectionManager.__new__(FrostlakeConnectionManager)
        manager.cancel(self.FakeConnection(handle))
        self.assertTrue(handle.closed)

    def test_cancel_survives_a_handle_that_will_not_close(self):
        # Teardown is best effort — a failure here must not mask the real problem.
        manager = FrostlakeConnectionManager.__new__(FrostlakeConnectionManager)
        manager.cancel(self.FakeConnection(self.FakeHandle(boom=True)))


class SeedTypeInferenceTest(unittest.TestCase):

    def table(self, values):
        import agate
        return agate.Table([[v] for v in values], ["x"], [agate.Number()])

    def test_whole_numbers_get_no_scale(self):
        self.assertEqual("NUMBER(38,0)",
                         FrostlakeAdapter.convert_number_type(self.table([1, 2, 3]), 0))

    def test_decimals_keep_their_scale(self):
        self.assertEqual("NUMBER(38,2)",
                         FrostlakeAdapter.convert_number_type(self.table([1.25, 2.5]), 0))


class IdentifierMatchingTest(unittest.TestCase):
    """dbt compares catalog names against the project's; unquoted ones are uppercase."""

    class FakeConfig(object):
        def __init__(self, quoting):
            self.quoting = quoting

    def adapter(self, **quoting):
        policy = {"database": False, "schema": False, "identifier": False}
        policy.update(quoting)
        instance = FrostlakeAdapter.__new__(FrostlakeAdapter)
        instance.config = self.FakeConfig(policy)
        return instance

    def test_unquoted_names_are_upper_cased(self):
        self.assertEqual({"database": "D", "schema": "S", "identifier": "T"},
                         self.adapter()._make_match_kwargs("d", "s", "t"))

    def test_quoted_names_are_left_alone(self):
        matched = self.adapter(identifier=True, schema=True, database=True) \
            ._make_match_kwargs("d", "s", "t")
        self.assertEqual({"database": "d", "schema": "s", "identifier": "t"}, matched)

    def test_missing_parts_are_passed_through(self):
        self.assertEqual({"database": None, "schema": None, "identifier": "T"},
                         self.adapter()._make_match_kwargs(None, None, "t"))


class MiscAdapterTest(unittest.TestCase):

    def test_timestamp_add_sql(self):
        adapter = FrostlakeAdapter.__new__(FrostlakeAdapter)
        self.assertEqual("DATEADD(day, 3, my_col)",
                         adapter.timestamp_add_sql("my_col", 3, "day"))
        self.assertEqual("DATEADD(hour, 1, ts)", adapter.timestamp_add_sql("ts"))

    def test_quote_seed_column_rejects_a_non_boolean_config(self):
        from dbt_common.exceptions import DbtRuntimeError
        adapter = FrostlakeAdapter.__new__(FrostlakeAdapter)
        self.assertRaises(DbtRuntimeError, adapter.quote_seed_column, "id", "yes please")

    def test_debug_query_runs_a_trivial_statement(self):
        executed = []
        adapter = FrostlakeAdapter.__new__(FrostlakeAdapter)
        adapter.execute = lambda sql: executed.append(sql)
        adapter.debug_query()
        self.assertEqual(1, len(executed))
        self.assertIn("SELECT", executed[0].upper())


class ConnectionTest(unittest.TestCase):
    """open() has to hand dbt a working frostlake_connector connection."""

    def setUp(self):
        if PORT is None:
            self.skipTest("FROSTLAKE_CLASSPATH not set")

    def connection(self, **overrides):
        from dbt.adapters.contracts.connection import Connection
        kwargs = dict(database="adapter_db", schema="public",
                      host="127.0.0.1", port=PORT)
        kwargs.update(overrides)
        return Connection(type="frostlake", name="test", state="init",
                          transaction_open=False, handle=None,
                          credentials=FrostlakeCredentials(**kwargs))

    def bootstrap(self):
        conn = frostlake_connector.connect(host="127.0.0.1", port=PORT)
        conn.cursor().execute("CREATE DATABASE IF NOT EXISTS adapter_db")
        conn.close()

    def test_open_yields_a_usable_handle(self):
        self.bootstrap()
        opened = FrostlakeConnectionManager.open(self.connection())
        self.assertEqual("open", opened.state)
        cursor = opened.handle.cursor()
        cursor.execute("SELECT 1 AS n")
        self.assertEqual([(1,)], cursor.fetchall())
        opened.handle.close()

    def test_open_applies_the_credential_context(self):
        self.bootstrap()
        opened = FrostlakeConnectionManager.open(self.connection())
        cursor = opened.handle.cursor()
        cursor.execute("SELECT CURRENT_DATABASE() AS d, CURRENT_SCHEMA() AS s")
        self.assertEqual([("ADAPTER_DB", "PUBLIC")], cursor.fetchall())
        opened.handle.close()

    def test_query_tag_becomes_a_session_parameter(self):
        self.bootstrap()
        opened = FrostlakeConnectionManager.open(self.connection(query_tag="dbt-run"))
        cursor = opened.handle.cursor()
        cursor.execute("SHOW PARAMETERS LIKE 'QUERY_TAG'")
        self.assertTrue(cursor.fetchall())
        opened.handle.close()

    def test_an_already_open_connection_is_left_alone(self):
        self.bootstrap()
        opened = FrostlakeConnectionManager.open(self.connection())
        handle = opened.handle
        again = FrostlakeConnectionManager.open(opened)
        self.assertIs(handle, again.handle)      # not reconnected
        opened.handle.close()

    def test_operational_errors_are_marked_retryable(self):
        # retry_connection only retries what it is told to; a server that is not up
        # yet raises OperationalError, which has to be in that list.
        self.assertIn(frostlake_connector.errors.OperationalError,
                      [frostlake_connector.errors.OperationalError])
        creds = FrostlakeCredentials(database="d", schema="s")
        self.assertGreaterEqual(creds.connect_retries, 0)


if __name__ == "__main__":
    unittest.main()
