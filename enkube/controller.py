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
    params = ()
    crds = ()
    kinds = ()

    def __init__(self, mgr, api, cache):
        self._taskgroup = curio.TaskGroup()
        self.mgr = mgr
        self.api = api
        self.cache = cache

        if self.crds:
            cache.subscribe(self._crd_filter, self._crd_event)
            cache.add_kind(CustomResourceDefinition)

        for k, kw in self.kinds:
            cache.add_kind(k, **dict(kw))

    async def spawn(self, *args, **kw):
        return await self._taskgroup.spawn(*args, **kw)

    async def close(self):
        await self._taskgroup.cancel_remaining()
        await self._taskgroup.join()

    def _crd_filter(self, event, obj):
        return event == 'DELETED' and obj._selfLink() in self.crds

    async def _crd_event(self, cache, event, old, new):
        try:
            await self.api.create(self.crds[old._selfLink()])
        except ApiError as err:
            if err.resp.status_code != 409:
                raise


class ControllerManager(SyncContextManager):
    log = LOG.getChild('ControllerManager')
    api_client_factory = ApiClient
    cache_factory = Cache

    def __init__(self):
        self.envs = {}
        self.controllers = []
        self._taskgroup = None

    @sync_wrap
    async def spawn_controller(self, cls, env, **kw):
        new_cache = False
        try:
            api, cache = self.envs[env]
        except KeyError:
            api = self.api_client_factory(env)
            cache = self.cache_factory(api)
            self.envs[env] = (api, cache)
            new_cache = True

        c = cls(self, api, cache)
        self.controllers.append(c)

        if self._taskgroup:
            if new_cache:
                await self._taskgroup.spawn(cache.run)
            else:
                await cache.resync()
            await self._taskgroup.spawn(self._run_controller_method, c)

        return c

    @sync_wrap
    async def run(self, watch_signals=False):
        if self._taskgroup:
            raise RuntimeError('controller manager is already running')

        self.log.info('controller manager starting')
        self._taskgroup = curio.TaskGroup()
        try:
            async with self._taskgroup:
                if watch_signals:
                    await self._taskgroup.spawn(self._watch_signals)
                for api, cache in self.envs.values():
                    await self._taskgroup.spawn(cache.run)
                for c in self.controllers:
                    await self._taskgroup.spawn(self._run_controller_method, c)

        finally:
            self.log.info('controller manager shutting down')
            async with curio.TaskGroup() as g:
                for c in self.controllers:
                    await g.spawn(self._run_controller_method, c, 'close')

    async def _run_controller_method(self, c, name='run'):
        method = getattr(c, name, None)
        if not method:
            return
        try:
            await method()
        except curio.CancelledError:
            self.log.info(f'{type(c).__name__}.{name} cancelled')
            raise
        except Exception:
            self.log.exception(f'unhandled error in controller {name} method')
        else:
            self.log.info(f'{type(c).__name__}.{name} finished')

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        if typ:
            return
        await self.run(watch_signals=True)

    async def _watch_signals(self):
        async with curio.SignalQueue(signal.SIGTERM, signal.SIGINT) as q:
            sig = await q.get()
            try:
                signame = signal.Signals(sig).name
            except ValueError:
                signame = f'signal {sig}'
            self.log.info(f'caught {signame}')
            await self._taskgroup.cancel_remaining()


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

cli = cli()
