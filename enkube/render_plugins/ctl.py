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


import json
import subprocess

from ..ctl import kubectl_popen
from ..util import load_yaml
from io import StringIO

class Ctl:
    def __init__(self, env):
        self.env = env

    def exec(self, dirname, args:'json', stdin) -> 'cb':
        args = json.loads(args)
        # We should probably only do this if the params don't
        # have a -o, but we'd also have to decide whether
        # to load_yaml on the output below.  This should work for
        # most cases of 'getting' something from the cluster.
        args.extend(['-o', 'yaml'])
        with StringIO(stdin) as f:
            p = kubectl_popen(
                self.env,
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = p.communicate(f)
        return {"stdout": load_yaml(stdout), "stderr": stderr}

