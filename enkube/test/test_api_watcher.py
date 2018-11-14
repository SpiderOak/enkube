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


class TestWatch(AsyncTestCase):
    def setUp(self):
        self.stream = FakeStreamIter([])
        async def stream_coro(*args, **kw):
            return self.stream
        self.api = MagicMock(**{'get.side_effect': stream_coro})
        self.spawned_tasks = []
        async def spawn_coro(*args, **kw):
            task = MagicMock()
            self.spawned_tasks.append(task)
            return task
        self.watches = set()
        self.taskgroup = MagicMock(**{'spawn.side_effect': spawn_coro})
        self.watch = watcher.Watch(self.api, self.watches, self.taskgroup, sentinel.path)

    async def test_spawn(self):
        await self.watch._spawn()
        self.taskgroup.spawn.assert_called_once_with(self.watch._get_next)
        self.assertEqual([self.watch._current_task], self.spawned_tasks)
        self.assertEqual(self.watches, {self.watch})

    async def test_get_next_without_stream(self):
        self.stream.items.append(sentinel.event)
        res = await self.watch._get_next()
        self.assertIs(res, sentinel.event)
        self.api.get.assert_called_once_with(sentinel.path, stream=True)
        self.taskgroup.spawn.assert_called_once_with(self.watch._get_next)
        self.assertEqual([self.watch._current_task], self.spawned_tasks)

    async def test_get_next_with_stream(self):
        self.stream.items.append(sentinel.event)
        self.watch._stream = self.stream
        res = await self.watch._get_next()
        self.assertIs(res, sentinel.event)
        self.api.get.assert_not_called()
        self.taskgroup.spawn.assert_called_once_with(self.watch._get_next)
        self.assertEqual([self.watch._current_task], self.spawned_tasks)

    async def test_get_next_restarts_after_stream_finishes(self):
        streams = [
            FakeStreamIter([]),
            FakeStreamIter([sentinel.event]),
        ]
        async def stream_coro(*args, **kw):
            return streams.pop(0)
        self.api.get.side_effect = stream_coro
        res = await self.watch._get_next()
        self.assertIs(res, sentinel.event)
        self.assertEqual(self.api.get.call_args_list, [
            ((sentinel.path,), {'stream': True}),
            ((sentinel.path,), {'stream': True}),
        ])
        self.taskgroup.spawn.assert_called_once_with(self.watch._get_next)
        self.assertEqual([self.watch._current_task], self.spawned_tasks)

    async def test_get_next_does_not_respawn_when_closed(self):
        self.watch._closed = True
        res = await self.watch._get_next()
        self.assertIs(res, None)
        self.taskgroup.spawn.assert_not_called()

    async def test_get_next_returns_none_when_blocked_on_stream_and_canceled(self):
        self.stream = BlockingStreamIter()
        self.watch._current_task = t = await curio.spawn(self.watch._get_next)
        await curio.sleep(0)
        await self.watch.cancel()
        self.assertIs(t.result, None)

    async def test_get_next_returns_none_when_blocked_on_get_and_canceled(self):
        async def block_coro(*args, **kw):
            await curio.Event().wait()
        self.api.get.side_effect = block_coro
        self.watch._current_task = t = await curio.spawn(self.watch._get_next)
        await curio.sleep(0)
        await self.watch.cancel()
        self.assertIs(t.result, None)

    async def test_cancel(self):
        await self.watch.cancel()
        self.assertTrue(self.watch._closed)

    async def test_cancel_discards_watch(self):
        self.watches.add(self.watch)
        await self.watch.cancel()
        self.assertTrue(self.watch._closed)
        self.assertEqual(self.watches, set())

    async def test_cancel_while_blocked(self):
        self.watch._current_task = t = MagicMock(**{'cancel.side_effect': dummy_coro})
        await self.watch.cancel()
        self.assertTrue(self.watch._closed)
        self.assertIs(self.watch._current_task, None)
        t.cancel.assert_called_once_with()


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
            'spawn.side_effect': dummy_coro,
        })
        watch = await self.watcher.watch('/foobar')
        self.assertTrue(isinstance(watch, watcher.Watch))
        self.assertEqual(watch.path, '/foobar')
        self.watcher._taskgroup.spawn.assert_called_once_with(watch._get_next)

    async def test_watch_after_cancel_raises_runtimeerror(self):
        await self.watcher.cancel()
        with self.assertRaises(RuntimeError) as err:
            await self.watcher.watch('/foobar')
        self.assertEqual(err.exception.args[0], 'Watcher is closed')

    async def test_cancel(self):
        self.watcher._taskgroup = MagicMock(**{'cancel_remaining.side_effect': dummy_coro})
        await self.watcher.cancel()
        self.watcher._taskgroup.cancel_remaining.assert_called_once_with()

    async def test_cancel_cancels_watches(self):
        self.watcher._taskgroup = MagicMock(**{
            'spawn.side_effect': dummy_coro,
            'cancel_remaining.side_effect': dummy_coro,
        })
        watch = await self.watcher.watch('/foobar')
        with patch.object(watch, 'cancel') as c:
            c.side_effect = dummy_coro
            await self.watcher.cancel()
        c.assert_called_once_with()

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
        w1 = await self.watcher.watch('/foo')
        w2 = await self.watcher.watch('/bar')
        await curio.sleep(0)
        await w2.cancel()
        res = []
        async for event, obj in self.watcher:
            await w1.cancel()
            res.append((event, obj))
        self.assertEqual(res, [
            ('FOO', '/foo'),
            ('FOO', '/bar'),
            ('FOO', '/foo'),
        ])

    async def test_cancel_blocked_watch(self):
        stream = BlockingStreamIter()
        async def get_coro(path, *args, **kw):
            return stream
        self.api.get.side_effect = get_coro
        w = await self.watcher.watch('/foo')
        await w.cancel()
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
        w = await self.watcher.watch('/foo')
        res = []
        async for event, obj in self.watcher:
            res.append((event, obj))
            await w.cancel()
        self.assertEqual(res, [('FOO', 'foo')])

    async def test_ungraceful_shutdown(self):
        await self.watcher.watch('/foo')


if __name__ == '__main__':
    unittest.main()
