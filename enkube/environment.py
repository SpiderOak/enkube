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
import tempfile

from curio import subprocess

from .kubeconfig import KubeConfig, InclusterConfig

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

    def kubeconfig_path(self):
        for d in self.search_dirs():
            p = os.path.join(d, '.kubeconfig')
            if os.path.exists(p):
                return p

    def get_kubeconfig(self):
        path = os.environ.get('KUBECONFIG') or self.kubeconfig_path()
        if not path:
            return InclusterConfig()
        return KubeConfig.load_from_file(path)

    def get_kubectl_path(self):
        return 'kubectl'

    def get_kubectl_environ(self):
        envvars = os.environ.copy()
        if 'KUBECONFIG' not in envvars:
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


class TempEnvironment(Environment):
    def __init__(self, kubeconfig=None):
        if isinstance(kubeconfig, bytes):
            kubeconfig = kubeconfig.decode('ascii')
        self.kubeconfig = kubeconfig
        self.tempdir = tempfile.TemporaryDirectory()
        super(TempEnvironment, self).__init__()
        if kubeconfig:
            with open(os.path.join(self.envdir, '.kubeconfig'), 'wb') as f:
                f.write(kubeconfig.encode('ascii'))

    def _find_envdir(self):
        return self.tempdir.name

    def __enter__(self):
        return self

    def __exit__(self, typ, exc, tb):
        self.tempdir.cleanup()
