---
name: jdbsee
description: >
  Operate JDBC databases with the JDBSee CLI. Use when a request explicitly
  involves JDBSee, JDBC or SQL database connections, database aliases or JDBC
  drivers, database schema exploration, SELECT queries, explicitly requested
  DDL/DML, or the JDBSee MCP server. Do not use for generic listing, searching,
  statistics, analytics, or reporting unless a SQL/JDBC database is part of
  the task.
---

# JDBSee

Use `jdbsee` for structured JDBC database access from the command line.

## Workflow

1. Verify the executable before the first operation:

   ```bash
   command -v jdbsee
   jdbsee --version
   ```

2. Select the connection mode:
   - Prefer an existing alias for repeated access. Discover aliases with
     `jdbsee alias list`.
   - Use `-d`, `-l`, `-u`, and related driver options for one-off access.
   - Never guess an alias, JDBC URL, credential, schema, or SQL dialect.

3. Default to read-only exploration:
   - List tables or views before querying an unfamiliar database.
   - Select explicit columns and add a restrictive predicate or row limit.
   - Use `query`; it validates that the SQL is a SELECT. Do not claim that it
     sets the JDBC connection read-only flag.

4. Treat mutations as a separate workflow:
   - Use `execute` only when the user explicitly requests a database change and
     the target database is unambiguous.
   - State the target alias or JDBC URL and the exact SQL before execution.
   - JDBSee does not expose transaction control for execute batches. Never
     promise atomic commit or rollback; later statements still run after an
     earlier failure. Use a database-native migration mechanism when atomicity
     is required.

5. Validate structured output:
   - Prefer the default JSON format for agent use.
   - Any failure exits non-zero and carries a structured error (`SQL_100`
     rejection, `DB_300` connection, `DB_301` SQL, `CFG_200` driver). Read the
     JSON for which failure it was; use the exit status for `markdown` and `csv`,
     which have no `success` field.
   - If stdout contains no JSON, treat the command as failed and read stderr.
     Never report empty stdout as "no rows".
   - For batch execution, require `summary.failure_count == 0` and inspect every
     item in `results`.
   - Read query rows from `data.objects` or `data.rows` and use
     `data.rowCount` as the returned row count.

6. Report the command outcome, target database, returned or affected row count,
   and any structured error code. Do not expose credentials in the response.

## Connection Patterns

Use an alias:

```bash
jdbsee schema tables -a mydb
jdbsee query -a mydb "SELECT id, name FROM users LIMIT 100"
```

Use an on-demand driver:

```bash
jdbsee query \
  -d "org.postgresql:postgresql:42.2.1" \
  -l "jdbc:postgresql://localhost:5432/mydb" \
  -u user \
  "SELECT id, name FROM users LIMIT 100"
```

For passwords, prefer an existing alias or create one interactively with `-P`.
Do not put a password on the command line unless the user explicitly accepts
shell history and process-list exposure. `-P` requires an interactive console;
in a non-interactive session, ask the user to configure the alias securely.

## Schema Exploration

Use `schema tables`, `schema views`, and `schema driver` for discovery. The CLI
has no table-description command: to inspect column names and JDBC types, run a
zero-row or tightly limited SELECT and read `data.columns` and `data.types`. Do
not infer constraints or indexes from `--metadata enhanced`; the current CLI
query path does not populate that schema detail.

The MCP server does have one. When operating over MCP, use the `describe_table`
tool for per-column name, type, size, and nullability instead of the zero-row
SELECT workaround.

## Safety Rules

- Treat `query` as SELECT validation, not a database permission boundary. It
  rejects non-SELECT statements, `SELECT ... INTO`, `FOR UPDATE`, and stacked
  input, but it cannot recognise every dialect-specific write.
- The `execute` validator blocks only `TRUNCATE`, `DROP DATABASE`, and
  `SHUTDOWN`. `DROP SCHEMA ... CASCADE` and predicate-free
  `DELETE`/`UPDATE` pass. Scope every mutation yourself.
- Never use `execute` to satisfy a request that only asks to inspect data.
- Never run `DROP`, broad `UPDATE`/`DELETE`, or migration files when the target
  or scope is unclear.
- Apply triggers and other block bodies with a database-native client. JDBSee
  refuses input whose `BEGIN ... END` body would be split (`SQL_110`); do not try
  to work around it by sending the fragments separately.
- Never claim that JDBSee can make a DDL/DML batch transactional or roll it back
  after a partial failure.
- Prefer `-o <file>` over shell redirection for CSV or other file output.
- Preserve `~/.jdbsee/.master.key`. Never delete or replace it as a generic
  recovery step; existing encrypted alias passwords depend on it.
- Do not start the MCP server on a non-loopback address unless the user asks for
  it. `/mcp` grants full query access under the server's stored credentials.

## References

- Read [references/commands.md](references/commands.md) when constructing an
  unfamiliar command or interpreting output fields.
- Read [references/error-codes.md](references/error-codes.md) after a structured
  error; follow its non-destructive recovery guidance.
- Read [references/examples.md](references/examples.md) for dialect-specific and
  end-to-end workflows.
- Read [references/faq.md](references/faq.md) for credential, export, connection,
  and master-key troubleshooting.
