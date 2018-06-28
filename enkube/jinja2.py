from jinja2 import Environment


class Renderer:
    def __init__(self, env):
        self.env = env
        self.jinja_env = Environment()

    def render(self, template, context):
        template = self.jinja_env.from_string(template)
        return template.render(**context)
