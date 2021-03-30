import os

from shutil import rmtree
from platform import system
from argparse import ArgumentParser


def symlink_modi_from_pymodi():
    cwd = os.path.dirname(__file__)
    src = os.path.join(cwd, 'backend', 'modi')
    dst = os.path.join(cwd, 'modi')
    if os.path.islink(dst):
        os.unlink(dst)
    os.symlink(src, dst)

def make_clean():
    cwd = os.path.dirname(__file__)
    dirnames = ['__pycache__', 'build', 'dist']
    for d in dirnames:
        dirpath = os.path.join(cwd, d)
        if os.path.isdir(dirpath):
            rmtree(dirpath)

    redundant_specfile = os.path.join(cwd, 'main.spec')
    if os.path.exists(redundant_specfile):
        os.remove(redundant_specfile)

    redundant_logfile = os.path.join(cwd, 'gmfu.log')
    if os.path.exists(redundant_logfile):
        os.remove(redundant_logfile)


def make_executable():
    make_clean()
    platform = system().lower()
    make_executable_for = {
        'windows': make_executable_win,
        'darwin': make_executable_mac,
    }.get(platform)
    if not make_executable_for:
        raise Exception('Not Supported OS')
    make_executable_for()

def make_executable_win():
    os.system('pyinstaller modi_updater.spec')

def make_executable_mac():
    pass


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument(
        '--mode', type=str, default='setup',
        choices=['setup', 'clean', 'install'],
        help='This is a script which makes your life a lot easier :)'
    )
    args = parser.parse_args()
    mode = args.mode
    mode_func = {
        'setup': symlink_modi_from_pymodi,
        'clean': make_clean,
        'install': make_executable,
    }.get(mode)
    mode_func()
