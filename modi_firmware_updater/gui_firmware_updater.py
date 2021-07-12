import logging
import os
import pathlib
import sys
import threading as th
import time
import traceback as tb
import io
import urllib.request as ur
import zipfile
import shutil
from base64 import b64decode, b64encode
from io import open
from os import path
from urllib.error import URLError

from PyQt5 import QtCore, QtGui, QtWidgets, uic
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QDialog

from modi_firmware_updater.core.esp32_updater import ESP32FirmwareUpdater
from modi_firmware_updater.core.esp32_updater import ESP32FirmwareMultiUpdater
from modi_firmware_updater.core.stm32_updater import STM32FirmwareUpdater


class StdoutRedirect(QObject):
    printOccur = pyqtSignal(str, str, name="print")

    def __init__(self):
        QObject.__init__(self, None)
        self.daemon = True
        self.sysstdout = sys.stdout.write
        self.sysstderr = sys.stderr.write
        self.logger = None

    def stop(self):
        sys.stdout.write = self.sysstdout
        sys.stderr.write = self.sysstderr

    def start(self):
        sys.stdout.write = self.write
        sys.stderr.write = lambda msg: self.write(msg, color="red")

    def write(self, s, color="black"):
        sys.stdout.flush()
        self.printOccur.emit(s, color)
        if self.logger and not self.__is_redundant_line(s):
            self.logger.info(s)

    @staticmethod
    def __is_redundant_line(line):
        return (
            line.startswith("\rUpdating") or
            line.startswith("\rFirmware Upload: [") or
            len(line) < 3
        )


class PopupMessageBox(QtWidgets.QMessageBox):
    def __init__(self, main_window, level):
        QtWidgets.QMessageBox.__init__(self)
        self.window = main_window
        self.setSizeGripEnabled(True)
        self.setWindowTitle("System Message")

        def error_popup():
            self.setIcon(self.Icon.Warning)
            self.setText("ERROR")

        def warning_popup():
            self.setIcon(self.Icon.Information)
            self.setText("WARNING")
            self.addButton("Ok", self.ActionRole)
            # restart_btn.clicked.connect(self.restart_btn)

        func = {
            "error": error_popup,
            "warning": warning_popup,
        }.get(level)
        func()

        close_btn = self.addButton("Exit", self.ActionRole)
        close_btn.clicked.connect(self.close_btn)
        # report_btn = self.addButton('Report Error', self.ActionRole)
        # report_btn.clicked.connect(self.report_btn)
        self.show()

    def event(self, e):
        MAXSIZE = 16_777_215
        MINHEIGHT = 100
        MINWIDTH = 200
        MINWIDTH_CHANGE = 500
        result = QtWidgets.QMessageBox.event(self, e)

        self.setMinimumHeight(MINHEIGHT)
        self.setMaximumHeight(MAXSIZE)
        self.setMinimumWidth(MINWIDTH)
        self.setMaximumWidth(MAXSIZE)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        textEdit = self.findChild(QtWidgets.QTextEdit)
        if textEdit is not None:
            textEdit.setMinimumHeight(MINHEIGHT)
            textEdit.setMaximumHeight(MAXSIZE)
            textEdit.setMinimumWidth(MINWIDTH_CHANGE)
            textEdit.setMaximumWidth(MAXSIZE)
            textEdit.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )

        return result

    def close_btn(self):
        self.window.close()

    def report_btn(self):
        pass
    # def restart_btn(self):
    #     self.window.stream.thread_signal.connect(self.restart_update)
    #     self.window.stream.thread_signal.emit(True)
    # @pyqtSlot(object)
    # def restart_update(self, click):
    #     self.window.update_network_stm32.clicked(click)


class ThreadSignal(QObject):
    thread_error = pyqtSignal(object)
    thread_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()


