import os
import tempfile
import subprocess
import threading
import json
from urllib.parse import quote
import click
import requests_unixsocket
from pygments import highlight, lexers, formatters

from .enkube import pass_env
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
        self._d = tempfile.TemporaryDirectory()
        self._sock = os.path.join(self._d.name, 'proxy.sock')
        self._p = self._popen()

    def _popen(self):
        args = ['proxy', '-u', self._sock]
        p = kubectl_popen(self.env.env, args, stdout=subprocess.PIPE)
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


class ConsoleApi(Api):
    def get(self, path):
        obj = super(ConsoleApi, self).get(path)
        if isinstance(obj, dict):
            formatted = highlight(json.dumps(
                obj, sort_keys=True, indent=2
            ), lexers.JsonLexer(), formatters.TerminalFormatter())
        else:
            formatted = obj
        click.echo(formatted)


@click.command()
@pass_env
def cli(env):
    '''Start a Python REPL with a Kubernetes API client object.'''
    try:
        import readline
    except Exception:
        pass
    import code

    with ConsoleApi(env) as api:
        shell = code.InteractiveConsole({'api': api})
        shell.interact()
