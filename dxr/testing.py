from commands import getstatusoutput
import json
from os import chdir, mkdir
import os.path
from os.path import dirname
from shutil import rmtree
import sys
from tempfile import mkdtemp
import unittest
from urllib2 import quote

from nose.tools import eq_
from pyelasticsearch import ElasticSearch

try:
    from nose.tools import assert_in
except ImportError:
    from nose.tools import ok_
    def assert_in(item, container, msg=None):
        ok_(item in container, msg=msg or '%r not in %r' % (item, container))

from dxr.app import make_app
from dxr.build import build_instance


class CommandFailure(Exception):
    """A command exited with a non-zero status code."""

    def __init__(self, command, status, output):
        self.command, self.status, self.output = command, status, output

    def __str__(self):
        return "'%s' exited with status %s. Output:\n%s" % (self.command,
                                                            self.status,
                                                            self.output)


def run(command):
    """Run a shell command, and return its stdout. On failure, raise
    `CommandFailure`.

    """
    status, output = getstatusoutput(command)
    if status:
        raise CommandFailure(command, status, output)
    return output


class TestCase(unittest.TestCase):
    """Abstract container for general convenience functions for DXR tests"""

    def client(self):
        # TODO: DRY between here and the config file with 'target'.
        app = make_app(os.path.join(self._config_dir_path, 'target'))

        app.config['TESTING'] = True  # Disable error trapping during requests.
        return app.test_client()

    def found_files(self, query, is_case_sensitive=True):
        """Return the set of paths of files found by a search query."""
        return set(result['path'] for result in
                   self.search_results(query,
                                       is_case_sensitive=is_case_sensitive))

    def found_files_eq(self, query, filenames, is_case_sensitive=True):
        """Assert that executing the search ``query`` finds the paths
        ``filenames``."""
        eq_(self.found_files(query,
                             is_case_sensitive=is_case_sensitive),
            set(filenames))

    def found_line_eq(self, query, content, line, is_case_sensitive=True):
        """Assert that a query returns a single file and single matching line
        and that its line number and content are as expected, modulo leading
        and trailing whitespace.

        This is a convenience function for searches that return only one
        matching file and only one line within it so you don't have to do a
        zillion dereferences in your test.

        """
        self.found_lines_eq(query,
                            [(content, line)],
                            is_case_sensitive=is_case_sensitive)

    def found_lines_eq(self, query, success_lines, is_case_sensitive=True):
        """Assert that a query returns a single file and that the highlighted
        lines are as expected, modulo leading and trailing whitespace."""
        results = self.search_results(query,
                                      is_case_sensitive=is_case_sensitive)
        num_results = len(results)
        eq_(num_results, 1, msg='Query passed to found_lines_eq() returned '
                                 '%s files, not one.' % num_results)
        lines = results[0]['lines']
        eq_([(line['line'].strip(), line['line_number']) for line in lines],
            success_lines)

    def found_nothing(self, query, is_case_sensitive=True):
        """Assert that a query returns no hits."""
        results = self.search_results(query,
                                      is_case_sensitive=is_case_sensitive)
        eq_(results, [])

    def search_response(self, query, is_case_sensitive=True):
        """Return the raw response of a JSON search query."""
        return self.client().get(
            '/code/search?q=%s&redirect=false&case=%s' %
                    (quote(query), 'true' if is_case_sensitive else 'false'),
            headers={'Accept': 'application/json'})

    def direct_result_eq(self, query, path, line_number, is_case_sensitive=True):
        """Assert that a direct result exists and takes the user to the given
        path at the given line number."""
        response = self.client().get(
            '/code/search?q=%s&redirect=true&case=%s' %
                    (quote(query), 'true' if is_case_sensitive else 'false'))
        eq_(response.status_code, 302)
        location = response.headers['Location']
        # Location is something like
        # http://localhost/code/source/main.cpp?from=main.cpp:6&case=true#6.
        eq_(location[:location.index('?')],
            'http://localhost/code/source/' + path)
        eq_(int(location[location.index('#') + 1:]), line_number)

    def search_results(self, query, is_case_sensitive=True):
        """Return the raw results of a JSON search query.

        Example::

          [
            {
              "path": "main.c",
              "lines": [
                {
                  "line_number": 7,
                  "line": "int <b>main</b>(int argc, char* argv[]) {"
                }
              ],
              "icon": "mimetypes/c"
            }
          ]

        """
        response = self.search_response(query,
                                        is_case_sensitive=is_case_sensitive)
        return json.loads(response.data)['results']

    @classmethod
    def _es(cls):
        return ElasticSearch('http://127.0.0.1:9200/')

    @classmethod
    def _delete_es_indices(cls):
        """Delete anything that is named like a DXR test index.

        Yes, this is scary as hell but very expedient. Won't work if
        ES's action.destructive_requires_name is set to true.

        """
        # When you delete an index, any alias to it goes with it.
        cls._es().delete_index('dxr_test_*')


