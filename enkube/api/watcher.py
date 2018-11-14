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
    def __init__(self, path):
        self.path = path
        self._closed = False
        self._current_task = None

    async def cancel(self):
        self._closed = True
        if self._current_task:
            await self._current_task.cancel()


class Watcher:
    def __init__(self, api):
        self.api = api
        self._closed = False
        self._taskgroup = curio.TaskGroup()
        self._watchdog_task = None

    async def _watchdog(self):
        try:
            await curio.Event().wait()
        finally:
            self._watchdog_task = None
            await self.cancel()

    async def _get_next(self, watch_task, stream=None):
        if not self._watchdog_task:
            await curio.spawn(self._watchdog)

        if stream is None:
            stream = await self.api.get(watch_task.path, stream=True)

        while True:
            try:
                event = await stream.__anext__()
                break
            except StopAsyncIteration:
                pass
            stream = await self.api.get(watch_task.path, stream=True)

        if not self._closed and not watch_task._closed:
            watch_task._current_task = await self._taskgroup.spawn(
                self._get_next, watch_task, stream)

        return event

    async def watch(self, path):
        if self._closed:
            raise RuntimeError('Watcher is closed')
        watch_task = Watch(path)
        watch_task._current_task = await self._taskgroup.spawn(
            self._get_next, watch_task)
        return watch_task

    async def cancel(self):
        self._closed = True
        await self._taskgroup.cancel_remaining()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            task = await self._taskgroup.next_done()
            if not task:
                raise StopAsyncIteration()
            try:
                return task.result['type'], task.result['object']
            except curio.TaskCancelled:
                pass