class Form(QDialog):
    """
    GUI Form of MODI Firmware Updater
    """

    def __init__(self, installer=False):
        QDialog.__init__(self)
        self.logger = self.__init_logger()
        self.__excepthook = sys.excepthook
        sys.excepthook = self.__popup_excepthook
        th.excepthook = self.__popup_thread_excepthook
        self.err_list = list()
        self.is_popup = False

        if installer:
            ui_path = os.path.join(os.path.dirname(__file__), "updater.ui")
            update_list_ui_path = os.path.join(os.path.dirname(__file__), "update_list.ui")
            if sys.platform.startswith("win"):
                self.component_path = pathlib.PurePosixPath(
                    pathlib.PurePath(__file__), ".."
                )
            else:
                self.component_path = os.path.dirname(__file__).replace(
                    "util", ""
                )
        else:
            ui_path = os.path.join(
                os.path.dirname(__file__), "assets", "updater.ui"
            )
            update_list_ui_path = os.path.join(
                os.path.dirname(__file__), "assets", "update_list.ui"
            )
            if sys.platform.startswith("win"):
                self.component_path = pathlib.PurePosixPath(
                    pathlib.PurePath(__file__), "..", "assets", "component"
                )
            else:
                self.component_path = os.path.join(
                    os.path.dirname(__file__), "assets", "component"
                )
        self.ui = uic.loadUi(ui_path)

        self.ui.setStyleSheet("background-color: white")
        self.ui.console.hide()
        self.ui.setFixedHeight(600)

        # Set LUXROBO logo image
        logo_path = os.path.join(self.component_path, "luxrobo_logo.png")
        qPixmapVar = QtGui.QPixmap()
        qPixmapVar.load(logo_path)
        self.ui.lux_logo.setPixmap(qPixmapVar)

        self.update_list_form = UpdateListForm(update_list_ui_path, self.component_path)

        # Buttons image
        self.active_path = pathlib.PurePosixPath(
            self.component_path, "btn_frame_active.png"
        )
        self.inactive_path = pathlib.PurePosixPath(
            self.component_path, "btn_frame_inactive.png"
        )
        self.pressed_path = pathlib.PurePosixPath(
            self.component_path, "btn_frame_pressed.png"
        )
        self.language_frame_path = pathlib.PurePosixPath(
            self.component_path, "lang_frame.png"
        )
        self.language_frame_pressed_path = pathlib.PurePosixPath(
            self.component_path, "lang_frame_pressed.png"
        )

        self.ui.update_network_esp32.setStyleSheet(
            f"border-image: url({self.active_path}); font-size: 16px"
        )
        self.ui.update_network_esp32_interpreter.setStyleSheet(
            f"border-image: url({self.active_path}); font-size: 16px"
        )
        self.ui.update_stm32_modules.setStyleSheet(
            f"border-image: url({self.active_path}); font-size: 16px"
        )
        self.ui.update_network_stm32.setStyleSheet(
            f"border-image: url({self.active_path}); font-size: 16px"
        )
        self.ui.translate_button.setStyleSheet(
            f"border-image: url({self.language_frame_path}); font-size: 13px"
        )
        self.ui.devmode_button.setStyleSheet(
            f"border-image: url({self.language_frame_path}); font-size: 13px"
        )
        self.ui.console.setStyleSheet("font-size: 10px")

        self.ui.setWindowTitle("MODI Firmware Updater")

        # Redirect stdout to text browser (i.e. console in our UI)
        self.stdout = StdoutRedirect()
        self.stdout.start()
        self.stdout.printOccur.connect(
            lambda line: self.__append_text_line(line)
        )
        self.stdout.logger = self.logger

        # Set signal for thread communication
        self.stream = ThreadSignal()

        # Init variable to check if the program is in installation mode
        self.ui.installation = installer

        # Connect up the buttons
        self.ui.update_network_esp32.clicked.connect(self.update_network_esp32)
        self.ui.update_network_esp32_interpreter.clicked.connect(self.update_network_esp32_interpreter)
        self.ui.update_stm32_modules.clicked.connect(self.update_stm32_modules)
        self.ui.update_network_stm32.clicked.connect(self.update_network_stm32)
        self.ui.translate_button.clicked.connect(self.translate_button_text)
        self.ui.devmode_button.clicked.connect(self.dev_mode_button)

        self.buttons = [
            self.ui.update_network_esp32,
            self.ui.update_network_esp32_interpreter,
            self.ui.update_stm32_modules,
            self.ui.update_network_stm32,
            self.ui.devmode_button,
            self.ui.translate_button,
        ]

        # Disable the first button to be focused when UI is loaded
        self.ui.update_network_esp32.setAutoDefault(False)
        self.ui.update_network_esp32.setDefault(False)

        # Print init status
        time_now_str = time.strftime("[%Y/%m/%d@%X]", time.localtime())
        print(time_now_str + " GUI MODI Firmware Updater has been started!")

        # Set up field variables
        self.firmware_updater = None
        self.button_in_english = False
        self.console = False

        # Set up ui field variables
        self.ui.is_english = False
        self.ui.active_path = self.active_path
        self.ui.pressed_path = self.pressed_path
        self.ui.language_frame_path = self.language_frame_path
        self.ui.language_frame_pressed_path = self.language_frame_pressed_path
        self.ui.stream = self.stream
        self.ui.popup = self._thread_signal_hook

        # Check module firmware
        self.local_firmware_path = path.join(path.dirname(__file__), "assets", "firmware", "latest")
        # module
        self.local_module_firmware_path = path.join(self.local_firmware_path, "stm32")
        self.local_module_version_path = path.join(self.local_module_firmware_path, "version.txt")
        self.latest_module_firmware_path = "https://download.luxrobo.com/modi-skeleton/skeleton.zip"
        self.latest_module_version_path = "https://download.luxrobo.com/modi-skeleton/version.txt"
        # network base
        self.local_network_firmware_path = path.join(self.local_firmware_path, "stm32")
        self.local_network_version_path = path.join(self.local_network_firmware_path, "base_version.txt")
        self.latest_network_firmware_path = "https://download.luxrobo.com/modi-network-os/network.zip"
        self.latest_network_version_path = "https://download.luxrobo.com/modi-network-os/version.txt"
        #esp32
        self.local_esp32_firmware_path = path.join(self.local_firmware_path, "esp32")
        self.local_esp32_version_path = path.join(self.local_esp32_firmware_path, "esp_version.txt")
        self.latest_esp32_firmware_path = [
            "https://download.luxrobo.com/modi-ota-firmware/ota.zip",
            "https://download.luxrobo.com/modi-esp32-firmware/esp.zip",
        ]
        self.latest_esp32_version_path = "https://download.luxrobo.com/modi-esp32-firmware/version.txt"
        self.check_module_firmware()

        # Set Button Status
        self.translate_button_text()
        self.translate_button_text()
        self.dev_mode_button()
        self.dev_mode_button()
        self.ui.show()

    #
    # Main methods
    #
    def update_network_esp32(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            self.update_list_form.ui.show()
            return
        self.ui.update_network_esp32.setStyleSheet(
            f"border-image: url({self.pressed_path}); font-size: 16px"
        )
        self.ui.console.clear()
        print("ESP32 Firmware Updater has been initialized for esp update!")
        th.Thread(
            target=self.__click_motion, args=(0, button_start), daemon=True
        ).start()
        self.update_list_form.reset_device_list()
        self.update_list_form.ui.show()
        esp32_updater = ESP32FirmwareMultiUpdater()
        esp32_updater.set_ui(self.ui, self.update_list_form)
        time.sleep(0.5)
        th.Thread(
            target=esp32_updater.update_firmware,
            daemon=True
        ).start()
        self.firmware_updater = esp32_updater

    def update_network_esp32_interpreter(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            self.update_list_form.ui.show()
            return
        self.ui.update_network_esp32_interpreter.setStyleSheet(
            f"border-image: url({self.pressed_path}); font-size: 16px"
        )
        self.ui.console.clear()
        print("ESP32 Firmware Updater has been initialized for esp interpreter update!")
        th.Thread(
            target=self.__click_motion, args=(1, button_start), daemon=True
        ).start()
        self.update_list_form.reset_device_list()
        self.update_list_form.ui.show()
        esp32_updater = ESP32FirmwareMultiUpdater()
        esp32_updater.set_ui(self.ui, self.update_list_form)
        time.sleep(0.5)
        th.Thread(
            target=esp32_updater.update_firmware,
            args=(True,),
            daemon=True
        ).start()
        self.firmware_updater = esp32_updater

    def update_stm32_modules(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            return
        self.ui.update_stm32_modules.setStyleSheet(
            f"border-image: url({self.pressed_path}); font-size: 16px"
        )
        self.ui.console.clear()
        print("STM32 Firmware Updater has been initialized for module update!")
        th.Thread(
            target=self.__click_motion, args=(2, button_start), daemon=True
        ).start()
        stm32_updater = STM32FirmwareUpdater()
        stm32_updater.set_ui(self.ui)
        th.Thread(
            target=stm32_updater.update_module_firmware,
            daemon=True
        ).start()
        self.firmware_updater = stm32_updater

    def update_network_stm32(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            return
        self.ui.update_network_stm32.setStyleSheet(
            f"border-image: url({self.pressed_path}); font-size: 16px"
        )
        self.ui.console.clear()
        print("STM32 Firmware Updater has been initialized for base update!")
        th.Thread(
            target=self.__click_motion, args=(3, button_start), daemon=True
        ).start()
        stm32_updater = STM32FirmwareUpdater()
        stm32_updater.set_ui(self.ui)
        th.Thread(
            target=stm32_updater.update_module_firmware,
            args=(True,),
            daemon=True,
        ).start()
        self.firmware_updater = stm32_updater

    def dev_mode_button(self):
        button_start = time.time()
        self.ui.devmode_button.setStyleSheet(
            f"border-image: url({self.language_frame_pressed_path});"
            "font-size: 13px"
        )
        th.Thread(
            target=self.__click_motion, args=(4, button_start), daemon=True
        ).start()
        if self.console:
            self.ui.console.hide()
            self.ui.setFixedHeight(600)
        else:
            self.ui.console.show()
            self.ui.setFixedHeight(780)
        self.console = not self.console

    def translate_button_text(self):
        button_start = time.time()
        self.ui.translate_button.setStyleSheet(
            f"border-image: url({self.language_frame_pressed_path});"
            "font-size: 13px"
        )
        th.Thread(
            target=self.__click_motion, args=(5, button_start), daemon=True
        ).start()
        button_en = [
            "Update Network ESP32",
            "Update Network ESP32 Interpreter",
            "Update STM32 Modules",
            "Update Network STM32",
            "Dev Mode",
            "한국어",
        ]
        button_kr = [
            "네트워크 모듈 업데이트",
            "네트워크 모듈 인터프리터 초기화",
            "모듈 초기화",
            "네트워크 모듈 초기화",
            "개발자 모드",
            "English",
        ]
        appropriate_translation = (
            button_kr if self.button_in_english else button_en
        )
        self.button_in_english = not self.button_in_english
        self.ui.is_english = not self.ui.is_english
        for i, button in enumerate(self.buttons):
            button.setText(appropriate_translation[i])

    def check_module_firmware(self):
        if not os.path.exists(self.local_firmware_path):
            os.mkdir(self.local_firmware_path)

        self.__check_module_version()
        self.__check_network_base_version()
        self.__check_esp32_version()

    def __download_module_firmware(self):
        try:
            # read latest version
            with ur.urlopen(self.latest_module_version_path, timeout=5) as conn:
                last_version_name = conn.read().decode("utf8")

            # skeleton update
            with ur.urlopen(self.latest_module_firmware_path, timeout=5) as conn:
                module_name = [
                    "button",
                    "dial",
                    "display",
                    "environment",
                    "gyro",
                    "ir",
                    "led",
                    "mic",
                    "motor",
                    "speaker",
                    "ultrasonic"
                ]
                download_response = conn.read()
                zip_content = zipfile.ZipFile(
                    io.BytesIO(download_response), "r"
                )

                for i, module in enumerate(module_name):
                    src_path = module + "/Base_module.bin"
                    bin_buffer = zip_content.read(src_path)
                    
                    if module == "environment":
                        dest_path = path.join(self.local_module_firmware_path, "env" + ".bin")
                    else:
                        dest_path = path.join(self.local_module_firmware_path, module + ".bin")
                    
                    with open(dest_path, "wb") as data_file:
                        data_file.write(bin_buffer)

            # version update
            with open(path.join(self.local_module_firmware_path, "version.txt"), "w") as data_file:
                data_file.write(last_version_name)

            return True

        except URLError:
            return False

    def __download_network_firmware(self):
        try:
            # read latest version
            with ur.urlopen(self.latest_network_version_path, timeout=5) as conn:
                last_version_name = conn.read().decode("utf8")

            # network base update
            with ur.urlopen(self.latest_network_firmware_path, timeout=5) as conn:
                download_response = conn.read()
                zip_content = zipfile.ZipFile(
                    io.BytesIO(download_response), "r"
                )

                with open(path.join(self.local_network_firmware_path, "network.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("network.bin"))

            # version update
            with open(path.join(self.local_network_firmware_path, "base_version.txt"), "w") as data_file:
                data_file.write(last_version_name)

            return True

        except URLError:
            return False

    def __download_esp32_firmware(self):
        try:
            # read latest version
            with ur.urlopen(self.latest_esp32_version_path, timeout=5) as conn:
                last_version_name = conn.read().decode("utf8")

            # ota update
            with ur.urlopen(self.latest_esp32_firmware_path[0], timeout=5) as conn:
                download_response = conn.read()
                zip_content = zipfile.ZipFile(
                    io.BytesIO(download_response), "r"
                )

                with open(path.join(self.local_esp32_firmware_path, "modi_ota_factory.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("modi_ota_factory.bin"))

                with open(path.join(self.local_esp32_firmware_path, "ota_data_initial.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("ota_data_initial.bin"))

            # bootloader, partitions, esp32 update
            with ur.urlopen(self.latest_esp32_firmware_path[1], timeout=5) as conn:
                download_response = conn.read()
                zip_content = zipfile.ZipFile(
                    io.BytesIO(download_response), "r"
                )

                with open(path.join(self.local_esp32_firmware_path, "bootloader.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("bootloader.bin"))

                with open(path.join(self.local_esp32_firmware_path, "partitions.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("partitions.bin"))

                with open(path.join(self.local_esp32_firmware_path, "esp32.bin"), "wb") as data_file:
                    data_file.write(zip_content.read("esp32.bin"))

            # version update
            with open(path.join(self.local_esp32_firmware_path, "esp_version.txt"), "w") as data_file:
                data_file.write(last_version_name)

            return True

        except URLError:
            return False

    def __check_module_version(self):
        try:
            local_version_info = None
            latest_version_info = None

            with ur.urlopen(self.latest_module_version_path, timeout=5) as conn:
                latest_version_name = conn.read().decode("utf8")
                latest_version_info = latest_version_name.lstrip("v").rstrip("\n")

            if os.path.exists(self.local_module_firmware_path):
                with open(self.local_module_version_path) as version_file:
                    local_version_info = version_file.readline().lstrip("v").rstrip("\n")
            else:
                os.mkdir(self.local_module_firmware_path)

            if (local_version_info == None) or (local_version_info != latest_version_info):
                self.__download_module_firmware()

        except URLError:
            if not os.path.exists(self.local_module_firmware_path):
                assert_path = path.join(path.dirname(__file__), "assets", "firmware", "stm32")
                shutil.copytree(assert_path, self.local_module_firmware_path)

    def __check_network_base_version(self):
        try:
            local_version_info = None
            latest_version_info = None

            with ur.urlopen(self.latest_network_version_path, timeout=5) as conn:
                latest_version_name = conn.read().decode("utf8")
                latest_version_info = latest_version_name.lstrip("v").rstrip("\n")

            if os.path.exists(self.local_network_firmware_path):
                with open(self.local_module_version_path) as version_file:
                    local_version_info = version_file.readline().lstrip("v").rstrip("\n")
            else:
                os.mkdir(self.local_network_firmware_path)

            if (local_version_info == None) or (local_version_info != latest_version_info):
                self.__download_network_firmware()

        except URLError:
            if not os.path.exists(self.local_network_firmware_path):
                assert_path = path.join(path.dirname(__file__), "assets", "firmware", "stm32")
                shutil.copytree(assert_path, self.local_network_firmware_path)

    def __check_esp32_version(self):
        try:
            local_version_info = None
            latest_version_info = None

            with ur.urlopen(self.latest_esp32_version_path, timeout=5) as conn:
                latest_version_name = conn.read().decode("utf8")
                latest_version_info = latest_version_name.lstrip("v").rstrip("\n")

            if os.path.exists(self.local_esp32_firmware_path):
                with open(self.local_module_version_path) as version_file:
                    local_version_info = version_file.readline().lstrip("v").rstrip("\n")
            else:
                os.mkdir(self.local_esp32_firmware_path)

            if (local_version_info == None) or (local_version_info != latest_version_info):
                self.__download_esp32_firmware()

        except URLError:
            if not os.path.exists(self.local_esp32_firmware_path):
                assert_path = path.join(path.dirname(__file__), "assets", "firmware", "esp32")
                shutil.copytree(assert_path, self.local_esp32_firmware_path)
    #
    # Helper functions
    #
    @staticmethod
    def __init_logger():
        logger = logging.getLogger("GUI MODI Firmware Updater Logger")
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler = logging.FileHandler("gmfu.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        return logger

    def __popup_excepthook(self, exctype, value, traceback):
        self.__excepthook(exctype, value, traceback)
        if self.is_popup:
            return
        self.popup = PopupMessageBox(self.ui, level="error")
        self.popup.setInformativeText(str(value))
        self.popup.setDetailedText(str(tb.extract_tb(traceback)))
        self.is_popup = True

    def __popup_thread_excepthook(self, err_msg):
        if err_msg.exc_type in self.err_list:
            return
        self.err_list.append(err_msg.exc_type)
        self.stream.thread_error.connect(self.__thread_error_hook)
        self.stream.thread_error.emit(err_msg)

    @pyqtSlot(object)
    def __thread_error_hook(self, err_msg):
        self.__popup_excepthook(
            err_msg.exc_type, err_msg.exc_value, err_msg.exc_traceback
        )

    @pyqtSlot(object)
    def _thread_signal_hook(self):
        self.thread_popup = PopupMessageBox(self.ui, level="warning")
        if self.button_in_english:
            text = (
                "Reconnect network module and "
                "click the button again please."
            )
        else:
            text = "네트워크 모듈을 재연결 후 버튼을 다시 눌러주십시오."
        self.thread_popup.setInformativeText(text)
        self.is_popup = True

    def __click_motion(self, button_type, start_time):
        # Busy wait for 0.2 seconds
        while time.time() - start_time < 0.2:
            pass

        if button_type in [4, 5]:
            self.buttons[button_type].setStyleSheet(
                f"border-image: url({self.language_frame_path});"
                "font-size: 13px"
            )
        else:
            self.buttons[button_type].setStyleSheet(
                f"border-image: url({self.active_path}); font-size: 16px"
            )
            for i, q_button in enumerate(self.buttons):
                if i in [button_type, 4, 5]:
                    continue
                q_button.setStyleSheet(
                    f"border-image: url({self.inactive_path}); font-size: 16px"
                )
                q_button.setEnabled(False)

    def __append_text_line(self, line):
        self.ui.console.moveCursor(
            QtGui.QTextCursor.End, QtGui.QTextCursor.MoveAnchor
        )
        self.ui.console.moveCursor(
            QtGui.QTextCursor.StartOfLine, QtGui.QTextCursor.MoveAnchor
        )
        self.ui.console.moveCursor(
            QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor
        )

        # Remove new line character if current line represents update_progress
        if self.__is_update_progress_line(line):
            self.ui.console.textCursor().removeSelectedText()
            self.ui.console.textCursor().deletePreviousChar()

        # Display user text input
        self.ui.console.moveCursor(QtGui.QTextCursor.End)
        self.ui.console.insertPlainText(line)
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ExcludeUserInputEvents
        )

    @staticmethod
    def __is_update_progress_line(line):
        return line.startswith("\rUpdating") or line.startswith(
            "\rFirmware Upload: ["
        )

class UpdateListForm(QDialog):

    progress_signal = pyqtSignal(str, int)
    total_progress_signal = pyqtSignal(int)
    total_status_signal = pyqtSignal(str)

    def __init__(self, ui_path, component_path):
        QDialog.__init__(self)

        self.ui = uic.loadUi(ui_path)
        self.component_path = component_path

        self.ui_icon_list = [
            self.ui.image_1,
            self.ui.image_2,
            self.ui.image_3,
            self.ui.image_4,
            self.ui.image_5,
            self.ui.image_6,
            self.ui.image_7,
            self.ui.image_8,
            self.ui.image_9,
            self.ui.image_10
        ]

        self.ui_port_list = [
            self.ui.port_1,
            self.ui.port_2,
            self.ui.port_3,
            self.ui.port_4,
            self.ui.port_5,
            self.ui.port_6,
            self.ui.port_7,
            self.ui.port_8,
            self.ui.port_9,
            self.ui.port_10
        ]

        self.ui_progress_list = [
            self.ui.progress_bar_1,
            self.ui.progress_bar_2,
            self.ui.progress_bar_3,
            self.ui.progress_bar_4,
            self.ui.progress_bar_5,
            self.ui.progress_bar_6,
            self.ui.progress_bar_7,
            self.ui.progress_bar_8,
            self.ui.progress_bar_9,
            self.ui.progress_bar_10,
        ]

        self.ui_progress_value_list = [
            self.ui.progress_value_1,
            self.ui.progress_value_2,
            self.ui.progress_value_3,
            self.ui.progress_value_4,
            self.ui.progress_value_5,
            self.ui.progress_value_6,
            self.ui.progress_value_7,
            self.ui.progress_value_8,
            self.ui.progress_value_9,
            self.ui.progress_value_10,
        ]

        self.ui.close_button.clicked.connect(self.ui.close)
        self.progress_signal.connect(self.progress_value_changed)
        self.total_progress_signal.connect(self.total_progress_value_changed)
        self.total_status_signal.connect(self.total_progress_status_changed)

    def reset_device_list(self):
        self.ui.progress_bar_total.setValue(0)
        self.ui.total_status.setText("")

        for i, progress in enumerate(self.ui_progress_list):
            icon_path = os.path.join(self.component_path, "modules", "network_none.png")
            pixmap = QtGui.QPixmap()
            pixmap.load(icon_path)

            self.ui_icon_list[i].setPixmap(pixmap)
            self.ui_port_list[i].setText("not connected")
            self.ui_progress_list[i].setValue(0)
            self.ui_progress_value_list[i].setText("0%")

    def set_device_list(self, device_list):
        self.reset_device_list()
        for i, device in enumerate(device_list):
            icon_path = os.path.join(self.component_path, "modules", "network.png")
            pixmap = QtGui.QPixmap()
            pixmap.load(icon_path)
            self.ui_icon_list[i].setPixmap(pixmap)
            self.ui_port_list[i].setText(device)

    def progress_value_changed(self, name, value):
        for i, ui_port in enumerate(self.ui_port_list):
            if ui_port.text() == name:
                self.ui_progress_list[i].setValue(value)
                self.ui_progress_value_list[i].setText(str(value) + "%")
                break

    def total_progress_value_changed(self, value):
        self.ui.progress_bar_total.setValue(value)

    def total_progress_status_changed(self, status):
        self.ui.total_status.setText(status)
