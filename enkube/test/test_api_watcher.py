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

import curio

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api import watcher


class FakeStreamIter:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return self.items.pop(0)
        except IndexError:
            raise StopAsyncIteration() from None


class BlockingStreamIter:
    def __init__(self):
        self.gate = curio.Event()

    def __atier__(self):
        return self

    async def __anext__(self):
        await self.gate.wait()


class TestWatcher(AsyncTestCase):
    def setUp(self):
        self.events = [('SENTINEL', None)]
        async def stream_coro(*args, **kw):
            return FakeStreamIter([
                {'type': event, 'object': obj} for event, obj in self.events
            ])
        self.api = MagicMock(**{'get.side_effect': stream_coro})
        self.watcher = watcher.Watcher(self.api)

    async def test_watch(self):
        self.watcher._taskgroup = MagicMock(**{
            'cancel_remaining.side_effect': dummy_coro,
            'spawn.side_effect': dummy_coro
        })
        watch_task = await self.watcher.watch('/foobar')
        self.watcher._taskgroup.spawn.assert_called_once_with(
            self.watcher._get_next, watch_task)
        self.assertTrue(isinstance(watch_task, watcher.Watch))
        self.assertEqual(watch_task.path, '/foobar')

    async def test_watch_after_cancel_raises_runtimeerror(self):
        await self.watcher.cancel()
        with self.assertRaises(RuntimeError) as err:
            await self.watcher.watch('/foobar')
        self.assertEqual(err.exception.args[0], 'Watcher is closed')

    async def test_get_next_without_stream(self):
        stream = FakeStreamIter([sentinel.event])
        async def stream_coro(*args, **kw):
            return stream
        self.api.get.side_effect = stream_coro
        self.watcher._taskgroup = MagicMock(**{
            'cancel_remaining.side_effect': dummy_coro,
            'spawn.side_effect': dummy_coro
        })
        watch_task = watcher.Watch(sentinel.path)
        res = await self.watcher._get_next(watch_task)
        self.assertIs(res, sentinel.event)
        self.api.get.assert_called_once_with(sentinel.path, stream=True)
        self.watcher._taskgroup.spawn.assert_called_once_with(
            self.watcher._get_next, watch_task, stream)

    async def test_get_next_with_stream(self):
        stream = FakeStreamIter([sentinel.event])
        self.api.get.side_effect = AssertionError
        self.watcher._taskgroup = MagicMock(**{
            'cancel_remaining.side_effect': dummy_coro,
            'spawn.side_effect': dummy_coro
        })
        watch_task = watcher.Watch(sentinel.path)
        res = await self.watcher._get_next(watch_task, stream)
        self.assertIs(res, sentinel.event)
        self.api.get.assert_not_called()
        self.watcher._taskgroup.spawn.assert_called_once_with(
            self.watcher._get_next, watch_task, stream)

    async def test_get_next_restarts_on_stopasynciteration(self):
        stream1 = FakeStreamIter([])
        stream2 = FakeStreamIter([sentinel.event])
        async def stream_coro(*args, **kw):
            self.api.get.side_effect = curio.TaskCancelled
            return stream2
        self.api.get.side_effect = stream_coro
        self.watcher._taskgroup = MagicMock(**{
            'cancel_remaining.side_effect': dummy_coro,
            'spawn.side_effect': dummy_coro
        })
        watch_task = watcher.Watch(sentinel.path)
        res = await self.watcher._get_next(watch_task, stream1)
        self.assertIs(res, sentinel.event)
        self.api.get.assert_called_once_with(sentinel.path, stream=True)
        self.watcher._taskgroup.spawn.assert_called_once_with(
            self.watcher._get_next, watch_task, stream2)

    async def test_cancel(self):
        self.watcher._taskgroup = MagicMock(**{'cancel_remaining.side_effect': dummy_coro})
        await self.watcher.cancel()
        self.watcher._taskgroup.cancel_remaining.assert_called_once_with()

    async def test_anext_without_tasks_raises_stopasynciteration(self):
        with self.assertRaises(StopAsyncIteration):
            await self.watcher.__anext__()

    async def test_iterate_single_watch(self):
        self.events = [
            ('ADDED', {'foo': 1}),
            ('MODIFIED', {'foo': 2}),
            ('DELETED', {'foo': 2}),
            ('SENTINEL', None),
        ]
        await self.watcher.watch('/foobar')
        res = []
        async for event, obj in self.watcher:
            if event == 'SENTINEL':
                await self.watcher.cancel()
            res.append((event, obj))
        self.assertEqual(res, self.events)

    async def test_iterate_multi_watch(self):
        async def get_coro(path, *args, **kw):
            return FakeStreamIter([{'type': 'FOO', 'object': path}])
        self.api.get.side_effect = get_coro
        await self.watcher.watch('/foo')
        await self.watcher.watch('/bar')
        res = []
        async for event, obj in self.watcher:
            if len(res) == 2:
                await self.watcher.cancel()
                break
            res.append((event, obj))
        self.assertEqual(res, [
            ('FOO', '/foo'),
            ('FOO', '/bar'),
        ])

    async def test_cancel_watch(self):
        async def get_coro(path, *args, **kw):
            return FakeStreamIter([{'type': 'FOO', 'object': path}])
        self.api.get.side_effect = get_coro
        task1 = await self.watcher.watch('/foo')
        task2 = await self.watcher.watch('/bar')
        await task2.cancel()
        res = []
        async for event, obj in self.watcher:
            await task1.cancel()
            res.append((event, obj))
        self.assertEqual(res, [
            ('FOO', '/foo'),
            ('FOO', '/bar'),
            ('FOO', '/foo'),
            ('FOO', '/foo'),
        ])

    async def test_cancel_blocked_watch(self):
        stream = BlockingStreamIter()
        async def get_coro(path, *args, **kw):
            return stream
        self.api.get.side_effect = get_coro
        task = await self.watcher.watch('/foo')
        await task.cancel()
        res = []
        async for event, obj in self.watcher:
            res.append((event, obj))
        self.assertEqual(res, [])

    async def test_cancel_blocked_watch_after_iteration(self):
        streams = [
            FakeStreamIter([{'type': 'FOO', 'object': 'foo'}]),
            BlockingStreamIter(),
        ]
        async def get_coro(path, *args, **kw):
            return streams.pop(0)
        self.api.get.side_effect = get_coro
        task = await self.watcher.watch('/foo')
        res = []
        async for event, obj in self.watcher:
            res.append((event, obj))
            await task.cancel()
        self.assertEqual(res, [('FOO', 'foo')])


if __name__ == '__main__':
    unittest.main()
