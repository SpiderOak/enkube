import click

from .enkube import pass_env
from .util import format_json
from .api import Api


@click.command()
@pass_env
def cli(env):
    '''Dump all objects from Kubernetes server.'''
    with Api(env) as api:
        for obj in api.walk():
            formatted = format_json(obj)
            click.secho('---', fg='blue')
            click.echo(formatted)
