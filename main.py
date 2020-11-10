import os
import sys
import argparse

from PyQt5 import QtWidgets

from modi.util.gui_firmware_updater import Form


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode', type=str, default='installer',
        choices=['native','installer'],
        help='What mode should the application run on?'
    )
    args = parser.parse_args()
    mode = args.mode
    installer = mode == 'installer'
    print("Running MODI Firmware Updater")
    app = QtWidgets.QApplication(sys.argv)
    w = Form(installer=installer)
    sys.exit(app.exec())
    print("Terminating MODI Firmware Updater")

