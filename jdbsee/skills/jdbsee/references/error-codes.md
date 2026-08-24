# JDBSee error codes

Use this reference after receiving a structured JDBSee error. Not every command
uses the same envelope: `query` and `execute` provide structured output in JSON
mode, while configuration commands may print an error to stderr.

## What is actually emitted

Runtime failures are classified onto these codes in both `query` and `execute`,
single-statement and batch alike:

| Code | Raised by |
|---|---|
| `DB_300` | connection refused, unknown host, socket timeout |
| `DB_301` | SQL execution error (bad identifier, syntax, constraint) |
| `CFG_200` | no suitable driver, driver class not found |
| `CFG_202` | unusable connection parameters, null DataSource |
| `SYS_500` | anything not classifiable |

Validation codes (`SQL_1xx`), configuration codes (`CFG_204`–`CFG_206`) and the
crypto codes (`SEC_4xx`) come from their own paths.

Codes still marked *(not emitted)* below have no emission site in the current
build. Do not branch on them.

## SQL validation

| Code | Meaning | Response |
|---|---|---|
| `SQL_100` | Query received a non-SELECT statement, or a `SELECT ... INTO` | Use `execute` only if the user explicitly requested a mutation |
| `SQL_101` | Stacked statements, or a separator followed only by a comment | Send one statement per invocation; do not try to batch through `query` |
| `SQL_102` | `SELECT ... FOR UPDATE` rejected, including inside a UNION branch or subquery | Remove the locking clause or use an explicitly authorized write workflow |
| `SQL_103` | SQL is empty or cannot be parsed | Check dialect, quoting, comments, and parentheses |
| `SQL_110` | Dangerous DDL blocked, or a block body that splitting would tear apart | Do not retry DROP DATABASE, TRUNCATE, or SHUTDOWN through JDBSee. For a `BEGIN ... END` body, use a database-native client rather than sending fragments |
| `SQL_111` | Execute received SELECT | Run the statement with `jdbsee query` |

## Configuration

| Code | Meaning | Response |
|---|---|---|
| `CFG_200` | JDBC driver not found | Check the Maven GAV, configured JAR, driver class, and network access |
| `CFG_201` | Database alias not found *(not emitted)* | Run `jdbsee alias list`; do not guess a replacement alias |
| `CFG_202` | Invalid connection parameters | Verify JDBC URL, user, alias overrides, and required driver options |
| `CFG_203` | Driver name already exists *(not emitted)* | Reuse the existing driver or choose a deliberate new name |
| `CFG_204` | Alias name already exists | Reuse the alias or choose a deliberate new name |
| `CFG_205` | JAR is outside an allowed dropins directory | Move/register the JAR through the configured dropins layout |
| `CFG_206` | JAR is outside `dropins/DRIVER_NAME/` | Place it in the named driver's dropins directory |
| `CFG_207` | Driver dropins directory does not exist *(not emitted)* | Create/configure the expected driver directory before retrying |

A missing alias or driver surfaces as an unstructured error, not `CFG_200`/
`CFG_201`. Confirm the alias with `jdbsee alias list` before retrying.

## Database and system

| Code | Meaning | Response |
|---|---|---|
| `DB_300` | Database connection failed | Verify the target host, port, JDBC URL, credentials, and database availability |
| `DB_301` | SQL execution failed | Read `error.details`; verify identifiers and dialect before changing SQL |
| `DB_302` | Query execution timeout *(only from a driver-raised SQLTimeoutException)* | Narrow the query or investigate database load; do not silently remove limits |
| `SYS_500` | Internal error | Preserve the error details and logs, then report the failed operation |
| `SYS_501` | File I/O error *(not emitted)* | Verify the input/output path and permissions without overwriting unrelated files |

JDBSee sets no statement timeout of its own, so `DB_302` appears only when the
driver or database raises one. A slow query otherwise blocks until they end it.
The MCP `--timeout` flag is a cache TTL and does not bound query duration.

## Encryption and migration

| Code | Meaning | Safe response |
|---|---|---|
| `SEC_400` | Master key is corrupted or not exactly 32 bytes | Stop writes; back up `~/.jdbsee` and restore the original key from a trusted backup |
| `SEC_401` | Master key cannot be read | Check ownership and owner read permission; do not replace the key |
| `SEC_402` | Alias password decryption failed | Preserve the key and configuration; recreate only the affected alias after confirming credentials |
| `SEC_403` | Password encryption failed | Check runtime crypto support, disk state, and directory permissions; keep the existing key |
| `SEC_404` | Some plaintext alias passwords were not migrated | Back up `~/.jdbsee`, identify affected aliases from logs, and recreate them securely |

Never delete or regenerate `.master.key` as a generic fix. Existing encrypted
alias passwords cannot be recovered without the original key. If the key is
irretrievably lost, preserve a backup of the configuration and recreate the
affected aliases with newly supplied credentials.

## Agent handling

1. Parse the structured code and preserve `message` and `details`.
2. Confirm the target alias or JDBC URL before retrying.
3. Prefer a diagnostic SELECT or metadata command over a mutation.
4. Do not weaken SQL validation or remove safety limits to make a command pass.
5. Report whether the retry succeeded by inspecting JSON fields, not only the
   process exit status.
