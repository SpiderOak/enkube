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
import signal
from itertools import chain

import click
import curio

from .util import close_kernel, sync_wrap, SyncContextManager
from .plugins import PluginLoader
from .api.types import CustomResourceDefinition
from .api.client import ApiClient, ApiError
from .api.cache import Cache
from .api.cache_synchronizer import CacheSynchronizer
from .main import pass_env

LOG = logging.getLogger(__name__)


class ControllerType(type):
    def __new__(cls, name, bases, attrs):
        crds = {}
        for b in bases:
            crds.update(getattr(b, 'crds', {}))
        crds.update(dict((c._selfLink(), c) for c in attrs.get('crds', ())))
        attrs['crds'] = crds

        kinds = set()
        for b in bases:
            kinds |= getattr(b, 'kinds', set())
        for k in attrs.get('kinds', ()):
            if isinstance(k, tuple):
                k, kw = k
            else:
                kw = {}
            kinds.add((k, frozenset(kw.items())))
        attrs['kinds'] = kinds

        return super(ControllerType, cls).__new__(cls, name, bases, attrs)


class Controller(metaclass=ControllerType):
    cache_synchronizer_factory = CacheSynchronizer
    params = ()
    crds = ()
    kinds = ()

    def __init__(self, mgr, env, api, cache, **kw):
        self._taskgroup = None
        self._run_task = None
        self.running = curio.Event()
        self.mgr = mgr
        self.env = env
        self.api = api
        self.cache = cache
        if self.crds:
            cache.subscribe(self._crd_filter, self._crd_event)

    async def spawn(self, *args, **kw):
        if not self._taskgroup:
            raise RuntimeError('not running')
        return await self._taskgroup.spawn(*args, **kw)

    async def start(self):
        if self._taskgroup:
            raise RuntimeError('already started')
        self._taskgroup = curio.TaskGroup()
        await self.ensure_crds()
        synchronizers = []
        for k, kw in self.kinds:
            if isinstance(k, str):
                k = await self.api.getKind(*k.rsplit('/', 1))
            sync = self.cache_synchronizer_factory(self.cache, self.api, k, **dict(kw))
            synchronizers.append(sync)
            await self.spawn(sync.run)
        await self.spawn(self._warmup, synchronizers)

    async def _warmup(self, synchronizers):
        for sync in synchronizers:
            await sync.warm.wait()
        self._run_task = await self.spawn(self.run)
        await self.running.set()

    async def join(self):
        await self.running.wait()
        return await self._run_task.join()

    async def run(self):
        pass

    async def close(self):
        if self._taskgroup:
            await self._taskgroup.cancel_remaining()
            await self._taskgroup.join()
            self._taskgroup = None

    def _crd_filter(self, event, old, new):
        return event == 'DELETED' and old._selfLink() in self.crds

    async def _crd_event(self, cache, event, old, new):
        await self.api.ensure_object(self.crds[old._selfLink()])

    async def ensure_object(self, obj):
        try:
            path = obj._selfLink()
        except AttributeError:
            pass
        else:
            if path in self.cache:
                return
        await self.api.ensure_object(obj)

    async def ensure_objects(self, objs):
        for obj in objs:
            await self.ensure_object(obj)

    async def ensure_crds(self):
        await self.ensure_objects(self.crds.values())


class ControllerManager(SyncContextManager):
    log = LOG.getChild('ControllerManager')
    api_client_factory = ApiClient
    cache_factory = Cache

    def __init__(self):
        self.envs = {}
        self.controllers = set()

    @sync_wrap
    async def spawn_controller(self, cls, env, **kw):
        try:
            api, cache, refs = self.envs[env]
        except KeyError:
            api = self.api_client_factory(env)
            cache = self.cache_factory()
            self.envs[env] = (api, cache, 1)
        else:
            self.envs[env] = (api, cache, refs + 1)

        c = cls(self, env, api, cache, **kw)
        self.controllers.add(c)
        await c.start()
        return c

    @sync_wrap
    async def stop_controller(self, c):
        if c not in self.controllers:
            raise RuntimeError('controller has not been spawned by this manager')
        self.log.info(f'stopping {type(c).__name__}')
        try:
            await c.close()
        except curio.CancelledError:
            raise
        except Exception:
            self.log.exception(f'unhandled error in controller close method')
        self.controllers.remove(c)
        api, cache, refs = self.envs[c.env]
        refs -= 1
        if refs <= 0:
            self.log.info('shutting down api for env')
            del self.envs[c.env]
            await api.close()
        else:
            self.envs[c.env] = (api, cache, refs)

    async def _join_controller(self, c):
        try:
            await c.join()
        except curio.CancelledError:
            raise
        except Exception:
            self.log.exception('unhandled exception in controller join method')
        finally:
            await self.stop_controller(c)

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        if typ:
            return
        g = curio.TaskGroup()
        stop_task = await g.spawn(self._watch_signals)
        for c in self.controllers:
            await g.spawn(self._join_controller(c))
        while self.controllers:
            task = await g.next_done()
            if task in (None, stop_task):
                break
        await g.cancel_remaining()

    async def _watch_signals(self):
        async with curio.SignalQueue(signal.SIGTERM, signal.SIGINT) as q:
            sig = await q.get()
            try:
                signame = signal.Signals(sig).name
            except ValueError:
                signame = f'signal {sig}'
            self.log.info(f'caught {signame}')


class ControllerLoader(PluginLoader):
    entrypoint_type = 'enkube.controllers'


def cli():
    controllers = list(chain.from_iterable(ControllerLoader().load_all()))

    @click.command()
    @pass_env
    def cli(env, **kw):
        '''Run a Kubernetes controller.'''
        try:
            with ControllerManager() as mgr:
                for controller in controllers:
                    mgr.spawn_controller(controller, env, **kw)
        finally:
            close_kernel()

    for controller in controllers:
        cli.params.extend(controller.params)

    return cli
