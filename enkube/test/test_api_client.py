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
import unittest
from unittest.mock import patch, MagicMock, sentinel, call

import curio

from .util import AsyncTestCase, apatch, dummy_coro

from enkube.api.types import *
from enkube.api import client


class TestStreamIter(AsyncTestCase):
    def setUp(self):
        Kind.instances.clear()
        class FooKind(Kind):
            apiVersion = 'v1'
        self.FooKind = FooKind
        self.objects = [
            {'foo': 1},
            {'bar': 2},
            {'baz': 3},
            {"apiVersion": "v1", "kind": "FooKind", "spec": "foospec"},
        ]
        class aiter:
            async def __anext__(it):
                try:
                    return self.chunks.pop(0)
                except IndexError:
                    raise StopAsyncIteration() from None
        async def close_coro():
            pass
        self.resp = MagicMock()
        self.resp.body.__aiter__ = aiter
        self.resp.body.close.side_effect = close_coro
        async def kindify(obj):
            try:
                return Kind.getKind(obj['apiVersion'], obj['kind'])(obj)
            except KeyError:
                return obj
        self.api = MagicMock(**{'_kindify.side_effect': kindify})
        self.si = client.StreamIter(self.api, self.resp)

    def test_iter(self):
        self.chunks = [b'\n'.join(json.dumps(o).encode('utf-8') for o in self.objects)]
        res = list(self.si)
        self.assertEqual(res, self.objects)
        self.assertTrue(isinstance(res[-1], self.FooKind))

    def test_iter_with_trailing_newline(self):
        self.chunks = [b'\n'.join(json.dumps(o).encode('utf-8') for o in self.objects) + b'\n']
        res = list(self.si)
        self.assertEqual(res, self.objects)
        self.assertTrue(isinstance(res[-1], self.FooKind))

    def test_iter_chunks(self):
        s = b'\n'.join(json.dumps(o).encode('utf-8') for o in self.objects)
        n = s.find(b'\n') + 3
        self.chunks = [s[:n], s[n:]]
        res = list(self.si)
        self.assertEqual(res, self.objects)
        self.assertTrue(isinstance(res[-1], self.FooKind))

    def test_context_manager(self):
        with self.si as ret:
            pass
        self.assertIs(ret, self.si)
        self.resp.body.close.assert_called_once_with()


