import shutil
import tempfile
import subprocess
import click

from .render import pass_renderer, RenderError
from .ctl import kubectl_popen


@click.command()
@click.option('--dry-run', is_flag=True)
@pass_renderer
def cli(renderer, dry_run):
    '''Render and apply Kubernetes manifests.'''
    args = ['apply', '-f', '-']
    if dry_run:
        args.append('--dry-run=true')

    with tempfile.TemporaryFile('w+', encoding='utf-8') as f:
        renderer.render_to_stream(f)
        f.seek(0)
        p = kubectl_popen(renderer.env.env, args, stdin=subprocess.PIPE)
        try:
            shutil.copyfileobj(f, p.stdin)
        finally:
            p.stdin.close()
            p.wait()
