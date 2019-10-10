# Copyright 2018 SpiderOak, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import shutil
import tempfile
import subprocess
import click

from .render import pass_renderer, RenderError
from .ctl import kubectl_popen


def cli():
    @click.command()
    @click.option('--dry-run', is_flag=True)
    @pass_renderer
    @click.pass_context
    def cli(ctx, renderer, dry_run):
        '''Render and apply Kubernetes manifests.'''
        args = ['apply', '-f', '-']
        if dry_run:
            args.append('--dry-run=true')

        with tempfile.TemporaryFile('w+', encoding='utf-8') as f:
            renderer.render_to_stream(f)
            f.seek(0)
            p = kubectl_popen(renderer.env, args, stdin=subprocess.PIPE)
            try:
                shutil.copyfileobj(f, p.stdin)
            finally:
                p.stdin.close()
                ctx.exit(p.wait())

    return cli
