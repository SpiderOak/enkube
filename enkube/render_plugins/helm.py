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
import tempfile
import subprocess

from ..util import load_yaml


class Helm:
    def __init__(self, env):
        self.env = env

    def template(self, dirname, chart, args:'json', values:'json') -> 'cb':
        args = json.loads(args)
        values = json.loads(values)

        for d in self.env.search_dirs(post=[dirname]):
            abs_chart = os.path.join(d, chart)
            if os.path.exists(abs_chart):
                break
        else:
            raise RuntimeError('chart not found')

        with tempfile.TemporaryDirectory() as d:
            values_file = os.path.join(d, 'values.json')
            with open(values_file, 'w') as f:
                json.dump(values, f)
            with subprocess.Popen(
                ['helm', 'template', abs_chart, '--values', values_file] + args,
                stdout=subprocess.PIPE,
            ) as p:
                return load_yaml(p.stdout, load_doc=True)