class DxrInstanceTestCase(TestCase):
    """Test case which builds an actual DXR instance that lives on the
    filesystem and then runs its tests

    This is suitable for complex tests with many files where the
    filesystem is the least confusing place to express them.

    """
    @classmethod
    def setup_class(cls):
        """Build the instance."""
        # nose does some amazing magic that makes this work even if there are
        # multiple test modules with the same name:
        cls._config_dir_path = dirname(sys.modules[cls.__module__].__file__)
        chdir(cls._config_dir_path)
        run('make')
        cls._es().refresh()

    @classmethod
    def teardown_class(cls):
        chdir(cls._config_dir_path)
        cls._delete_es_indices()
        run('make clean')


class SingleFileTestCase(TestCase):
    """Container for tests that need only a single source file

    You can express the source as a string rather than creating a
    whole bunch of files in the filesystem. I'll slam it down into a
    temporary DXR instance and then kick off the usual build process,
    deleting the instance afterward.

    """
    # Set this to False in a subclass to keep the generated instance around and
    # print its path so you can examine it:
    should_delete_instance = True

    @classmethod
    def setup_class(cls):
        """Create a temporary DXR instance on the filesystem, and build it."""
        cls._config_dir_path = mkdtemp()
        code_path = os.path.join(cls._config_dir_path, 'code')
        mkdir(code_path)
        _make_file(code_path, 'main.cpp', cls.source)
        # $CXX gets injected by the clang DXR plugin:
        _make_file(cls._config_dir_path, 'dxr.config', """
[DXR]
enabled_plugins = pygmentize clang
temp_folder = {config_dir_path}/temp
target_folder = {config_dir_path}/target
nb_jobs = 4
es_index = dxr_test_{{format}}_{{tree}}_{{unique}}
es_alias = dxr_test_{{format}}_{{tree}}

[code]
source_folder = {config_dir_path}/code
object_folder = {config_dir_path}/code
build_command = $CXX -o main main.cpp
""".format(config_dir_path=cls._config_dir_path))

        chdir(cls._config_dir_path)
        build_instance(os.path.join(cls._config_dir_path, 'dxr.config'))
        cls._es().refresh()

    @classmethod
    def teardown_class(cls):
        if cls.should_delete_instance:
            cls._delete_es_indices()
            rmtree(cls._config_dir_path)
        else:
            print 'Not deleting instance in %s.' % cls._config_dir_path

    def _source_for_query(self, s):
        return (s.replace('<b>', '')
                 .replace('</b>', '')
                 .replace('&lt;', '<')
                 .replace('&gt;', '>')
                 .replace('&quot;', '"')
                 .replace('&amp;', '&'))

    def found_line_eq(self, query, content, line=None, is_case_sensitive=True):
        """A specialization of ``found_line_eq`` that computes the line number
        if not given

        :arg line: The expected line number. If omitted, we'll compute it,
            given a match for ``content`` (minus ``<b>`` tags) in
            ``self.source``.

        """
        if not line:
            line = self.source.count( '\n', 0, self.source.index(
                self._source_for_query(content))) + 1
        super(SingleFileTestCase, self).found_line_eq(
                query, content, line, is_case_sensitive=is_case_sensitive)

    def direct_result_eq(self, query, line_number, is_case_sensitive=True):
        """Assume the filename "main.cpp"."""
        return super(SingleFileTestCase, self).direct_result_eq(query, 'main.cpp', line_number, is_case_sensitive=is_case_sensitive)


def _make_file(path, filename, contents):
    """Make file ``filename`` within ``path``, full of unicode ``contents``."""
    with open(os.path.join(path, filename), 'w') as file:
        file.write(contents.encode('utf-8'))


# Tests that don't otherwise need a main() can append this one just to get
# their code to compile:
MINIMAL_MAIN = """
    int main(int argc, char* argv[]) {
        return 0;
    }
    """
