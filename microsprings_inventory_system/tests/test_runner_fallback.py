from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from microsprings_inventory_system.test_runner import IsolatedMySQLTestRunner


class RunnerFallbackTests(SimpleTestCase):
    @override_settings(DATABASES={
        'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
    })
    def test_sqlite_engine_delegates_to_parent_setup(self):
        runner = IsolatedMySQLTestRunner(verbosity=0, interactive=False)
        with patch(
            'django.test.runner.DiscoverRunner.setup_databases',
            return_value='sentinel',
        ) as parent:
            result = runner.setup_databases()
        self.assertEqual(result, 'sentinel')
        parent.assert_called_once()
        self.assertIsNone(runner.test_database_name)
