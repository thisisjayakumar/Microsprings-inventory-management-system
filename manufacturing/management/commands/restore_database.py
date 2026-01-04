"""
Django management command to restore database from backup
Usage: python manage.py restore_database <backup_file_path>
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import connection
import os
import subprocess
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Restore database from a backup file'

    def add_arguments(self, parser):
        parser.add_argument(
            'backup_file',
            type=str,
            help='Path to backup file (.sql or .json)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt',
        )

    def handle(self, *args, **options):
        backup_file = Path(options['backup_file'])
        force = options.get('force', False)
        
        if not backup_file.exists():
            raise CommandError(f'Backup file not found: {backup_file}')
        
        # Confirmation
        if not force:
            self.stdout.write(
                self.style.WARNING(
                    '\n' + '='*70 + '\n'
                    'WARNING: This will REPLACE ALL DATA in the database!\n'
                    'Current data will be lost!\n'
                    '='*70 + '\n'
                )
            )
            confirm = input('Type "RESTORE DATABASE" to confirm: ')
            if confirm != 'RESTORE DATABASE':
                self.stdout.write(self.style.ERROR('Restore cancelled.'))
                return
        
        try:
            self.stdout.write(f'Starting database restore...')
            self.stdout.write(f'Backup file: {backup_file}')
            
            # Determine format from file extension
            if backup_file.suffix == '.sql':
                # SQL restore
                if connection.vendor == 'mysql':
                    db_config = settings.DATABASES['default']
                    cmd = [
                        'mysql',
                        f'--user={db_config["USER"]}',
                        f'--host={db_config["HOST"]}',
                        f'--port={db_config["PORT"]}',
                        db_config['NAME']
                    ]
                    
                    if db_config.get('PASSWORD'):
                        cmd.insert(1, f'--password={db_config["PASSWORD"]}')
                    
                    with open(backup_file, 'r') as f:
                        result = subprocess.run(
                            cmd,
                            stdin=f,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                    
                    if result.returncode != 0:
                        raise CommandError(f'MySQL restore failed: {result.stderr}')
                        
                elif connection.vendor == 'postgresql':
                    db_config = settings.DATABASES['default']
                    env = os.environ.copy()
                    if db_config.get('PASSWORD'):
                        env['PGPASSWORD'] = db_config['PASSWORD']
                    
                    cmd = [
                        'psql',
                        f'--host={db_config["HOST"]}',
                        f'--port={db_config["PORT"]}',
                        f'--username={db_config["USER"]}',
                        '--dbname={}'.format(db_config['NAME']),
                        '--file={}'.format(str(backup_file))
                    ]
                    
                    result = subprocess.run(
                        cmd,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env
                    )
                    
                    if result.returncode != 0:
                        raise CommandError(f'PostgreSQL restore failed: {result.stderr}')
                        
                else:
                    # SQLite - just copy the file
                    db_path = settings.DATABASES['default']['NAME']
                    import shutil
                    shutil.copy2(backup_file, db_path)
                    
            elif backup_file.suffix == '.json':
                # JSON fixtures restore
                from django.core.management import call_command
                
                call_command('loaddata', str(backup_file), verbosity=1)
                
            else:
                raise CommandError(f'Unsupported backup format: {backup_file.suffix}')
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ Database restore completed successfully!'
                )
            )
            
        except FileNotFoundError as e:
            raise CommandError(
                f'Database restore tool not found. Please install:\n'
                f'  MySQL: mysql\n'
                f'  PostgreSQL: psql\n'
                f'Error: {str(e)}'
            )
        except Exception as e:
            logger.error(f'Restore failed: {str(e)}', exc_info=True)
            raise CommandError(f'Restore failed: {str(e)}')

