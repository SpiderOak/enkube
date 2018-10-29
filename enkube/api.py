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

import os
import sys
import json
import logging
import tempfile
from urllib.parse import quote_plus
from functools import partialmethod

import click
import curio
from curio import subprocess
from curio.meta import finalize
import asks
asks.init('curio')

from .util import (
    flatten_kube_lists,
    format_json, format_python,
    sync_wrap, AsyncObject,
    get_kernel, close_kernel
)
from .enkube import pass_env

LOG = logging.getLogger(__name__)


class KubeObjProxy:
    def __init__(self, obj):
        self.__dict__['obj'] = obj

    def __getattr__(self, attr):
        if attr in self.obj:
            v = self.obj[attr]
            if isinstance(v, dict):
                return KubeObjProxy(v)
            elif isinstance(v, list):
                return KubeListProxy(v)
            return v
        raise AttributeError(attr)

    def __setattr__(self, attr, value):
        self.obj[attr] = value

    def __delattr__(self, attr):
        del self.obj[attr]


class KubeListProxy:
    def __init__(self, l):
        self.__dict__['l'] = l

    def __len__(self):
        return len(self.l)

    def __getitem__(self, idx):
        v = self.l[idx]
        if isinstance(v, dict):
            return KubeObjProxy(v)
        elif isinstance(v, list):
            return KubeListProxy(v)
        return v

    def __setitem__(self, idx, value):
        self.l[idx] = value

    def __delitem__(self, idx):
        del self.l[idx]

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]


class KubeObjType(type):
    def __init__(cls, name, bases, attrs):
        super(KubeObjType, cls).__init__(name, bases, attrs)
        if name in ('KubeObj', 'KubeCRD'):
            return

        for b in bases:
            if isinstance(b, KubeObjType):
                registry = b._registry
                break
        else:
            raise TypeError(f'{cls.__name__!r} is not a KubeObj subclass')

        try:
            k = (cls.apiVersion, cls.kind)
        except AttributeError:
            raise TypeError('KubeObj subclasses must have apiVersion and kind') from None

        if k in registry:
            raise TypeError(f'KubeObj kind {k} already registered')

        registry[k] = cls


class ValidationError(ValueError):
    pass


class KubeObj(metaclass=KubeObjType):
    _registry = {}

    def __getattr__(self, attr):
        return KubeObjProxy(self.kubeObj).__getattr__(attr)

    @classmethod
    def from_api(cls, obj, api=None):
        k = (obj['apiVersion'], obj['kind'])
        if cls is KubeObj and k in cls._registry:
            cls = cls._registry[k]
            for b in cls.__mro__:
                if 'from_api' in b.__dict__:
                    return b.__dict__['from_api'].__get__(None, cls)(obj, api)
            raise AttributeError(f"{cls.__name__!r} object has no attribute 'from_api'")

        cls.validate(obj)
        self = object.__new__(cls)
        self.api = api
        self.kubeObj = obj
        return self

    @classmethod
    def validate(cls, obj):
        try:
            md = obj['metadata']
        except KeyError:
            raise ValidationError('object has no metadata') from None
        if 'name' not in md:
            raise ValidationError('object has no name')
        if cls in (KubeObj, KubeCRD):
            return
        if obj.get('apiVersion') != cls.apiVersion:
            raise ValidationError('apiVersion mismatch')
        if obj.get('kind') != cls.kind:
            raise ValidationError('kind mismatch')
        if not cls.namespaced and md.get('namespace'):
            raise ValidationError('namespace defined on cluster-scoped object')
        if cls.namespaced and not md.get('namespace'):
            raise ValidationError('namespace not defined on namespaced object')

    @property
    def name(self):
        return self.metadata.name

    @name.setter
    def name(self, value):
        self.metadata.name = value

    @property
    def namespace(self):
        return self.metadata.namespace

    @namespace.setter
    def namespace(self, value):
        self.metadata.namespace = value


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


class KubeCRD(KubeObj):
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


