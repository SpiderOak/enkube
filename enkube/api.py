import os
import sys
import json
import logging
import tempfile
import asyncio
import signal
from urllib.parse import quote_plus

import click
import aiohttp

from .util import (
    format_json, format_python,
    flatten_kube_lists,
    sync_wrap, sync_wrap_iter, close_event_loop
)
from .enkube import pass_env
from .ctl import kubectl_popen

LOG = logging.getLogger(__name__)


class ApiClosedError(RuntimeError):
    pass


class ProxyManager:
    log = LOG.getChild('ProxyManager')
    _max_retries = 3

    def __init__(self, env, loop=None):
        self.env = env
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.sock = None
        self._proc = None
        self._ready_event = asyncio.Event(loop=loop)
        self._close_event = asyncio.Event(loop=loop)

    def schedule(self):
        self.loop.create_task(self._run())

    async def _run(self):
        with tempfile.TemporaryDirectory() as d:
            self.sock = os.path.join(d, 'proxy.sock')
            try:
                await self._subproc_loop()
            finally:
                self.sock = None

    async def _subproc_loop(self):
        args = [self.env.get_kubectl_path(), 'proxy', '-u', self.sock]
        kw = {
            'loop': self.loop,
            'stdin': None,
            'stdout': asyncio.subprocess.PIPE,
            'stderr': None,
            'preexec_fn': os.setpgrp,
            'env': self.env.get_kubectl_environ(),
        }

        retries = self._max_retries
        while retries > 0 and not self._close_event.is_set():
            self.log.debug(f'running {" ".join(args)}')
            self._proc = p = await asyncio.create_subprocess_exec(*args, **kw)
            self.log.debug(f'kubectl pid {p.pid}')

            try:
                await asyncio.gather(self._read_stdout(p), p.wait())
            except asyncio.CancelledError:
                self.close()
            except Exception as err:
                self.log.debug(
                    f'exception while reading from '
                    f'subprocess (pid {p.pid}): {err!r}'
                )
            finally:
                self._ready_event.clear()
                # If we get here, either p.stdout has been consumed entirely,
                # _close_event is set and we broke out of the loop early, the
                # subprocess exited early, or we ignored an exception.
                # Hopefully in the latter cases, there's nothing more to read,
                # as p.wait() will deadlock if the child process continues to
                # fill the buffer.
                try:
                    p.terminate()
                    await p.wait()
                except ProcessLookupError:
                    pass
                if p.returncode == 0:
                    self.log.debug(
                        f'subprocess (pid {p.pid}) exited cleanly')
                elif p.returncode != -signal.SIGTERM:
                    retries -= 1
                    self.log.warning(
                        f'subprocess (pid {p.pid}) died '
                        f'with return code {p.returncode}'
                    )

    async def _read_stdout(self, p):
        try:
            async for line in p.stdout:
                if self._close_event.is_set():
                    break
                if line.startswith(b'Starting to serve'):
                    self.log.debug(
                        f'subprocess is now listening on {self.sock}')
                    self._ready_event.set()
        finally:
            self._ready_event.clear()

    async def wait(self):
        while not self._close_event.is_set():
            if self._proc is not None:
                # poll for child liveness
                await asyncio.wait([self._proc.wait()], timeout=0)
            await asyncio.wait([
                self._close_event.wait(),
                self._ready_event.wait()
            ], return_when=asyncio.FIRST_COMPLETED)
            if self._close_event.is_set():
                break
            if self._proc is not None and self._ready_event.is_set():
                return
        raise RuntimeError(f'{self!r} is closed')

    def close(self):
        self._ready_event.clear()
        self._close_event.set()


class AsyncClient:
    def __init__(self, proxy, loop=None):
        self.proxy = proxy
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.session = None
        self._sock = None

    async def _ensure_session(self):
        await self.proxy.wait()
        if self.session is not None and self.proxy.sock != self._sock:
            await self.close()
        if self.session is None:
            self._sock = self.proxy.sock
            self.session = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(self._sock, loop=self.loop))

    async def get_async(self, path):
        url = f'http://localhost{path}'
        await self._ensure_session()
        async with self.session.get(url) as resp:
            return await resp.json()

    get = sync_wrap(get_async)

    async def stream_async(self, path):
        url = f'http://localhost{path}'
        await self._ensure_session()
        async with self.session.get(url) as resp:
            async for line in resp.content:
                yield json.loads(line)

    stream = sync_wrap_iter(stream_async)

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None
            self._sock = None


class MultiWatch:
    def __init__(self, api, watches=(), loop=None):
        self.api = api
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.queue = asyncio.Queue(1, loop=loop)
        self._tasks = set()
        for kw in watches:
            self.watch(**kw)

    async def watch_task(self, *args, **kwargs):
        async for event, obj in self.api.watch_async(*args, **kwargs):
            await self.queue.put((event, obj))

    def watch(
        self, apiVersion, kind, namespace=None, name=None, resourceVersion=None
    ):
        t = self.loop.create_task(self.watch_task(
            apiVersion, kind, namespace, name, resourceVersion=resourceVersion
        ))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def __aiter__(self):
        return self

    async def __anext__(self):
        # give canceled tasks a chance to cleanup
        await asyncio.sleep(0)

        if not self._tasks:
            raise StopAsyncIteration
        return await self.queue.get()

    def __iter__(self):
        return self

    __next__ = sync_wrap(__anext__)

    def cancel(self):
        for task in self._tasks:
            task.cancel()


