-- HSQLDB TEXT TABLE does not support ALTER TABLE ADD COLUMN directly.
-- Must disconnect source, drop, recreate with new column, and reconnect source.

-- Step 1: Disconnect all text table sources
SET TABLE jdbs_aliases SOURCE OFF;
SET TABLE jdbs_jars SOURCE OFF;
SET TABLE jdbs_deps SOURCE OFF;
SET TABLE jdbs_drivers SOURCE OFF;

-- Step 2: Drop tables in reverse dependency order
DROP TABLE IF EXISTS jdbs_aliases;
DROP TABLE IF EXISTS jdbs_jars;
DROP TABLE IF EXISTS jdbs_deps;
DROP TABLE IF EXISTS jdbs_drivers;

-- Step 3: Recreate jdbs_drivers
CREATE TEXT TABLE IF NOT EXISTS jdbs_drivers (
  id BIGINT IDENTITY PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  clazz VARCHAR(500),
  clazz_expr VARCHAR(500),
  CONSTRAINT uk_drivers_name UNIQUE (name)
);
SET TABLE jdbs_drivers SOURCE 'drivers.csv;encoding=UTF-8';

-- Step 4: Recreate jdbs_jars
CREATE TEXT TABLE IF NOT EXISTS jdbs_jars (
  id BIGINT IDENTITY PRIMARY KEY,
  driver_id BIGINT NOT NULL,
  path VARCHAR(1000) NOT NULL
);
ALTER TABLE jdbs_jars
ADD CONSTRAINT fk_jdbs_jars_driver
FOREIGN KEY (driver_id)
REFERENCES jdbs_drivers (id)
ON DELETE CASCADE;
SET TABLE jdbs_jars SOURCE 'jars.csv;encoding=UTF-8';

-- Step 5: Recreate jdbs_deps
CREATE TEXT TABLE IF NOT EXISTS jdbs_deps (
  id BIGINT IDENTITY PRIMARY KEY,
  driver_id BIGINT NOT NULL,
  gav VARCHAR(200) NOT NULL
);
ALTER TABLE jdbs_deps
ADD CONSTRAINT fk_jdbs_deps_driver
FOREIGN KEY (driver_id)
REFERENCES jdbs_drivers (id)
ON DELETE CASCADE;
SET TABLE jdbs_deps SOURCE 'deps.csv;encoding=UTF-8';

-- Step 6: Recreate jdbs_aliases with new remark column
CREATE TEXT TABLE IF NOT EXISTS jdbs_aliases (
  id BIGINT IDENTITY PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  url VARCHAR(2000) NOT NULL,
  username VARCHAR(500),
  password VARCHAR(2000),
  remark VARCHAR(200),
  driver_id BIGINT NOT NULL,
  CONSTRAINT uk_aliases_name UNIQUE (name)
);
ALTER TABLE jdbs_aliases
ADD CONSTRAINT fk_jdbs_aliases_driver
FOREIGN KEY (driver_id)
REFERENCES jdbs_drivers (id)
ON DELETE CASCADE;
SET TABLE jdbs_aliases SOURCE 'aliases.csv;encoding=UTF-8';
