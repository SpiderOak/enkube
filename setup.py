from setuptools import setup

setup(
    name='enkube',
    packages=['enkube'],
    entry_points={
        'console_scripts': [
            'enkube = enkube.enkube:cli',
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
        ],
        'enkube.renderers': [
            'jinja2 = enkube.jinja2:Renderer'
        ],
    }
)