class TestApiClient(AsyncTestCase):
    def setUp(self):
        Kind.instances.clear()
        self.api = client.ApiClient(MagicMock())
        self.api.log = MagicMock()
        self.api.session = MagicMock(**{'close.side_effect': dummy_coro})

    async def test_close(self):
        await self.api.close()
        self.api.session.close.assert_called_once_with()

    def test_context_manager(self):
        with patch.object(self.api, 'close') as c:
            c.side_effect = dummy_coro
            with self.api:
                pass
        c.assert_called_once_with()

    async def test_request(self):
        resp = MagicMock(status_code=200, headers={'content-type': 'application/json'})
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        res = await self.api.request('GET', '/', foo='bar')
        self.assertIs(res, resp.json.return_value)
        self.api.session.request.assert_called_once_with(method='GET', path='/', foo='bar')
        resp.json.assert_called_once_with()

    async def test_request_non_json(self):
        resp = MagicMock(status_code=200)
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        res = await self.api.request('GET', '/', foo='bar')
        self.assertIs(res, resp.text)
        self.api.session.request.assert_called_once_with(method='GET', path='/', foo='bar')
        self.assertFalse(resp.json.called)

    async def test_request_non_2xx_raises_apierror(self):
        resp = MagicMock(status_code=500)
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        with self.assertRaises(client.ApiError) as err:
            await self.api.request('GET', '/', foo='bar')
        self.assertIs(err.exception.resp, resp)

    async def test_request_non_2xx_raises_apierror_with_reason(self):
        resp = MagicMock(status_code=500, headers={'content-type': 'application/json'})
        resp.json.return_value = {'message': sentinel.reason}
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        with self.assertRaises(client.ApiError) as err:
            await self.api.request('GET', '/', foo='bar')
        self.assertIs(err.exception.resp, resp)
        self.assertIs(err.exception.reason, sentinel.reason)

    async def test_request_resource_not_found(self):
        resp = MagicMock(status_code=404)
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        with self.assertRaises(client.ResourceNotFoundError) as err:
            await self.api.request('GET', '/', foo='bar')
        self.assertIs(err.exception.resp, resp)

    @apatch('enkube.api.client.ApiClient.getKind')
    async def test_request_kindtype(self, gk):
        class FooKind(Kind):
            apiVersion = 'v1'
        async def gk_coro(*args, **kw):
            return FooKind
        gk.side_effect = gk_coro
        resp = MagicMock(status_code=200, headers={'content-type': 'application/json'})
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        res = await self.api.request('GET', '/')
        self.assertEqual(res, resp.json.return_value)
        gk.assert_called_once_with('v1', 'FooKind')
        self.assertTrue(isinstance(res, FooKind))

    @apatch('enkube.api.client.ApiClient.getKind')
    async def test_request_kindtype_not_found(self, gk):
        class FooKind(Kind):
            apiVersion = 'v1'
        async def gk_coro(*args, **kw):
            raise client.ResourceKindNotFoundError()
        gk.side_effect = gk_coro
        resp = MagicMock(status_code=200, headers={'content-type': 'application/json'})
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        res = await self.api.request('GET', '/')
        self.assertEqual(res, resp.json.return_value)
        gk.assert_called_once_with('v1', 'FooKind')
        self.assertFalse(isinstance(res, FooKind))

    @apatch('enkube.api.client.ApiClient.getKind')
    async def test_request_kindtype_other_error(self, gk):
        class FooKind(Kind):
            apiVersion = 'v1'
        exc = client.ApiError(resp=MagicMock(status_code=500))
        async def gk_coro(*args, **kw):
            raise exc
        gk.side_effect = gk_coro
        resp = MagicMock(status_code=200, headers={'content-type': 'application/json'})
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        with self.assertRaises(client.ApiError) as err:
            await self.api.request('GET', '/')
        self.assertIs(err.exception, exc)

    @apatch('enkube.api.client.StreamIter')
    async def test_request_stream(self, si):
        resp = MagicMock(status_code=200)
        async def req_coro(*args, **kw):
            return resp
        self.api.session.request.side_effect = req_coro
        res = await self.api.request('GET', '/', foo='bar', stream=True)
        self.assertIs(res, si.return_value)
        self.api.session.request.assert_called_once_with(
            method='GET', path='/', foo='bar', stream=True)
        si.assert_called_once_with(self.api, resp)

    async def test_get_apiversion_v1(self):
        v = {
            'apiVersion': 'v1',
            'kind': 'APIResourceList',
            'groupVersion': 'v1',
            'resources': [
                {
                    'kind': 'FooKind',
                    'name': 'foos',
                    'singularName': '',
                    'namespaced': True,
                    'verbs': ['get', 'list'],
                },
            ],
        }
        async def get_coro(*args, **kw):
            return v
        self.api.get = MagicMock(side_effect=get_coro)
        res = await self.api._get_apiVersion('v1')
        self.api.get.assert_called_once_with('/api/v1')
        self.assertTrue(isinstance(res, APIResourceList))
        self.assertEqual(res, v)
        self.assertIs(res, self.api._apiVersion_cache['/api/v1'])
        self.assertEqual(self.api._kind_cache['v1', 'FooKind'], v['resources'][0])

    async def test_get_apiversion(self):
        v = {
            'apiVersion': 'v1',
            'kind': 'APIResourceList',
            'groupVersion': 'v1',
            'resources': [
                {
                    'kind': 'FooKind',
                    'name': 'foos',
                    'singularName': '',
                    'namespaced': True,
                    'verbs': ['get', 'list'],
                },
            ],
        }
        async def get_coro(*args, **kw):
            return v
        self.api.get = MagicMock(side_effect=get_coro)
        res = await self.api._get_apiVersion('apps/v1')
        self.api.get.assert_called_once_with('/apis/apps/v1')
        self.assertTrue(isinstance(res, APIResourceList))
        self.assertEqual(res, v)
        self.assertIs(res, self.api._apiVersion_cache['/apis/apps/v1'])
        self.assertEqual(self.api._kind_cache['apps/v1', 'FooKind'], v['resources'][0])

    async def test_get_apiversion_cached(self):
        self.api.get = MagicMock()
        self.api._apiVersion_cache['/apis/apps/v1'] = sentinel.result
        res = await self.api._get_apiVersion('apps/v1')
        self.api.get.assert_not_called()
        self.assertIs(res, sentinel.result)

    async def test_get_apiversion_apierror(self):
        async def get_coro(*args, **kw):
            raise client.ApiError(MagicMock(status_code=400))
        self.api.get = MagicMock(side_effect=get_coro)
        with self.assertRaises(client.ApiError) as err:
            await self.api._get_apiVersion('apps/v1')
        self.assertIs(err.exception.reason, None)

    async def test_get_apiversion_not_found(self):
        async def get_coro(*args, **kw):
            raise client.ApiError(MagicMock(status_code=404))
        self.api.get = MagicMock(side_effect=get_coro)
        with self.assertRaises(client.ApiVersionNotFoundError) as err:
            await self.api._get_apiVersion('apps/v1')
        self.assertEqual(err.exception.reason, 'apiVersion not found')

    async def test_get_resourcekind(self):
        v = {
            'apiVersion': 'v1',
            'kind': 'APIResourceList',
            'groupVersion': 'v1',
            'resources': [
                {
                    'kind': 'FooKind',
                    'name': 'foos',
                    'singularName': '',
                    'namespaced': True,
                    'verbs': ['get', 'list'],
                },
            ],
        }
        async def get_coro(*args, **kw):
            return v
        self.api.get = MagicMock(side_effect=get_coro)
        res = await self.api._get_resourceKind('apps/v1', 'FooKind')
        self.api.get.assert_called_once_with('/apis/apps/v1')
        self.assertEqual(res, v['resources'][0])
        self.assertTrue(isinstance(res, APIResource))

    async def test_get_resourcekind_not_found(self):
        v = {
            'apiVersion': 'v1',
            'kind': 'APIResourceList',
            'groupVersion': 'v1',
            'resources': [
                {
                    'kind': 'FooKind',
                    'name': 'foos',
                    'singularName': '',
                    'namespaced': True,
                    'verbs': ['get', 'list'],
                },
            ],
        }
        async def get_coro(*args, **kw):
            return v
        self.api.get = MagicMock(side_effect=get_coro)
        with self.assertRaises(client.ResourceKindNotFoundError) as err:
            await self.api._get_resourceKind('apps/v1', 'BarKind')
        self.assertEqual(err.exception.reason, 'resource kind not found')

    async def test_get_resourcekind_ignores_subresources(self):
        v = {
            'apiVersion': 'v1',
            'kind': 'APIResourceList',
            'groupVersion': 'v1',
            'resources': [
                {
                    'kind': 'FooKind',
                    'name': 'foos/status',
                    'singularName': '',
                    'namespaced': True,
                    'verbs': ['get', 'list'],
                },
            ],
        }
        async def get_coro(*args, **kw):
            return v
        self.api.get = MagicMock(side_effect=get_coro)
        with self.assertRaises(client.ApiError) as err:
            await self.api._get_resourceKind('apps/v1', 'FooKind')
        self.assertEqual(err.exception.reason, 'resource kind not found')

    @apatch('enkube.api.types.Kind.from_apiresource')
    @apatch('enkube.api.client.ApiClient._get_resourceKind')
    async def test_getkind(self, fa, gr):
        async def gr_coro(*args, **kw):
            return sentinel.rk
        gr.side_effect = gr_coro
        fa.return_value = sentinel.fookind
        FooKind = await self.api.getKind('v1', 'FooKind')
        self.assertIs(FooKind, sentinel.fookind)
        fa.assert_called_once_with('v1', sentinel.rk)
        gr.assert_called_once_with('v1', 'FooKind')

    async def test_getkind_local_kind(self):
        class FooKind(Kind):
            apiVersion = 'v1'
        res = await self.api.getKind('v1', 'FooKind')
        self.assertIs(res, FooKind)

    async def test_check_health_ok(self):
        async def get_coro(path):
            return 'ok'
        self.api.get = MagicMock(side_effect=get_coro)
        self.assertTrue(await self.api.check_health())
        self.assertTrue(self.api.healthy.is_set())
        self.api.get.assert_called_once_with('/healthz')

    async def test_check_health_error(self):
        await self.api.healthy.set()
        async def get_coro(path):
            raise client.ApiError()
        self.api.get = MagicMock(side_effect=get_coro)
        self.assertFalse(await self.api.check_health())
        self.assertFalse(self.api.healthy.is_set())

    async def test_check_health_gibberish(self):
        await self.api.healthy.set()
        async def get_coro(path):
            return 'foo'
        self.api.get = MagicMock(side_effect=get_coro)
        self.assertFalse(await self.api.check_health())
        self.assertFalse(self.api.healthy.is_set())

    @apatch('curio.sleep')
    async def test_wait_until_healthy(self, sleep):
        sleep.side_effect = dummy_coro
        responses = [client.ApiError(), 'ok']
        async def get_coro(path):
            return responses.pop(0)
        self.api.get = MagicMock(side_effect=get_coro)
        await self.api.wait_until_healthy()
        self.assertEqual(responses, [])
        sleep.assert_called_once_with(client.ApiClient._health_check_interval)
        self.assertTrue(self.api.healthy.is_set())

    async def test_ensure_object(self):
        self.api.create = MagicMock(side_effect=dummy_coro)
        await self.api.ensure_object(sentinel.obj)
        self.api.create.assert_called_once_with(sentinel.obj)

    async def test_ensure_object_ignores_conflict(self):
        async def conflict_coro(*args, **kw):
            raise client.ApiError(MagicMock(status_code=409))
        self.api.create = MagicMock(side_effect=conflict_coro)
        await self.api.ensure_object(sentinel.obj)
        self.api.create.assert_called_once_with(sentinel.obj)

    async def test_ensure_object_raises_non_conflict_errors(self):
        async def conflict_coro(*args, **kw):
            raise client.ApiError(MagicMock(status_code=500))
        self.api.create = MagicMock(side_effect=conflict_coro)
        with self.assertRaises(client.ApiError):
            await self.api.ensure_object(sentinel.obj)

    async def test_ensure_objects(self):
        self.api.ensure_object = MagicMock(side_effect=dummy_coro)
        await self.api.ensure_objects([sentinel.obj1, sentinel.obj2])
        self.api.ensure_object.assert_has_calls([
            call(sentinel.obj1),
            call(sentinel.obj2),
        ])


if __name__ == '__main__':
    unittest.main()
