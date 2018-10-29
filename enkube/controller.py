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

from .api import Api, MultiWatch
from .util import close_kernel, sync_wrap
from .enkube import pass_env, PluginLoader

LOG = logging.getLogger(__name__)


class BaseCRDType(type):
    def __init__(cls, name, bases, attrs):
        super(BaseCRDType, cls).__init__(name, bases, attrs)
        if not bases:
            return

        try:
            s = cls.crd['spec']
            v = '{group}/{version}'.format(**s)
            k = s['names']['kind']
        except (AttributeError, KeyError):
            raise
            raise TypeError('CRD subclasses must have valid crd attribute') from None

        for b in bases:
            if isinstance(b, BaseCRDType):
                registry = b.crd_registry
                break
        else:
            raise TypeError(f'{cls.__name__!r} is not a CRD subclass')

        if (v, k) in registry:
            raise TypeError(f'CRD {cls.crd["metadata"]["name"]} already registered')

        registry[v, k] = cls


class _default_descr:
    def __init__(self, default_cb):
        self.default_cb = default_cb

    def __set_name__(self, cls, name):
        self.name = name

    def __get__(self, inst, cls):
        try:
            return cls.__dict__[self.name]
        except KeyError:
            return self.default_cb(cls)


class BaseCRD(metaclass=BaseCRDType):
    crd_registry = {}

    kind = _default_descr(lambda cls: cls.__name__)
    singular = _default_descr(lambda cls: cls.kind.lower())
    plural = _default_descr(lambda cls: f'{cls.singular}s')
    shortNames = _default_descr(lambda cls: [])

    class crd:
        def __get__(self, inst, cls):
            g, v = cls.apiVersion.split('/', 1)
            return {
                'apiVersion': 'apiextensions.k8s.io/v1beta1',
                'kind': 'CustomResourceDefinition',
                'metadata': {
                    'name': f'{cls.plural}.{g}',
                },
                'spec': {
                    'group': g,
                    'version': v,
                    'scope': 'Namespaced' if cls.namespaced else 'Cluster',
                    'names': {
                        'kind': cls.kind,
                        'singular': cls.singular,
                        'plural': cls.plural,
                        'shortNames': cls.shortNames,
                    },
                    #'validation': {
                    #    'openAPIV3Schema': { 'properties': { 'spec': { 'properties': {
                    #    } } } }
                    #},
                },
                'subresources': {
                    'status': {},
                },
            }
    crd = crd()

    @classmethod
    def from_kube(cls, obj, controller=None):
        if cls is BaseCRD:
            k = (obj['apiVersion'], obj['kind'])
            try:
                c = cls.crd_registry[k]
            except KeyError:
                raise TypeError(f'unregistered resource kind: {k}') from None
            for b in c.__mro__:
                if 'from_kube' in b.__dict__:
                    return b.__dict__['from_kube'].__get__(None, c)(obj, controller)
            raise AttributeError(f"{c.__name__!r} object has no attribute 'from_kube'")

        cls.validate(obj)
        self = object.__new__(cls)
        self.controller = controller
        self.kubeObj = obj
        return self

    @classmethod
    def validate(cls, obj):
        if obj.get('apiVersion') != cls.apiVersion:
            raise TypeError('apiVersion mismatch')
        if obj.get('kind') != cls.kind:
            raise TypeError('kind mismatch')
        try:
            md = obj['metadata']
        except KeyError:
            raise TypeError('object has no metadata') from None
        if 'name' not in md:
            raise TypeError('object has no name')
        if not cls.namespaced and md.get('namespace'):
            raise TypeError('namespace defined on cluster-scoped object')
        if cls.namespaced and not md.get('namespace'):
            raise TypeError('namespace not defined on namespaced object')

    @property
    def name(self):
        return self.kubeObj['metadata']['name']


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


class Controller:
    '''Dispatch API events to handlers. Create CRDs.'''
    log = LOG.getChild('Controller')
    crd_check_interval = 60
    watch_interval = 2

    def __init__(self, api, handlers=()):
        self.api = api
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

        shutdown_event = curio.SignalEvent(signal.SIGINT, signal.SIGTERM)
        try:
            async with curio.TaskGroup(wait=any) as g:
                await g.spawn(self._crd_task)
                await g.spawn(self._watch_task)
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

    async def _watch_task(self):
        state = {}
        next_time = 0
        while True:
            next_time = await curio.wake_at(next_time) + self.watch_interval
            watches = [dict(w) for w in {
                tuple(sorted(e.watch_spec.items()))
                for h in self.handlers
                for e in h.event_handlers
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
