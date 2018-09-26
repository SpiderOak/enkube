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
from jinja2 import Environment, BaseLoader, TemplateNotFound


class Loader(BaseLoader):
    def __init__(self, env):
        self.env = env

    def get_source(self, jinja_env, template):
        for d in self.env.search_dirs():
            p = os.path.join(d, template)
            if not os.path.exists(p):
                continue
            mtime = os.path.getmtime(p)
            with open(p, 'r') as f:
                source = f.read()
            return source, p, lambda: mtime == os.path.getmtime(p)
        raise TemplateNotFound(template)


class Renderer:
    def __init__(self, env):
        self.env = env
        self.jinja_env = Environment(loader=Loader(env))

    def render(self, template, context):
        template = self.jinja_env.get_template(template)
        return template.render(**context)

    def render_string(self, template_string, context):
        template = self.jinja_env.from_string(template_string)
        return template.render(**context)