class ProxyManager:
    log = LOG.getChild('ProxyManager')

    def __init__(self, env):
        self.env = env
        self._d = None
        self.sock = None
        self._proc = None
        self._ready = False
        self._closed = False
        self._cond = curio.Condition()
        self._terminating = False
        self._subproc_read_task = None

    def _ensure_sock(self):
        if not self._d:
            self._d = tempfile.TemporaryDirectory()
            self.sock = os.path.join(self._d.name, 'proxy.sock')

    async def _poll_subproc(self, wait=False):
        p = self._proc
        if not p:
            return False

        try:
            if wait:
                await p.wait()
            else:
                p.poll()
        except ProcessLookupError:
            self.log.warning(f'subprocess with pid {p.pid} not found')
        else:
            if p.returncode is None:
                return True
            if p.returncode == 0:
                self.log.debug(f'subprocess (pid {p.pid}) exited cleanly')
            else:
                lvl = logging.DEBUG if self._closed else logging.WARNING
                self.log.log(
                    lvl,
                    f'subprocess (pid {p.pid}) terminated '
                    f'with return code {p.returncode}'
                )

        async with self._cond:
            self._proc = None
            self._ready = False

        if self._subproc_read_task:
            t = self._subproc_read_task
            self._subproc_read_task = None
            await t.cancel()

        return False

    async def _ensure_subproc(self):
        self._ensure_sock()
        if await self._poll_subproc():
            return
        args = [self.env.get_kubectl_path(), 'proxy', '-u', self.sock]
        kw = {
            'stdout': subprocess.PIPE,
            'preexec_fn': os.setpgrp,
            'env': self.env.get_kubectl_environ(),
        }
        self.log.debug(f'running {" ".join(args)}')
        self._proc = p = subprocess.Popen(args, **kw)
        self.log.debug(f'kubectl pid {p.pid}')
        assert self._subproc_read_task is None
        self._subproc_read_task = await curio.spawn(self._subproc_read_loop)

    async def _subproc_read_loop(self):
        try:
            async with self._proc.stdout as stdout:
                async for line in stdout:
                    if line.startswith(b'Starting to serve'):
                        async with self._cond:
                            self._ready = True
                            await self._cond.notify_all()
        except curio.TaskCancelled:
            return
        finally:
            await self._terminate_subproc()

    async def _terminate_subproc(self):
        if self._terminating or not await self._poll_subproc():
            return
        self._terminating = True
        p = self._proc
        self.log.debug(f'terminating subprocess (pid {p.pid})')
        try:
            p.terminate()
        except ProcessLookupError:
            pass
        await self._poll_subproc(wait=True)
        self._terminating = False

    @sync_wrap
    async def close(self):
        if self._closed:
            return
        async with self._cond:
            self._closed = True
            self._ready = False
            await self._cond.notify_all()
        await self._terminate_subproc()

    async def wait(self):
        while not self._closed:
            await self._poll_subproc()
            async with self._cond:
                if self._ready:
                    return
            await self._ensure_subproc()
            async with self._cond:
                await self._cond.wait()
        raise RuntimeError('ProxyManager is closed')

    def __del__(self):
        if self._d:
            self._d.cleanup()
            self._d = None


class UnixSession(asks.Session):
    def __init__(self, sock, **kw):
        super(UnixSession, self).__init__(**kw)
        self._sock = sock

    def _make_url(self):
        return 'http://localhost'

    async def _connect(self, host_loc):
        sock = await curio.open_unix_connection(self._sock)
        sock._active = True
        return sock, '80'

    async def close(self):
        await self.__aexit__(None, None, None)


class ApiError(Exception):
    def __init__(self, resp):
        self.resp = resp


class ApiClient:
    _max_conns = 20

    def __init__(self, proxy):
        self.proxy = proxy
        self.session = None
        self._sock = None

    async def _ensure_session(self):
        await self.proxy.wait()
        if self.session is not None and self.proxy.sock != self._sock:
            await self.close()
        if self.session is None:
            self._sock = self.proxy.sock
            self.session = UnixSession(
                self._sock, connections=self._max_conns)

    @sync_wrap
    async def request(self, method, path, **kw):
        await self._ensure_session()
        resp = await self.session.request(method=method, path=path, **kw)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ApiError(resp)
        return resp.json()

    get = partialmethod(request, 'GET')
    head = partialmethod(request, 'HEAD')
    post = partialmethod(request, 'POST')
    put = partialmethod(request, 'PUT')
    patch = partialmethod(request, 'PATCH')
    delete = partialmethod(request, 'DELETE')
    options = partialmethod(request, 'OPTIONS')

    @sync_wrap
    async def stream(self, path):
        await self._ensure_session()
        resp = await self.session.get(path=path, stream=True)
        if resp.status_code != 200:
            raise ApiError(resp)
        buf = b''
        async with resp.body:
            async for chunk in resp.body:
                buf += chunk
                while True:
                    newline = buf.find(b'\n') + 1
                    if newline > 0:
                        line = buf[:newline]
                        buf = buf[newline:]
                        yield json.loads(line)
                    else:
                        break

    @sync_wrap
    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None
            self._sock = None


