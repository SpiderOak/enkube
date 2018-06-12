import os
import tempfile
import subprocess
from urllib.parse import quote
import click
import requests_unixsocket

from .enkube import pass_env
from .ctl import kubectl_popen


class ApiClosedError(RuntimeError):
    pass


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
        return kubectl_popen(self.env.env, args, stdout=subprocess.DEVNULL)

    def _construct_url(self, path):
        if self._sock is None:
            raise ApiClosedError()
        if not path.startswith('/'):
            path = '/{}'.format(path)
        return 'http+unix://{}{}'.format(quote(self._sock, ''), path)

    def get(self, path):
        url = self._construct_url(path)
        return self.session.get(url).json()

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


@click.command()
@pass_env
def cli(env):
    '''Start a Python REPL with a Kubernetes API client object.'''
    try:
        import readline
    except Exception:
        pass
    import code

    with Api(env) as api:
        shell = code.InteractiveConsole({'api': api})
        shell.interact()
