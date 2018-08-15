import time
import logging
import asyncio

import click

from .api import Api, MultiWatch
from .util import async_gen_next_timeout, close_event_loop
from .enkube import pass_env

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

    def handle_event(self, controller, event, obj):
        if self.func is None:
            raise TypeError(f'{self.__class__.__name__} object is not bound')
        if self.events is None or event in self.events:
            return self.func(controller, event, obj)


class ControllerType(type):
    def __new__(cls, clsname, supers, attrs):
        handlers = []
        for k in attrs:
            v = attrs[k]
            if isinstance(v, EventHandler):
                handlers.append(v)
                attrs[k] = v.func
        attrs['handlers'] = handlers
        return type.__new__(cls, clsname, supers, attrs)


class BaseController(metaclass=ControllerType):
    '''Dispatch API events to handlers. Optionally create CRDs.

    Subclass this to create a controller. Example event handler:

    @EventHandler('apps/v1', 'Deployment')
    def deployment_event(self, event, obj):
        print(f"{event} deployment {obj['metadata']['name']}")

    '''

    log = LOG.getChild('BaseController')
    watch_timeout = 60
    crds = ()

    def __init__(self, api):
        self.api = api

    def ensure_crds(self):
        if not self.crds:
            self.log.debug('no CRDs to create')
            return
        current = set(crd['metadata']['name'] for crd in self.api.list(
            'apiextensions.k8s.io/v1beta1', 'CustomResourceDefinition'))
        for crd in self.crds:
            if crd['metadata']['name'] not in current:
                self.log.info(f"creating CRD {crd['metadata']['name']}")
                self.api.create(crd)

    def run(self):
        state = {}
        while True:
            self.ensure_crds()
            watches = [h.watch_spec for h in self.handlers]
            if not watches:
                time.sleep(self.watch_timeout)
                continue
            w = MultiWatch(self.api, watches)
            try:
                self._watch_loop(state, w)
            finally:
                w.cancel()

    def _watch_loop(self, state, w):
        while True:
            try:
                event, obj = async_gen_next_timeout(
                    w, self.watch_timeout)
            except asyncio.TimeoutError:
                self.log.debug('timed out waiting for event')
                return
            except StopIteration:
                self.log.debug('watch exhausted')
                return

            path = self.api.ref_to_path(obj)
            v = obj.get('metadata', {}).get('resourceVersion')

            if path not in state or state[path] != v:
                try:
                    self.handle_event(event, obj)
                except Exception:
                    self.log.exception(
                        'unhandled error in event handler')

            if event == 'DELETED':
                state.pop(path, None)
            else:
                state[path] = v

    def handle_event(self, event, obj):
        for handler in self.handlers:
            handler.handle_event(self, event, obj)


@click.command()
@pass_env
def cli(env):
    '''Run a Kubernetes controller.'''
    try:
        with Api(env) as api:
            BaseController(api).run()
    finally:
        close_event_loop()
