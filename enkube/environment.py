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

import os
import json
import logging

from curio import subprocess

from .plugins import RenderPluginLoader

LOG = logging.getLogger(__name__)


class Environment:
    log = LOG.getChild('Environment')

    def __init__(self, name=None, search=()):
        self.name = name
        self.search = list(search)
        self.envdir = None
        self.parents = []
        self.envdir = self._find_envdir()
        self.parents = self._load_parents()
        self.render_plugin_loader = RenderPluginLoader()
        self.renderers = {}

    def _find_envdir(self):
        if not self.name:
            return
        for d in self.search_dirs():
            p = os.path.join(d, 'envs', self.name)
            if os.path.isdir(p):
                self.log.debug(f'using environment directory {p}')
                return p

    def _load_parents(self):
        if not self.envdir:
            return []
        try:
            f = open(os.path.join(self.envdir, 'parent_envs'))
        except FileNotFoundError:
            return []
        with f:
            return [type(self)(n.rstrip('\n'), self.search) for n in f]

    def search_dirs(self, pre=(), post=()):
        for d in pre:
            yield d
        for d in self.search:
            yield d
        if self.envdir:
            yield self.envdir
        for parent in self.parents:
            for d in parent.search_dirs():
                yield d
        yield os.getcwd()
        for d in post:
            yield d

    def load_renderer(self, name):
        if name not in self.renderers:
            renderer = self.render_plugin_loader.load(name)(self)
            if renderer is None:
                return None
            self.renderers[name] = renderer
        return self.renderers[name]

    def kubeconfig_path(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.kubeconfig')
            if os.path.exists(p):
                return p

    def get_kubectl_path(self):
        return 'kubectl'

    def get_kubectl_environ(self):
        envvars = os.environ.copy()
        p = self.kubeconfig_path()
        if p:
            envvars['KUBECONFIG'] = p
        return envvars

    def spawn_kubectl(self, args, **kw):
        args = [self.get_kubectl_path()] + list(args)
        env = self.get_kubectl_environ()
        if 'env' in kw:
            env.update(kw['env'])
        kw['env'] = env
        self.log.debug(f'running {" ".join(args)}')
        p = subprocess.Popen(args, **kw)
        self.log.debug(f'kubectl pid {p.pid}')
        return p

    def gpgsecret_keyid(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.gpgkeyid')
            try:
                return open(p, 'r').read().strip()
            except FileNotFoundError:
                continue

    def to_dict(self):
        return {
            'name': self.name,
            'dir': self.envdir,
            'parents': [p.to_dict() for p in self.parents]
        }

    def to_json(self):
        return json.dumps(self.to_dict())
