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

import sys
import logging

import curio

from ..util import sync_wrap, SyncIter, SyncContextManager
from .types import Kind

LOG = logging.getLogger(__name__)


class Watch:
    log = LOG.getChild('Watch')

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
        self._watches.pop(self.path, None)

    async def _get_stream(self):
        if self._stream is None:
            self.log.debug(f'stream {self.path}')
            self._stream = await self.api.get(self.path, stream=True)

    async def _spawn(self):
        if self._closed:
            return
        await self._get_stream()
        self._watches[self.path] = self
        self._current_task = await self._taskgroup.spawn(self._get_next)

    async def _get_next(self):
        event = None
        exc = None
        try:
            while not self._closed:
                await self._get_stream()
                try:
                    event = await self._stream.__anext__()
                    break
                except StopAsyncIteration:
                    self.log.debug('stream ended')
                    self._stream = None

        except Exception:
            exc = sys.exc_info()

        return self, event, exc


class Watcher(SyncIter, SyncContextManager):
    def __init__(self, api):
        self.api = api
        self._watches = {}
        self._closed = False
        self._taskgroup = curio.TaskGroup()

    @sync_wrap
    async def watch(self, path):
        if self._closed:
            raise RuntimeError('Watcher is closed')
        if path in self._watches:
            return self._watches[path]
        watch = Watch(self.api, self._watches, self._taskgroup, path)
        await watch._spawn()
        return watch

    @sync_wrap
    async def cancel(self):
        self._closed = True
        for w in list(self._watches.values()):
            await w.cancel()
        await self._taskgroup.cancel_remaining()

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        await self.cancel()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            task = await self._taskgroup.next_done()
            if not task:
                break
            watch, event, exc = task.result
            if exc:
                typ, val, tb = exc
                if issubclass(typ, curio.CancelledError) and watch._closed:
                    continue
                await watch._spawn()
                raise typ.with_traceback(val, tb)
            if event:
                await watch._spawn()
                event, obj = event['type'], event['object']
                if isinstance(obj, dict) and not isinstance(obj, Kind):
                    obj = await self.api._kindify(obj)
                return event, obj
        raise StopAsyncIteration()
