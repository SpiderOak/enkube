import os
import pkg_resources
import click


class PluginLoaderCli(click.MultiCommand):
    @property
    def _entrypoints(self):
        if '_entrypoints' not in self.__dict__:
            self.__dict__['_entrypoints'] = dict(
                (ep.name, ep) for ep in
                pkg_resources.iter_entry_points('enkube.commands')
            )
        return self.__dict__['_entrypoints']

    def list_commands(self, ctx):
        return self._entrypoints.keys()

    def get_command(self, ctx, name):
        if '_plugins' not in self.__dict__:
            self.__dict__['_plugins'] = {}
        if name not in self._plugins:
            try:
                self._plugins[name] = self._entrypoints[name].load()
            except KeyError:
                return None
        return self._plugins[name]


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

    def kubeconfig_path(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.kubeconfig')
            if os.path.exists(p):
                return p

    def gpgsecret_keyid(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.gpgkeyid')
            try:
                return open(p, 'r').read().strip()
            except FileNotFoundError:
                continue

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


pass_env = click.make_pass_decorator(Environment, ensure=True)


@click.command(cls=PluginLoaderCli)
@click.option('--env', '-e', envvar='ENKUBE_ENV')
@click.option('--search', '-J', multiple=True, type=click.Path(), envvar='ENKUBE_SEARCH')
@click.pass_context
def cli(ctx, env, search):
    '''Manage Kubernetes manifests.'''
    ctx.obj = Environment(env, search)


if __name__ == '__main__':
    cli()
