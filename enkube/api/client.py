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
import json
import logging
import tempfile
from functools import partialmethod

import curio
from curio import subprocess

import asks
asks.init('curio')

from ..util import sync_wrap, SyncIter, SyncContextManager, flatten_kube_lists
from .types import APIResourceList, Kind

LOG = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, resp=None, reason=None):
        self.resp = resp
        self.reason = reason


class ApiVersionNotFoundError(ApiError):
    pass


class ResourceKindNotFoundError(ApiError):
    pass


class ResourceNotFoundError(ApiError):
    pass


class StreamIter(SyncIter, SyncContextManager):
    def __init__(self, api, resp):
        self.api = api
        self.resp = resp
        self.buf = b''
        self.it = resp.body.__aiter__()

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            newline = self.buf.find(b'\n') + 1
            if newline > 0:
                line = self.buf[:newline]
                self.buf = self.buf[newline:]
                return await self.api._kindify(json.loads(line))
            try:
                self.buf += await self.it.__anext__()
            except StopAsyncIteration:
                if not self.buf:
                    raise
                line = self.buf
                self.buf = b''
                return await self.api._kindify(json.loads(line))

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        await self.resp.body.close()


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


class ApiClient(SyncContextManager):
    log = LOG.getChild('ApiClient')
    _max_conns = 20

    def __init__(self, env):
        self.env = env
        self.session = None
        self._tmpdir = None
        self._sock = None
        self._proxy = None
        self._closed = False
        self._startup_lock = curio.Lock()
        self._poll_lock = curio.Lock()
        self._apiVersion_cache = {}
        self._kind_cache = {}

    async def _poll_proxy(self, wait=False):
        async with self._poll_lock:
            if not self._proxy:
                return False
            p = self._proxy
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
            self._proxy = None
            return False

    async def _read_proxy_stdout(self):
        if not self._proxy:
            return
        try:
            async for _ in self._proxy.stdout:
                pass
        except Exception:
            pass
        finally:
            await self._poll_proxy()

    async def _wait_for_proxy(self):
        assert self._startup_lock.locked()
        line = await self._proxy.stdout.readline()
        if not line.startswith(b'Starting to serve'):
            raise ApiError(reason='Got gibberish from kubectl proxy')
        await curio.spawn(self._read_proxy_stdout, daemon=True)

    async def _ensure_proxy(self):
        assert self._startup_lock.locked()
        if await self._poll_proxy():
            return

        if not self._tmpdir:
            self._tmpdir = tempfile.TemporaryDirectory()
            self._sock = os.path.join(self._tmpdir.name, 'proxy.sock')

        self._proxy = self.env.spawn_kubectl(
            ['proxy', '-u', self._sock],
            stdout=subprocess.PIPE,
            preexec_fn=os.setpgrp,
        )

        await self._wait_for_proxy()

    async def _ensure_session(self):
        async with self._startup_lock:
            await self._ensure_proxy()
            if self.session is None:
                self.session = UnixSession(
                    self._sock, connections=self._max_conns)

    def _cleanup_tmpdir(self):
        self._sock = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None

    def __del__(self):
        if self._proxy:
            try:
                self._proxy.terminate()
            except ProcessLookupError:
                pass
        self._closed = True
        self._cleanup_tmpdir()

    @sync_wrap
    async def close(self):
        self.__del__()
        if self._proxy:
            await self._poll_proxy(wait=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        await self.close()

    async def _kindify(self, obj):
        if 'apiVersion' in obj and 'kind' in obj:
            try:
                kindCls = await self.getKind(obj['apiVersion'], obj['kind'])
            except (ApiVersionNotFoundError, ResourceKindNotFoundError):
                pass
            else:
                obj = kindCls(obj)
        return obj

    @sync_wrap
    async def request(self, method, path, **kw):
        await self._ensure_session()
        resp = await self.session.request(method=method, path=path, **kw)
        if not (200 <= resp.status_code < 300):
            if resp.status_code == 404:
                raise ResourceNotFoundError(resp)
            raise ApiError(resp)
        if kw.get('stream'):
            return StreamIter(self, resp)
        return await self._kindify(resp.json())

    get = partialmethod(request, 'GET')
    head = partialmethod(request, 'HEAD')
    post = partialmethod(request, 'POST')
    put = partialmethod(request, 'PUT')
    patch = partialmethod(request, 'PATCH')
    delete = partialmethod(request, 'DELETE')
    options = partialmethod(request, 'OPTIONS')

    async def _get_apiVersion(self, apiVersion):
        path = f"/api{'' if apiVersion == 'v1' else 's'}/{apiVersion}"
        if path not in self._apiVersion_cache:
            try:
                res = APIResourceList(await self.get(path))
            except ApiError as err:
                if err.resp.status_code == 404:
                    raise ApiVersionNotFoundError(
                        resp=err.resp, reason='apiVersion not found'
                    ) from None
                raise
            res._validate()
            self._apiVersion_cache[path] = res
            self._kind_cache.update(dict(
                ((apiVersion, r['kind']), r) for r in res.resources
                if '/' not in r['name']
            ))
        return self._apiVersion_cache[path]

    async def _get_resourceKind(self, apiVersion, kind):
        await self._get_apiVersion(apiVersion)
        try:
            return self._kind_cache[apiVersion, kind]
        except KeyError:
            raise ResourceKindNotFoundError(reason='resource kind not found') from None

    @sync_wrap
    async def getKind(self, apiVersion, kind):
        try:
            return Kind.getKind(apiVersion, kind)
        except KeyError:
            res = await self._get_resourceKind(apiVersion, kind)
            return Kind.from_apiresource(apiVersion, res)

    # LEGACY STUFF - need to update (and add tests)

    async def _last_applied(self, obj):
        try:
            obj = await self._kindify(json.loads(
                obj['metadata']['annotations'][
                    'kubectl.kubernetes.io/last-applied-configuration'
                ]
            ))
        except KeyError:
            return obj
        if not obj['metadata']['annotations']:
            del obj['metadata']['annotations']
        if not obj._namespaced:
            if 'namespace' in obj['metadata']:
                del obj['metadata']['namespace']
        return obj

    @sync_wrap
    async def list(
        self, apiVersion=None, kind=None, namespace=None,
        name=None, last_applied=False, **kwargs
    ):
        if apiVersion is None:
            versions = (await self.get('/api'))['versions']
            versions.extend(
                v['groupVersion']
                for g in (await self.get('/apis'))['groups']
                for v in g['versions']
            )
        else:
            versions = [apiVersion]
        for apiVersion in versions:
            if kind is None:
                await self._get_apiVersion(apiVersion)
                kinds = sorted(k for v, k in self._kind_cache if v == apiVersion)
            else:
                kinds = [kind]
            for k in kinds:
                kindCls = await self.getKind(apiVersion, k)
                if namespace and not kindCls._namespaced:
                    continue
                path = kindCls._makeLink(name, namespace, **kwargs)
                try:
                    res = await self.get(path)
                except ApiError as e:
                    if e.resp.status_code in (404, 405):
                        continue
                    raise
                if res.get('kind', '').endswith('List'):
                    for obj in res.get('items', []):
                        obj['apiVersion'] = apiVersion
                        obj['kind'] = kind
                        if last_applied:
                            obj = await self._last_applied(obj)
                        else:
                            obj = await self._kindify(obj)
                        yield obj
                else:
                    if last_applied:
                        res = await self._last_applied(res)
                    yield res

    @sync_wrap
    async def get_refs(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            if not isinstance(ref, Kind):
                kindCls = await self.getKind(ref['apiVersion'], ref['kind'])
                ref = kindCls(ref)
            path = ref._selfLink()
            try:
                obj = await self.get(path)
            except ResourceNotFoundError:
                continue
            if last_applied:
                obj = await self._last_applied(obj)
            yield obj
