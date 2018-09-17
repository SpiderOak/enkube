import time
import logging
import inspect
import signal

import click
import curio

from .api import Api, MultiWatch
from .util import close_kernel, sync_wrap
from .enkube import pass_env, PluginLoader

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

    async def handle_event(self, controller, event, obj):
        if self.func is None:
            raise TypeError(f'{self.__class__.__name__} object is not bound')
        if self.events is None or event in self.events:
            return await self.func(controller, event, obj)


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


class Controller:
    '''Dispatch API events to handlers. Create CRDs.'''
    log = LOG.getChild('Controller')
    crd_check_interval = 60
    watch_interval = 2

    def __init__(self, api, handlers=()):
        self.api = api
        self.crds = []
        self.event_handlers = []
        for h in handlers:
            self.add_handler(h)

    def add_handler(self, handler):
        self.crds.extend(handler.crds)
        self.event_handlers.extend(handler.event_handlers)

    @sync_wrap
    async def run(self):
        self.log.info('controller starting')
        shutdown_event = curio.SignalEvent(signal.SIGINT, signal.SIGTERM)
        try:
            async with curio.TaskGroup(wait=any) as g:
                await g.spawn(self._crd_task)
                await g.spawn(self._watch_task)
                shutdown_task = await g.spawn(shutdown_event.wait)
        finally:
            del shutdown_event
        if g.completed is shutdown_task:
            self.log.info('controller shutting down')

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
                    await self.api.create(crd)

    async def _watch_task(self):
        state = {}
        next_time = 0
        while True:
            next_time = await curio.wake_at(next_time) + self.watch_interval
            watches = [dict(w) for w in {
                tuple(sorted(h.watch_spec.items()))
                for h in self.event_handlers
            }]
            if not watches:
                continue
            w = await MultiWatch(self.api, watches)
            async with w:
                try:
                    async for event, obj in w:
                        await self._handle(state, event, obj)
                    else:
                        self.log.debug('watch exhausted')
                except curio.TaskCancelled:
                    return
                except Exception as err:
                    self.log.warning(
                        f'error while watching for events: {err}')

    async def _handle(self, state, event, obj):
        path = await self.api.ref_to_path(obj)
        v = obj.get('metadata', {}).get('resourceVersion')

        if path not in state or state[path] != v:
            try:
                await self.handle_event(event, obj)
            except Exception:
                self.log.exception(
                    'unhandled error in event handler')

        if event == 'DELETED':
            state.pop(path, None)
        else:
            state[path] = v

    async def handle_event(self, event, obj):
        for handler in self.event_handlers:
            await handler.handle_event(self, event, obj)


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
            with Api(env) as api:
                kw['api'] = api
                handlers = [_pass_kwargs(kw, plugin) for plugin in handler_plugins]
                Controller(api, handlers).run()
        finally:
            close_kernel()

    for plugin in handler_plugins:
        cli.params.extend(plugin.click_params)

    return cli

cli = cli()
