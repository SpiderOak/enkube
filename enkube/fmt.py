import os
import subprocess
import click


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
def cli(args):
    '''Format jsonnet files according to conventions.'''
    subprocess.run([
        'jsonnet', 'fmt', '-i', '-n', '2',
        '--string-style', 'd',
        '--comment-style', 'h',
        '--no-sort-imports'
    ] + list(args))
