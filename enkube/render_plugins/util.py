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

import re
import yaml


class Regex:
    def __init__(self, env):
        pass

    def capture(self, dirname, rx, s) -> 'cb':
        m = re.search(rx, s)
        if m:
            return m.groupdict()
        return {}


class Yaml:
    def __init__(self, env):
        pass

    def parseObj(self, dirname, s) -> 'cb':
        return yaml.safe_load(s)

    def parseDoc(self, dirname, s) -> 'cb':
        return list(yaml.safe_load_all(s))
