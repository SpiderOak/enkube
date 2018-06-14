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
    path = ['', 'api', 'v1']
    if namespace:
        path.extend(['namespaces', namespace])
    path.append('pods?labelSelector={}'.format(quote(','.join(labels))))
    path = '/'.join(path)
    with Api(env) as api:
        pods = api.get(path)

    for pod in flatten_kube_lists([pods]):
        if pod['status']['phase'] == 'Running':
            podname = pod['metadata']['name']
            break
    else:
        click.secho('No running pods found', fg='red')
        sys.exit(1)

    click.secho('Found pod {}'.format(podname), fg='cyan')
    args = ['-n', namespace, 'exec', podname] + list(args)
    kubectl_popen(env, args).wait()
