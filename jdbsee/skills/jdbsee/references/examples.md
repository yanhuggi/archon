# JDBSee workflows

Treat names and SQL below as examples. Confirm the actual alias, schema,
identifiers, and database dialect before running them.

## Read-only exploration

List available aliases and tables:

```bash
jdbsee alias list
jdbsee schema tables -a sales_db
```

Inspect returned columns and JDBC types without reading table data:

```bash
jdbsee query -a sales_db \
  "SELECT * FROM order_items WHERE 1 = 0"
```

Run a bounded query:

```bash
jdbsee query -a sales_db \
  "SELECT id, order_id, quantity, unit_price FROM order_items ORDER BY id LIMIT 100"
```

Read rows from `data.objects` and verify `success == true`.

## Dialect-specific date analysis

Do not reuse date expressions across database products without checking the
dialect. For PostgreSQL, last-month sales can be queried as:

```bash
jdbsee query -a sales_postgres \
  "SELECT p.product_name,
          SUM(oi.quantity * oi.unit_price) AS total_sales
     FROM order_items oi
     JOIN products p ON p.id = oi.product_id
     JOIN orders o ON o.id = oi.order_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '1 month'
    GROUP BY p.product_name
    ORDER BY total_sales DESC
    LIMIT 10"
```

For another database, replace the date expression with that database's syntax.

## On-demand H2 query

```bash
jdbsee query \
  -d "com.h2database:h2:1.4.196" \
  -l "jdbc:h2:mem:testdb" \
  -u sa \
  "SELECT id, name FROM users ORDER BY id LIMIT 20"
```

Add `-c org.h2.Driver` if automatic driver detection is ambiguous.

## Secure alias creation

In an interactive terminal, avoid putting the password in command history:

```bash
jdbsee driver create postgres \
  -d "org.postgresql:postgresql:42.2.1" \
  -c org.postgresql.Driver

jdbsee alias create postgres sales_db \
  "jdbc:postgresql://localhost:5432/sales" \
  -u analyst -P -r "Read-only sales reporting"
```

After creation, obtain the numeric alias ID with `jdbsee alias list` before
using `alias show` or `alias delete`.

## Explicit development mutation

Only use this workflow after the user explicitly requests the change and the
`dev_db` target is confirmed:

```bash
jdbsee execute -a dev_db \
  "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100), email VARCHAR(200))"

jdbsee execute -a dev_db \
  "INSERT INTO users (id, name, email) VALUES (1, 'Alice', 'alice@example.com')"

jdbsee query -a dev_db \
  "SELECT id, name, email FROM users WHERE id = 1"
```

For multiple statements, inspect the batch summary and every result:

```bash
jdbsee execute -a dev_db \
  "INSERT INTO users (id, name) VALUES (2, 'Bob');
   INSERT INTO users (id, name) VALUES (3, 'Carol')"
```

Do not treat the batch as successful unless `summary.failure_count` is zero.
Do not promise atomic execution or rollback; JDBSee does not expose transaction
control for execute batches.

## Migration file

Review the file contents and target database before execution:

```bash
jdbsee execute -a dev_db --sql-file migration.sql -o migration-result.json
```

Inspect `migration-result.json`, then verify the intended schema or data change
with a read-only command.

A file containing a trigger body whose `BEGIN ... END` would be split is refused
with `SQL_110` before anything runs; apply it with a database-native client.

The batch is not transactional: a failure partway through leaves earlier
statements applied. Verify the outcome rather than assuming all-or-nothing.

## CSV export

Prefer the built-in output option over shell redirection:

```bash
jdbsee query -a sales_db -f csv -o users.csv \
  "SELECT id, name, status FROM users ORDER BY id"
```

The query still needs a deliberate scope. Add a predicate or pagination for a
large table.

## Structured result checks

For a single JSON query:

```bash
jdbsee query -a sales_db "SELECT COUNT(*) AS total FROM orders" \
  | jq -e '.success == true'
```

For a batch execute result saved to a file:

```bash
jq -e '.summary.failure_count == 0' migration-result.json
```

These checks validate the JDBSee envelope; still inspect returned values and
affected row counts against the user's intent.

## MCP server

Start with a configured default alias and explicit resource limits. The server
binds to loopback and prints a generated bearer token:

```bash
jdbsee mcp start \
  --alias sales_db \
  --port 8080 \
  --timeout 300 \
  --max-results 10000
```

```
MCP Server started on 127.0.0.1:8080
Auth token (自动生成): <token>
```

Call a tool with the token:

```bash
curl -s -X POST localhost:8080/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"describe_table","arguments":{"table_name":"orders"}}}'
```

The process remains in the foreground until interrupted. Inspect each tool
response's `success` field, and check `metadata.truncated` on `execute_query`
results before treating the rows as complete.

Pass `--auth-token` to pin a known token. Only add `--bind 0.0.0.0` when the user
explicitly wants network access; it exposes the database to every host that can
reach the port.
