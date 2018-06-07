from setuptools import setup

setup(
    name='enkube',
    packages=['enkube'],
    entry_points={
        'console_scripts': [
            'enkube = enkube.enkube:main',
        ],
        'enkube.commands': [
            'render = enkube.enkube:RenderCommand',
            'apply = enkube.enkube:ApplyCommand',
            'diff = enkube.kubediff:DiffCommand',
            'ctl = enkube.kubectl:CtlCommand',
        ],
    }
)
