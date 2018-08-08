import os
import sys
import json
import tempfile
import subprocess
import threading
import asyncio
import functools
from urllib.parse import quote_plus

import click
import aiohttp

from .enkube import pass_env
from .util import format_json, flatten_kube_lists
from .ctl import kubectl_popen


class ApiClosedError(RuntimeError):
    pass


class _Reader(threading.Thread):
    def __init__(self, stream):
        super(_Reader, self).__init__()
        self.stream = stream
        self.event = threading.Event()
        self.start()

    def run(self):
        try:
            for line in self.stream:
                if line.startswith('Starting to serve'):
                    self.event.set()
        except Exception:
            pass

    def wait(self):
        return self.event.wait()


def sync_wrap(coro_func):
    @functools.wraps(coro_func)
    def wrapper(self, *args, **kwargs):
        try:
            return self.loop.run_until_complete(coro_func(self, *args, **kwargs))
        except StopAsyncIteration:
            raise StopIteration
    return wrapper


def sync_wrap_iter(async_gen_func):
    @functools.wraps(async_gen_func)
    def wrapped(self, *args, **kwargs):
        it = async_gen_func(self, *args, **kwargs)
        while True:
            try:
                yield self.loop.run_until_complete(it.__anext__())
            except StopAsyncIteration:
                break
    return wrapped


class AsyncClient:
    def __init__(self, sock):
        self.sock = sock
        self.loop = asyncio.new_event_loop()
        self.session = aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(sock, loop=self.loop))

    async def get_async(self, path):
        url = f'http://localhost{path}'
        async with self.session.get(url) as resp:
            return await resp.json()

    get = sync_wrap(get_async)

    async def stream_async(self, path):
        url = f'http://localhost{path}'
        async with self.session.get(url) as resp:
            async for line in resp.content:
                yield json.loads(line)

    stream = sync_wrap_iter(stream_async)

    def close(self):
        self.loop.run_until_complete(self.session.close())
        # lifted with minor modifications from asyncio.runners.run()
        # https://github.com/python/cpython/blob/3.7/Lib/asyncio/runners.py
        loop = self.loop
        try:
            to_cancel = asyncio.all_tasks(loop)
            if to_cancel:
                for task in to_cancel:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(
                    *to_cancel, loop=loop, return_exceptions=True))
                for task in to_cancel:
                    if task.cancelled():
                        continue
                    if task.exception() is not None:
                        loop.call_exception_handler({
                            'message': (
                                f'unhandled exception during '
                                f'{self.__class__.__name__}.close()',
                            ),
                            'exception': task.exception(),
                            'task': task
                        })
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class MultiWatch:
    def __init__(self, api, watches=()):
        self.api = api
        self.queue = asyncio.Queue(1, loop=self.loop)
        self._tasks = set()
        for kw in watches:
            self.watch(**kw)

    @property
    def loop(self):
        return self.api.loop

    async def watch_task(self, *args, **kwargs):
        kwargs['verb'] = 'watch'
        while True:
            path = await self.api.build_path_async(*args, **kwargs)
            async for event in self.api.stream_async(path):
                try:
                    kwargs['resourceVersion'] = \
                        event['object']['metadata']['resourceVersion']
                except KeyError:
                    pass
                await self.queue.put((event['type'], event['object']))
                if event['type'] == 'ERROR':
                    return

    def watch(
        self, apiVersion, kind, namespace=None, name=None, resourceVersion=None
    ):
        t = self.api.loop.create_task(self.watch_task(
            apiVersion, kind, namespace, name, resourceVersion=resourceVersion
        ))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)  # give canceled tasks a chance to cleanup
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
    def __init__(self, env):
        self.env = env
        self._lock = threading.RLock()
        self._ver_cache = {}
        self._kind_cache = {}
        self._d = tempfile.TemporaryDirectory()
        self._sock = os.path.join(self._d.name, 'proxy.sock')
        self._p = self._popen()
        self.client = AsyncClient(self._sock)

    @property
    def loop(self):
        return self.client.loop

    def _popen(self):
        args = ['proxy', '-u', self._sock]
        p = kubectl_popen(
            self.env, args, stdout=subprocess.PIPE, preexec_fn=os.setpgrp)
        self._reader = _Reader(p.stdout)
        self._reader.wait()
        return p

    def get_async(self, path):
        return self.client.get_async(path)

    get = sync_wrap(get_async)

    def stream_async(self, path):
        return self.client.stream_async(path)

    stream = sync_wrap_iter(stream_async)

    async def list_apiVersions_async(self):
        with self._lock:
            if not self._ver_list:
                self._ver_list = await self.client.get_async('/api')['versions']
                for group in (await self.client.get_async('/apis'))['groups']:
                    self._ver_list.extend(
                        v['groupVersion'] for v in group['versions'])
            return self._ver_list

    list_apiVersions = sync_wrap(list_apiVersions_async)

    async def get_apiVersion_async(self, apiVersion):
        with self._lock:
            if apiVersion not in self._ver_cache:
                path = await self.build_path_async(apiVersion)
                v = self._ver_cache[apiVersion] = await self.client.get_async(path)
                for r in v['resources']:
                    k = (apiVersion, r['kind'])
                    if k in self._kind_cache or '/' in r['name']:
                        continue
                    self._kind_cache[k] = r
            return self._ver_cache[apiVersion]

    get_apiVersion = sync_wrap(get_apiVersion_async)

    async def list_resourceKinds_async(self, apiVersion):
        with self._lock:
            await self.get_apiVersion_async(apiVersion)
            return sorted(k for v, k in self._kind_cache if v == apiVersion)

    list_resourceKinds = sync_wrap(list_resourceKinds_async)

    async def get_resourceKind_async(self, apiVersion, kind):
        with self._lock:
            await self.get_apiVersion_async(apiVersion)
            return self._kind_cache[apiVersion, kind]

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
            if kind is not None and namespace is not None:
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

    async def list_async(
        self, apiVersion=None, kind=None, namespace=None,
        name=None, resourceVersion=None
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
            for kind in kinds:
                path = await self.build_path_async(
                    apiVersion, kind, namespace, name, resourceVersion)
                res = await self.client.get_async(path)
                if res.get('kind', '').endswith('List'):
                    for obj in res.get('items', []):
                        yield dict(obj, apiVersion=apiVersion, kind=kind)
                elif res.get('code') == 404:
                    continue
                else:
                    yield res

    list = sync_wrap_iter(list_async)

    def last_applied(self, obj):
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
        if not self.get_resourceKind(
            obj['apiVersion'], obj['kind']
        )['namespaced']:
            del obj['metadata']['namespace']
        return obj

    def walk(self, last_applied=False):
        for obj in self.list():
            if last_applied:
                obj = self.last_applied(obj)
            yield obj

    def ref_to_path(self, ref):
        md = ref.get('metadata', {})
        return self.build_path(
            ref['apiVersion'],
            ref['kind'],
            md.get('namespace'),
            md.get('name')
        )

    def get_refs(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            obj = self.get(self.ref_to_path(ref))
            if obj.get('code') == 404:
                continue
            if last_applied:
                obj = self.last_applied(obj)
            yield obj

    def close(self):
        self.client.close()
        self._sock = None
        try:
            self._p.terminate()
            self._p.wait()
            self._reader.join()
        finally:
            self._d.cleanup()
        del self._p, self._d, self._reader

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def displayhook(value):
    if value is None:
        return
    __builtins__['_'] = None
    if isinstance(value, dict) or isinstance(value, list):
        formatted = format_json(value)
        click.echo(formatted, nl=False)
    else:
        formatted = repr(value)
        click.echo(formatted)
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
