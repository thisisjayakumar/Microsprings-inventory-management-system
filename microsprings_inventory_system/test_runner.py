"""
Custom Test Runner for Isolated MySQL Test Databases
Creates a new database with random suffix for each test run and cleans up afterward.
"""
import logging
import os
import time
import uuid

from django.conf import settings
from django.db import connection
from django.test.runner import DiscoverRunner

logger = logging.getLogger(__name__)


class IsolatedMySQLTestRunner(DiscoverRunner):
    """
    Custom test runner that creates an isolated MySQL database for each test run.
    
    Features:
    - Creates a new database with random suffix (e.g., test_msp_erp_db_a1b2c3d4)
    - Automatically creates all tables using migrations
    - Cleans up database after tests complete
    - Prevents conflicts with other test runs
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_database_name = None
        self.original_database_name = None
    
    def setup_databases(self, **kwargs):
        """
        Create the test database with a random suffix.
        """
        if 'mysql' not in settings.DATABASES['default']['ENGINE']:
            return super().setup_databases(**kwargs)

        # Get the original database configuration
        db_config = settings.DATABASES['default']
        self.original_database_name = db_config['NAME']
        
        # Generate a unique database name with random suffix
        # Format: test_<original_name>_<timestamp>_<random_uuid>
        # This ensures uniqueness even with parallel test runs
        random_suffix = str(uuid.uuid4()).replace('-', '')[:8]  # 8 character random suffix
        timestamp = int(time.time()) % 100000  # Last 5 digits of timestamp
        process_id = os.getpid() % 10000  # Process ID for additional uniqueness
        self.test_database_name = f"test_{self.original_database_name}_{timestamp}_{process_id}_{random_suffix}"
        
        # MySQL database name length limit is 64 characters
        if len(self.test_database_name) > 64:
            # Truncate if too long, but keep it unique
            max_base_length = 64 - len(f"_{timestamp}_{process_id}_{random_suffix}")
            base_name = self.original_database_name[:max_base_length]
            self.test_database_name = f"test_{base_name}_{timestamp}_{process_id}_{random_suffix}"
        
        logger.info(f"🔧 Creating isolated test database: {self.test_database_name}")
        
        # Update the database name in settings
        db_config['NAME'] = self.test_database_name
        
        # Create the test database
        try:
            # Connect to MySQL server (without specifying database)
            connection_params = {
                'host': db_config['HOST'],
                'port': db_config['PORT'],
                'user': db_config['USER'],
                'password': db_config['PASSWORD'],
            }
            
            # Use MySQL connection to create database
            import pymysql
            mysql_conn = pymysql.connect(**connection_params)
            cursor = mysql_conn.cursor()
            
            # Create database with UTF8MB4 charset
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.test_database_name}` "
                          f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            mysql_conn.commit()
            cursor.close()
            mysql_conn.close()
            
            logger.info(f"✅ Test database '{self.test_database_name}' created successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to create test database: {e}")
            raise
        
        # Call parent method to set up the database (creates tables, etc.)
        return super().setup_databases(**kwargs)
    
    def teardown_databases(self, old_config, **kwargs):
        """
        Drop the test database after tests complete.
        """
        if 'mysql' not in settings.DATABASES['default']['ENGINE']:
            return super().teardown_databases(old_config, **kwargs)

        if self.test_database_name:
            logger.info(f"🧹 Cleaning up test database: {self.test_database_name}")
            
            try:
                # Close all connections first
                connection.close()
                
                # Connect to MySQL server to drop database
                db_config = settings.DATABASES['default']
                connection_params = {
                    'host': db_config['HOST'],
                    'port': db_config['PORT'],
                    'user': db_config['USER'],
                    'password': db_config['PASSWORD'],
                }
                
                import pymysql
                mysql_conn = pymysql.connect(**connection_params)
                cursor = mysql_conn.cursor()
                
                # Drop the test database
                cursor.execute(f"DROP DATABASE IF EXISTS `{self.test_database_name}`")
                mysql_conn.commit()
                cursor.close()
                mysql_conn.close()
                
                logger.info(f"✅ Test database '{self.test_database_name}' dropped successfully")
                
            except Exception as e:
                logger.warning(f"⚠️  Failed to drop test database '{self.test_database_name}': {e}")
                logger.warning("   You may need to manually drop it: "
                              f"DROP DATABASE IF EXISTS `{self.test_database_name}`;")
        
        # Restore original database name in settings
        if self.original_database_name:
            settings.DATABASES['default']['NAME'] = self.original_database_name
        
        # Update old_config to prevent parent from trying to drop non-existent database
        # The parent method uses old_config to determine which database to drop
        if old_config and 'default' in old_config:
            old_config['default']['NAME'] = self.original_database_name or old_config['default']['NAME']
        
        # Call parent method - it will try to drop, but old_config now points to original DB
        # which should be safe (it won't actually drop the original DB)
        try:
            super().teardown_databases(old_config, **kwargs)
        except Exception as e:
            # Ignore errors from parent teardown since we've already cleaned up
            logger.debug(f"Parent teardown completed (may have warnings): {e}")

