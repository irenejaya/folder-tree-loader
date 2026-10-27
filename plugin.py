"""Register Folder Tree Loader in the QGIS menu and toolbar."""

from pathlib import Path

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction


class FolderTreeLoaderPlugin:
    MENU_NAME = "&Folder Tree Loader"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.toolbar = None
        self.dlg = None

    def initGui(self):
        if self.action is not None:
            return
        icon = QIcon(str(Path(__file__).with_name("icon.svg")))
        self.action = QAction(icon, "Folder Tree Loader", self.iface.mainWindow())
        self.action.setObjectName("FolderTreeLoaderAction")
        self.action.setToolTip(
            "Load GIS data from a folder, preserving its folder tree as QGIS groups"
        )
        self.action.triggered.connect(self.run)
        self.toolbar = self.iface.addToolBar("Folder Tree Loader")
        self.toolbar.setObjectName("FolderTreeLoaderToolbar")
        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu(self.MENU_NAME, self.action)

    def unload(self):
        self._discard_dialog()
        if self.action is not None:
            self.iface.removePluginMenu(self.MENU_NAME, self.action)
            if self.toolbar is not None:
                self.toolbar.removeAction(self.action)
            self.action.deleteLater()
            self.action = None
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None

    def run(self):
        from .folder_tree_loader_dialog import FolderTreeLoaderDialog

        # Modeless: QGIS stays usable while the dialog is open. Reuse one
        # instance so the toolbar button raises it instead of opening another.
        try:
            if self.dlg is None:
                self.dlg = FolderTreeLoaderDialog(self.iface.mainWindow())
                self.dlg.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.svg"))))
            self.dlg.show()
            self.dlg.raise_()
            self.dlg.activateWindow()
        except Exception as exc:
            import traceback
            self.iface.messageBar().pushCritical(
                "Folder Tree Loader", f"Failed to launch: {exc}"
            )
            traceback.print_exc()
            self._discard_dialog()

    def _discard_dialog(self):
        if self.dlg is not None:
            self.dlg.cancel_task(detach=True)
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None
