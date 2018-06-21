import os
import sys
import subprocess
import tempfile
import yaml
import json
import click


def fmt(args, f=None):
    pargs = ['jsonnet', 'fmt']
    if f is None:
        pargs.append('-i')
    pargs.extend(['-n', '2',
        '--string-style', 'd',
        '--comment-style', 'h',
        '--no-sort-imports'
    ])
    pargs.extend(args)
    subprocess.run(pargs, stdin=f)


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
def cli(args):
    '''Format jsonnet files according to conventions.'''
    if args:
        fmt(args)
    else:
        s = yaml.safe_load(sys.stdin)
        with tempfile.TemporaryFile('w+') as f:
            json.dump(s, f)
            f.seek(0)
            fmt(['-'], f)
