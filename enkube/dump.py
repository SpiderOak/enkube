import click

from .enkube import pass_env
from .util import format_yaml
from .api import Api


@click.command()
@pass_env
def cli(env):
    '''Dump all objects from Kubernetes server.'''
    with Api(env) as api:
        for obj in api.walk():
            click.echo(format_yaml(obj))
