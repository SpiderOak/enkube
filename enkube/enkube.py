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
            self._plugins[name] = self._entrypoints[name].load()
        return self._plugins[name]


class Environment:
    def __init__(self, env=None, search=()):
        self.env = env
        self.search = list(search)

        cwd = os.getcwd()
        self.search.append(cwd)
        if self.env:
            self.search.append(os.path.join(cwd, 'envs', self.env))


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
