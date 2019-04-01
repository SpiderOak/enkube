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

from enkube.api.types import *
from enkube.api.client import ResourceNotFoundError, ResourceKindNotFoundError
from enkube.api.cache import Cache
from enkube.api import cache_synchronizer


class FooKind(Kind):
    _namespaced = False
    apiVersion = 'enkube.local/v1'


class EventStream:
    def __init__(self, tc):
        self.tc = tc

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            try:
                event = self.tc.events.pop(0)
            except IndexError:
                raise StopAsyncIteration()
            if isinstance(event, Exception):
                raise event
            elif isinstance(event, curio.Event):
                await event.wait()
                continue
            return event


class TestCacheSynchronizer(AsyncTestCase):
    def setUp(self):
        self.list = []
        self.streams = []
        self.events = []
        async def get_coro(path, stream=False):
            if stream:
                try:
                    self.events = self.streams.pop(0)
                except IndexError:
                    raise cache_synchronizer._TestLoopBreaker()
                return EventStream(self)
            else:
                return FooKind.List(
                    metadata={'resourceVersion': 'fooversion'},
                    items=self.list[:],
                )
        async def getkind_coro(apiVersion, kind):
            return FooKind
        async def kindify_coro(obj):
            return FooKind(obj)
        self.api = MagicMock(**{
            'get.side_effect': get_coro,
            'getKind.side_effect': getkind_coro,
            '_kindify.side_effect': kindify_coro,
        })
        self.cache = MagicMock(wraps=Cache())
        self.sync = cache_synchronizer.CacheSynchronizer(self.cache, self.api, FooKind)
        self.sync.log = MagicMock()

    async def test_caches_objects_from_list(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        self.list.extend(objs)
        await self.sync.run()
        self.assertEqual(self.cache.set.call_args_list, [
            call(o._selfLink(), o) for o in objs
        ])
        self.assertEqual(self.api.get.call_args_list, [
            call('/apis/enkube.local/v1/fookinds'),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
        ])

    async def test_ignores_objects_without_selflink_from_list(self):
        objs = [
            {'foo': 'bar'},
        ]
        self.list.extend(objs)
        await self.sync.run()
        self.cache.set.assert_not_called()

    async def test_caches_objects_from_events(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        self.streams.append([{'type': 'ADDED', 'object': dict(obj)} for obj in objs])
        await self.sync.run()
        self.assertEqual(self.cache.set.call_args_list, [
            call(o._selfLink(), o) for o in objs
        ])

    async def test_ignores_objects_without_selflink_from_events(self):
        objs = [
            {'foo': 'bar'},
        ]
        self.streams.append([{'type': 'ADDED', 'object': dict(obj)} for obj in objs])
        await self.sync.run()
        self.cache.set.assert_not_called()

    async def test_removes_deleted_objects(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        self.streams.append([{'type': 'ADDED', 'object': dict(obj)} for obj in objs])
        self.streams.append([{'type': 'DELETED', 'object': dict(obj)} for obj in objs])
        await self.sync.run()
        self.assertEqual(self.cache.set.call_args_list, [
            call(o._selfLink(), o) for o in objs
        ])
        self.assertEqual(self.cache.delete.call_args_list, [
            call(o._selfLink()) for o in objs
        ])

    async def test_updates_modified_objects(self):
        objs = [
            FooKind(metadata={'name': 'foo'}),
            FooKind(metadata={'name': 'bar'}),
            FooKind(metadata={'name': 'baz'}),
        ]
        self.streams.append([{'type': 'ADDED', 'object': dict(obj)} for obj in objs])
        self.streams.append([{'type': 'MODIFIED', 'object': dict(obj)} for obj in objs])
        await self.sync.run()
        self.assertEqual(self.cache.set.call_args_list, [
            call(o._selfLink(), o) for o in objs
        ] + [
            call(o._selfLink(), o) for o in objs
        ])
        self.assertEqual(self.api.get.call_args_list, [
            call('/apis/enkube.local/v1/fookinds'),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
        ])

    async def test_updates_resourceversion(self):
        objs = [
            FooKind(metadata={'name': 'foo', 'resourceVersion': 'foo'}),
            FooKind(metadata={'name': 'bar', 'resourceVersion': 'bar'}),
            FooKind(metadata={'name': 'baz', 'resourceVersion': 'baz'}),
        ]
        self.streams.append([{'type': 'ADDED', 'object': dict(obj)} for obj in objs])
        self.streams.append([{'type': 'MODIFIED', 'object': dict(obj)} for obj in objs])
        await self.sync.run()
        self.assertEqual(self.cache.set.call_args_list, [
            call(o._selfLink(), o) for o in objs
        ] + [
            call(o._selfLink(), o) for o in objs
        ])
        self.assertEqual(self.api.get.call_args_list, [
            call('/apis/enkube.local/v1/fookinds'),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=baz&watch=true', stream=True),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=baz&watch=true', stream=True),
        ])

    async def test_sets_warm_event_after_list_before_watch(self):
        self.list.append(FooKind(metadata={'name': 'foo'}))
        self.streams.append([{'type': 'ADDED', 'object': dict(FooKind(metadata={'name': 'bar'}))}])
        warmup_state = {}
        async def event_handler(cache, event, old, new):
            warmup_state[new.metadata.name] = self.sync.warm.is_set()
        self.cache.subscribe(lambda evt, old, new: True, event_handler)
        await self.sync.run()
        self.assertEqual(warmup_state, {
            'foo': False,
            'bar': True,
        })
        self.assertTrue(self.sync.warm.is_set())

    async def test_resync(self):
        self.streams.append([curio.Event()])
        async with curio.TaskGroup() as g:
            await g.spawn(self.sync.run)
            await self.sync.warm.wait()
            await self.sync.resync()
        self.assertEqual(self.api.get.call_args_list, [
            call('/apis/enkube.local/v1/fookinds'),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
            call('/apis/enkube.local/v1/fookinds'),
            call('/apis/enkube.local/v1/fookinds?resourceVersion=fooversion&watch=true', stream=True),
        ])

    async def test_cancellation(self):
        self.streams.append([curio.TaskCancelled()])
        with self.assertRaises(curio.CancelledError):
            await self.sync.run()


if __name__ == '__main__':
    unittest.main()
