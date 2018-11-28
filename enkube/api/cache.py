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
import weakref

import curio

from ..util import sync_wrap


LOG = logging.getLogger(__name__)


class Cache:
    log = LOG.getChild('Cache')

    def __init__(self, watcher):
        self.watcher = watcher
        self.state = {}
        self.subscriptions = {}

    @sync_wrap
    async def run(self):
        while True:
            try:
                event, obj = await self.watcher.__anext__()
            except (StopAsyncIteration, curio.CancelledError):
                break
            except Exception as err:
                self.log.warn(f'watch iteration resulted in error: {err}')
                continue
            path = obj._selfLink()
            if event == 'DELETED':
                old = obj
                new = None
                if path in self.state:
                    del self.state[path]
            else:
                old = self.state.get(path)
                new = obj
                self.state[path] = new
                if old == new:
                    continue

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
        ref = weakref.ref(func) if weak else func
        self.subscriptions[ref] = cond

    def unsubscribe(self, func):
        for ref in list(self.subscriptions):
            cur_func = ref() if isinstance(ref, weakref.ref) else ref
            if cur_func is None or cur_func == func:
                del self.subscriptions[ref]
