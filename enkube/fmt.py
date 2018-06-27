import os
import sys
import subprocess
import json
import click

from .util import load_yaml


class JsonnetProcessError(RuntimeError):
    def __init__(self, returncode, message):
        self.message = message
        self.returncode = returncode


class Formatter:
    args = [
        '-n', '2',
        '--string-style', 'd',
        '--comment-style', 'h',
        '--no-sort-imports'
    ]

    def _communicate(self, args, s, outfile):
        if s is None:
            infile = None
            outfile = None
            s = None
        else:
            try:
                s.fileno()
            except Exception:
                infile = subprocess.PIPE
                if outfile is None:
                    outfile = subprocess.PIPE
            else:
                infile = s
                s = None

        a = ['jsonnet', 'fmt']
        a.extend(self.args)
        a.extend(args)

        p = subprocess.Popen(
            a, stdin=infile, stdout=outfile, stderr=subprocess.PIPE)
        out, err = p.communicate(s)
        if p.returncode:
            raise JsonnetProcessError(p.returncode, err.decode('utf-8').strip())
        return out

    def format_path(self, path, inplace=False, outfile=None):
        args = [path]
        if inplace:
            args.insert(0, '-i')
        return self._communicate(args, None, outfile)

    def format(self, s, outfile=None):
        return self._communicate(['-'], s, outfile)


@click.command(
    context_settings={'ignore_unknown_options': True},
    add_help_option=False
)
@click.argument('files', nargs=-1, type=click.Path(exists=True))
@click.option('--inplace', '-i', is_flag=True)
@click.option('--yaml', '-y', 'isyaml', is_flag=True)
def cli(files, inplace, isyaml):
    '''Format jsonnet files according to conventions.'''
    if isyaml and inplace:
        click.secho('--inplace not supported for yaml', fg='red', err=True)
        sys.exit(1)
    if not files:
        files = ['-']

    fmt = Formatter()
    try:
        for f in files:
            if isyaml:
                if f == '-':
                    obj = load_yaml(click.get_text_stream('stdin'))
                else:
                    with open(f, 'r') as f:
                        obj = load_yaml(f)
                fmt.format(
                    json.dumps(obj, indent=2).encode('utf-8'),
                    outfile=click.get_text_stream('stdout')
                )
            else:
                fmt.format_path(f, inplace)

    except JsonnetProcessError as err:
        click.secho(err.message, fg='red', err=True)
        sys.exit(err.returncode)
