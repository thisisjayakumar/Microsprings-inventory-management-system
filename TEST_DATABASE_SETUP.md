# Isolated MySQL Test Database Setup

This project uses a custom test runner that creates an isolated MySQL database for each test run, ensuring complete isolation and preventing conflicts with your development database.

## Features

✅ **Automatic Database Creation**: Creates a new database with random suffix for each test run  
✅ **Complete Isolation**: Tests never touch your development database  
✅ **Automatic Cleanup**: Database is automatically dropped after tests complete  
✅ **Random Naming**: Database names include timestamp and UUID for uniqueness  
✅ **Parallel Test Safe**: Multiple test runs can execute simultaneously without conflicts  

## How It Works

The `IsolatedMySQLTestRunner` custom test runner:

1. **Before Tests**: Creates a new MySQL database with name format: `test_<original_db_name>_<timestamp>_<random_uuid>`
   - Example: `test_msp_erp_db_12345_a1b2c3d4`

2. **During Tests**: Runs all migrations to create tables in the isolated database

3. **After Tests**: Automatically drops the test database

## Usage

### Running Tests

Simply run tests as you normally would:

```bash
# Run all tests
python manage.py test

# Run specific test file
python manage.py test manufacturing.tests.test_batch_process_flow

# Run specific test case
python manage.py test manufacturing.tests.test_batch_process_flow.BatchProcessFlowTestCase

# Run specific test method
python manage.py test manufacturing.tests.test_batch_process_flow.BatchProcessFlowTestCase.test_complete_batch_process_flow
```

### Keep Database for Debugging

If you want to keep the test database after tests complete (for debugging), you can temporarily modify the test runner or use Django's `--keepdb` flag (though this won't work with random names).

For debugging, you can:
1. Add a breakpoint in `teardown_databases` method
2. Comment out the database drop code
3. Check the logs for the database name

### Viewing Test Database Name

The test runner logs the database name when creating it:

```
🔧 Creating isolated test database: test_msp_erp_db_12345_a1b2c3d4
✅ Test database 'test_msp_erp_db_12345_a1b2c3d4' created successfully
```

## Configuration

### Using Custom Test Database Name

If you want to use a fixed test database name instead of random, set in `.env`:

```env
TEST_DATABASE_NAME=test_msp_erp_db
```

This will override the random naming and use the specified name.

### Database Connection

The test runner uses the same MySQL connection settings from your `.env` file:
- `DATABASE_HOST`
- `DATABASE_PORT`
- `DATABASE_USER`
- `DATABASE_PASSWORD`

**Important**: The MySQL user must have `CREATE DATABASE` and `DROP DATABASE` privileges.

## Manual Database Cleanup

If tests are interrupted or fail, you may need to manually clean up test databases:

```sql
-- List all test databases
SHOW DATABASES LIKE 'test_%';

-- Drop a specific test database
DROP DATABASE IF EXISTS `test_msp_erp_db_12345_a1b2c3d4`;

-- Drop all test databases (use with caution!)
-- This will drop ALL databases starting with 'test_'
```

Or use a cleanup script:

```bash
python manage.py shell -c "
from django.db import connection
import pymysql
db_config = connection.settings_dict
conn = pymysql.connect(
    host=db_config['HOST'],
    port=db_config['PORT'],
    user=db_config['USER'],
    password=db_config['PASSWORD']
)
cursor = conn.cursor()
cursor.execute(\"SHOW DATABASES LIKE 'test_%'\")
dbs = cursor.fetchall()
for db in dbs:
    print(f'Dropping {db[0]}')
    cursor.execute(f'DROP DATABASE IF EXISTS `{db[0]}`')
conn.commit()
cursor.close()
conn.close()
"
```

## Troubleshooting

### Permission Errors

If you get permission errors when creating/dropping databases:

```sql
-- Grant necessary privileges to your MySQL user
GRANT CREATE, DROP ON *.* TO 'your_user'@'localhost';
FLUSH PRIVILEGES;
```

### Database Already Exists

If you see "database already exists" errors:
- This shouldn't happen with random names, but if it does, manually drop the database
- Check for leftover test databases: `SHOW DATABASES LIKE 'test_%';`

### Connection Errors

Ensure your MySQL server is running and accessible:
```bash
mysql -u your_user -p -h localhost -P 3306
```

## Best Practices

1. **Never run tests against production database** - The isolated test runner prevents this
2. **Use transactions when possible** - Django's `TestCase` uses transactions for speed
3. **Use `TransactionTestCase` sparingly** - Only when you need to test transaction behavior
4. **Keep tests independent** - Each test should be able to run in isolation
5. **Clean up test data** - Use `setUp` and `tearDown` methods properly

## Comparison with Default Django Test Runner

| Feature | Default Django | IsolatedMySQLTestRunner |
|---------|---------------|-------------------------|
| Database Isolation | Uses `test_` prefix | Random suffix ensures uniqueness |
| Parallel Test Safety | Can conflict | Safe for parallel runs |
| Cleanup | Automatic | Automatic with better error handling |
| Debugging | Can use `--keepdb` | Check logs for database name |
| Database Name | `test_<db_name>` | `test_<db_name>_<timestamp>_<uuid>` |

## Example Test Output

```
🔧 Creating isolated test database: test_msp_erp_db_12345_a1b2c3d4
✅ Test database 'test_msp_erp_db_12345_a1b2c3d4' created successfully
Creating test database for alias 'default'...
Operations to perform:
  Apply all migrations: ...
Running migrations:
  ...
test_complete_batch_process_flow ... ok
test_subsequent_supervisor_cannot_start_before_previous_completes ... ok
test_quantity_flow_with_sequence_order ... ok

----------------------------------------------------------------------
Ran 3 tests in 2.345s

OK
🧹 Cleaning up test database: test_msp_erp_db_12345_a1b2c3d4
✅ Test database 'test_msp_erp_db_12345_a1b2c3d4' dropped successfully
```

