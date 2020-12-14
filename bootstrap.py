import os
import shutil
import pathlib
import argparse


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
            shutil.rmtree(dirpath)

    redundant_specfile = os.path.join(cwd, 'main.spec')
    if os.path.exists(redundant_specfile):
        os.remove(redundant_specfile)

def make_executable():
    make_clean()
    os.system('pyinstaller modi_updater.spec')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode', type=str, default='setup',
        choices=['setup', 'clean', 'install'],
        help='Script which makes your life a lot easier XD'
    )
    args = parser.parse_args()
    mode = args.mode
    mode_func = {
        'setup': symlink_modi_from_pymodi,
        'clean': make_clean,
        'install': make_executable,
    }.get(mode)
    mode_func()

