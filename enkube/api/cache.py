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
from types import MethodType

LOG = logging.getLogger(__name__)


class Cache:
    log = LOG.getChild('Cache')

    def __init__(self):
        self._d = {}
        self.subscriptions = {}

    def __getitem__(self, k):
        return self._d[k]

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __contains__(self, k):
        return k in self._d

    def __iter__(self):
        return iter(self._d)

    def keys(self):
        return self._d.keys()

    def values(self):
        return self._d.values()

    def items(self):
        return self._d.items()

    async def set(self, key, value):
        if key in self._d:
            old = self._d[key]
            event = 'MODIFIED'
        else:
            old = None
            event = 'ADDED'
        self._d[key] = value
        if old != value:
            await self.notify(event, old, value)

    async def delete(self, key):
        old = self._d.pop(key)
        await self.notify('DELETED', old, None)

    async def notify(self, event, old, new):
        for ref in list(self.subscriptions):
            func = ref() if isinstance(ref, weakref.ref) else ref
            if not func:
                del self.subscriptions[ref]
                continue
            cond = self.subscriptions[ref]
            try:
                if cond(event, old, new):
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
