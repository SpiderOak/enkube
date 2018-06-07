'''Wrap kubectl.
'''
import os
import argparse
import subprocess


def init_parser(parser):
    parser.set_defaults(cmd=cmd_ctl, finalize_opts=finalize_opts_ctl)
    parser.add_argument('args', nargs='*')


def finalize_opts_ctl(opts):
    pass


def cmd_ctl(opts):
    env = os.environ.copy()
    env['KUBECONFIG'] = 'envs/{}/.kubeconfig'.format(opts.env)
    p = subprocess.Popen(['kubectl'] + opts.args, env=env)
    p.wait()
