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

import logging

import curio

from ..util import sync_wrap, SyncIter, SyncContextManager

LOG = logging.getLogger(__name__)


class Watch:
    def __init__(self, api, watches, taskgroup, path):
        self.api = api
        self._watches = watches
        self._taskgroup = taskgroup
        self.path = path
        self._stream = None
        self._closed = False
        self._current_task = None

    async def cancel(self):
        self._closed = True
        if self._current_task:
            await self._current_task.cancel()
            self._current_task = None
        self._watches.discard(self)

    async def _spawn(self):
        self._watches.add(self)
        self._current_task = await self._taskgroup.spawn(self._get_next)

    async def _get_next(self):
        event = None
        try:
            while not self._closed:
                if self._stream is None:
                    self._stream = await self.api.get(self.path, stream=True)
                try:
                    event = await self._stream.__anext__()
                    break
                except StopAsyncIteration:
                    self._stream = None

            if not self._closed:
                await self._spawn()

        except curio.CancelledError:
            pass

        return event


class Watcher:
    def __init__(self, api):
        self.api = api
        self._watches = set()
        self._closed = False
        self._taskgroup = curio.TaskGroup()
        self._watchdog_task = None

    async def _watchdog(self):
        try:
            await curio.Event().wait()
        finally:
            self._watchdog_task = None
            await self.cancel()

    async def watch(self, path):
        if self._closed:
            raise RuntimeError('Watcher is closed')
        if not self._watchdog_task:
            self._watchdog_task = await curio.spawn(self._watchdog)
        watch = Watch(self.api, self._watches, self._taskgroup, path)
        await watch._spawn()
        return watch

    async def cancel(self):
        self._closed = True
        for w in list(self._watches):
            await w.cancel()
        await self._taskgroup.cancel_remaining()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            task = await self._taskgroup.next_done()
            if not task or not task.result:
                raise StopAsyncIteration()
            try:
                return task.result['type'], task.result['object']
            except curio.CancelledError:
                pass