class Api:
    def __init__(self, env, loop=None):
        self.env = env
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop

        self._closed = False
        self._ver_list = []
        self._ver_cache = {}
        self._kind_cache = {}

        self.proxy = ProxyManager(env, loop=loop)
        self.proxy.schedule()
        self.client = AsyncClient(self.proxy, loop=loop)

    async def list_apiVersions_async(self):
        if not self._ver_list:
            self._ver_list = (await self.get_async('/api'))['versions']
            for group in (await self.get_async('/apis'))['groups']:
                self._ver_list.extend(
                    v['groupVersion'] for v in group['versions'])
        return self._ver_list

    list_apiVersions = sync_wrap(list_apiVersions_async)

    async def get_apiVersion_async(self, apiVersion):
        if apiVersion not in self._ver_cache:
            path = await self.build_path_async(apiVersion)
            v = await self.get_async(path)
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

    get_apiVersion = sync_wrap(get_apiVersion_async)

    async def list_resourceKinds_async(self, apiVersion):
        await self.get_apiVersion_async(apiVersion)
        return sorted(k for v, k in self._kind_cache if v == apiVersion)

    list_resourceKinds = sync_wrap(list_resourceKinds_async)

    async def get_resourceKind_async(self, apiVersion, kind):
        await self.get_apiVersion_async(apiVersion)
        try:
            return self._kind_cache[apiVersion, kind]
        except KeyError:
            raise ValueError(f'resource kind {kind} not found on server')

    get_resourceKind = sync_wrap(get_resourceKind_async)

    async def build_path_async(
        self, apiVersion, kind=None, namespace=None, name=None,
        resourceVersion=None, verb=None
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
            k = await self.get_resourceKind_async(apiVersion, kind)
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

        if resourceVersion is not None:
            query['resourceVersion'] = resourceVersion

        if query:
            query = '&'.join(f'{k}={quote_plus(v)}' for k, v in query.items())
            path = f'{path}?{query}'

        return path

    build_path = sync_wrap(build_path_async)

    async def last_applied_async(self, obj):
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
        if not (await self.get_resourceKind_async(
            obj['apiVersion'], obj['kind']
        ))['namespaced']:
            del obj['metadata']['namespace']
        return obj

    last_applied = sync_wrap(last_applied_async)

    async def get_async(self, path, last_applied=False):
        obj = await self.client.get_async(path)
        if last_applied:
            obj = await self.last_applied_async(obj)
        return obj

    get = sync_wrap(get_async)

    async def list_async(
        self, apiVersion=None, kind=None, namespace=None,
        name=None, resourceVersion=None, last_applied=False
    ):
        if apiVersion is None:
            versions = await self.list_apiVersions_async()
        else:
            versions = [apiVersion]
        for apiVersion in versions:
            if kind is None:
                kinds = await self.list_resourceKinds_async(apiVersion)
            else:
                kinds = [kind]
            for k in kinds:
                path = await self.build_path_async(
                    apiVersion, k, namespace, name, resourceVersion)
                res = await self.get_async(path, last_applied=last_applied)
                if res.get('kind', '').endswith('List'):
                    for obj in res.get('items', []):
                        if last_applied:
                            obj = await self.last_applied_async(obj)
                        else:
                            obj = dict(obj, apiVersion=apiVersion, kind=k)
                        yield obj
                elif res.get('code') == 404:
                    continue
                else:
                    yield res

    list = sync_wrap_iter(list_async)

    def walk_async(self, last_applied=False):
        return self.list_async(last_applied=last_applied)

    walk = sync_wrap_iter(walk_async)

    def ref_to_path_async(self, ref):
        md = ref.get('metadata', {})
        return self.build_path_async(
            ref['apiVersion'],
            ref['kind'],
            md.get('namespace'),
            md.get('name')
        )

    ref_to_path = sync_wrap(ref_to_path_async)

    async def get_refs_async(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            path = await self.ref_to_path_async(ref)
            obj = await self.get_async(path, last_applied=last_applied)
            if obj.get('code') == 404:
                continue
            yield obj

    get_refs = sync_wrap_iter(get_refs_async)

    def stream_async(self, path):
        return self.client.stream_async(path)

    stream = sync_wrap_iter(stream_async)

    async def watch_async(
        self, apiVersion, kind, namespace=None, name=None, resourceVersion=None
    ):
        kw = {
            'apiVersion': apiVersion, 'kind': kind,
            'namespace': namespace, 'name': name,
            'resourceVersion': resourceVersion,
            'verb': 'watch'
        }
        while True:
            path = await self.build_path_async(**kw)
            async for event in self.stream_async(path):
                try:
                    kw['resourceVersion'] = \
                        event['object']['metadata']['resourceVersion']
                except KeyError:
                    pass
                yield event['type'], event['object']
                if event['type'] == 'ERROR':
                    return

    watch = sync_wrap_iter(watch_async)

    async def close(self):
        if self._closed:
            return
        try:
            await self.client.close()
            self.proxy.close()
            await asyncio.sleep(0)
        finally:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sync_wrap(self.close)()


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
        close_event_loop()
