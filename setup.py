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

from setuptools import setup

setup(
    name='enkube',
    packages=['enkube'],
    entry_points={
        'console_scripts': [
            'enkube = enkube.main:cli',
        ],
        'enkube.commands': [
            'render = enkube.render:cli',
            'apply = enkube.apply:cli',
            'diff = enkube.diff:cli',
            'ctl = enkube.ctl:cli',
            'api = enkube.api:cli',
            'dump = enkube.dump:cli',
            'fmt = enkube.fmt:cli',
            'exec = enkube.exec:cli',
            'gpg = enkube.gpg:cli',
            'controller = enkube.controller:cli',
        ],
        'enkube.render_plugins': [
            'regex = enkube.render_plugins.util:Regex',
            'yaml = enkube.render_plugins.util:Yaml',
            'render/jinja2 = enkube.render_plugins.jinja2:Renderer',
            'render/helm = enkube.render_plugins.helm:Helm',
            'render/ctl = enkube.render_plugins.ctl:Ctl',
        ],
    },
    test_suite='enkube.test',
)
