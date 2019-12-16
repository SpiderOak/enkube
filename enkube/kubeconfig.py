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
import atexit
import tempfile
import ssl
from base64 import b64decode
import yaml

from .api.types import KubeDict, required, list_of

# https://github.com/kubernetes/client-go/blob/master/tools/clientcmd/api/v1/types.go


SERVICE_TOKEN_FILENAME = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_CERT_FILENAME = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_tempfiles = {}

def _get_tempfile_for_data(data):
    global _tempfiles
    if not _tempfiles:
        atexit.register(_cleanup_tempfiles)
    if data in _tempfiles:
        return _tempfiles[data]
    h, n = tempfile.mkstemp()
    with open(h, 'wb') as f:
        f.write(b64decode(data))
    _tempfiles[data] = n
    return n

def _cleanup_tempfiles():
    global _tempfiles
    for n in _tempfiles.values():
        try:
            os.remove(n)
        except OSError:
            pass
    _tempfiles = {}


class Cluster(KubeDict):
    server: required(str)
    insecure_skip_tls_verify: bool
    certificate_authority: str
    certificate_authority_data: str
    _json_attr_map = {
        'insecure_skip_tls_verify': 'insecure-skip-tls-verify',
        'certificate_authority': 'certificate-authority',
        'certificate_authority_data': 'certificate-authority-data',
    }

    def get_ssl_context(self):
        if 'certificate-authority-data' in self:
            cadata = b64decode(self['certificate-authority-data']).decode('ascii')
        else:
            cadata = None
        ctx = ssl.create_default_context(
            cafile=self.get('certificate-authority'), cadata=cadata)
        if self.get('insecure-skip-tls-verify', False):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


class NamedCluster(KubeDict):
    name: required(str)
    cluster: required(Cluster)


class User(KubeDict):
    token: str
    tokenFile: str
    username: str
    password: str
    client_certificate: str
    client_certificate_data: str
    client_key: str
    client_key_data: str
    as_: str
    as_groups: list_of(str)
    as_user_extra: dict
    _json_attr_map = {
        'client_certificate': 'client-certificate',
        'client_certificate_data': 'client-certificate-data',
        'client_key': 'client-key',
        'client_key_data': 'client-key-data',
        'as_': 'as',
        'as_groups': 'as-groups',
        'as_user_extra': 'as-user-extra',
    }

    def load_client_certificate(self, ctx):
        if 'client-certificate-data' in self:
            cert_file = _get_tempfile_for_data(self['client-certificate-data'])
        else:
            cert_file = self.get('client-certificate')
        if cert_file:
            if 'client-key-data' in self:
                key_file = _get_tempfile_for_data(self['client-key-data'])
            else:
                key_file = self.get('client-key')
            ctx.load_cert_chain(cert_file, key_file)

    def get_token(self):
        if 'token' in self:
            return self['token']
        if 'tokenFile' in self:
            with open(self['tokenFile'], 'r') as f:
                return f.read()

    def get_auth_headers(self):
        headers = {}
        token = self.get_token()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if 'username' in self:
            basic = b64encode(
                '{username}:{password}'.format(**self).encode('utf-8')
            ).decode('ascii')
            headers['Authorization'] = f'Basic {basic}'
        if 'as' in self:
            headers['Impersonate-User'] = self['as']
            if 'as-groups' in self:
                headers['Impersonate-Group'] = ','.join(self['as-groups'])
            if 'as-user-extra' in self:
                for k, v in self['as-user-extra'].items():
                    headers[f'Impersonate-User-{k}'] = ','.join(v)
        return headers


class NamedUser(KubeDict):
    name: required(str)
    user: required(User)


class Context(KubeDict):
    cluster: required(str)
    user: required(str)
    namespace: str


class NamedContext(KubeDict):
    name: required(str)
    context: required(Context)


class KubeConfig(KubeDict):
    apiVersion: required(str) = 'v1'
    kind: required(str) = 'Config'
    preferences: required(dict) = {}
    clusters: required(list_of(NamedCluster)) = []
    users: required(list_of(NamedUser)) = []
    contexts: required(list_of(NamedContext)) = []
    current_context: str
    _json_attr_map = {
        'current_context': 'current-context',
    }

    @classmethod
    def load_from_file(cls, path):
        with open(path, 'rb') as f:
            return cls(yaml.safe_load(f))

    def get_context(self, name=None):
        name = self.get('current-context', name)
        for context in self.contexts:
            if context.name == name:
                return context.context
        raise KeyError(name)

    def get_cluster(self, name=None):
        if name is None:
            name = self.get_context().cluster
        for cluster in self.clusters:
            if cluster.name == name:
                return cluster.cluster
        raise KeyError(name)

    def get_user(self, name=None):
        if name is None:
            name = self.get_context().user
        for user in self.users:
            if user.name == name:
                return user.user
        raise KeyError(name)

    def get_ssl_context(self, context_name=None):
        kctx = self.get_context(context_name)
        ctx = self.get_cluster(kctx.cluster).get_ssl_context()
        self.get_user(kctx.user).load_client_certificate(ctx)
        return ctx

    def get_connection_info(self, context_name=None):
        kctx = self.get_context(context_name)
        cluster = self.get_cluster(kctx.cluster)
        ctx = cluster.get_ssl_context()
        user = self.get_user(kctx.user)
        user.load_client_certificate(ctx)
        return ctx, cluster.server, user.get_auth_headers()


class InclusterConfig:
    def __init__(self, token_file=SERVICE_TOKEN_FILENAME, cert_file=SERVICE_CERT_FILENAME):
        self.token_file = token_file
        self.cert_file = cert_file

    def get_connection_info(self):
        cluster = Cluster({'certificate-authority': self.cert_file})
        user = User(tokenFile=self.token_file)
        host = os.environ['KUBERNETES_SERVICE_HOST']
        if ':' in host or '%' in host:
            host = f'[{host}]'
        port = os.environ['KUBERNETES_SERVICE_PORT']
        url = f'https://{host}:{port}'
        return cluster.get_ssl_context(), url, user.get_auth_headers()