class MultiWatch(AsyncObject):
    log = LOG.getChild('MultiWatch')

    async def __init__(self, api, watches=()):
        self.api = api
        self.queue = curio.Queue(1)
        self._group = curio.TaskGroup()
        self._running = 0
        self._closed = False
        for kw in watches:
            await self.watch(**kw)

    async def watch_task(self, *args, **kwargs):
        try:
            async with finalize(self.api.watch(*args, **kwargs)) as stream:
                async for event, obj in stream:
                    await self.queue.put((event, obj))
        except Exception:
            await self.queue.put(sys.exc_info())
        finally:
            self._running -= 1
            if self._closed or self._running <= 0 and not self.queue.full():
                await self.queue.put(self._sentinel)

    @sync_wrap
    async def watch(
        self, apiVersion, kind, namespace=None, name=None, **kwargs
    ):
        if self._closed:
            raise RuntimeError('MultiWatch is closed')
        self._running += 1
        await self._group.spawn(
            self.watch_task, apiVersion, kind, namespace, name, **kwargs)

    def __iter__(self):
        if self._closed:
            raise RuntimeError('MultiWatch is closed')
        return self

    __aiter__ = __iter__

    _sentinel = object()

    async def _next(self):
        while True:
            if self._closed or self._running <= 0:
                return self._sentinel

            item = await self.queue.get()
            if item is self._sentinel:
                return item
            elif len(item) == 3:
                if item[0] is curio.TaskCancelled:
                    continue
                raise item[1] from None

            return item

    def __next__(self):
        item = get_kernel().run(self._next)
        if item is self._sentinel:
            raise StopIteration
        return item

    async def __anext__(self):
        item = await self._next()
        if item is self._sentinel:
            raise StopAsyncIteration
        return item

    @sync_wrap
    async def close(self):
        await self._group.cancel_remaining()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()


