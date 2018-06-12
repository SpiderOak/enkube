import json
from pygments import highlight, lexers, formatters
import click

from .enkube import pass_env
from .api import Api


def walk_api(api):
    for group in api.get('/apis')['groups']:
        v = group['preferredVersion']['groupVersion']
        for resource in api.get('/apis/{}'.format(v))['resources']:
            r = resource['name']
            l = api.get('/apis/{}/{}'.format(v, r))
            if l.get('kind', '').endswith('List'):
                for obj in l.get('items', []):
                    yield obj


@click.command()
@pass_env
def cli(env):
    '''Dump all objects from Kubernetes server.'''
    with Api(env) as api:
        for obj in walk_api(api):
            formatted = highlight(json.dumps(
                obj, sort_keys=True, indent=2
            ), lexers.JsonLexer(), formatters.TerminalFormatter())
            click.secho('---', fg='blue')
            click.echo(formatted)
