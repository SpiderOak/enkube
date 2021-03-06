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
import asks

from ..util import sync_wrap, SyncIter, SyncContextManager, flatten_kube_lists
from ..kubeconfig import KubeConfig
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


class ApiClient(SyncContextManager):
    log = LOG.getChild('ApiClient')
    _max_conns = 20
    _health_check_interval = 30

    def __init__(self, env):
        self.env = env
        self.session = None
        self._apiVersion_cache = {}
        self._kind_cache = {}
        self.healthy = curio.Event()

    @sync_wrap
    async def close(self):
        if self.session:
            await self.session.close()

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

    def _get_session(self):
        ctx, url, headers = self.env.get_kubeconfig().get_connection_info()
        return asks.Session(
            url, headers=headers, ssl_context=ctx, connections=self._max_conns)

    @sync_wrap
    async def request(self, method, path, **kw):
        self.log.debug(f'{method} {path}')
        if not self.session:
            self.session = self._get_session()
        resp = await self.session.request(method=method, path=path, **kw)
        if not (200 <= resp.status_code < 300):
            if resp.status_code == 404:
                raise ResourceNotFoundError(resp)
            if resp.headers.get('content-type', '').split(';', 1)[0] == 'application/json':
                j = resp.json()
                reason = j.get('message')
            else:
                reason = None
            raise ApiError(resp, reason)
        if kw.get('stream'):
            return StreamIter(self, resp)
        if resp.headers.get('content-type', '').split(';', 1)[0] == 'application/json':
            return await self._kindify(resp.json())
        return resp.text

    get = partialmethod(request, 'GET')
    head = partialmethod(request, 'HEAD')
    post = partialmethod(request, 'POST')
    put = partialmethod(request, 'PUT')
    patch = partialmethod(request, 'PATCH')
    delete = partialmethod(request, 'DELETE')
    options = partialmethod(request, 'OPTIONS')

    async def check_health(self):
        try:
            healthy = (await self.get('/healthz') == 'ok')
        except ApiError:
            healthy = False
        if healthy:
            await self.healthy.set()
        else:
            self.healthy.clear()
        return healthy

    async def wait_until_healthy(self):
        while not await self.check_health():
            await curio.sleep(self._health_check_interval)

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
    async def ref_to_path(self, ref):
        if not isinstance(ref, Kind):
            kindCls = await self.getKind(ref['apiVersion'], ref['kind'])
            ref = kindCls(ref)
        return ref._selfLink()

    @sync_wrap
    async def get_refs(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            if not isinstance(ref, Kind):
                try:
                    kindCls = await self.getKind(ref['apiVersion'], ref['kind'])
                except (ApiVersionNotFoundError, ResourceKindNotFoundError):
                    continue
                ref = kindCls(ref)
            path = ref._selfLink()
            try:
                obj = await self.get(path)
            except ResourceNotFoundError:
                continue
            if last_applied:
                obj = await self._last_applied(obj)
            yield obj

    @sync_wrap
    async def create(self, obj):
        if not isinstance(obj, Kind):
            obj = await self._kindify(obj)
        kw = {}
        if 'namespace' in obj.metadata:
            kw['namespace'] = obj.metadata.namespace
        path = obj._makeLink(**kw)
        return await self.post(path, json=obj)

    @sync_wrap
    async def ensure_object(self, obj):
        try:
            await self.create(obj)
        except ApiError as err:
            if err.resp.status_code != 409:
                raise

    @sync_wrap
    async def ensure_objects(self, objs):
        for obj in objs:
            await self.ensure_object(obj)

    @sync_wrap
    async def replace(self, obj):
        if not isinstance(obj, Kind):
            obj = await self._kindify(obj)
        path = obj._selfLink()
        return await self.put(path, json=obj)

    @sync_wrap
    async def build_path(
        self, apiVersion, kind=None, namespace=None, name=None,
        verb=None, **kwargs
    ):
        if kind:
            k = await self.getKind(apiVersion, kind)
            return k._makeLink(namespace=namespace, name=name, verb=verb, **kwargs)
        return f"/api{'' if apiVersion == 'v1' else 's'}/{apiVersion}"
