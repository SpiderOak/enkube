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

import time
import logging
import inspect
import signal

import click
import curio

from .util import close_kernel, sync_wrap
from .plugins import PluginLoader
from .api.client import ApiClient
from .api.watcher import Watcher
from .api.cache import Cache
from .main import pass_env

LOG = logging.getLogger(__name__)


class EventHandler:
    def __init__(
        self, apiVersion=None, kind=None, namespace=None, name=None,
        events=None, func=None, **kwargs
    ):
        self.watch_spec = {
            'apiVersion': apiVersion,
            'kind': kind,
            'namespace': namespace,
            'name': name,
        }
        self.watch_spec.update(kwargs)
        self.events = events
        self.func = func

    def __call__(self, func):
        return type(self)(events=self.events, func=func, **self.watch_spec)

    async def handle_event(self, handler, controller, event, obj):
        if self.func is None:
            raise TypeError(f'{self.__class__.__name__} object is not bound')
        if self.events is None or event in self.events:
            return await self.func(handler, controller, event, obj)


class HandlerType(type):
    def __new__(cls, clsname, supers, attrs):
        event_handlers = []
        for k in attrs:
            v = attrs[k]
            if isinstance(v, EventHandler):
                event_handlers.append(v)
                attrs[k] = v.func
        attrs['event_handlers'] = event_handlers
        return type.__new__(cls, clsname, supers, attrs)


class BaseHandler(metaclass=HandlerType):
    '''Handler base class.

    A handler is an object that has a list of CRDs to create, and event
    handlers that the controller will dispatch events to.

    Subclass this to create a handler. Example event handler:

    @EventHandler('apps/v1', 'Deployment')
    async def deployment_event(self, event, obj):
        print(f"{event} deployment {obj['metadata']['name']}")

    '''
    crds = ()
    click_params = ()

    async def spawn_controller_tasks(self, g):
        pass


class Controller:
    '''Dispatch API events to handlers. Create CRDs.'''
    log = LOG.getChild('Controller')
    crd_check_interval = 60
    watch_interval = 2

    def __init__(self, api, handlers=()):
        self.api = api
        self.watcher = Watcher(api)
        self.cache = Cache(self.watcher)
        self.cache.subscribe(lambda event, obj: True, self.handle_event)
        self.crds = []
        self.handlers = []
        for h in handlers:
            self.add_handler(h)

    def add_handler(self, handler):
        self.crds.extend(handler.crds)
        self.handlers.append(handler)

    @sync_wrap
    async def run(self):
        self.log.info('controller starting')
        for handler in self.handlers:
            if hasattr(handler, 'start'):
                try:
                    await handler.start()
                except Exception:
                    self.log.exception('error starting handler')

        seen = set()
        for h in self.handlers:
            for e in h.event_handlers:
                k = await self.api.getKind(e.watch_spec['apiVersion'], e.watch_spec['kind'])
                path = k._makeLink(e.watch_spec['name'], e.watch_spec['namespace'], 'watch')
                if path in seen:
                    continue
                seen.add(path)
                await self.watcher.watch(path)

        shutdown_event = curio.SignalEvent(signal.SIGINT, signal.SIGTERM)
        try:
            async with curio.TaskGroup(wait=any) as g:
                await g.spawn(self.cache.run)
                await g.spawn(self._crd_task)
                for handler in self.handlers:
                    await handler.spawn_controller_tasks(g)
                shutdown_task = await g.spawn(shutdown_event.wait)

        except Exception:
            self.log.exception('error in controller')

        finally:
            del shutdown_event
            self.log.info('controller shutting down')
            for handler in self.handlers:
                if hasattr(handler, 'close'):
                    try:
                        await handler.close()
                    except Exception:
                        self.log.exception('error closing handler')

    async def _crd_task(self):
        first = True
        while True:
            if not first:
                await curio.sleep(self.crd_check_interval)
            first = False
            if not self.crds:
                self.log.debug('no CRDs to create')
                continue
            current = {crd['metadata']['name'] async for crd in self.api.list(
                'apiextensions.k8s.io/v1beta1', 'CustomResourceDefinition')}
            for crd in self.crds:
                if crd['metadata']['name'] not in current:
                    self.log.info(f"creating CRD {crd['metadata']['name']}")
                    try:
                        await self.api.create(crd)
                    except Exception as err:
                        self.log.error(f'error creating CRD: {err}')

    async def handle_event(self, cache, event, old, new):
        obj = old if event == 'DELETE' else new
        for handler in self.handlers:
            for event_handler in handler.event_handlers:
                await event_handler.handle_event(handler, self, event, obj)


class HandlerPluginLoader(PluginLoader):
    entrypoint_type = 'enkube.controller.handlers'


def _pass_kwargs(kw, plugin):
    kw = dict(
        (k, kw[k]) for k in inspect.signature(plugin).parameters if k in kw)
    return plugin(**kw)


def cli():
    handler_plugins = HandlerPluginLoader().load_all()

    @click.command()
    @pass_env
    def cli(env, **kw):
        '''Run a Kubernetes controller.'''

        #from curio.monitor import Monitor
        #from .util import get_kernel
        #k = get_kernel()
        #m = Monitor(k)
        #k._call_at_shutdown(m.close)

        try:
            with ApiClient(env) as api:
                kw['api'] = api
                handlers = [_pass_kwargs(kw, plugin) for plugin in handler_plugins]
                Controller(api, handlers).run()
        finally:
            close_kernel()

    for plugin in handler_plugins:
        cli.params.extend(plugin.click_params)

    return cli

cli = cli()
