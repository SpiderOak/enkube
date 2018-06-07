'''Wrap kubectl.
'''
import os
import argparse
import subprocess


DESCRIPTION = __doc__


class CtlCommand:
    handles_extra_args = True

    def __init__(self, sp):
        parser = sp.add_parser('ctl', help=DESCRIPTION)
        parser.set_defaults(command=self)

    def popen(self, opts, args, **kwargs):
        env = os.environ.copy()
        env['KUBECONFIG'] = 'envs/{}/.kubeconfig'.format(opts.env)
        k = {'env': env, 'universal_newlines': True}
        k.update(kwargs)
        return subprocess.Popen(['kubectl'] + args, **k)

    def main(self, opts):
        self.popen(opts, opts.args).wait()
