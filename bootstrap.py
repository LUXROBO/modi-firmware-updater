import os

from shutil import rmtree
from platform import system
from argparse import ArgumentParser


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

    result = os.system('pyinstaller modi_updater.spec')

    if result != 0:
        exit(1)

    if system() == "Darwin":
        if os.path.exists(f"./dist/MODI Firmware Updater.dmg"):
            os.remove(f"./dist/MODI Firmware Updater.dmg")

        create_dmg_cmd = """create-dmg \
            --volname "MODI Firmware Updater" \
            --volicon "modi_firmware_updater/assets/component/network_module.ico" \
            --window-pos 200 120 \
            --window-size 800 300 \
            --icon-size 100 \
            --icon "MODI Firmware Updater.app" 200 100 \
            --hide-extension "MODI Firmware Updater.app" \
            --app-drop-link 600 100 \
            "./dist/MODI Firmware Updater.dmg" \
            "./dist/MODI Firmware Updater.app"
        """

        result = os.system(create_dmg_cmd)

        if result != 0:
            exit(1)

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument(
        '--mode', type=str, default='install',
        choices=['clean', 'install'],
        help='This is a script which makes your life a lot easier :)'
    )
    args = parser.parse_args()
    mode = args.mode
    mode_func = {
        'clean': make_clean,
        'install': make_executable,
    }.get(mode)
    mode_func()
