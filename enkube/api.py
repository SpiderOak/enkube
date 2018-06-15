import os
import sys
import tempfile
import subprocess
import threading
import json
from textwrap import indent
from urllib.parse import quote
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


class Api:
    def __init__(self, env):
        self.env = env
        self.session = requests_unixsocket.Session()
        self.session.trust_env = False
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
            path = '/{}'.format(path)
        return 'http+unix://{}{}'.format(quote(self._sock, ''), path)

    def get(self, path):
        url = self._construct_url(path)
        resp = self.session.get(url)
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def walk(self):
        for group in self.get('/apis')['groups']:
            v = group['preferredVersion']['groupVersion']
            for resource in self.get('/apis/{}'.format(v))['resources']:
                r = resource['name']
                l = self.get('/apis/{}/{}'.format(v, r))
                if l.get('kind', '').endswith('List'):
                    for obj in l.get('items', []):
                        yield obj

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

    def get_refs(self, refs, last_applied=False):
        for ref in flatten_kube_lists(refs):
            obj = self.get(self.ref_to_path(ref))
            if obj.get('code') == 404:
                continue
            if last_applied:
                obj = self.last_applied(obj)
            yield obj

    def get_apiVersion(self, apiVersion):
        if apiVersion not in self._ver_cache:
            path = self.apiVersion_to_path(apiVersion)
            self._ver_cache[apiVersion] = self.get(path)
        return self._ver_cache[apiVersion]

    def get_resourceKind(self, apiVersion, Kind):
        k = (apiVersion, Kind)
        if k not in self._kind_cache:
            for res in self.get_apiVersion(apiVersion)['resources']:
                if res['kind'] == Kind:
                    self._kind_cache[k] = res
                    break
            else:
                raise ValueError(
                    'Resource kind {} not found on server.'.format(k))
        return self._kind_cache[k]

    def apiVersion_to_path(self, apiVersion):
        if '/' in apiVersion:
            return '/apis/{}'.format(apiVersion)
        return '/api/{}'.format(apiVersion)

    def ref_to_path(self, ref):
        v = ref['apiVersion']
        k = self.get_resourceKind(v, ref['kind'])
        n = ref['metadata']['name']
        components = [self.apiVersion_to_path(v)]
        if k['namespaced']:
            ns = ref['metadata']['namespace']
            components.extend(['namespaces', ns])
        components.extend([k['name'], n])
        return '/'.join(components)

    def close(self):
        self._sock = None
        try:
            self._p.terminate()
            self._p.wait()
        finally:
            self._d.cleanup()
        del self._p, self._d

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
            shell = code.InteractiveConsole({'api': api})
            shell.interact()
    finally:
        sys.displayhook = old_displayhook
