# JDBSee troubleshooting

## How should large queries be handled?

Select only required columns and use predicates plus a dialect-appropriate row
limit. Prefer keyset pagination over a growing OFFSET when a stable indexed key
is available.

```sql
SELECT id, name
FROM users
WHERE status = 'active' AND id > 1000
ORDER BY id
LIMIT 1000
```

Do not run an unbounded `SELECT *` merely to discover the schema. Use a zero-row
query such as `SELECT * FROM users WHERE 1 = 0`.

## How should passwords containing special characters be supplied?

Do not rely on shell quoting as the primary protection. Command-line passwords
can be exposed through history and process listings, and double quotes still
allow some shell expansion.

Prefer an existing alias or create one in an interactive terminal:

```bash
jdbsee alias create postgres mydb \
  "jdbc:postgresql://localhost:5432/mydb" \
  -u app_user -P
```

`-P` requires a real console. In a non-interactive agent session, ask the user
to create the alias securely instead of placing the password in the command.

## How should CSV be exported?

Use the built-in output option, which replaces the target file:

```bash
jdbsee query -a mydb -f csv -o users.csv \
  "SELECT id, name FROM users ORDER BY id"
```

Confirm before overwriting an existing material file.

## How is a failure signalled?

Through both the exit status and the payload. Every known failure exits non-zero,
including a validation rejection, in all formats. In JSON mode also:

- Require `success == true` for a single query or execute result.
- Require `summary.failure_count == 0` for a batch.
- Inspect every batch item because execution continues after failures.

For `markdown` and `csv` there is no `success` field, so the exit status is the
only reliable signal.

## How is a connection or SQL failure reported?

As a structured error with exit status 1. stderr carries only a one-line
`Command failed: ...` summary:

```bash
$ jdbsee query -a mydb "SELECT id FROM nope"
{"success":false,"error":{"code":"DB_301","message":"SQL execution error",
 "details":"..."},"metadata":{...}}
```

Codes: `DB_300` for a connection problem, `DB_301` for a SQL error, `CFG_200` for
a missing driver, `CFG_202` for unusable connection parameters, `SYS_500`
otherwise. Read `error.details` for the driver's own message.

If stdout is empty something unexpected happened; capture stderr rather than
assuming the table had no rows.

## How should JSON be inspected?

```bash
jdbsee query -a mydb "SELECT id, name FROM users LIMIT 5" \
  | jq -e '.success == true'
```

Read query rows from `.data.objects` or `.data.rows`. The `.data` field is an
object, not a top-level array.

## What should be checked after a connection failure?

1. Confirm the intended alias with `jdbsee alias list`.
2. Verify the database service, host, port, and network path.
3. Verify the JDBC URL and driver dependency/class.
4. Test with a bounded query such as `SELECT 1` using the confirmed target.
5. Preserve the structured error details when reporting the failure.

Do not substitute another environment or alias merely because it connects.

## Do alias and driver commands use names or IDs?

- `query -a` and `schema -a` use an alias name.
- `alias show` and `alias delete` use numeric alias IDs.
- `driver show`, `driver delete`, `driver jar-add`, and dependency/JAR removal
  commands use numeric IDs returned by `driver list`.
- `driver create` accepts a new name; `alias create` accepts a driver name or ID.

## How are alias remarks added?

Use `-r` during alias creation:

```bash
jdbsee alias create h2 devdb "jdbc:h2:mem:dev" \
  -u sa -r "Development test database"
```

Remarks are limited by the backing `VARCHAR(200)` column. No separate skill
rebuild step is required.

## What is the difference between query and execute?

- `query` accepts a single SELECT and rejects `SELECT ... FOR UPDATE`,
  `SELECT ... INTO`, and stacked statements.
- `execute` is for explicitly requested DDL/DML and rejects SELECT, TRUNCATE,
  DROP DATABASE, SHUTDOWN, parse failures, and suspicious patterns.
- `query` does not set the JDBC connection read-only flag. Use database
  permissions as the real authorization boundary.

## What should be done when `.master.key` is damaged or missing?

Stop alias writes and back up the complete `~/.jdbsee` directory. Restore the
original 32-byte key from a trusted backup if possible. Do not delete or replace
the key as a generic repair: existing encrypted alias passwords depend on it.

If the original key is irretrievably lost, the encrypted passwords cannot be
recovered. Preserve the configuration backup, then recreate affected aliases
with newly supplied credentials.