class Api:
    log = LOG.getChild('Api')

    def __init__(self, env):
        self.env = env
        self._closed = False
        self._ver_list = []
        self._ver_cache = {}
        self._kind_cache = {}
        self.proxy = ProxyManager(env)
        self.client = ApiClient(self.proxy)

    @sync_wrap
    async def list_apiVersions(self):
        if not self._ver_list:
            self._ver_list = (await self.get('/api'))['versions']
            for group in (await self.get('/apis'))['groups']:
                self._ver_list.extend(
                    v['groupVersion'] for v in group['versions'])
        return self._ver_list

    @sync_wrap
    async def get_apiVersion(self, apiVersion):
        if apiVersion not in self._ver_cache:
            path = await self.build_path(apiVersion)
            v = await self.get(path)
            if v.get('code') == 404:
                raise ValueError(f'apiVersion {apiVersion} not found on server')
            if v.get('kind') != 'APIResourceList':
                raise RuntimeError('unexpected response from server')
            for r in v['resources']:
                k = (apiVersion, r['kind'])
                if k in self._kind_cache or '/' in r['name']:
                    continue
                self._kind_cache[k] = r
            self._ver_cache[apiVersion] = v
        return self._ver_cache[apiVersion]

    @sync_wrap
    async def list_resourceKinds(self, apiVersion):
        await self.get_apiVersion(apiVersion)
        return sorted(k for v, k in self._kind_cache if v == apiVersion)

    @sync_wrap
    async def get_resourceKind(self, apiVersion, kind):
        await self.get_apiVersion(apiVersion)
        try:
            return self._kind_cache[apiVersion, kind]
        except KeyError:
            raise ValueError(f'resource kind {kind} not found on server')

    @sync_wrap
    async def build_path(
        self, apiVersion, kind=None, namespace=None, name=None,
        verb=None, **kwargs
    ):
        query = {}
        components = ['']
        if '/' in apiVersion:
            components.append('apis')
        else:
            components.append('api')
        components.append(apiVersion)

        if verb is not None:
            components.append(verb)

        if namespace is not None:
            components.extend(['namespaces', namespace])

        if kind is not None:
            k = await self.get_resourceKind(apiVersion, kind)
            if namespace and not k['namespaced']:
                raise TypeError(
                    'cannot get namespaced path to cluster-scoped resource')
            if verb and verb not in k['verbs']:
                raise TypeError(f'{verb} not supported on {kind} resource')
            components.append(k['name'])

        if name is not None:
            if kind is not None and (
                namespace is not None or not k['namespaced']
            ):
                components.append(name)
            else:
                query['fieldSelector'] = f'metadata.name={name}'

        path = '/'.join(components)

        query.update(kwargs)
        if query:
            query = '&'.join(f'{k}={quote_plus(v)}' for k, v in query.items())
            path = f'{path}?{query}'

        return path

    @sync_wrap
    async def last_applied(self, obj):
        try:
            obj = json.loads(
                obj['metadata']['annotations'][
                    'kubectl.kubernetes.io/last-applied-configuration'
                ]
            )
        except KeyError:
            return obj
        if not obj['metadata']['annotations']:
            del obj['metadata']['annotations']
        if not (await self.get_resourceKind(
            obj['apiVersion'], obj['kind']
        ))['namespaced']:
            if 'namespace' in obj['metadata']:
                del obj['metadata']['namespace']
        return obj

    @sync_wrap
    async def get(self, path, last_applied=False):
        self.log.debug(f'get {path}')
        obj = await self.client.get(path)
        if last_applied:
            obj = await self.last_applied(obj)
        return obj

    @sync_wrap
    async def list(
        self, apiVersion=None, kind=None, namespace=None,
        name=None, last_applied=False, **kwargs
    ):
        if apiVersion is None:
            versions = await self.list_apiVersions()
        else:
            versions = [apiVersion]
        for apiVersion in versions:
            if kind is None:
                kinds = await self.list_resourceKinds(apiVersion)
            else:
                kinds = [kind]
            for k in kinds:
                if (
                    namespace and not
                    (await self.get_resourceKind(apiVersion, k))['namespaced']
                ):
                    continue
                path = await self.build_path(
                    apiVersion, k, namespace, name, **kwargs)
                try:
                    res = await self.get(path, last_applied=last_applied)
                except ApiError as e:
                    if e.resp.status_code in (404, 405):
                        continue
                    raise
                if res.get('kind', '').endswith('List'):
                    for obj in res.get('items', []):
                        if last_applied:
                            obj = await self.last_applied(obj)
                        else:
                            obj = dict(obj, apiVersion=apiVersion, kind=k)
                        yield obj
                else:
                    yield res

    @sync_wrap
    async def create(self, obj):
        path = await self.ref_to_path(obj)
        if obj.get('metadata', {}).get('name'):
            path = path.rsplit('/', 1)[0]
        self.log.debug(f'post {path}')
        return await self.client.post(path, json=obj)

    @sync_wrap
    async def replace(self, obj):
        path = await self.ref_to_path(obj)
        self.log.debug(f'put {path}')
        return await self.client.put(path, json=obj)

    @sync_wrap
    async def patch(self, ref, patch):
        path = await self.ref_to_path(ref)
        self.log.debug(f'patch {path}')
        return await self.client.patch(path, json=patch, headers={
            'Content-type': 'application/merge-patch+json',
        })

    def ref_to_path(self, ref):
        md = ref.get('metadata', {})
        return self.build_path(
            ref['apiVersion'],
            ref['kind'],
            md.get('namespace'),
            md.get('name')
        )

    @sync_wrap
    async def get_refs(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            path = await self.ref_to_path(ref)
            try:
                obj = await self.get(path, last_applied=last_applied)
            except ApiError as err:
                if err.resp.status_code == 404:
                    continue
                raise
            yield obj

    @sync_wrap
    async def stream(self, path):
        self.log.debug(f'stream {path}')
        async with finalize(self.client.stream(path)) as stream:
            async for event in stream:
                yield event

    @sync_wrap
    async def watch(
        self, apiVersion, kind, namespace=None, name=None, **kwargs
    ):
        kw = {
            'apiVersion': apiVersion, 'kind': kind,
            'namespace': namespace, 'name': name,
            'verb': 'watch'
        }
        path = await self.build_path(**kw)
        async with finalize(self.stream(path)) as stream:
            async for event in stream:
                yield event['type'], event['object']
                if event['type'] == 'ERROR':
                    return

    @sync_wrap
    async def close(self):
        if self._closed:
            return
        try:
            await self.client.close()
            await self.proxy.close()
        finally:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()


def displayhook(value):
    if value is None:
        return
    __builtins__['_'] = None
    formatted = None
    if isinstance(value, dict) or isinstance(value, list):
        try:
            formatted = format_json(value)
        except Exception:
            pass
    if formatted is None:
        formatted = format_python(value)
    click.echo(formatted, nl=False)
    __builtins__['_'] = value


@click.command()
@pass_env
def cli(env):
    '''Start a Python REPL with a Kubernetes API client object.'''
    try:
        import readline
    except Exception:
        pass
    import code

    old_displayhook = sys.displayhook
    sys.displayhook = displayhook
    try:
        with Api(env) as api:
            context = {
                'api': api,
                'MultiWatch': MultiWatch,
            }
            shell = code.InteractiveConsole(context)
            shell.interact()
    finally:
        sys.displayhook = old_displayhook
        close_kernel()
