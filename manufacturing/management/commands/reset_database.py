"""
Django management command to reset/truncate the entire database
WARNING: This will delete ALL data from the database!

Usage: python manage.py reset_database [--load-fixtures]
"""
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import connection, transaction
from django.conf import settings
from django.core.management.sql import sql_flush
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reset/truncate the entire database (WARNING: Deletes ALL data!)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--load-fixtures',
            action='store_true',
            help='Load initial fixtures after reset (if available)',
        )
        parser.add_argument(
            '--skip-migrations',
            action='store_true',
            help='Skip running migrations after reset (faster but may cause issues)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt (use with caution!)',
        )

    def handle(self, *args, **options):
        # Safety check: Only allow in DEBUG mode or if explicitly forced
        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'ERROR: Database reset is only allowed in DEBUG mode!\n'
                'If you really want to reset in production, use --force flag.\n'
                'THIS WILL DELETE ALL DATA!'
            )

        # Confirmation prompt
        if not options['force']:
            self.stdout.write(
                self.style.WARNING(
                    '\n' + '='*70 + '\n'
                    'WARNING: This will DELETE ALL DATA from the database!\n'
                    'This action cannot be undone!\n'
                    '='*70 + '\n'
                )
            )
            confirm = input('Type "RESET DATABASE" to confirm: ')
            if confirm != 'RESET DATABASE':
                self.stdout.write(self.style.ERROR('Reset cancelled.'))
                return

        try:
            with transaction.atomic():
                self.stdout.write(self.style.WARNING('Starting database reset...'))
                
                # Get all table names
                with connection.cursor() as cursor:
                    # Disable foreign key checks (MySQL)
                    if connection.vendor == 'mysql':
                        cursor.execute('SET FOREIGN_KEY_CHECKS = 0')
                    
                    # Get all table names
                    if connection.vendor == 'mysql':
                        cursor.execute("""
                            SELECT table_name 
                            FROM information_schema.tables 
                            WHERE table_schema = DATABASE()
                            AND table_type = 'BASE TABLE'
                        """)
                    elif connection.vendor == 'postgresql':
                        cursor.execute("""
                            SELECT tablename 
                            FROM pg_tables 
                            WHERE schemaname = 'public'
                        """)
                    else:
                        # SQLite
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    
                    tables = [row[0] for row in cursor.fetchall()]
                    
                    # Exclude Django system tables that should be preserved
                    exclude_tables = {
                        'django_migrations',  # Keep migration history
                        'django_content_type',  # Keep content types (optional)
                        'django_session',  # Keep sessions (optional)
                    }
                    
                    tables_to_truncate = [t for t in tables if t not in exclude_tables]
                    
                    self.stdout.write(f'Found {len(tables_to_truncate)} tables to truncate...')
                    
                    # Truncate each table
                    for table in tables_to_truncate:
                        try:
                            if connection.vendor == 'mysql':
                                cursor.execute(f'TRUNCATE TABLE `{table}`')
                            elif connection.vendor == 'postgresql':
                                cursor.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
                            else:
                                # SQLite
                                cursor.execute(f'DELETE FROM "{table}"')
                                cursor.execute(f'DELETE FROM sqlite_sequence WHERE name="{table}"')
                            
                            self.stdout.write(f'  ✓ Truncated: {table}')
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(f'  ⚠ Could not truncate {table}: {str(e)}')
                            )
                    
                    # Re-enable foreign key checks (MySQL)
                    if connection.vendor == 'mysql':
                        cursor.execute('SET FOREIGN_KEY_CHECKS = 1')
                
                self.stdout.write(self.style.SUCCESS('\n✓ Database reset completed!'))
                
                # Run migrations if not skipped
                if not options['skip_migrations']:
                    self.stdout.write('\nRunning migrations...')
                    call_command('migrate', verbosity=0)
                    self.stdout.write(self.style.SUCCESS('✓ Migrations completed'))
                
                # Load fixtures if requested
                if options['load_fixtures']:
                    self.stdout.write('\nLoading fixtures...')
                    try:
                        call_command('loaddata', 'initial_data', verbosity=0, ignore_errors=True)
                        self.stdout.write(self.style.SUCCESS('✓ Fixtures loaded'))
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(f'⚠ Could not load fixtures: {str(e)}')
                        )
                
                self.stdout.write(
                    self.style.SUCCESS(
                        '\n' + '='*70 + '\n'
                        'Database reset completed successfully!\n'
                        '='*70 + '\n'
                    )
                )
                
        except Exception as e:
            logger.error(f'Database reset failed: {str(e)}', exc_info=True)
            raise CommandError(f'Database reset failed: {str(e)}')

