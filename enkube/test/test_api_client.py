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
from unittest.mock import patch, MagicMock, sentinel

import curio
from curio import subprocess

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

    @apatch('enkube.api.client.UnixSession')
    async def test_ensure_session(self, us):
        self.api._sock = sentinel.sock
        async def ep_coro():
            pass
        with patch.object(self.api, '_ensure_proxy') as ep:
            ep.return_value = ep_coro()
            await self.api._ensure_session()
        self.assertIs(self.api.session, us.return_value)
        ep.assert_called_once_with()
        us.assert_called_once_with(sentinel.sock, connections=self.api._max_conns)

    @apatch('enkube.api.client.UnixSession')
    async def test_ensure_session_with_existing_session(self, us):
        self.api._sock = sentinel.sock
        self.api.session = sentinel.session
        async def ep_coro():
            pass
        with patch.object(self.api, '_ensure_proxy') as ep:
            ep.return_value = ep_coro()
            await self.api._ensure_session()
        self.assertIs(self.api.session, sentinel.session)
        ep.assert_called_once_with()
        self.assertFalse(us.called)

    async def test_ensure_proxy_requires_startup_lock(self):
        with self.assertRaises(AssertionError):
            await self.api._ensure_proxy()

    async def test_read_proxy_stdout_without_proxy(self):
        await self.api._read_proxy_stdout()

    async def test_read_proxy_stdout(self):
        lines = ['foo', 'bar', 'baz']
        class stdout:
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return lines.pop(0)
                except IndexError:
                    raise StopAsyncIteration() from None
        self.api._proxy = p = MagicMock(stdout=stdout())
        with patch.object(self.api, '_poll_proxy') as pp:
            pp.side_effect = dummy_coro
            await self.api._read_proxy_stdout()
        self.assertEqual(lines, [])
        pp.assert_called_once_with()

    async def test_read_proxy_stdout_ignores_exceptions(self):
        lines = ['foo', 'bar', 'baz']
        class stdout:
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    line = lines.pop(0)
                except IndexError:
                    raise StopAsyncIteration() from None
                if line == 'bar':
                    raise RuntimeError()
                return line
        self.api._proxy = p = MagicMock(stdout=stdout())
        with patch.object(self.api, '_poll_proxy') as pp:
            pp.side_effect = dummy_coro
            await self.api._read_proxy_stdout()
        self.assertEqual(lines, ['baz'])
        pp.assert_called_once_with()

    async def test_read_proxy_stdout_ignores_cancellation(self):
        next_calls = []
        class stdout:
            def __aiter__(self):
                return self
            async def __anext__(self):
                next_calls.append(1)
                await curio.Event().wait()
        self.api._proxy = p = MagicMock(stdout=stdout())
        with patch.object(self.api, '_poll_proxy') as pp:
            pp.side_effect = dummy_coro
            task = await curio.spawn(self.api._read_proxy_stdout)
            await task.cancel(blocking=True)
        pp.assert_called_once_with()
        self.assertEqual(next_calls, [1])

    @apatch('curio.spawn')
    async def test_wait_for_proxy(self, spawn):
        spawn.side_effect = dummy_coro
        self.api._startup_lock = MagicMock(**{'locked.return_value': True})
        self.api._proxy = p = MagicMock()
        lines = [b'Starting to serve\n']
        async def readline_coro():
            return lines.pop(0)
        p.stdout.readline.side_effect = readline_coro
        await self.api._wait_for_proxy()
        p.stdout.readline.assert_called_once_with()
        spawn.assert_called_once_with(self.api._read_proxy_stdout, daemon=True)

    async def test_wait_for_proxy_raises_apierror_on_gibberish(self):
        self.api._startup_lock = MagicMock(**{'locked.return_value': True})
        self.api._proxy = p = MagicMock()
        lines = [b'foobar\n']
        async def readline_coro():
            return lines.pop(0)
        p.stdout.readline.side_effect = readline_coro
        with self.assertRaises(client.ApiError) as err:
            await self.api._wait_for_proxy()

    async def test_wait_for_proxy_requires_startup_lock(self):
        with self.assertRaises(AssertionError):
            await self.api._wait_for_proxy()

    @apatch('tempfile.TemporaryDirectory')
    async def test_ensure_proxy(self, td):
        td.return_value.name = 'tempdirname'
        self.api._startup_lock = MagicMock(**{'locked.return_value': True})
        async def pp_coro():
            return False
        with patch.object(
            self.api, '_poll_proxy'
        ) as pp, patch.object(
            self.api, '_wait_for_proxy'
        ) as wp:
            wp.side_effect = dummy_coro
            pp.side_effect = pp_coro
            await self.api._ensure_proxy()

        td.assert_called_once_with()
        self.assertEqual(self.api._sock, os.path.join('tempdirname', 'proxy.sock'))
        self.api.env.spawn_kubectl.assert_called_once_with(
            ['proxy', '-u', self.api._sock],
            stdout=subprocess.PIPE,
            preexec_fn=os.setpgrp,
        )
        self.assertIs(self.api._proxy, self.api.env.spawn_kubectl.return_value)
        wp.assert_called_once_with()

    @apatch('tempfile.TemporaryDirectory')
    async def test_ensure_proxy_with_existing_tmpdir(self, td):
        td.return_value.name = 'tempdirname'
        self.api._startup_lock = MagicMock(**{'locked.return_value': True})
        self.api._tmpdir = MagicMock()
        self.api._sock = sentinel.sock
        async def pp_coro():
            return False
        with patch.object(
            self.api, '_poll_proxy'
        ) as pp, patch.object(
            self.api, '_wait_for_proxy'
        ) as wp:
            wp.side_effect = dummy_coro
            pp.return_value = pp_coro()
            await self.api._ensure_proxy()

        self.assertFalse(td.called)

    @apatch('tempfile.TemporaryDirectory')
    async def test_ensure_proxy_with_existing_proxy(self, td):
        td.return_value.name = 'tempdirname'
        self.api._startup_lock = MagicMock(**{'locked.return_value': True})
        async def pp_coro():
            return True
        with patch.object(self.api, '_poll_proxy') as pp:
            pp.return_value = pp_coro()
            await self.api._ensure_proxy()

        self.assertFalse(td.called)
        self.assertFalse(self.api.env.spawn_kubectl.called)

    async def test_poll_proxy_not_started(self):
        self.assertFalse(await self.api._poll_proxy())

    async def test_poll_proxy_not_found(self):
        self.api._proxy = p = MagicMock(**{'poll.side_effect': ProcessLookupError, 'pid': 31337})
        self.assertFalse(await self.api._poll_proxy())
        p.poll.assert_called_once_with()
        self.api.log.warning.assert_called_once_with('subprocess with pid 31337 not found')
        self.assertIs(self.api._proxy, None)

    async def test_poll_proxy_still_running(self):
        self.api._proxy = p = MagicMock(**{'returncode': None})
        self.assertTrue(await self.api._poll_proxy())
        p.poll.assert_called_once_with()
        self.assertIs(self.api._proxy, p)

    async def test_poll_proxy_exited_cleanly(self):
        self.api._proxy = p = MagicMock(**{'returncode': 0, 'pid': 31337})
        self.assertFalse(await self.api._poll_proxy())
        p.poll.assert_called_once_with()
        self.api.log.debug.assert_called_once_with('subprocess (pid 31337) exited cleanly')
        self.assertIs(self.api._proxy, None)

    async def test_poll_proxy_exited_with_code(self):
        self.api._proxy = p = MagicMock(**{'returncode': -15, 'pid': 31337})
        self.assertFalse(await self.api._poll_proxy())
        p.poll.assert_called_once_with()
        self.api.log.log.assert_called_once_with(
            logging.WARNING, 'subprocess (pid 31337) terminated with return code -15')
        self.assertIs(self.api._proxy, None)

    async def test_poll_proxy_exited_with_code_after_close(self):
        await self.api.close()
        self.api._proxy = p = MagicMock(**{'returncode': -15, 'pid': 31337})
        self.assertFalse(await self.api._poll_proxy())
        p.poll.assert_called_once_with()
        self.api.log.log.assert_called_once_with(
            logging.DEBUG, 'subprocess (pid 31337) terminated with return code -15')
        self.assertIs(self.api._proxy, None)

    async def test_poll_proxy_wait(self):
        async def wait_coro():
            pass
        self.api._proxy = p = MagicMock(
            **{'returncode': 0, 'pid': 31337, 'wait.return_value': wait_coro()})
        self.assertFalse(await self.api._poll_proxy(wait=True))
        self.assertFalse(p.poll.called)
        p.wait.assert_called_once_with()
        self.api.log.debug.assert_called_once_with('subprocess (pid 31337) exited cleanly')
        self.assertIs(self.api._proxy, None)

    async def test_poll_proxy_serializes_calls(self):
        wake_event = curio.Event()
        self.api._proxy = p = MagicMock(**{'wait.side_effect': wake_event.wait})
        p.returncode = 0
        async with curio.TaskGroup() as g:
            await g.spawn(self.api._poll_proxy(wait=True))
            await g.spawn(self.api._poll_proxy(wait=True))
            await curio.sleep(0)
            await wake_event.set()
        p.wait.assert_called_once_with()

    def test_cleanup_tmpdir(self):
        self.api._tmpdir = td = MagicMock()
        self.api._cleanup_tmpdir()
        self.assertIs(self.api._sock, None)
        self.assertIs(self.api._tmpdir, None)
        td.cleanup.assert_called_once_with()

    def test_cleanup_tmpdir_not_set(self):
        self.api._cleanup_tmpdir()
        self.assertIs(self.api._sock, None)
        self.assertIs(self.api._tmpdir, None)

    def test_del(self):
        with patch.object(self.api, '_cleanup_tmpdir') as ct:
            self.api.__del__()
        self.assertTrue(self.api._closed)
        ct.assert_called_once_with()

    def test_del_with_proxy(self):
        self.api._proxy = p = MagicMock()
        with patch.object(self.api, '_cleanup_tmpdir') as ct:
            self.api.__del__()
        self.assertTrue(self.api._closed)
        ct.assert_called_once_with()
        p.terminate.assert_called_once_with()

    def test_del_with_proxy_ignores_processlookuperror(self):
        self.api._proxy = p = MagicMock()
        p.terminate.side_effect = ProcessLookupError
        with patch.object(self.api, '_cleanup_tmpdir') as ct:
            self.api.__del__()
        self.assertTrue(self.api._closed)
        ct.assert_called_once_with()
        p.terminate.assert_called_once_with()

    async def test_close(self):
        with patch.object(self.api, '__del__') as d:
            await self.api.close()
        d.assert_called_once_with()

    async def test_close_with_proxy(self):
        self.api._proxy = MagicMock()
        with patch.object(
            self.api, '__del__'
        ) as d, patch.object(
            self.api, '_poll_proxy'
        ) as pp:
            pp.side_effect = dummy_coro
            await self.api.close()
        d.assert_called_once_with()
        pp.assert_called_once_with(wait=True)

    def test_context_manager(self):
        with patch.object(self.api, 'close') as c:
            c.side_effect = dummy_coro
            with self.api:
                pass
        c.assert_called_once_with()

    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request(self, es):
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=200)
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        res = await self.api.request('GET', '/', foo='bar')
        es.assert_called_once_with()
        self.assertIs(res, resp.json.return_value)
        self.api.session.request.assert_called_once_with(method='GET', path='/', foo='bar')
        resp.json.assert_called_once_with()

    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_non_2xx_raises_apierror(self, es):
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=500)
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        with self.assertRaises(client.ApiError) as err:
            await self.api.request('GET', '/', foo='bar')
        self.assertIs(err.exception.resp, resp)

    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_resource_not_found(self, es):
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=404)
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        with self.assertRaises(client.ResourceNotFoundError) as err:
            await self.api.request('GET', '/', foo='bar')
        self.assertIs(err.exception.resp, resp)

    @apatch('enkube.api.client.ApiClient.getKind')
    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_kindtype(self, gk, es):
        class FooKind(Kind):
            apiVersion = 'v1'
        async def gk_coro(*args, **kw):
            return FooKind
        gk.side_effect = gk_coro
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        res = await self.api.request('GET', '/')
        self.assertEqual(res, resp.json.return_value)
        gk.assert_called_once_with('v1', 'FooKind')
        self.assertTrue(isinstance(res, FooKind))

    @apatch('enkube.api.client.ApiClient.getKind')
    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_kindtype_not_found(self, gk, es):
        class FooKind(Kind):
            apiVersion = 'v1'
        async def gk_coro(*args, **kw):
            raise client.ResourceKindNotFoundError()
        gk.side_effect = gk_coro
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        res = await self.api.request('GET', '/')
        self.assertEqual(res, resp.json.return_value)
        gk.assert_called_once_with('v1', 'FooKind')
        self.assertFalse(isinstance(res, FooKind))

    @apatch('enkube.api.client.ApiClient.getKind')
    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_kindtype_other_error(self, gk, es):
        class FooKind(Kind):
            apiVersion = 'v1'
        exc = client.ApiError(resp=MagicMock(status_code=500))
        async def gk_coro(*args, **kw):
            raise exc
        gk.side_effect = gk_coro
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'apiVersion': 'v1', 'kind': 'FooKind', 'spec': 'foospec'}
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        with self.assertRaises(client.ApiError) as err:
            await self.api.request('GET', '/')
        self.assertIs(err.exception, exc)

    @apatch('enkube.api.client.StreamIter')
    @apatch('enkube.api.client.ApiClient._ensure_session')
    async def test_request_stream(self, si, es):
        es.side_effect = dummy_coro
        resp = MagicMock(status_code=200)
        async def req_coro(*args, **kw):
            return resp
        self.api.session = MagicMock(**{'request.side_effect': req_coro})
        res = await self.api.request('GET', '/', foo='bar', stream=True)
        es.assert_called_once_with()
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


if __name__ == '__main__':
    unittest.main()
