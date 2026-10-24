"""Run with an OSGeo4W python-qgis launcher; uses a temporary project/data.

Example: python-qgis tests/smoke_test.py
Previews are written to dist/previews, outside the installable ZIP.
"""

import ast
import configparser
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsProject, Qgis
from qgis.PyQt.QtCore import Qt, QTimer, QT_VERSION_STR
from qgis.PyQt.QtGui import QColor, QPalette, QFont, QFontDatabase
from qgis.PyQt.QtWidgets import (
    QApplication, QFileDialog, QHeaderView, QMainWindow, QMessageBox,
)
from osgeo import gdal, ogr, osr


ROOT = Path(__file__).resolve().parents[1]
app = QgsApplication([], True)
app.initQgis()
app.setStyle("Fusion")
# The Windows offscreen platform does not discover system fonts itself.
font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
if font_path.exists():
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    assert font_id >= 0
    app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 9))
print(f"Runtime: QGIS {Qgis.QGIS_VERSION}; Qt {QT_VERSION_STR}", flush=True)
for source in ROOT.rglob("*.py"):
    if "dist" not in source.parts:
        ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
metadata = configparser.ConfigParser(interpolation=None)
metadata.read(ROOT / "metadata.txt", encoding="utf-8")
archive_path = ROOT / "dist" / f"folder_tree_loader-{metadata['general']['version']}.zip"
preview_dir = ROOT / "dist" / "previews"
preview_dir.mkdir(parents=True, exist_ok=True)


class TestInterface:
    def __init__(self):
        self.window = QMainWindow()
        self.actions = []
        self.errors = []

    def mainWindow(self):
        return self.window

    def addToolBar(self, name):
        return self.window.addToolBar(name)

    def addPluginToMenu(self, menu, action):
        self.actions.append((menu, action))

    def removePluginMenu(self, menu, action):
        self.actions.remove((menu, action))

    def messageBar(self):
        return self

    def pushCritical(self, title, message):
        self.errors.append((title, message))


