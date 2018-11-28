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
from unittest.mock import patch, MagicMock, sentinel

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api.types import *
from enkube.api import cache


class FakeWatcher:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return self.events.pop(0)
        except IndexError:
            raise StopAsyncIteration() from None


class FooKind(Kind):
    _namespaced = False
    apiVersion = 'v1'


class TestCache(AsyncTestCase):
    def setUp(self):
        self.events = []
        self.watcher = FakeWatcher(self.events)
        self.cache = cache.Cache(self.watcher)

    async def test_caches_objects_from_events(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        state = dict((o._selfLink(), o) for o in objs)
        self.events.extend(('ADDED', obj) for obj in objs)
        await self.cache.run()
        self.assertEqual(self.cache.state, state)

    async def test_removes_deleted_objects(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        state = {objs[-1]._selfLink(): objs[-1]}
        self.events.extend(('ADDED', obj) for obj in objs)
        self.events.extend(('DELETED', obj) for obj in objs[:-1])
        await self.cache.run()
        self.assertEqual(self.cache.state, state)

    async def test_updates_modified_objects(self):
        obj1 = FooKind(metadata={'name': 'foo'}, foo=1)
        obj2 = FooKind(metadata={'name': 'foo'}, foo=2)
        state = {obj2._selfLink(): obj2}
        self.events.append(('ADDED', obj1))
        self.events.append(('MODIFIED', obj2))
        await self.cache.run()
        self.assertEqual(self.cache.state, state)

    async def test_subscribe_added(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        await self.cache.run()
        self.assertEqual(receiver.call_args_list, [
            ((self.cache, evt, None, obj),) for evt, obj in events])

    async def test_subscribe_modified(self):
        old_objs = [
            FooKind(metadata={'name': 'foo'}, ver=1),
            FooKind(metadata={'name': 'bar'}, ver=1),
            FooKind(metadata={'name': 'baz'}, ver=1),
        ]
        new_objs = [
            FooKind(metadata={'name': 'foo'}, ver=2),
            FooKind(metadata={'name': 'bar'}, ver=2),
            FooKind(metadata={'name': 'baz'}, ver=2),
        ]
        events = [('ADDED', obj) for obj in old_objs]
        self.events.extend(events)
        await self.cache.run()
        events = [('MODIFIED', obj) for obj in new_objs]
        self.events.extend(events)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        await self.cache.run()
        self.assertEqual(receiver.call_args_list, [
            ((self.cache, 'MODIFIED', old, new),) for old, new in zip(old_objs, new_objs)])

    async def test_subscribe_ignores_duplicates(self):
        obj = FooKind(metadata={'name': 'foo'}, ver=1)
        events = [
            ('ADDED', obj),
            ('MODIFIED', obj),
            ('MODIFIED', obj),
        ]
        self.events.extend(events)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        await self.cache.run()
        self.assertEqual(receiver.call_args_list, [((self.cache, 'ADDED', None, obj),)])

    async def test_subscribe_deleted(self):
        objs = [
            FooKind(metadata={'name': 'foo'}, ver=1),
            FooKind(metadata={'name': 'bar'}, ver=1),
            FooKind(metadata={'name': 'baz'}, ver=1),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        await self.cache.run()
        events = [('DELETED', obj) for obj in objs]
        self.events.extend(events)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        await self.cache.run()
        self.assertEqual(receiver.call_args_list, [
            ((self.cache, 'DELETED', old, None),) for old in objs])

    async def test_subscribe_cond_true(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        cond = MagicMock(return_value=True)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(cond, receiver)
        await self.cache.run()
        self.assertEqual(cond.call_args_list, [((evt, obj),) for evt, obj in events])
        self.assertEqual(receiver.call_args_list, [
            ((self.cache, evt, None, obj),) for evt, obj in events])

    async def test_subscribe_cond_false(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        cond = MagicMock(return_value=False)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(cond, receiver)
        await self.cache.run()
        self.assertEqual(cond.call_args_list, [((evt, obj),) for evt, obj in events])
        self.assertFalse(receiver.called)

    async def test_subscribe_strong(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        res = []
        async def receiver(cache, event, old, new):
            res.append(event)
        self.cache.subscribe(lambda evt, obj: True, receiver, weak=False)
        del receiver
        await self.cache.run()
        self.assertEqual(res, [evt for evt, _ in events])

    async def test_subscribe_weak(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        res = []
        async def receiver(cache, event, old, new):
            res.append(event)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        del receiver
        await self.cache.run()
        self.assertEqual(res, [])
        self.assertEqual(self.cache.subscriptions, {})

    async def test_subscribe_weak_method(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.events.extend(events)
        res = []
        class Foo:
            async def receiver(self, cache, event, old, new):
                res.append(event)
        foo = Foo()
        self.cache.subscribe(lambda evt, obj: True, foo.receiver)
        await self.cache.run()
        self.assertEqual(res, ['ADDED' for _ in objs])

    def test_unsubscribe_strong(self):
        async def receiver(cache, event, old, new):
            pass
        self.cache.subscribe(lambda evt, obj: True, receiver, weak=False)
        self.assertEqual(len(self.cache.subscriptions), 1)
        self.cache.unsubscribe(receiver)
        self.assertEqual(len(self.cache.subscriptions), 0)

    async def test_unsubscribe_weak(self):
        async def receiver(cache, event, old, new):
            pass
        self.cache.subscribe(lambda evt, obj: True, receiver, weak=True)
        self.assertEqual(len(self.cache.subscriptions), 1)
        self.cache.unsubscribe(receiver)
        self.assertEqual(len(self.cache.subscriptions), 0)

    async def test_subscription_exception_logs_traceback(self):
        self.events.append(('ADDED', FooKind(metadata={'name': 'foo'})))
        async def receiver1(cache, event, old, new):
            raise RuntimeError('foobar')
        receiver2 = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver1)
        self.cache.subscribe(lambda evt, obj: True, receiver2)
        with patch.object(self.cache, 'log') as log:
            await self.cache.run()
        log.exception.assert_called_once_with('unhandled error in subscription handler')
        self.assertTrue(receiver2.called)

    async def test_subscription_cond_exception_logs_traceback(self):
        self.events.append(('ADDED', FooKind(metadata={'name': 'foo'})))
        def cond1(event, obj):
            raise RuntimeError('foobar')
        cond2 = lambda event, obj: True
        receiver1 = MagicMock(side_effect=dummy_coro)
        receiver2 = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(cond1, receiver1)
        self.cache.subscribe(cond2, receiver2)
        with patch.object(self.cache, 'log') as log:
            await self.cache.run()
        log.exception.assert_called_once_with('unhandled error in subscription handler')
        self.assertFalse(receiver1.called)
        self.assertTrue(receiver2.called)

    async def test_run_ignores_watch_iteration_errors(self):
        exceptions = [RuntimeError()]
        def pop(idx):
            raise exceptions.pop(0)
        self.watcher.events = MagicMock(**{'pop.side_effect': pop})
        await self.cache.run()


if __name__ == '__main__':
    unittest.main()
