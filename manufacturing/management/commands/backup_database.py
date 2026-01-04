"""
Django management command to backup the database
Usage: python manage.py backup_database [--output-dir]
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import connection
import os
import subprocess
import json
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backup the entire database to a file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            type=str,
            default=None,
            help='Directory to save backup file (default: BASE_DIR/backups)',
        )
        parser.add_argument(
            '--format',
            type=str,
            choices=['sql', 'json'],
            default='sql',
            help='Backup format: sql (database dump) or json (Django fixtures)',
        )

    def handle(self, *args, **options):
        output_dir = options.get('output_dir')
        backup_format = options.get('format', 'sql')
        
        # Determine output directory
        if not output_dir:
            output_dir = Path(settings.BASE_DIR) / 'backups'
        else:
            output_dir = Path(output_dir)
        
        # Create backup directory if it doesn't exist
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        db_name = settings.DATABASES['default']['NAME']
        
        if backup_format == 'sql':
            backup_filename = f'{db_name}_backup_{timestamp}.sql'
        else:
            backup_filename = f'{db_name}_backup_{timestamp}.json'
        
        backup_path = output_dir / backup_filename
        
        try:
            self.stdout.write(f'Starting database backup...')
            self.stdout.write(f'Database: {db_name}')
            self.stdout.write(f'Format: {backup_format}')
            self.stdout.write(f'Output: {backup_path}')
            
            if backup_format == 'sql':
                # SQL dump backup
                if connection.vendor == 'mysql':
                    db_config = settings.DATABASES['default']
                    cmd = [
                        'mysqldump',
                        f'--user={db_config["USER"]}',
                        f'--host={db_config["HOST"]}',
                        f'--port={db_config["PORT"]}',
                        '--single-transaction',
                        '--routines',
                        '--triggers',
                        db_config['NAME']
                    ]
                    
                    # Add password if provided
                    if db_config.get('PASSWORD'):
                        cmd.insert(1, f'--password={db_config["PASSWORD"]}')
                    
                    with open(backup_path, 'w') as f:
                        result = subprocess.run(
                            cmd,
                            stdout=f,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                    
                    if result.returncode != 0:
                        raise CommandError(f'MySQL dump failed: {result.stderr}')
                        
                elif connection.vendor == 'postgresql':
                    db_config = settings.DATABASES['default']
                    env = os.environ.copy()
                    if db_config.get('PASSWORD'):
                        env['PGPASSWORD'] = db_config['PASSWORD']
                    
                    cmd = [
                        'pg_dump',
                        f'--host={db_config["HOST"]}',
                        f'--port={db_config["PORT"]}',
                        f'--username={db_config["USER"]}',
                        '--format=plain',
                        '--no-owner',
                        '--no-acl',
                        db_config['NAME']
                    ]
                    
                    with open(backup_path, 'w') as f:
                        result = subprocess.run(
                            cmd,
                            stdout=f,
                            stderr=subprocess.PIPE,
                            text=True,
                            env=env
                        )
                    
                    if result.returncode != 0:
                        raise CommandError(f'PostgreSQL dump failed: {result.stderr}')
                        
                else:
                    # SQLite - just copy the file
                    db_path = settings.DATABASES['default']['NAME']
                    import shutil
                    shutil.copy2(db_path, backup_path)
                    
            else:
                # JSON fixtures backup
                from django.core.management import call_command
                from io import StringIO
                
                output = StringIO()
                call_command('dumpdata', '--natural-foreign', '--natural-primary', stdout=output)
                output.seek(0)
                
                with open(backup_path, 'w') as f:
                    f.write(output.read())
            
            # Get file size
            file_size = backup_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            # Create metadata file
            metadata = {
                'backup_filename': backup_filename,
                'backup_path': str(backup_path),
                'database_name': db_name,
                'format': backup_format,
                'created_at': datetime.now().isoformat(),
                'file_size_bytes': file_size,
                'file_size_mb': round(file_size_mb, 2),
                'status': 'completed'
            }
            
            metadata_path = output_dir / f'{backup_filename}.meta.json'
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ Backup completed successfully!\n'
                    f'  File: {backup_filename}\n'
                    f'  Size: {file_size_mb:.2f} MB\n'
                    f'  Location: {backup_path}'
                )
            )
            
            # Don't return metadata - it's already saved to .meta.json file
            # Returning a dict causes issues with call_command stdout handling
            return None
            
        except FileNotFoundError as e:
            raise CommandError(
                f'Database dump tool not found. Please install:\n'
                f'  MySQL: mysqldump\n'
                f'  PostgreSQL: pg_dump\n'
                f'Error: {str(e)}'
            )
        except Exception as e:
            logger.error(f'Backup failed: {str(e)}', exc_info=True)
            # Clean up failed backup file
            if backup_path.exists():
                backup_path.unlink()
            raise CommandError(f'Backup failed: {str(e)}')

