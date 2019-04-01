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

LOG = logging.getLogger(__name__)


class _TestLoopBreaker(curio.TaskCancelled):
    pass


class CacheSynchronizer:
    log = LOG.getChild('CacheSynchronizer')

    def __init__(self, cache, api, kind, **kw):
        self.cache = cache
        self.api = api
        self.kind = kind
        self.kw = kw
        self.warm = curio.Event()
        self._resync = curio.Event()

    async def run(self):
        while True:
            self._resync.clear()
            g = curio.TaskGroup()
            run_task = await g.spawn(self._run)
            await g.spawn(self._resync.wait)
            done = await g.next_done(cancel_remaining=True)
            try:
                done.result
            except _TestLoopBreaker:
                break
            if done is run_task:
                break

    async def _run(self):
        seen = set()
        path = self.kind._makeLink(**self.kw)
        self.log.debug(f'list {path}')
        l = await self.api.get(path)
        resourceVersion = l.metadata.resourceVersion

        for obj in l['items']:
            try:
                path = obj._selfLink()
            except AttributeError:
                continue
            seen.add(path)
            await self.cache.set(path, obj)

        for path in set(self.cache) - seen:
            if isinstance(self.cache[path], self.kind):
                await self.cache.delete(path)

        await self.warm.set()

        while True:
            path = self.kind._makeLink(**dict(
                self.kw, resourceVersion=resourceVersion, watch='true'))
            self.log.debug(f'watch {path}')
            stream = await self.api.get(path, stream=True)
            async for event in stream:
                obj = await self.api._kindify(event['object'])
                try:
                    path = obj._selfLink()
                except AttributeError:
                    continue
                resourceVersion = obj.metadata.get('resourceVersion', resourceVersion)
                if event['type'] == 'DELETED':
                    await self.cache.delete(path)
                else:
                    await self.cache.set(path, obj)

    async def resync(self):
        await self._resync.set()
