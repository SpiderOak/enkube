import os
import subprocess
import click

from .enkube import pass_env


def kubectl_popen(env, args, **kwargs):
    envvars = os.environ.copy()
    envvars['KUBECONFIG'] = 'envs/{}/.kubeconfig'.format(env)
    k = {'env': envvars, 'universal_newlines': True}
    k.update(kwargs)
    return subprocess.Popen(['kubectl'] + args, **k)


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
@pass_env
def cli(env, args):
    '''Wrap kubectl, setting KUBECONFIG according to selected environment.'''
    kubectl_popen(env.env, list(args)).wait()
