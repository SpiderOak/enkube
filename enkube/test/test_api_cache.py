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

from itertools import zip_longest
import unittest
from unittest.mock import patch, MagicMock, sentinel

import curio

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api.types import *
from enkube.api.client import ResourceNotFoundError, ResourceKindNotFoundError
from enkube.api import cache


class FakeWatcher:
    def __init__(self, events):
        self.events = events
        self.watch = MagicMock(side_effect=dummy_coro)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            try:
                event, args = self.events.pop(0)
            except IndexError:
                raise StopAsyncIteration() from None

            if event == 'TRAP':
                exhausted, resume = args
                await exhausted.set()
                await resume.wait()
                continue

            return event, args

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        pass


class FooKind(Kind):
    _namespaced = False
    apiVersion = 'enkube.local/v1'


class BarKind(Kind):
    _namespaced = False
    apiVersion = 'enkube.local/v1'


class TestCache(AsyncTestCase):
    def setUp(self):
        KindType.instances = {(FooKind.apiVersion, FooKind.kind): FooKind}
        self.list = []
        self.events = []
        async def get_coro(path):
            items = self.list[:]
            del self.list[:]
            return FooKind.List(
                metadata={'resourceVersion': 'fooversion'},
                items=items,
            )
        async def getkind_coro(apiVersion, kind):
            return Kind.getKind(apiVersion, kind)
        self.api = MagicMock(**{
            'wait_until_healthy.side_effect': dummy_coro,
            'get.side_effect': get_coro,
            'getKind.side_effect': getkind_coro,
        })
        self.watcher = FakeWatcher(self.events)
        self.cache = cache.Cache(self.api, [(FooKind, {})], lambda api: self.watcher)
        self.cache.log = MagicMock()

    def assertWatchCallsEqual(self, calls, msg=None):
        for call, expected in zip_longest(self.watcher.watch.call_args_list, calls):
            if None in (call, expected):
                self.fail(msg or 'unexpected number of calls')
            self.assertEqual(call[1], {})
            path, = call[0]
            path, query = path.split('?', 1)
            kw = dict(q.split('=', 1) for q in query.split('&'))
            ex_typ, ex_kw = expected
            ex_kw['watch'] = 'true'
            self.assertEqual(path, ex_typ._makeLink())
            self.assertEqual(kw, ex_kw)

    def test_add_kind(self):
        self.cache.add_kind(BarKind, fieldSelector='metadata.namespace=myns')
        self.cache.add_kind('v1/Secret')
        self.assertEqual(self.cache.kinds, {
            (FooKind, frozenset()),
            (BarKind, frozenset({'fieldSelector': 'metadata.namespace=myns'}.items())),
            ('v1/Secret', frozenset()),
        })

    async def test_caches_objects_from_list(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        state = dict((o._selfLink(), o) for o in objs)
        self.list.extend(objs)
        await self.cache.run()
        self.assertEqual(self.cache, state)
        self.api.get.assert_called_once_with(FooKind._makeLink())
        self.assertFalse(self.api.getKind.called)

    async def test_ignores_missing_kinds(self):
        async def not_found(*args):
            raise ResourceNotFoundError()
        self.api.get.side_effect = not_found
        await self.cache.run()
        self.cache.log.warning.assert_called_once_with(
            'ignoring unknown kind: enkube.local/v1/FooKind')

    async def test_handles_string_kinds_on_list(self):
        self.cache.kinds = {('enkube.local/v1/FooKind', frozenset())}
        objs = [
            FooKind(metadata={'name': 'foo'}),
        ]
        state = dict((o._selfLink(), o) for o in objs)
        self.list.extend(objs)
        await self.cache.run()
        self.assertEqual(self.cache, state)
        self.api.get.assert_called_once_with(FooKind._makeLink())
        self.api.getKind.assert_called_with('enkube.local/v1', 'FooKind')

    async def test_ignores_missing_string_kinds(self):
        async def not_found(*args):
            raise ResourceKindNotFoundError()
        self.cache.kinds = {('enkube.local/v1/FooKind', frozenset())}
        self.api.getKind.side_effect = not_found
        await self.cache.run()
        self.cache.log.warning.assert_called_once_with(
            'ignoring unknown kind: enkube.local/v1/FooKind')

    async def test_caches_objects_from_events(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        state = dict((o._selfLink(), o) for o in objs)
        self.events.extend(('ADDED', obj) for obj in objs)
        await self.cache.run()
        self.assertEqual(self.cache, state)
        self.assertFalse(self.api.getKind.called)

    async def test_handles_string_kinds_on_watch(self):
        self.cache.kinds = {('enkube.local/v1/FooKind', frozenset())}
        objs = [
            FooKind(metadata={'name': 'foo'}),
        ]
        state = dict((o._selfLink(), o) for o in objs)
        self.events.extend(('ADDED', obj) for obj in objs)
        await self.cache.run()
        self.assertEqual(self.cache, state)
        self.api.getKind.assert_called_with('enkube.local/v1', 'FooKind')

    async def test_passes_resourceversion_to_watch(self):
        await self.cache.run()
        self.assertWatchCallsEqual([
            (FooKind, {'resourceVersion': 'fooversion'})
        ])

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
        self.assertEqual(self.cache, state)

    async def test_updates_modified_objects(self):
        obj1 = FooKind(metadata={'name': 'foo'}, foo=1)
        obj2 = FooKind(metadata={'name': 'foo'}, foo=2)
        state = {obj2._selfLink(): obj2}
        self.events.append(('ADDED', obj1))
        self.events.append(('MODIFIED', obj2))
        await self.cache.run()
        self.assertEqual(self.cache, state)

    async def test_subscribe_added_by_list(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        events = [('ADDED', obj) for obj in objs]
        self.list.extend(objs)
        receiver = MagicMock(side_effect=dummy_coro)
        self.cache.subscribe(lambda evt, obj: True, receiver)
        await self.cache.run()
        self.assertEqual(receiver.call_args_list, [
            ((self.cache, evt, None, obj),) for evt, obj in events])

    async def test_warmup_notifications_deferred(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        self.list.extend(objs)
        expected_state = dict((o._selfLink(), o) for o in objs)
        state_at_foo_event = {}
        async def foo_event(cache, event, old, new):
            state_at_foo_event.update(cache)
        receiver = MagicMock(side_effect=foo_event)
        self.cache.subscribe(lambda evt, obj: obj is objs[0], receiver)
        await self.cache.run()
        self.assertEqual(state_at_foo_event, expected_state)

    async def test_sets_warm_event_after_list_before_watch(self):
        self.list.append(FooKind(metadata={'name': 'foo'}))
        self.events.append(('ADDED', FooKind(metadata={'name': 'bar'})))
        warmup_state = {}
        async def event_handler(cache, event, old, new):
            warmup_state[new.metadata.name] = cache.warm.is_set()
        self.cache.subscribe(lambda evt, obj: True, event_handler)
        await self.cache.run()
        self.assertEqual(warmup_state, {
            'foo': False,
            'bar': True,
        })
        self.assertTrue(self.cache.warm.is_set())

    async def test_subscribe_added_by_watch(self):
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

    async def test_subscribe_modified_by_list(self):
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
        self.list.extend(old_objs)

        watch_exhausted = curio.Event()
        self.events.append(('TRAP', (watch_exhausted, curio.Event())))

        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await watch_exhausted.wait()

            events = [('MODIFIED', obj) for obj in new_objs]
            self.list.extend(new_objs)
            receiver = MagicMock(side_effect=dummy_coro)
            self.cache.subscribe(lambda evt, obj: True, receiver)
            await self.cache.resync()

        self.assertEqual(receiver.call_args_list, [
            ((self.cache, 'MODIFIED', old, new),) for old, new in zip(old_objs, new_objs)])

    async def test_subscribe_modified_by_watch(self):
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
        self.list.extend(old_objs)

        watch_exhausted = curio.Event()
        resume_watch = curio.Event()
        self.events.append(('TRAP', (watch_exhausted, resume_watch)))

        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await watch_exhausted.wait()

            events = [('MODIFIED', obj) for obj in new_objs]
            self.events.extend(events)
            receiver = MagicMock(side_effect=dummy_coro)
            self.cache.subscribe(lambda evt, obj: True, receiver)
            await resume_watch.set()

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

    async def test_subscribe_deleted_by_list(self):
        objs = [
            FooKind(metadata={'name': 'foo'}, ver=1),
            FooKind(metadata={'name': 'bar'}, ver=1),
            FooKind(metadata={'name': 'baz'}, ver=1),
        ]
        self.list.extend(objs)

        watch_exhausted = curio.Event()
        self.events.append(('TRAP', (watch_exhausted, curio.Event())))

        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await watch_exhausted.wait()

            events = [('DELETED', obj) for obj in objs]
            receiver = MagicMock(side_effect=dummy_coro)
            self.cache.subscribe(lambda evt, obj: True, receiver)
            await self.cache.resync()

        # these deletions can happen in any order
        deletion_calls = [((self.cache, 'DELETED', old, None),) for old in objs]
        receiver.assert_has_calls(deletion_calls, any_order=True)
        self.assertEqual(receiver.call_count, len(deletion_calls))

    async def test_subscribe_deleted_by_watch(self):
        objs = [
            FooKind(metadata={'name': 'foo'}, ver=1),
            FooKind(metadata={'name': 'bar'}, ver=1),
            FooKind(metadata={'name': 'baz'}, ver=1),
        ]
        self.list.extend(objs)

        watch_exhausted = curio.Event()
        resume_watch = curio.Event()
        self.events.append(('TRAP', (watch_exhausted, resume_watch)))

        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await watch_exhausted.wait()

            events = [('DELETED', obj) for obj in objs]
            self.events.extend(events)
            receiver = MagicMock(side_effect=dummy_coro)
            self.cache.subscribe(lambda evt, obj: True, receiver)
            await resume_watch.set()

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
        await self.cache.run()
        self.cache.log.exception.assert_called_once_with('unhandled error in subscription handler')
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
        await self.cache.run()
        self.cache.log.exception.assert_called_once_with('unhandled error in subscription handler')
        self.assertFalse(receiver1.called)
        self.assertTrue(receiver2.called)

    async def test_run_waits_until_api_is_healthy(self):
        has_waited = curio.Event()
        is_ready = curio.Event()
        async def wait_coro():
            await has_waited.set()
            await is_ready.wait()
        self.api.wait_until_healthy.side_effect = wait_coro
        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await has_waited.wait()
            self.api.getKind.assert_not_called()
            self.api.get.assert_not_called()
            await is_ready.set()

    async def test_run_ignores_watch_iteration_errors(self):
        exceptions = [RuntimeError()]
        def pop(idx):
            raise exceptions.pop(0)
        self.watcher.events = MagicMock(**{'pop.side_effect': pop})
        await self.cache.run()
        self.cache.log.warning.assert_called_once_with(
            'watch iteration resulted in error: RuntimeError()')

    async def test_get_or_update_is_deprected(self):
        with self.assertWarns(DeprecationWarning):
            await self.cache.get_or_update('/foo')

    async def test_resync(self):
        warmup_state = {}
        async def record_warmup_state(cache, event, old, new):
            obj = new or old
            warmup_state[(event, obj.metadata.name, obj.ver)] = cache.warm.is_set()
        receiver = MagicMock(side_effect=record_warmup_state)
        self.cache.subscribe(lambda evt, obj: True, receiver)

        list_objs1 = [
            FooKind(metadata={'name': 'foo'}, ver=1),
            FooKind(metadata={'name': 'bar'}, ver=1),
        ]
        watch_objs1 = [
            FooKind(metadata={'name': 'foo'}, ver=2),
        ]
        self.list.extend(list_objs1)
        self.events.extend(('MODIFIED', obj) for obj in watch_objs1)

        watch_exhausted = curio.Event()
        self.events.append(('TRAP', (watch_exhausted, curio.Event())))

        async with curio.TaskGroup() as g:
            await g.spawn(self.cache.run)
            await watch_exhausted.wait()

            list_objs2 = [
                FooKind(metadata={'name': 'foo'}, ver=3),
            ]
            watch_objs2 = [
                FooKind(metadata={'name': 'foo'}, ver=4),
            ]
            self.list.extend(list_objs2)
            self.events.extend(('MODIFIED', obj) for obj in watch_objs2)
            await self.cache.resync()

        self.assertEqual(receiver.call_args_list, [
            ((self.cache, 'ADDED', None, obj),) for obj in list_objs1
        ] + [
            ((self.cache, 'MODIFIED', list_objs1[0], watch_objs1[0]),),
            ((self.cache, 'MODIFIED', watch_objs1[0], list_objs2[0]),),
            ((self.cache, 'DELETED', list_objs1[-1], None),),
            ((self.cache, 'MODIFIED', list_objs2[0], watch_objs2[0]),),
        ])
        self.assertEqual(warmup_state, {
            ('ADDED', 'foo', 1): False,
            ('ADDED', 'bar', 1): False,
            ('MODIFIED', 'foo', 2): True,
            ('DELETED', 'bar', 1): True,
            ('MODIFIED', 'foo', 3): True,
            ('MODIFIED', 'foo', 4): True,
        })

    async def test_cancellation(self):
        exceptions = [curio.TaskCancelled()]
        def pop(idx):
            raise exceptions.pop(0)
        self.watcher.events = MagicMock(**{'pop.side_effect': pop})
        with self.assertRaises(curio.CancelledError):
            await self.cache.run()


if __name__ == '__main__':
    unittest.main()
