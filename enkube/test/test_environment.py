# Copyright 2018 SpiderOak, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import unittest
from unittest.mock import patch, Mock

from enkube.environment import Environment


class TestEnvironment(unittest.TestCase):
    def test_envdir(self):
        envdir = os.path.join(os.getcwd(), 'envs', 'envname')
        with patch('os.path.isdir') as isdir:
            isdir.return_value = True
            env = Environment('envname')
        isdir.assert_called_once_with(envdir)
        self.assertEqual(env.envdir, envdir)

    def test_envdir_not_found(self):
        envdir = os.path.join(os.getcwd(), 'envs', 'envname')
        with patch('os.path.isdir') as isdir:
            isdir.return_value = False
            env = Environment('envname')
        isdir.assert_called_once_with(envdir)
        self.assertIs(env.envdir, None)

    def test_envdir_with_search(self):
        envdir = os.path.join('foo', 'envs', 'envname')
        with patch('os.path.isdir') as isdir:
            isdir.return_value = True
            env = Environment('envname', search=('foo',))
        isdir.assert_called_once_with(envdir)
        self.assertEqual(env.envdir, envdir)

    def test_envdir_with_search_not_found(self):
        with patch('os.path.isdir') as isdir:
            isdir.return_value = False
            env = Environment('envname', search=('foo',))
        self.assertEqual(isdir.call_args_list, [
            ((os.path.join('foo', 'envs', 'envname'),),),
            ((os.path.join(os.getcwd(), 'envs', 'envname'),),),
        ])
        self.assertIs(env.envdir, None)

    def test_parents(self):
        envs = {
            'foo': ['bar\n', 'baz'],
            'bar': None,
            'baz': ['qux'],
            'qux': None,
        }
        envdirs = set(os.path.join('search', 'envs', e) for e in envs)
        files = {}
        for e in envs:
            parents = envs[e]
            if parents is None:
                continue
            p = os.path.join('search', 'envs', e, 'parent_envs')
            m = files[p] = Mock()
            m.__enter__ = Mock(return_value=m)
            m.__exit__ = Mock()
            m.__iter__ = Mock(return_value=iter(parents))

        def mock_open(name):
            try:
                return files[name]
            except KeyError:
                raise FileNotFoundError()

        with patch(
            'enkube.environment.open', side_effect=mock_open
        ) as o, patch('os.path.isdir', side_effect=envdirs.__contains__):
            env = Environment('foo', search=('search',))
        self.assertEqual([e.name for e in env.parents], ['bar', 'baz'])
        self.assertTrue(all(e.search == ['search'] for e in env.parents))
        baz = env.parents[1]
        self.assertEqual([e.name for e in baz.parents], ['qux'])
        self.assertTrue(all(e.search == ['search'] for e in baz.parents))


class TestSearchDirs(unittest.TestCase):
    def test_default(self):
        env = Environment()
        res = list(env.search_dirs())
        self.assertEqual(res, [os.getcwd()])

    def test_with_pre(self):
        env = Environment()
        res = list(env.search_dirs(pre=('foo', 'bar')))
        self.assertEqual(res, ['foo', 'bar', os.getcwd()])

    def test_with_search(self):
        env = Environment(search=('foo', 'bar'))
        res = list(env.search_dirs())
        self.assertEqual(res, ['foo', 'bar', os.getcwd()])

    def test_with_envdir(self):
        env = Environment()
        env.envdir = 'foo'
        res = list(env.search_dirs())
        self.assertEqual(res, ['foo', os.getcwd()])

    def test_with_parents(self):
        env = Environment()
        parent = Mock()
        parent.search_dirs.return_value = ['foo', 'bar']
        env.parents = [parent]
        res = list(env.search_dirs())
        self.assertEqual(res, ['foo', 'bar', os.getcwd()])

    def test_with_post(self):
        env = Environment()
        res = list(env.search_dirs(post=('foo', 'bar')))
        self.assertEqual(res, [os.getcwd(), 'foo', 'bar'])

    def test_with_all(self):
        env = Environment(search=('search',))
        env.envdir = 'envdir'
        parent = Mock()
        parent.search_dirs.return_value = ['parent']
        env.parents = [parent]
        res = list(env.search_dirs(pre=('pre',), post=('post',)))
        self.assertEqual(res, ['pre', 'search', 'envdir', 'parent', os.getcwd(), 'post'])


if __name__ == '__main__':
    unittest.main()
