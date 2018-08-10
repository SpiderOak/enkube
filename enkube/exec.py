import sys
from urllib.parse import quote
import click

from .util import flatten_kube_lists, format_json
from .enkube import pass_env
from .api import Api
from .ctl import kubectl_popen


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.option('-l', 'labels', multiple=True)
@click.option('-n', 'namespace')
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
@pass_env
def cli(env, namespace, labels, args):
    '''Convenience wrapper for kubectl exec.'''
    with Api(env) as api:
        for pod in api.list(
            'v1', 'Pod', namespace, labelSelector=','.join(labels)
        ):
            if pod['status']['phase'] == 'Running':
                podname = pod['metadata']['name']
                break
        else:
            click.secho('No running pods found', fg='red')
            sys.exit(1)

    click.secho(f'Found pod {podname}', fg='cyan')
    args = ['-n', namespace, 'exec', podname] + list(args)
    kubectl_popen(env, args).wait()
