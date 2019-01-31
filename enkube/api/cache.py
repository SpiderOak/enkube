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

import warnings
import logging
import weakref
from types import MethodType

import curio

from ..util import sync_wrap
from .watcher import Watcher


LOG = logging.getLogger(__name__)


class Cache(dict):
    log = LOG.getChild('Cache')

    def __init__(self, api, kinds=(), watcher_factory=Watcher):
        self._resync_event = curio.Event()
        self.warm = curio.Event()
        self.watcher_factory = watcher_factory
        self.api = api
        self.subscriptions = {}
        self.kinds = set()
        for k, kw in kinds:
            self.add_kind(k, **kw)

    def add_kind(self, kind, **kw):
        self.kinds.add((kind, frozenset(kw.items())))

    @sync_wrap
    async def resync(self):
        await self._resync_event.set()

    @sync_wrap
    async def run(self):
        while True:
            self._resync_event.clear()
            async with curio.TaskGroup(wait=any) as g:
                run_task = await g.spawn(self._run)
                await g.spawn(self._resync_event.wait)
            g.completed.result # re-raise any exception
            if g.completed is run_task:
                break

    async def _run(self):
        versions = {}
        warmup_notifications = []
        seen = set()
        for k, kw in self.kinds:
            if isinstance(k, str):
                k = await self.api.getKind(*k.rsplit('/', 1))
            l = await self.api.get(k._makeLink(**dict(kw)))
            versions[k] = l.metadata.resourceVersion
            for obj in l['items']:
                path = obj._selfLink()
                seen.add(path)
                old = self.get(path)
                self[path] = obj
                warmup_notifications.append((obj, old, obj))

        for path in set(self) - seen:
            obj = self.pop(path)
            warmup_notifications.append((obj, obj, None))

        for args in warmup_notifications:
            await self._maybe_notify(*args)

        await self.warm.set()

        async with self.watcher_factory(self.api) as watcher:
            for k, kw in self.kinds:
                if isinstance(k, str):
                    k = await self.api.getKind(*k.rsplit('/', 1))
                kw = dict(kw, resourceVersion=versions[k])
                await k._watch(watcher, **kw)

            while True:
                try:
                    event, obj = await watcher.__anext__()
                except StopAsyncIteration:
                    break
                except curio.CancelledError:
                    raise
                except Exception as err:
                    self.log.warning(f'watch iteration resulted in error: {err!r}')
                    continue
                path = obj._selfLink()
                if event == 'DELETED':
                    old = obj
                    new = None
                    if path in self:
                        del self[path]
                else:
                    old = self.get(path)
                    new = obj
                    self[path] = new

                await self._maybe_notify(obj, old, new)

    async def _maybe_notify(self, obj, old, new):
        if old is None:
            event = 'ADDED'
        elif new is None:
            event = 'DELETED'
        elif old == new:
            return
        else:
            event = 'MODIFIED'
        await self.notify_subscriptions(event, obj, old, new)

    async def notify_subscriptions(self, event, obj, old, new):
        for ref in list(self.subscriptions):
            func = ref() if isinstance(ref, weakref.ref) else ref
            if not func:
                del self.subscriptions[ref]
                continue
            cond = self.subscriptions[ref]
            try:
                if cond(event, obj):
                    await func(self, event, old, new)
            except Exception:
                self.log.exception('unhandled error in subscription handler')

    def subscribe(self, cond, func, weak=True):
        if weak:
            if isinstance(func, MethodType):
                ref = weakref.WeakMethod(func)
            else:
                ref = weakref.ref(func)
        else:
            ref = func
        self.subscriptions[ref] = cond

    def unsubscribe(self, func):
        for ref in list(self.subscriptions):
            cur_func = ref() if isinstance(ref, weakref.ref) else ref
            if cur_func is None or cur_func == func:
                del self.subscriptions[ref]

    # TODO: DEPRECATED
    async def get_or_update(self, path):
        warnings.warn('get_or_update will be removed soon', DeprecationWarning)
        if path in self:
            obj = self[path]
        else:
            self[path] = obj = await self.api.get(path)
            await self.notify_subscriptions('ADDED', obj, None, obj)
        return obj
