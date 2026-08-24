# JDBSee command reference

## Contents

- [Common connection options](#common-connection-options)
- [query](#query)
- [execute](#execute)
- [schema](#schema)
- [driver](#driver)
- [alias](#alias)
- [mcp](#mcp)
- [Result validation](#result-validation)

## Common connection options

`query`, `execute`, and `schema` database subcommands share these options:

| Option | Value | Meaning |
|---|---|---|
| `-a, --alias` | alias name | Reuse a stored connection and driver |
| `-l, --url` | JDBC URL | Use or override the connection URL |
| `-u, --user` | username | Use or override the username |
| `-p, --password` | password | Use a command-line password; exposes it to shell history/process listings |
| `-P, --ask-for-password` | none | Prompt in an interactive console |
| `-d, --dependency` | Maven GAV | Resolve a driver dependency; repeatable |
| `-j, --jar` | JAR path | Load a local driver JAR; repeatable |
| `-c, --driver-class` | class name | Select an explicit JDBC driver class |
| `-m, --driver-class-match` | regex | Match a driver class; defaults to `(.*)Driver(.*)` |
| `-w, --output-width` | integer | Set legacy table output width |

Prefer aliases. Do not combine an alias with overrides unless the override is
intentional and visible to the user.

## query

Run one validated SELECT statement:

```bash
jdbsee query [CONNECTION_OPTIONS] [OUTPUT_OPTIONS] "SELECT ..."
```

Output options:

| Option | Value | Meaning |
|---|---|---|
| `-f, --format` | `json`, `markdown`, `csv` | Output format; defaults to `json` |
| `--metadata` | `basic`, `enhanced`, `full` | Requested metadata level; defaults to `basic` |
| `--pretty` | none | Indent JSON output; affects `-f json` only |
| `-o, --output-file` | path | Write output to a file, replacing its contents |

`--pretty` changes whitespace only — the parsed structure is identical to
compact `json`. It exists for human inspection; keep the compact form for agent
use so the output stays one line per result.

A fourth format code, `json-pretty`, is deprecated: it prints a bare array on
success but a structured envelope on error, and a validation error goes to
stdout without creating the `-o` file. It warns on stderr and will be removed.
Use `-f json --pretty` instead.

The CLI also exposes repeatable `-x, --execute` legacy pre-query statements.
Do not use it in agent workflows: the default JSON/Markdown/CSV path accepts
exactly one statement. Passing more fails with exit 1 and a `CFG_202` envelope
whose details read `AI format only supports single SQL statement` — the code
says connection parameters, but the cause is the statement count.

`query` accepts SELECT statements only and rejects `SELECT ... FOR UPDATE`.
Validation does not set the JDBC connection's read-only flag, so database-side
permissions remain the real security boundary.

Validation reads the parse tree, so it also rejects `SELECT ... INTO` (a write on
SQL Server and Sybase), stacked statements, a separator followed only by a
comment, and `FOR UPDATE` inside a UNION branch or subquery. Keywords appearing
inside string literals no longer trigger a rejection.

Still not a permission boundary: PostgreSQL data-modifying CTEs
(`WITH d AS (DELETE ... RETURNING *) ...`) are rejected because jsqlparser cannot
parse them, not because they are recognised as writes. A dialect variant that
does parse could get through. Database permissions remain the real control.

### JSON success shape

Column labels and JDBC class names depend on the driver. The shape is:

```json
{
  "success": true,
  "data": {
    "columns": ["ID", "NAME"],
    "types": ["java.lang.Integer", "java.lang.String"],
    "rows": [[1, "Alice"]],
    "objects": [{"ID": 1, "NAME": "Alice"}],
    "rowCount": 1
  },
  "metadata": {
    "executionTime": -1,
    "rowCount": 1,
    "columnCount": 2
  }
}
```

`basic` already includes columns and types. The current CLI query path does not
populate table, schema, index, constraint, or statistics fields, so do not rely
on `enhanced` or `full` for table-description data.

### JSON validation error shape

```json
{
  "success": false,
  "error": {
    "code": "SQL_100",
    "message": "Only SELECT statements are allowed",
    "details": "Detected statement type: Update"
  },
  "metadata": {
    "query": "UPDATE users SET active = true",
    "timestamp": 0
  }
}
```

The timestamp is an epoch-millisecond value, not literally `0`.

## execute

Run explicitly requested DDL/DML:

```bash
jdbsee execute [CONNECTION_OPTIONS] [OUTPUT_OPTIONS] "SQL_STATEMENT"
jdbsee execute [CONNECTION_OPTIONS] [OUTPUT_OPTIONS] "SQL1; SQL2; SQL3"
jdbsee execute [CONNECTION_OPTIONS] [OUTPUT_OPTIONS] --sql-file migration.sql
```

Additional options:

| Option | Value | Meaning |
|---|---|---|
| `--sql-file` | path | Read SQL statements from a file |
| `-f, --format` | `json`, `markdown`, `csv` | Output format; defaults to `json` |
| `-o, --output-file` | path | Write output to a file, replacing its contents |

Use the command for expected `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`,
and `DROP TABLE`/`DROP INDEX` operations.

The validator blocks exactly three things by name — `TRUNCATE`, `DROP DATABASE`,
and `SHUTDOWN` — plus SELECT statements and SQL it cannot parse. It is a narrow
blocklist, not a permission policy. Verified to pass validation:

| SQL | Why it matters |
|---|---|
| `DROP SCHEMA public CASCADE` | Drops every object in the schema |
| `DELETE FROM users` | No predicate; deletes all rows |
| `UPDATE users SET active = true` | No predicate; rewrites all rows |

`GRANT`, `REVOKE`, and `ALTER USER ... PASSWORD` are currently rejected only
because jsqlparser fails to parse them, reported as `SQL_103` rather than
`SQL_110`. That is a parser artifact, not a deliberate rule: a dialect variant
that does parse would be allowed through.

Scope every mutation yourself. Do not rely on the validator to catch an
over-broad statement.

### Statement splitting

Multi-statement input is split by parsing. A `CREATE TRIGGER ... BEGIN a; b; END`
body would be torn into fragments, running the inner statements standalone
against the tables, so JDBSee refuses that input before executing anything:

```json
{
  "success": false,
  "error": {
    "code": "SQL_110",
    "message": "Block body cannot be executed safely",
    "details": "Statement splitting would tear this BEGIN ... END body apart..."
  }
}
```

Apply triggers, procedures, and function bodies with a database-native client.

`CREATE PROCEDURE` and `CREATE FUNCTION` bodies are kept intact by the parser
and execute normally. Semicolons inside string literals are also handled
correctly (`INSERT INTO t(a) VALUES ('a;b')` stays one statement).

Single-statement JSON success:

```json
{
  "success": true,
  "statement_type": "DML",
  "rows_affected": 3,
  "sql": "UPDATE users SET active = true WHERE team_id = 7"
}
```

Batch JSON shape:

```json
{
  "results": [
    {
      "index": 0,
      "success": true,
      "statement_type": "DDL",
      "rows_affected": 0,
      "sql": "CREATE TABLE example (id INT)"
    },
    {
      "index": 1,
      "success": false,
      "error": {
        "code": "SQL_110",
        "message": "Dangerous DDL operation is blocked",
        "details": "DROP DATABASE is not allowed"
      },
      "sql": "DROP DATABASE example"
    }
  ],
  "summary": {
    "total": 2,
    "success_count": 1,
    "failure_count": 1
  }
}
```

Statements share one connection, execute independently, and continue after a
failure. JDBSee does not expose begin/commit/rollback controls for this command;
do not claim the batch is atomic or can be rolled back. Use a database-native
migration mechanism when transaction guarantees are required.

## schema

```bash
jdbsee schema tables -a mydb [PATTERN]
jdbsee schema views -a mydb [PATTERN]
jdbsee schema driver -a mydb [PROPERTY_PATTERN]
```

Patterns are JDBC metadata patterns and may be case-sensitive depending on how
the database stores identifiers. These commands list tables/views or driver
properties; they do not provide a full table-description command.

## driver

```bash
jdbsee driver create NAME [-c DRIVER_CLASS] [-m CLASS_REGEX] [-j JAR]... [-d GAV]...
jdbsee driver list
jdbsee driver show DRIVER_ID
jdbsee driver delete DRIVER_ID...
jdbsee driver jar-add DRIVER_ID JAR...
jdbsee driver jar-remove JAR_ID...
jdbsee driver dependency-add DRIVER_ID GAV...
jdbsee driver dependency-remove DEPENDENCY_ID...
```

Use numeric IDs returned by `driver list` for show, delete, and modification
commands. Only `driver create` uses a driver name as its primary argument.
Maven coordinates use `groupId:artifactId:version` notation.

## alias

```bash
jdbsee alias create DRIVER NAME JDBC_URL [-u USER] [-p PASSWORD] [-P] [-r REMARK]
jdbsee alias list
jdbsee alias show ALIAS_ID
jdbsee alias delete ALIAS_ID...
```

`DRIVER` may be a driver name or ID. `alias show` and `alias delete` require the
numeric ID from `alias list`, not the alias name. Remarks are stored in a
`VARCHAR(200)` column.

Prefer `-P` when creating an alias in an interactive terminal. Stored non-empty
passwords are encrypted with AES-256-GCM, and `alias show` omits passwords.

## mcp

```bash
jdbsee mcp start [-a ALIAS] [--port PORT] [--bind HOST] [--auth-token TOKEN] \
  [--timeout SECONDS] [--max-results ROWS]
```

Defaults are bind `127.0.0.1`, port `8080`, cache TTL `300` seconds, and `10000`
rows per query result. Values must be positive. The process blocks until
interrupted.

`--timeout` is the result-cache TTL, not a query timeout. JDBSee does not expose
a statement timeout; a slow query is bounded by the driver and the database, not
by this flag.

### Authentication

Every request to `/mcp` requires a bearer token. When `--auth-token` is not
supplied, the server generates one and prints it at startup:

```
Auth token (自动生成): <token>
```

Send it on both the GET stream and the POST requests:

```bash
curl -X POST localhost:8080/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

A missing or wrong token returns HTTP 401 with JSON-RPC error `-32001`.

The server binds to loopback by default. `--bind 0.0.0.0` makes the database
reachable from the network; it prints a warning and should only be used when the
user explicitly asks for it.

### Tools

The HTTP/SSE JSON-RPC endpoint is `/mcp`. Available tools are `execute_query`,
`list_tables`, `describe_table`, `get_result`, and `list_aliases`.

Unlike the CLI, MCP does provide a real table-description operation:
`describe_table` returns `column_name`, `data_type`, `column_size`, and
`nullable` per column.

`execute_query` returns a `result_id` plus metadata rather than the rows; fetch
rows with `get_result`. Its metadata includes `truncated`: when true, the result
set was larger than `max_results` and the response holds only the first
`max_results` rows. Treat a truncated result as incomplete — narrow the query
instead of reporting partial data as complete.

## Result validation

Verified CLI behaviour in JSON mode:

| Situation | Output | Exit code |
|---|---|---|
| Successful query or execute | JSON envelope, `success:true` | 0 |
| Validation rejection (non-SELECT) | JSON, `success:false`, e.g. `SQL_100` | 1 |
| SQL error (bad identifier, syntax) | JSON, `success:false`, `DB_301` | 1 |
| Connection failure | JSON, `success:false`, `DB_300` | 1 |
| Missing driver | JSON, `success:false`, `CFG_200` | 1 |
| Batch with any failed statement | JSON, `summary.failure_count > 0` | 1 |

Every failure carries a structured envelope and exits non-zero, in every format.
That matters for `markdown` and `csv`, which have no `success` field: the exit
status is the only cross-format signal. stderr holds a one-line
`Command failed: ...` summary.

Still read the JSON rather than relying on the exit status alone — it tells you
which failure occurred and how far a batch got.

- For a single JSON result, require `success == true`.
- For a batch JSON result, require `summary.failure_count == 0` and inspect each
  item in `results`.
- If stdout holds no JSON at all, something unexpected happened — treat the
  command as failed and read stderr.
- Treat `data.rowCount` as returned rows and `rows_affected` as the JDBC update
  count; neither proves that the broader user intent was satisfied.
- JSON, Markdown, and CSV use different error envelopes. Request JSON whenever
  an agent must branch on the result.
