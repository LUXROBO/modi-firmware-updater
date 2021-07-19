import logging
import os
import pathlib
import sys
import threading as th
import time
import traceback as tb

from PyQt5 import QtCore, QtGui, QtWidgets, uic
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QDialog

from modi_firmware_updater.util.connection_util import list_modi_ports
from modi_firmware_updater.core.esp32_updater import ESP32FirmwareMultiUpdater
from modi_firmware_updater.core.stm32_updater import STM32FirmwareMultiUpdater


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
            restart_btn.clicked.connect(self.restart_btn)

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
            esp32_update_list_ui_path = os.path.join(os.path.dirname(__file__), "esp32_update_list.ui")
            stm32_update_list_ui_path = os.path.join(os.path.dirname(__file__), "stm32_update_list.ui")
            if sys.platform.startswith("win"):
                self.component_path = pathlib.PurePosixPath(pathlib.PurePath(__file__), "..")
            else:
                self.component_path = os.path.dirname(__file__).replace("util", "")
        else:
            ui_path = os.path.join(os.path.dirname(__file__), "assets", "updater.ui")
            esp32_update_list_ui_path = os.path.join(os.path.dirname(__file__), "assets", "esp32_update_list.ui")
            stm32_update_list_ui_path = os.path.join(os.path.dirname(__file__), "assets", "stm32_update_list.ui")
            if sys.platform.startswith("win"):
                self.component_path = pathlib.PurePosixPath(pathlib.PurePath(__file__), "..", "assets", "component")
            else:
                self.component_path = os.path.join(os.path.dirname(__file__), "assets", "component")
        self.ui = uic.loadUi(ui_path)

        self.ui.setStyleSheet("background-color: white")
        self.ui.console.hide()
        self.ui.setFixedHeight(600)

        # Set LUXROBO logo image
        logo_path = os.path.join(self.component_path, "luxrobo_logo.png")
        qPixmapVar = QtGui.QPixmap()
        qPixmapVar.load(logo_path)
        self.ui.lux_logo.setPixmap(qPixmapVar)

        self.esp32_update_list_form = ESP32UpdateListForm(esp32_update_list_ui_path, self.component_path)
        self.stm32_update_list_form = STM32UpdateListForm(stm32_update_list_ui_path, self.component_path)

        # Buttons image
        self.active_path = pathlib.PurePosixPath(self.component_path, "btn_frame_active.png")
        self.inactive_path = pathlib.PurePosixPath(self.component_path, "btn_frame_inactive.png")
        self.pressed_path = pathlib.PurePosixPath(self.component_path, "btn_frame_pressed.png")
        self.language_frame_path = pathlib.PurePosixPath(self.component_path, "lang_frame.png")
        self.language_frame_pressed_path = pathlib.PurePosixPath(self.component_path, "lang_frame_pressed.png")

        self.ui.update_network_esp32.setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
        self.ui.update_network_esp32_interpreter.setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
        self.ui.update_stm32_modules.setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
        self.ui.update_network_stm32.setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
        self.ui.translate_button.setStyleSheet(f"border-image: url({self.language_frame_path}); font-size: 13px")
        self.ui.devmode_button.setStyleSheet(f"border-image: url({self.language_frame_path}); font-size: 13px")
        self.ui.console.setStyleSheet("font-size: 10px")

        self.ui.setWindowTitle("MODI Firmware Updater")

        # Redirect stdout to text browser (i.e. console in our UI)
        # self.stdout = StdoutRedirect()
        # self.stdout.start()
        # self.stdout.printOccur.connect(
        #     lambda line: self.__append_text_line(line)
        # )
        # self.stdout.logger = self.logger

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

        # for kyowon
        self.ui.update_network_stm32.setVisible(False)

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
            self.esp32_update_list_form.ui.show()
            return
        self.ui.update_network_esp32.setStyleSheet(f"border-image: url({self.pressed_path}); font-size: 16px")
        self.ui.console.clear()
        print("ESP32 Firmware Updater has been initialized for esp update!")
        th.Thread(
            target=self.__click_motion, args=(0, button_start), daemon=True
        ).start()
        self.esp32_update_list_form.reset_device_list()
        self.esp32_update_list_form.ui.show()
        esp32_updater = ESP32FirmwareMultiUpdater()
        esp32_updater.set_ui(self.ui, self.esp32_update_list_form)
        th.Thread(
            target=esp32_updater.update_firmware,
            daemon=True
        ).start()
        self.firmware_updater = esp32_updater

    def update_network_esp32_interpreter(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            self.esp32_update_list_form.ui.show()
            return
        self.ui.update_network_esp32_interpreter.setStyleSheet(f"border-image: url({self.pressed_path}); font-size: 16px")
        self.ui.console.clear()
        print("ESP32 Firmware Updater has been initialized for esp interpreter update!")
        th.Thread(
            target=self.__click_motion, args=(1, button_start), daemon=True
        ).start()
        self.esp32_update_list_form.reset_device_list()
        self.esp32_update_list_form.ui.show()
        esp32_updater = ESP32FirmwareMultiUpdater()
        esp32_updater.set_ui(self.ui, self.esp32_update_list_form)
        th.Thread(
            target=esp32_updater.update_firmware,
            args=(True,),
            daemon=True
        ).start()
        self.firmware_updater = esp32_updater

    def update_stm32_modules(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            self.stm32_update_list_form.ui.show()
            return
        self.ui.update_stm32_modules.setStyleSheet(f"border-image: url({self.pressed_path}); font-size: 16px")
        self.ui.console.clear()
        print("STM32 Firmware Updater has been initialized for module update!")
        th.Thread(
            target=self.__click_motion, args=(2, button_start), daemon=True
        ).start()

        modi_ports = list_modi_ports()
        if not modi_ports:
            raise Exception("No MODI port is connected")

        self.stm32_update_list_form.reset_device_list()
        self.stm32_update_list_form.ui.show()
        stm32_updater = STM32FirmwareMultiUpdater()
        stm32_updater.set_ui(self.ui, self.stm32_update_list_form)
        th.Thread(
            target=stm32_updater.update_module_firmware,
            args=(modi_ports, ),
            daemon=True
        ).start()
        self.firmware_updater = stm32_updater

    def update_network_stm32(self):
        button_start = time.time()
        if self.firmware_updater and self.firmware_updater.update_in_progress:
            self.stm32_update_list_form.ui.show()
            return
        self.ui.update_network_stm32.setStyleSheet(
            f"border-image: url({self.pressed_path}); font-size: 16px"
        )
        self.ui.console.clear()
        print("STM32 Firmware Updater has been initialized for base update!")
        th.Thread(
            target=self.__click_motion, args=(3, button_start), daemon=True
        ).start()
        self.stm32_update_list_form.reset_device_list()
        stm32_updater = STM32FirmwareMultiUpdater()
        stm32_updater.set_ui(self.ui, self.stm32_update_list_form)
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
        self.ui.translate_button.setStyleSheet(f"border-image: url({self.language_frame_pressed_path}); font-size: 13px")
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

    #
    # Helper functions
    #
    @staticmethod
    def __init_logger():
        logger = logging.getLogger("GUI MODI Firmware Updater Logger")
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
            self.buttons[button_type].setStyleSheet(f"border-image: url({self.language_frame_path}); font-size: 13px")
        else:
            self.buttons[button_type].setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
            for i, q_button in enumerate(self.buttons):
                if i in [button_type, 4, 5]:
                    continue
                q_button.setStyleSheet(f"border-image: url({self.inactive_path}); font-size: 16px")
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
        return line.startswith("\rUpdating") or line.startswith("\rFirmware Upload: [")

class ESP32UpdateListForm(QDialog):

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


class STM32UpdateListForm(QDialog):

    network_state_signal = pyqtSignal(str, int)
    network_id_signal = pyqtSignal(str, int)
    current_module_changed_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(str, int, int)
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

        self.ui_current_icon_list = [
            self.ui.image_current_1,
            self.ui.image_current_2,
            self.ui.image_current_3,
            self.ui.image_current_4,
            self.ui.image_current_5,
            self.ui.image_current_6,
            self.ui.image_current_7,
            self.ui.image_current_8,
            self.ui.image_current_9,
            self.ui.image_current_10,
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

        self.ui_network_id_list = [
            self.ui.network_id_1,
            self.ui.network_id_2,
            self.ui.network_id_3,
            self.ui.network_id_4,
            self.ui.network_id_5,
            self.ui.network_id_6,
            self.ui.network_id_7,
            self.ui.network_id_8,
            self.ui.network_id_9,
            self.ui.network_id_10,
        ]

        self.ui_current_progress_list = [
            self.ui.progress_bar_current_1,
            self.ui.progress_bar_current_2,
            self.ui.progress_bar_current_3,
            self.ui.progress_bar_current_4,
            self.ui.progress_bar_current_5,
            self.ui.progress_bar_current_6,
            self.ui.progress_bar_current_7,
            self.ui.progress_bar_current_8,
            self.ui.progress_bar_current_9,
            self.ui.progress_bar_current_10,
        ]

        self.ui_total_progress_list = [
            self.ui.progress_bar_total_1,
            self.ui.progress_bar_total_2,
            self.ui.progress_bar_total_3,
            self.ui.progress_bar_total_4,
            self.ui.progress_bar_total_5,
            self.ui.progress_bar_total_6,
            self.ui.progress_bar_total_7,
            self.ui.progress_bar_total_8,
            self.ui.progress_bar_total_9,
            self.ui.progress_bar_total_10,
        ]

        self.ui_current_progress_value_list = [
            self.ui.progress_current_value_1,
            self.ui.progress_current_value_2,
            self.ui.progress_current_value_3,
            self.ui.progress_current_value_4,
            self.ui.progress_current_value_5,
            self.ui.progress_current_value_6,
            self.ui.progress_current_value_7,
            self.ui.progress_current_value_8,
            self.ui.progress_current_value_9,
            self.ui.progress_current_value_10,
        ]

        self.ui_total_progress_value_list = [
            self.ui.progress_total_value_1,
            self.ui.progress_total_value_2,
            self.ui.progress_total_value_3,
            self.ui.progress_total_value_4,
            self.ui.progress_total_value_5,
            self.ui.progress_total_value_6,
            self.ui.progress_total_value_7,
            self.ui.progress_total_value_8,
            self.ui.progress_total_value_9,
            self.ui.progress_total_value_10,
        ]

        self.ui.close_button.clicked.connect(self.close_form)

        self.network_state_signal.connect(self.set_network_state)
        self.network_id_signal.connect(self.set_network_id)
        self.current_module_changed_signal.connect(self.current_module_changed)
        self.progress_signal.connect(self.progress_value_changed)
        self.total_progress_signal.connect(self.total_progress_value_changed)
        self.total_status_signal.connect(self.total_progress_status_changed)

    def reset_device_list(self):
        self.ui.progress_bar_total.setValue(0)
        self.ui.total_status.setText("")

        for i, progress in enumerate(self.ui_port_list):
            icon_path = os.path.join(self.component_path, "modules", "network_none.png")
            icon_pixmap = QtGui.QPixmap()
            icon_pixmap.load(icon_path)
            self.ui_icon_list[i].setPixmap(icon_pixmap)

            current_icon_path = os.path.join(self.component_path, "modules", "network_none_28.png")
            current_icon_pixmap = QtGui.QPixmap()
            current_icon_pixmap.load(current_icon_path)
            self.ui_current_icon_list[i].setPixmap(current_icon_pixmap)

            self.ui_port_list[i].setText("not connected")
            self.ui_network_id_list[i].setText("network id")
            self.ui_current_progress_list[i].setValue(0)
            self.ui_total_progress_list[i].setValue(0)
            self.ui_current_progress_value_list[i].setText("0%")
            self.ui_total_progress_value_list[i].setText("0%")

    def set_device_list(self, device_list):
        self.reset_device_list()
        for i, device in enumerate(device_list):
            icon_path = os.path.join(self.component_path, "modules", "network.png")
            pixmap = QtGui.QPixmap()
            pixmap.load(icon_path)
            self.ui_icon_list[i].setPixmap(pixmap)
            self.ui_port_list[i].setText(device)

    def set_network_state(self, name, state):
        for i, ui_port in enumerate(self.ui_port_list):
            if ui_port.text() == name:
                pixmap = QtGui.QPixmap()
                if state == -1:
                    icon_path = os.path.join(self.component_path, "modules", "network_error.png")
                    pixmap.load(icon_path)
                elif state == 0:
                    icon_path = os.path.join(self.component_path, "modules", "network.png")
                    pixmap.load(icon_path)
                else:
                    icon_path = os.path.join(self.component_path, "modules", "network_reconnect.png")
                    pixmap.load(icon_path)

                self.ui_icon_list[i].setPixmap(pixmap)
                break

    def set_network_id(self, name, id):
        for i, ui_port in enumerate(self.ui_port_list):
            if ui_port.text() == name:
                self.ui_network_id_list[i].setText(hex(id))
                break

    def current_module_changed(self, name, module_type):
        if module_type:
            for i, ui_port in enumerate(self.ui_port_list):
                if ui_port.text() == name:
                    icon_path = os.path.join(self.component_path, "modules", module_type + "_28.png")
                    pixmap = QtGui.QPixmap()
                    pixmap.load(icon_path)
                    self.ui_current_icon_list[i].setPixmap(pixmap)
                    break

    def progress_value_changed(self, name, current, total):
        for i, ui_port in enumerate(self.ui_port_list):
            if ui_port.text() == name:
                self.ui_current_progress_list[i].setValue(current)
                self.ui_current_progress_value_list[i].setText(str(current) + "%")
                self.ui_total_progress_list[i].setValue(total)
                self.ui_total_progress_value_list[i].setText(str(total) + "%")
                break

    def total_progress_value_changed(self, value):
        self.ui.progress_bar_total.setValue(value)

    def total_progress_status_changed(self, status):
        self.ui.total_status.setText(status)

    def close_form(self):
        self.ui.close()
