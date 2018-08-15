import os
import json
import logging
import pkg_resources

import click

from .log import init_logging


class PluginLoader:
    @property
    def _entrypoints(self):
        if '_entrypoints' not in self.__dict__:
            self.__dict__['_entrypoints'] = dict(
                (ep.name, ep) for ep in
                pkg_resources.iter_entry_points(self.entrypoint_type)
            )
        return self.__dict__['_entrypoints']

    def list(self):
        return self._entrypoints.keys()

    def load(self, name):
        if '_plugins' not in self.__dict__:
            self.__dict__['_plugins'] = {}
        if name not in self._plugins:
            try:
                self._plugins[name] = self._entrypoints[name].load()
            except KeyError:
                return None
        return self._plugins[name]


class CommandPluginLoader(click.MultiCommand, PluginLoader):
    entrypoint_type = 'enkube.commands'

    def list_commands(self, ctx):
        return self.list()

    def get_command(self, ctx, name):
        return self.load(name)


class RenderPluginLoader(PluginLoader):
    entrypoint_type = 'enkube.renderers'


class Environment:
    def __init__(self, name=None, search=()):
        self.name = name
        self.search = list(search)
        if self.name:
            self.envdir = os.path.join(os.getcwd(), 'envs', self.name)
            self.parents = self._load_parents()
        else:
            self.envdir = None
            self.parents = []
        self.render_plugin_loader = RenderPluginLoader()
        self.renderers = {}

    def _load_parents(self):
        try:
            f = open(os.path.join(self.envdir, 'parent_envs'))
        except FileNotFoundError:
            return []
        with f:
            return [type(self)(n.rstrip('\n')) for n in f]

    def search_dirs(self, pre=()):
        for d in pre:
            yield d
        for d in self.search:
            yield d
        if self.envdir:
            yield self.envdir
        for parent in self.parents:
            for d in parent.search_dirs():
                yield d
        yield os.getcwd()

    def load_renderer(self, name):
        if name not in self.renderers:
            renderer = self.render_plugin_loader.load(name)(self)
            if renderer is None:
                return None
            self.renderers[name] = renderer
        return self.renderers[name]

    def kubeconfig_path(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.kubeconfig')
            if os.path.exists(p):
                return p

    def get_kubectl_path(self):
        return 'kubectl'

    def get_kubectl_environ(self):
        envvars = os.environ.copy()
        p = self.kubeconfig_path()
        if p:
            envvars['KUBECONFIG'] = p
        return envvars

    def gpgsecret_keyid(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.gpgkeyid')
            try:
                return open(p, 'r').read().strip()
            except FileNotFoundError:
                continue

    def to_dict(self):
        return {
            'name': self.name,
            'dir': self.envdir,
            'parents': [p.to_dict() for p in self.parents]
        }

    def to_json(self):
        return json.dumps(self.to_dict())


pass_env = click.make_pass_decorator(Environment, ensure=True)


@click.command(cls=CommandPluginLoader)
@click.option('--env', '-e', envvar='ENKUBE_ENV')
@click.option('--search', '-J', multiple=True, type=click.Path(), envvar='ENKUBE_SEARCH')
@click.option('-v', '--verbose', count=True)
@click.pass_context
def cli(ctx, env, search, verbose):
    '''Manage Kubernetes manifests.'''
    init_logging(logging.WARNING - 10 * verbose)
    ctx.obj = Environment(env, search)


if __name__ == '__main__':
    cli()
