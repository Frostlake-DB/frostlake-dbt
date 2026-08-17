# dbt-frostlake

A [dbt](https://www.getdbt.com/) adapter for [Frostlake](https://frostlake.dev), so a
dbt project runs locally with zero warehouse cost.

It is a **standalone adapter**: built directly on `dbt-adapters`, with its own macros and
dialect rules. No other adapter is installed or imported. The transport is
[`frostlake-connector`](https://pypi.org/project/frostlake-connector/) over
[`frostlake`](https://pypi.org/project/frostlake/), which speak Frostlake's HTTP protocol.

## Install

```bash
pip install dbt-frostlake
```

That brings dbt-core, `frostlake-connector` and `frostlake` with it. No JVM is needed on
the client.

## Profile

```yaml
my_project:
  target: dev
  outputs:
    dev:
      type: frostlake
      host: localhost      # Frostlake HTTP server
      port: 18082
      database: demo_db
      schema: public
      threads: 1
```

`account`, `user` and `password` are optional: they are accepted so a profile carried
over from another warehouse still loads, and ignored — the server has no login.

## Start a server for it

The engine is published to Maven Central, so no build is required:

```bash
mvn dependency:get -Dartifact=dev.frostlake:frostlake-db:0.0.7
java -cp "<frostlake-db jar>:<its runtime deps>" dev.frostlake.http.DatabaseHttpServer 18082
```

Requires **JDK 17** and a Frostlake engine **0.0.7 or newer**. Create the target database
once it is up:

```sql
CREATE DATABASE IF NOT EXISTS demo_db;
```

## Smoke-tested surface

Against a booted `DatabaseHttpServer`: `dbt debug`,
`dbt seed` (typed CSV loads incl. DECIMAL), `dbt run` (view + table materializations),
`dbt test` (`unique`, `not_null`), `dbt build`, `dbt snapshot` (check strategy, repeat
runs), incremental models (first and subsequent runs), and `dbt docs generate`
(information_schema catalog) — all green.

## Dialect notes

Frostlake resolves unquoted identifiers uppercase, so the adapter turns dbt's default
quoting off, and seed columns are unquoted unless a project sets `quote_columns`.

Two engine limitations are worked around in the macros: `ALTER VIEW … RENAME TO` rejects
a qualified target (tables accept one), and `CASCADE` is accepted on `DROP SCHEMA` but
not on `DROP TABLE`/`DROP VIEW`.

A runnable example project lives in
[`frostlake-dbt-demo`](https://github.com/Frostlake-DB/frostlake-dbt-demo), a sibling
repository: two seeds, two staging views, a table model and five data tests.

## Tests

```sh
export JAVA_HOME=/path/to/jdk17
export FROSTLAKE_CLASSPATH="/path/to/frostlake-db.jar:<engine deps>"
python3 test/test_adapter.py
```

Covers the plugin wiring dbt reads on load (adapter and credentials classes, the
empty plugin dependency list, the shipped include path), the credential
surface and its `unique_field` connection key, and `open()` against a live server —
handle usability, database/schema context, query tags, and reuse of an already-open
connection. The plugin and credential tests need no server; the connection tests skip
without `FROSTLAKE_CLASSPATH`.

## Still to do

Grind dbt's standard acceptance suite (`dbt-tests-adapter`) green; every failure is either
a facade gap or a Frostlake SQL gap worth fixing.