with tempfile.TemporaryDirectory(prefix="folder_tree_loader_test_") as temporary:
    temporary = Path(temporary)
    with ZipFile(archive_path) as archive:
        assert len(archive.namelist()) == 7
        assert all(name.startswith("folder_tree_loader/") for name in archive.namelist())
        archive.extractall(temporary / "plugins")
    sys.path.insert(0, str(temporary / "plugins"))
    before = len(QApplication.topLevelWidgets())
    package = importlib.import_module("folder_tree_loader")
    module = importlib.import_module("folder_tree_loader.folder_tree_loader_dialog")
    assert len(QApplication.topLevelWidgets()) == before, "Import launched a dialog"
    iface = TestInterface()
    plugin = package.classFactory(iface)
    plugin.initGui()
    plugin.initGui()
    assert len(iface.actions) == 1 and not plugin.action.icon().isNull()

    data_root = temporary / "Example GIS folder"
    nested = data_root / "Survey layers"
    nested.mkdir(parents=True)
    data = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": 1},
        "geometry": {"type": "Point", "coordinates": [145, -37]},
    }]}
    (nested / "Long survey layer name for checking available column width.geojson").write_text(
        json.dumps(data), encoding="utf-8"
    )
    gdal.UseExceptions()
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(4326)
    raster = gdal.GetDriverByName("GTiff").Create(str(nested / "Terrain.tif"), 2, 2, 1, gdal.GDT_Byte)
    raster.SetGeoTransform([145, 0.01, 0, -37, 0, -0.01])
    raster.SetProjection(crs.ExportToWkt())
    raster.GetRasterBand(1).Fill(1)
    raster = None
    gdb = ogr.GetDriverByName("OpenFileGDB").CreateDataSource(str(data_root / "Assets.gdb"))
    layer = gdb.CreateLayer("Inspection_points", crs, ogr.wkbPoint, options=["FEATURE_DATASET=Asset_dataset"])
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt("POINT (145 -37)"))
    assert layer.CreateFeature(feature) == 0
    feature = layer = gdb = None

    dialog = module.FolderTreeLoaderDialog()
    dialog.show()
    app.processEvents()
    assert not dialog.load_btn.isEnabled()
    assert not dialog.filter_edit.isEnabled() and not dialog.toggle_btn.isEnabled()
    assert dialog.layer_tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    original_browse = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = lambda *args: str(data_root)
    try:
        dialog._browse_folder()
    finally:
        QFileDialog.getExistingDirectory = original_browse
    app.processEvents()
    assert len(dialog.item_list) == 3, dialog.item_list
    assert any(i.get("gdb_dataset") == "Asset_dataset" for i in dialog.item_list)
    assert len(dialog._get_checked_items()) == 3
    dialog._uncheck_all()
    assert not dialog._get_checked_items() and not dialog.load_btn.isEnabled()
    dialog._check_all()
    leaves = []
    def collect(item):
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            leaves.append(item)
        for index in range(item.childCount()):
            collect(item.child(index))
    for index in range(dialog.layer_tree.topLevelItemCount()):
        collect(dialog.layer_tree.topLevelItem(index))
    for leaf in leaves[:2]:
        leaf.setSelected(True)
    leaves[0].setCheckState(0, Qt.CheckState.Unchecked)
    assert len(dialog._get_checked_items()) == 1
    dialog.layer_tree.clearSelection()
    dialog._check_all()

    # Search changes visibility, never the layer choices, and reveals matches
    # inside collapsed folders. Check All / Uncheck All affect shown rows only.
    dialog.layer_tree.collapseAll()
    dialog.filter_edit.setText("  INSPECTION  ")
    assert len(list(dialog._iter_layer_items(visible_only=True))) == 1
    assert len(dialog._get_checked_items()) == 3
    visible = list(dialog._iter_layer_items(visible_only=True))[0]
    assert visible.parent().isExpanded()
    dialog._uncheck_all()
    assert len(dialog._get_checked_items()) == 2
    dialog._check_all()
    assert len(dialog._get_checked_items()) == 3
    dialog.filter_edit.setText("no matching dataset")
    assert not list(dialog._iter_layer_items(visible_only=True))
    assert "0 shown" in dialog.count_label.text()
    dialog._uncheck_all()
    assert len(dialog._get_checked_items()) == 3
    dialog.filter_edit.setText("SURVEY LAYERS")
    assert len(list(dialog._iter_layer_items(visible_only=True))) == 2
    folder = next(leaf.parent() for leaf in leaves if leaf.text(0) == "Terrain")
    folder.setSelected(True)
    assert dialog.toggle_btn.isEnabled()
    # Highlighting both a parent and child must not apply the toggle twice.
    folder.child(0).setSelected(True)
    assert len(dialog._selected_layer_items()) == 2
    dialog.toggle_btn.click()
    assert len(dialog._get_checked_items()) == 1
    dialog.toggle_btn.click()
    assert len(dialog._get_checked_items()) == 3
    dialog.layer_tree.clearSelection()
    assert not dialog.toggle_btn.isEnabled()
    survey_leaves = list(dialog._iter_layer_items(visible_only=True))
    survey_leaves[0].setCheckState(0, Qt.CheckState.Unchecked)
    for leaf in survey_leaves:
        leaf.setSelected(True)
    dialog.toggle_btn.click()
    assert len(dialog._get_checked_items()) == 3, "Mixed selection should become checked"
    # A rescan keeps the search text and reapplies it to the rebuilt tree.
    dialog._scan_folder()
    assert dialog.filter_edit.text() == "SURVEY LAYERS"
    assert len(list(dialog._iter_layer_items(visible_only=True))) == 2
    assert not dialog.toggle_btn.isEnabled()
    dialog.filter_edit.clear()
    assert len(list(dialog._iter_layer_items(visible_only=True))) == 3
    assert len(dialog._get_checked_items()) == 3
    dialog.group_check.setChecked(False)
    assert not dialog.expand_check.isEnabled()
    dialog.group_check.setChecked(True)
    assert dialog.expand_check.isEnabled()
    assert abs(dialog.group_check.geometry().y() - dialog.expand_check.geometry().y()) <= 2
    for width in (1000, 1200, 1600):
        dialog.resize(width, 750)
        app.processEvents()
        assert dialog.filter_edit.geometry().left() > dialog.probe_check.geometry().right()
        assert abs(dialog.filter_edit.geometry().center().y() - dialog.probe_check.geometry().center().y()) <= 2
        assert dialog.filter_edit.width() >= 120
        assert dialog.layer_tree.columnWidth(0) >= 180, [dialog.layer_tree.columnWidth(i) for i in range(5)]
        assert sum(dialog.layer_tree.columnWidth(i) for i in range(5)) <= dialog.layer_tree.viewport().width() + 2, (width, dialog.width(), dialog.layer_tree.viewport().width(), [dialog.layer_tree.columnWidth(i) for i in range(5)])
    dialog.resize(1200, 700)
    app.processEvents()
    light_palette = app.palette()
    if QT_VERSION_STR.startswith("6."):
        assert dialog.grab().save(str(preview_dir / "light.png"))
        dark = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, "#303030"),
            (QPalette.ColorRole.WindowText, "#eeeeee"),
            (QPalette.ColorRole.Base, "#252525"),
            (QPalette.ColorRole.AlternateBase, "#353535"),
            (QPalette.ColorRole.Text, "#eeeeee"),
            (QPalette.ColorRole.PlaceholderText, "#9aa0a6"),
            (QPalette.ColorRole.Button, "#404040"),
            (QPalette.ColorRole.ButtonText, "#eeeeee"),
            (QPalette.ColorRole.Highlight, "#2878b8"),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
        ):
            dark.setColor(role, QColor(color))
        app.setPalette(dark)
        app.processEvents()
        assert dialog.layer_tree.palette().base().color() == QColor("#252525")
        assert dialog.grab().save(str(preview_dir / "dark.png"))
        app.setPalette(light_palette)
    messages = []
    QMessageBox.information = lambda *args: messages.append(("info", args[2]))
    QMessageBox.warning = lambda *args: messages.append(("warning", args[2]))
    dialog.expand_check.setChecked(False)
    dialog._run_load()
    project = QgsProject.instance()
    assert len(project.mapLayers()) == 3 and all(layer.isValid() for layer in project.mapLayers().values())
    top = project.layerTreeRoot().findGroup(data_root.name)
    assert top is not None and not top.isExpanded()
    assert top.findGroup("Survey layers") and top.findGroup("Asset_dataset")
    assert messages[-1][0] == "info", messages
    project.clear()
    dialog.group_check.setChecked(False)
    dialog._run_load()
    assert len(project.mapLayers()) == 3 and not project.layerTreeRoot().findGroups()
    project.clear()
    empty = temporary / "empty"
    empty.mkdir()
    dialog.folder_edit.setText(str(empty))
    dialog._scan_folder()
    assert not dialog.item_list and not dialog.load_btn.isEnabled()
    assert not dialog.filter_edit.isEnabled() and not dialog.toggle_btn.isEnabled()
    dialog.close()

    def close_fresh_dialog():
        assert plugin.dlg is not None and not plugin.dlg.folder_edit.text()
        plugin.dlg.accept()
    for _ in range(2):
        QTimer.singleShot(100, close_fresh_dialog)
        plugin.action.trigger()
        assert plugin.dlg is None and not iface.errors, iface.errors
    plugin.unload()
    assert not iface.actions and plugin.action is None and plugin.toolbar is None
    iface.window.close()

print("PASS: ZIP import, toolbar/menu lifecycle, fresh dialog, widths at 1000/1200/1600, inline filter, layer/folder search and rescan, filtered bulk checks, mixed/group selection toggle, vector/raster/GDB load, grouped/flat load and empty scan", flush=True)
