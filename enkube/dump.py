import click

from .enkube import pass_env
from .util import format_yaml, close_kernel
from .api import Api


@click.command()
@pass_env
def cli(env):
    '''Dump all objects from Kubernetes server.'''
    try:
        with Api(env) as api:
            for obj in api.list():
                click.echo(format_yaml(obj))
    finally:
        close_kernel()
