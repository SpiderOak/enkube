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

import unittest
from unittest.mock import patch, MagicMock, sentinel, call

import curio

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api import cache


class TestCache(AsyncTestCase):
    def setUp(self):
        self.cond = MagicMock(return_value=True)
        self.handler = MagicMock(side_effect=dummy_coro)
        self.cache = cache.Cache()
        self.cache.log = MagicMock()
        self.cache.subscribe(self.cond, self.handler)

    async def test_set_new_key(self):
        await self.cache.set('foo', 'bar')
        self.assertEqual(dict(self.cache), {'foo': 'bar'})
        self.cond.assert_called_once_with('ADDED', None, 'bar')
        self.handler.assert_called_once_with(self.cache, 'ADDED', None, 'bar')

    async def test_set_existing_key(self):
        await self.cache.set('foo', 'bar')
        await self.cache.set('foo', 'baz')
        self.assertEqual(dict(self.cache), {'foo': 'baz'})
        self.assertEqual(self.cond.call_args_list, [
            call('ADDED', None, 'bar'),
            call('MODIFIED', 'bar', 'baz'),
        ])
        self.assertEqual(self.handler.call_args_list, [
            call(self.cache, 'ADDED', None, 'bar'),
            call(self.cache, 'MODIFIED', 'bar', 'baz'),
        ])

    async def test_set_ignores_duplicates(self):
        await self.cache.set('foo', 'bar')
        await self.cache.set('foo', 'bar')
        self.assertEqual(dict(self.cache), {'foo': 'bar'})
        self.assertEqual(self.cond.call_args_list, [
            call('ADDED', None, 'bar'),
        ])
        self.assertEqual(self.handler.call_args_list, [
            call(self.cache, 'ADDED', None, 'bar'),
        ])

    async def test_delete_existing_key(self):
        await self.cache.set('foo', 'bar')
        await self.cache.delete('foo')
        self.assertEqual(dict(self.cache), {})
        self.assertEqual(self.cond.call_args_list, [
            call('ADDED', None, 'bar'),
            call('DELETED', 'bar', None),
        ])
        self.assertEqual(self.handler.call_args_list, [
            call(self.cache, 'ADDED', None, 'bar'),
            call(self.cache, 'DELETED', 'bar', None),
        ])

    async def test_delete_nonexisting_key_raises_keyerror(self):
        with self.assertRaises(KeyError):
            await self.cache.delete('foo')
        self.cond.assert_not_called()
        self.handler.assert_not_called()

    async def test_notify_multiple(self):
        cond2 = MagicMock(return_value=True)
        handler2 = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(cond2, handler2)
        await self.cache.notify('ADDED', None, 'bar')
        self.cond.assert_called_once_with('ADDED', None, 'bar')
        cond2.assert_called_once_with('ADDED', None, 'bar')
        self.handler.assert_called_once_with(self.cache, 'ADDED', None, 'bar')
        handler2.assert_called_once_with(self.cache, 'ADDED', None, 'bar')

    async def test_notify_dead_ref(self):
        cond = MagicMock(return_value=True)
        async def func(*args, **kw):
            pass
        self.cache.subscribe(cond, func)
        self.assertEqual(len(self.cache.subscriptions), 2)
        del func
        await self.cache.notify('ADDED', None, 'bar')
        cond.assert_not_called()
        self.assertEqual(len(self.cache.subscriptions), 1)

    async def test_subscribe_strong(self):
        cond = MagicMock(return_value=True)
        async def func(*args, **kw):
            pass
        self.cache.subscribe(cond, func, weak=False)
        self.assertEqual(len(self.cache.subscriptions), 2)
        del func
        await self.cache.notify('ADDED', None, 'bar')
        cond.assert_called_once_with('ADDED', None, 'bar')
        self.assertEqual(len(self.cache.subscriptions), 2)

    async def test_subscribe_weak_method(self):
        cond = MagicMock(return_value=True)
        res = []
        class Foo:
            async def func(self, *args, **kw):
                res.append((args, kw))
        foo = Foo()
        self.cache.subscribe(cond, foo.func)
        await self.cache.notify('ADDED', None, 'bar')
        self.assertEqual(res, [((self.cache, 'ADDED', None, 'bar'), {})])

    async def test_subscribe_dead_method(self):
        cond = MagicMock(return_value=True)
        res = []
        class Foo:
            async def func(self, *args, **kw):
                res.append((args, kw))
        foo = Foo()
        self.cache.subscribe(cond, foo.func)
        del Foo.func
        await self.cache.notify('ADDED', None, 'bar')
        self.assertEqual(res, [])

    async def test_subscribe_strong_method(self):
        cond = MagicMock(return_value=True)
        res = []
        class Foo:
            async def func(self, *args, **kw):
                res.append((args, kw))
        foo = Foo()
        self.cache.subscribe(cond, foo.func, weak=False)
        del Foo.func
        await self.cache.notify('ADDED', None, 'bar')
        self.assertEqual(res, [((self.cache, 'ADDED', None, 'bar'), {})])

    async def test_cond_false(self):
        self.cond.return_value = False
        await self.cache.set('foo', 'bar')
        self.assertEqual(dict(self.cache), {'foo': 'bar'})
        self.cond.assert_called_once_with('ADDED', None, 'bar')
        self.handler.assert_not_called()

    def test_unsubscribe_strong(self):
        self.assertEqual(len(self.cache.subscriptions), 1)
        self.cache.unsubscribe(self.handler)
        self.assertEqual(len(self.cache.subscriptions), 0)

    async def test_unsubscribe_weak(self):
        cond = MagicMock(return_value=True)
        async def func(cache, event, old, new):
            pass
        self.cache.subscribe(cond, func, weak=True)
        self.assertEqual(len(self.cache.subscriptions), 2)
        self.cache.unsubscribe(func)
        self.assertEqual(len(self.cache.subscriptions), 1)

    async def test_subscription_exception_logs_traceback(self):
        self.handler.side_effect = RuntimeError
        await self.cache.notify('ADDED', None, 'bar')
        self.cache.log.exception.assert_called_once_with('unhandled error in subscription handler')

    async def test_subscription_cond_exception_logs_traceback(self):
        self.cond.side_effect = RuntimeError
        await self.cache.notify('ADDED', None, 'bar')
        self.cache.log.exception.assert_called_once_with('unhandled error in subscription handler')
        self.handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
