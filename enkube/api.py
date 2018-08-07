import os
import sys
import json
import tempfile
import subprocess
import threading
from queue import Queue
from textwrap import indent
from urllib.parse import quote, quote_plus
import click
import requests_unixsocket

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


class WatchThread(threading.Thread):
    def __init__(self, api, stop_event, event_queue, **kw):
        super(WatchThread, self).__init__()
        self.api = api
        self.stop_event = stop_event
        self.event_queue = event_queue
        self.kw = kw.copy()
        self.start()

    def run(self):
        try:
            while not self.stop_event.isSet():
                for event, obj in self.api.watch(**self.kw):
                    self.kw['resourceVersion'] = obj['metadata']['resourceVersion']
                    self.event_queue.put((event, obj))
        except Exception:
            self.event_queue.put(sys.exc_info())
        finally:
            self.event_queue.put(self)


class MultiWatch:
    def __init__(self, api, watches=()):
        self.api = api
        self.stop_event = threading.Event()
        self._queue = Queue()
        self._lock = threading.RLock()
        self._threads = set()
        for kw in watches:
            self.watch(**kw)

    def watch(
        self, apiVersion, kind, namespace=None, name=None, resourceVersion=None
    ):
        if self.stop_event.isSet():
            raise RuntimeError(
                f"can't call watch on a stopped {self.__class__.__name__}")
        kw = {
            'apiVersion': apiVersion, 'kind': kind, 'namespace': namespace,
            'name': name, 'resourceVersion': resourceVersion,
        }
        with self._lock:
            t = WatchThread(self.api, self.stop_event, self._queue, **kw)
            self._threads.add(t)

    def stop(self):
        self.stop_event.set()

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            while self._threads:
                event = self._queue.get()
                if isinstance(event, threading.Thread):
                    self._threads.discard(event)
                    continue
                if len(event) == 3:
                    raise event[0].with_traceback(event[1], event[2])
                return event
        raise StopIteration()


class Api:
    def __init__(self, env):
        self.env = env
        self.session = requests_unixsocket.Session()
        self.session.trust_env = False
        self._lock = threading.RLock()
        self._ver_cache = {}
        self._kind_cache = {}
        self._d = tempfile.TemporaryDirectory()
        self._sock = os.path.join(self._d.name, 'proxy.sock')
        self._p = self._popen()

    def _popen(self):
        args = ['proxy', '-u', self._sock]
        p = kubectl_popen(self.env, args, stdout=subprocess.PIPE)
        self._reader = _Reader(p.stdout)
        self._reader.wait()
        return p

    def _construct_url(self, path):
        if self._sock is None:
            raise ApiClosedError()
        if not path.startswith('/'):
            path = f'/{path}'
        sock = quote(self._sock, '')
        return f'http+unix://{sock}{path}'

    def get(self, path):
        url = self._construct_url(path)
        resp = self.session.get(url)
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def stream(self, path):
        url = self._construct_url(path)
        resp = self.session.get(url, stream=True)
        for line in resp.iter_lines():
            try:
                yield json.loads(line)
            except ValueError:
                yield line

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

    def list_apiVersions(self):
        with self._lock:
            if not self._ver_list:
                self._ver_list = self.get('/api')['versions']
                for group in self.get('/apis')['groups']:
                    self._ver_list.extend(
                        v['groupVersion'] for v in group['versions'])
            return self._ver_list

    def get_apiVersion(self, apiVersion):
        with self._lock:
            if apiVersion not in self._ver_cache:
                path = self.build_path(apiVersion)
                v = self._ver_cache[apiVersion] = self.get(path)
                for r in v['resources']:
                    k = (apiVersion, r['kind'])
                    if k in self._kind_cache or '/' in r['name']:
                        continue
                    self._kind_cache[k] = r
            return self._ver_cache[apiVersion]

    def list_resourceKinds(self, apiVersion):
        with self._lock:
            self.get_apiVersion(apiVersion)
            return sorted(k for v, k in self._kind_cache if v == apiVersion)

    def get_resourceKind(self, apiVersion, kind):
        with self._lock:
            self.get_apiVersion(apiVersion)
            return self._kind_cache[apiVersion, kind]

    def build_path(
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
            k = self.get_resourceKind(apiVersion, kind)
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

    def walk(self, last_applied=False):
        for obj in self.list():
            if last_applied:
                obj = self.last_applied(obj)
            yield obj

    def list(
        self, apiVersion=None, kind=None, namespace=None,
        name=None, resourceVersion=None
    ):
        if apiVersion is None:
            versions = self.list_apiVersions()
        else:
            versions = [apiVersion]
        for apiVersion in versions:
            if kind is None:
                kinds = self.list_resourceKinds(apiVersion)
            else:
                kinds = [kind]
            for kind in kinds:
                path = self.build_path(
                    apiVersion, kind, namespace, name, resourceVersion)
                res = self.get(path)
                if res.get('kind', '').endswith('List'):
                    for obj in res.get('items', []):
                        yield dict(obj, apiVersion=apiVersion, kind=kind)
                elif res.get('code') == 404:
                    continue
                else:
                    yield res

    def watch(
        self, apiVersion, kind, namespace=None, name=None, resourceVersion=None
    ):
        path = self.build_path(
            apiVersion, kind, namespace, name, resourceVersion, 'watch')
        for event in self.stream(path):
            yield event['type'], event['object']

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
