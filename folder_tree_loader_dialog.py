"""
Folder Tree Loader
============================================
Load a folder of GIS data (vectors and rasters in any format) into
QGIS while preserving the folder structure as layer-tree groups.

Features:
- Recursively scan a folder for vector and raster files
- Detect File Geodatabases (.gdb) and enumerate their layers
  using the OpenFileGDB driver (with Feature Dataset hierarchy)
- Display all discovered files in a tree matching the folder structure
- Show file extension, geometry/raster type, feature count, and CRS
- QGIS native layer-type icons (point, line, polygon, raster, table)
- Select / deselect individual layers to load
- Recreates the folder hierarchy as layer-tree groups in QGIS
- Progress reporting and detailed logging

Supported vector formats:
  .shp, .gpkg, .geojson, .json, .gml, .kml, .tab, .mif,
  .sqlite, .db, .fgb, .gdb (File Geodatabase)

Supported raster formats:
  .tif, .tiff, .img, .adf, .asc, .dem, .dt0, .dt1, .dt2,
  .nc, .hdf, .ecw, .jp2, .png, .jpg, .jpeg, .bmp, .vrt, .flt

Author: Irene Jaya
"""

import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QEvent
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit,
    QGroupBox, QCheckBox, QLineEdit, QTabWidget,
    QWidget, QScrollArea, QFileDialog, QApplication, QMessageBox,
    QHeaderView, QFrame, QTreeWidget, QTreeWidgetItem, QSizePolicy,
    QAbstractItemView,
)
from qgis.PyQt.QtGui import QFont, QPalette

from qgis.core import (
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsMessageLog, Qgis, QgsLayerTreeGroup,
)
from qgis.gui import QgsFilterLineEdit


LOG_TAG = "Folder Tree Loader"

# ============================================================================
# SUPPORTED EXTENSIONS
# ============================================================================

VECTOR_EXTENSIONS = {
    '.shp', '.gpkg', '.geojson', '.json', '.gml', '.kml',
    '.tab', '.mif', '.sqlite', '.db', '.fgb',
}

RASTER_EXTENSIONS = {
    '.tif', '.tiff', '.img', '.adf', '.asc', '.dem',
    '.dt0', '.dt1', '.dt2', '.nc', '.hdf', '.ecw',
    '.jp2', '.png', '.jpg', '.jpeg', '.bmp', '.vrt', '.flt',
}

ALL_GIS_EXTENSIONS = VECTOR_EXTENSIONS | RASTER_EXTENSIONS


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def format_timestamp() -> str:
    """Return formatted timestamp for logging."""
    return datetime.now().strftime("%H:%M:%S")


def _probe_vector(filepath: str) -> Dict:
    """Probe a vector file to extract geometry type, feature count, CRS.

    Returns a dict with keys: geom_type, geom_base, crs, feat_count.
    """
    info = {
        "geom_type": "",
        "geom_base": "unknown",
        "crs": "",
        "feat_count": -1,
    }

    try:
        from osgeo import ogr

        ds = ogr.Open(filepath, 0)
        if ds is None:
            return info

        lyr = ds.GetLayer(0)
        if lyr is None:
            ds = None
            return info

        gt = lyr.GetGeomType()

        # Flatten Z/M/ZM flags
        try:
            flat = ogr.GT_Flatten(gt)
        except Exception:
            flat = gt & 0x0FFFFFFF

        _BASE_MAP = {
            0: ("Unknown", "unknown"),
            1: ("Point", "point"),
            2: ("LineString", "line"),
            3: ("Polygon", "polygon"),
            4: ("MultiPoint", "point"),
            5: ("MultiLineString", "line"),
            6: ("MultiPolygon", "polygon"),
            7: ("GeometryCollection", "unknown"),
            100: ("None", "table"),
        }

        label, family = _BASE_MAP.get(flat, (f"Type({flat})", "unknown"))

        if gt == ogr.wkbNone:
            label, family = "Table", "table"
        else:
            try:
                has_z = ogr.GT_HasZ(gt)
                has_m = ogr.GT_HasM(gt)
            except Exception:
                has_z = bool(gt & 0x80000000)
                has_m = bool(gt & 0x40000000)

            if has_z and has_m:
                label += " ZM"
            elif has_z:
                label += " Z"
            elif has_m:
                label += " M"

        info["geom_type"] = label
        info["geom_base"] = family
        info["feat_count"] = lyr.GetFeatureCount()

        # CRS
        srs = lyr.GetSpatialRef()
        if srs:
            auth_name = srs.GetAuthorityName(None) or ""
            auth_code = srs.GetAuthorityCode(None) or ""
            if auth_name and auth_code:
                info["crs"] = f"{auth_name}:{auth_code}"
            else:
                info["crs"] = srs.GetName() or ""

        ds = None

    except Exception as exc:
        QgsMessageLog.logMessage(
            f"Could not read vector metadata for '{filepath}': {exc}",
            LOG_TAG, Qgis.MessageLevel.Warning, notifyUser=False,
        )

    return info


def _probe_raster(filepath: str) -> Dict:
    """Probe a raster file to extract basic info.

    Returns a dict with keys: geom_type, geom_base, crs, feat_count.
    feat_count is set to band count for rasters.
    """
    info = {
        "geom_type": "Raster",
        "geom_base": "raster",
        "crs": "",
        "feat_count": -1,
    }

    try:
        from osgeo import gdal

        ds = gdal.Open(filepath, gdal.GA_ReadOnly)
        if ds is None:
            return info

        info["feat_count"] = ds.RasterCount  # band count

        # Dimensions for display
        w, h = ds.RasterXSize, ds.RasterYSize
        info["geom_type"] = f"Raster ({w}×{h}, {ds.RasterCount} band{'s' if ds.RasterCount != 1 else ''})"

        # CRS
        from osgeo import osr
        srs = osr.SpatialReference()
        wkt = ds.GetProjection()
        if wkt:
            srs.ImportFromWkt(wkt)
            auth_name = srs.GetAuthorityName(None) or ""
            auth_code = srs.GetAuthorityCode(None) or ""
            if auth_name and auth_code:
                info["crs"] = f"{auth_name}:{auth_code}"
            else:
                info["crs"] = srs.GetAttrValue("PROJCS") or srs.GetAttrValue("GEOGCS") or ""

        ds = None

    except Exception as exc:
        QgsMessageLog.logMessage(
            f"Could not read raster metadata for '{filepath}': {exc}",
            LOG_TAG, Qgis.MessageLevel.Warning, notifyUser=False,
        )

    return info


def _enumerate_gdb_layers(gdb_path: str) -> List[Dict]:
    """Enumerate spatial layers inside a .gdb using OpenFileGDB.

    Returns a list of dicts compatible with the scan_folder result format.
    Each dict has: name, path, ext, kind, geom_type, geom_base, crs,
                   feat_count, rel_parts, is_gdb_layer, gdb_dataset.
    """
    results: List[Dict] = []
    norm = os.path.normpath(gdb_path)

    _BASE_MAP = {
        0: ("Unknown", "unknown"),
        1: ("Point", "point"),
        2: ("LineString", "line"),
        3: ("Polygon", "polygon"),
        4: ("MultiPoint", "point"),
        5: ("MultiLineString", "line"),
        6: ("MultiPolygon", "polygon"),
        7: ("GeometryCollection", "unknown"),
        100: ("None", "table"),
    }

    def _geom_label(ogr_type):
        try:
            from osgeo import ogr as _o
            flat = _o.GT_Flatten(ogr_type)
        except Exception:
            flat = ogr_type & 0x0FFFFFFF
        label, family = _BASE_MAP.get(flat, (f"Type({flat})", "unknown"))
        try:
            from osgeo import ogr as _o
            has_z = _o.GT_HasZ(ogr_type)
            has_m = _o.GT_HasM(ogr_type)
        except Exception:
            has_z = bool(ogr_type & 0x80000000)
            has_m = bool(ogr_type & 0x40000000)
        if has_z and has_m:
            label += " ZM"
        elif has_z:
            label += " Z"
        elif has_m:
            label += " M"
        return label, family

    def _crs_str(ogr_lyr):
        try:
            srs = ogr_lyr.GetSpatialRef()
            if srs is None:
                return ""
            a = srs.GetAuthorityName(None) or ""
            c = srs.GetAuthorityCode(None) or ""
            if a and c:
                return f"{a}:{c}"
            return srs.GetName() or ""
        except Exception:
            return ""

    try:
        from osgeo import gdal, ogr

        ds = gdal.OpenEx(norm, gdal.OF_VECTOR)
        if ds is None:
            return results

        root_grp = ds.GetRootGroup() if hasattr(ds, "GetRootGroup") else None

        if root_grp is not None:
            # Top-level layers
            top = (root_grp.GetVectorLayerNames()
                   if hasattr(root_grp, "GetVectorLayerNames") else []) or []
            for lname in top:
                lyr = ds.GetLayerByName(lname)
                if lyr is None:
                    continue
                gt = lyr.GetGeomType()
                if gt == ogr.wkbNone:
                    continue
                gl, gf = _geom_label(gt)
                results.append({
                    "name": lname,
                    "path": f"{norm}|layername={lname}",
                    "ext": ".gdb",
                    "kind": "vector",
                    "geom_type": gl,
                    "geom_base": gf,
                    "crs": _crs_str(lyr),
                    "feat_count": lyr.GetFeatureCount(),
                    "is_gdb_layer": True,
                    "gdb_dataset": None,
                })

            # Feature Dataset sub-groups
            grp_names = (root_grp.GetGroupNames()
                         if hasattr(root_grp, "GetGroupNames") else []) or []
            for gname in grp_names:
                grp = root_grp.OpenGroup(gname)
                if grp is None:
                    continue
                children = (grp.GetVectorLayerNames()
                            if hasattr(grp, "GetVectorLayerNames") else []) or []
                for lname in children:
                    lyr = ds.GetLayerByName(lname)
                    if lyr is None:
                        continue
                    gt = lyr.GetGeomType()
                    if gt == ogr.wkbNone:
                        continue
                    gl, gf = _geom_label(gt)
                    results.append({
                        "name": lname,
                        "path": f"{norm}|layername={lname}",
                        "ext": ".gdb",
                        "kind": "vector",
                        "geom_type": gl,
                        "geom_base": gf,
                        "crs": _crs_str(lyr),
                        "feat_count": lyr.GetFeatureCount(),
                        "is_gdb_layer": True,
                        "gdb_dataset": gname,
                    })

            ds = None
            if results:
                return results

        # Flat enumeration fallback
        for i in range(ds.GetLayerCount()):
            lyr = ds.GetLayerByIndex(i)
            if lyr is None:
                continue
            gt = lyr.GetGeomType()
            if gt == ogr.wkbNone:
                continue
            gl, gf = _geom_label(gt)
            results.append({
                "name": lyr.GetName(),
                "path": f"{norm}|layername={lyr.GetName()}",
                "ext": ".gdb",
                "kind": "vector",
                "geom_type": gl,
                "geom_base": gf,
                "crs": _crs_str(lyr),
                "feat_count": lyr.GetFeatureCount(),
                "is_gdb_layer": True,
                "gdb_dataset": None,
            })
        ds = None

    except Exception as exc:
        QgsMessageLog.logMessage(
            f"GDB enumeration failed for '{gdb_path}': {exc}",
            LOG_TAG, Qgis.MessageLevel.Warning,
        )

    return results


def scan_folder(
    folder_path: str,
    include_rasters: bool = True,
    include_vectors: bool = True,
    probe_metadata: bool = True,
) -> List[Dict]:
    """
    Recursively scan a folder for GIS data files.

    Returns a list of dicts, each with:
        name         – display name (filename or GDB layer name)
        path         – full file path or GDB layer URI
        ext          – file extension (lowercase, e.g. '.shp')
        kind         – 'vector', 'raster', or 'table'
        geom_type    – human-readable type string
        geom_base    – family for icon: 'point','line','polygon',
                       'raster','table','unknown'
        crs          – CRS auth string (e.g. 'EPSG:28355') or ''
        feat_count   – feature count (vector) / band count (raster) / -1
        is_gdb_layer – True if this is a layer from inside a .gdb
        gdb_dataset  – Feature Dataset name or None
        rel_parts    – tuple of path parts relative to scan root
    """
    results: List[Dict] = []
    root_norm = os.path.normpath(folder_path)

    allowed = set()
    if include_vectors:
        allowed |= VECTOR_EXTENSIONS
    if include_rasters:
        allowed |= RASTER_EXTENSIONS

    try:
        for dirpath, dirnames, filenames in os.walk(folder_path):
            # Detect .gdb directories at this level
            gdb_dirs = [d for d in dirnames if d.lower().endswith('.gdb')]
            for gdb_name in gdb_dirs:
                if not include_vectors:
                    continue
                gdb_full = os.path.join(dirpath, gdb_name)
                gdb_rel = os.path.relpath(gdb_full, root_norm)
                gdb_parts = Path(gdb_rel).parts

                gdb_layers = _enumerate_gdb_layers(gdb_full)
                for gl in gdb_layers:
                    # Build rel_parts: parent dirs + .gdb name + dataset + layer
                    parts = list(gdb_parts)
                    if gl.get("gdb_dataset"):
                        parts.append(gl["gdb_dataset"])
                    parts.append(gl["name"])
                    gl["rel_parts"] = tuple(parts)
                    results.append(gl)

            # Don't recurse into .gdb internals
            dirnames[:] = [d for d in dirnames
                           if not d.lower().endswith('.gdb')]

            for fname in sorted(filenames):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in allowed:
                    continue

                full_path = os.path.join(dirpath, fname)
                rel = os.path.relpath(full_path, root_norm)
                rel_parts = Path(rel).parts

                is_raster = ext in RASTER_EXTENSIONS
                is_vector = ext in VECTOR_EXTENSIONS

                if is_raster and include_rasters:
                    if probe_metadata:
                        info = _probe_raster(full_path)
                    else:
                        info = {
                            "geom_type": "Raster",
                            "geom_base": "raster",
                            "crs": "",
                            "feat_count": -1,
                        }
                    results.append({
                        "name": os.path.splitext(fname)[0],
                        "path": full_path,
                        "ext": ext,
                        "kind": "raster",
                        "geom_type": info["geom_type"],
                        "geom_base": info["geom_base"],
                        "crs": info["crs"],
                        "feat_count": info["feat_count"],
                        "is_gdb_layer": False,
                        "gdb_dataset": None,
                        "rel_parts": rel_parts,
                    })

                elif is_vector and include_vectors:
                    if probe_metadata:
                        info = _probe_vector(full_path)
                    else:
                        info = {
                            "geom_type": "",
                            "geom_base": "unknown",
                            "crs": "",
                            "feat_count": -1,
                        }
                    results.append({
                        "name": os.path.splitext(fname)[0],
                        "path": full_path,
                        "ext": ext,
                        "kind": "vector",
                        "geom_type": info["geom_type"],
                        "geom_base": info["geom_base"],
                        "crs": info["crs"],
                        "feat_count": info["feat_count"],
                        "is_gdb_layer": False,
                        "gdb_dataset": None,
                        "rel_parts": rel_parts,
                    })

    except Exception as e:
        QgsMessageLog.logMessage(
            f"Error scanning folder: {e}",
            LOG_TAG, Qgis.MessageLevel.Warning,
        )

    return results


def build_folder_tree(items: List[Dict]) -> Dict:
    """
    Build a nested folder tree from the scanned items.

    Each folder node is a dict with sub-folder keys and a '_items'
    list of item dicts (leaves).
    """
    tree: Dict = {"_items": []}
    for item in items:
        parts = item.get("rel_parts", ())
        if not parts:
            tree["_items"].append(item)
            continue
        # Navigate to the parent folder node, create sub-dicts as needed
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {"_items": []}
            current = current[part]
        current["_items"].append(item)
    return tree


# ============================================================================
# MAIN DIALOG CLASS
# ============================================================================

class FolderTreeLoaderDialog(QDialog):
    """Main dialog for loading a folder of GIS data into QGIS."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Folder Tree Loader")
        self.setWindowIcon(
            QgsApplication.getThemeIcon("/mActionAddOgrLayer.svg")
        )
        self.resize(1200, 700)
        self.setMinimumWidth(1000)

        # State
        self.scan_root: Optional[str] = None
        self.item_list: List[Dict] = []
        self._block_item_changed: bool = False  # guard against recursive itemChanged
        self._summary_state = "idle"

        self._build_ui()
        self._wire_signals()
        self._apply_theme()
        self._validate()

    def _status_color(self, status: str) -> str:
        dark = self.palette().window().color().lightness() < 128
        colors = {
            "muted": ("#6b7280", "#9aa0a6"),
            "warning": ("#b45f06", "#f0a030"),
            "success": ("#1a7f37", "#3fb950"),
            "info": ("#0969da", "#58a6ff"),
        }
        return colors[status][int(dark)]

    def _apply_theme(self):
        if hasattr(self, "summary_label"):
            if self._summary_state == "idle":
                self.summary_label.setStyleSheet(
                    f"color: {self._status_color('muted')}; font-style: italic;"
                )
            elif self._summary_state == "empty":
                self.summary_label.setStyleSheet(
                    f"color: {self._status_color('warning')};"
                )
            else:
                self.summary_label.setStyleSheet("font-weight: bold;")
        if hasattr(self, "count_label") and hasattr(self, "load_btn"):
            self._update_count()

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            if not getattr(self, "_theming", False):
                self._theming = True
                try:
                    self._apply_theme()
                finally:
                    self._theming = False
        super().changeEvent(event)

    # ==================================================================
    # UI CONSTRUCTION
    # ==================================================================
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        # Parameters tab
        self.parameters_tab = QWidget()
        self._setup_parameters_tab()
        self.tab_widget.addTab(self.parameters_tab, "Parameters")

        # Log tab
        self.log_tab = QWidget()
        self._setup_log_tab()
        self.tab_widget.addTab(self.log_tab, "Log")

        # Bottom bar
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(3, 3, 3, 3)
        bottom_layout.setSpacing(3)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(3)
        btn_layout.addStretch()

        self.load_btn = QPushButton("Load")
        self.load_btn.setDefault(True)
        self.load_btn.setEnabled(False)
        btn_layout.addWidget(self.load_btn)

        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.close_btn)

        bottom_layout.addLayout(btn_layout)
        main_layout.addWidget(bottom)

    # ------------------------------------------------------------------
    def _setup_parameters_tab(self):
        main_h = QHBoxLayout(self.parameters_tab)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        # LEFT – scrollable parameters
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setBackgroundRole(QPalette.ColorRole.Base)
        scroll.viewport().setAutoFillBackground(True)

        scroll_content = QWidget()
        scroll_content.setObjectName("scrollContent")
        scroll_content.setBackgroundRole(QPalette.ColorRole.Base)
        scroll_content.setAutoFillBackground(True)
        scroll_content.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(6, 6, 6, 6)
        scroll_layout.setSpacing(4)

        scroll_layout.addWidget(self._create_input_group(), 1)
        scroll_layout.addWidget(self._create_options_group())

        scroll.setWidget(scroll_content)
        main_h.addWidget(scroll, 70)

        # RIGHT – help panel
        help_panel = self._create_help_panel()
        main_h.addWidget(help_panel, 30)

    # ------------------------------------------------------------------
    def _create_input_group(self) -> QGroupBox:
        group = QGroupBox("Input Folder")
        layout = QVBoxLayout()
        layout.setSpacing(6)

        lbl = QLabel("Folder containing GIS data:")
        layout.addWidget(lbl)

        path_row = QHBoxLayout()
        path_row.setSpacing(4)

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select a folder to scan…")
        path_row.addWidget(self.folder_edit)

        self.browse_btn = QPushButton()
        self.browse_btn.setIcon(
            QgsApplication.getThemeIcon("/mActionFileOpen.svg")
        )
        self.browse_btn.setToolTip("Browse for folder")
        self.browse_btn.setFixedSize(24, 24)
        path_row.addWidget(self.browse_btn)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setMaximumWidth(80)
        self.scan_btn.setEnabled(False)
        path_row.addWidget(self.scan_btn)

        layout.addLayout(path_row)

        # Scan options — set before clicking Scan
        scan_opts_row = QHBoxLayout()
        scan_opts_row.setSpacing(16)

        self.vectors_check = QCheckBox("Vector layers")
        self.vectors_check.setChecked(True)
        self.vectors_check.setToolTip("Scan for and load vector datasets")
        scan_opts_row.addWidget(self.vectors_check)

        self.rasters_check = QCheckBox("Raster layers")
        self.rasters_check.setChecked(True)
        self.rasters_check.setToolTip("Scan for and load raster datasets")
        scan_opts_row.addWidget(self.rasters_check)

        self.probe_check = QCheckBox("Read metadata")
        self.probe_check.setChecked(True)
        self.probe_check.setToolTip(
            "When checked, each file is opened briefly to read its "
            "geometry type, CRS, and feature/band count. Disable for "
            "faster scanning on folders with many files."
        )
        scan_opts_row.addWidget(self.probe_check)

        self.filter_edit = QgsFilterLineEdit()
        self.filter_edit.setShowSearchIcon(True)
        self.filter_edit.setPlaceholderText("Search layer or folder name…")
        self.filter_edit.setAccessibleName("Filter layers or folders")
        self.filter_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.filter_edit.setMinimumWidth(120)
        self.filter_edit.setToolTip(
            "Filter layer or folder names (case-insensitive).\n"
            "Checked layers hidden by the filter will still load."
        )
        self.filter_edit.setEnabled(False)
        scan_opts_row.addWidget(self.filter_edit, 1)

        layout.addLayout(scan_opts_row)

        # Summary
        self.summary_label = QLabel(
            "No folder selected. Browse to a folder and click Scan."
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # Layer tree — 5 columns
        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderLabels(
            ["Name", "Ext.", "Type", "Count", "CRS"]
        )
        for column, description in enumerate((
            "Dataset or folder name", "File extension", "Geometry or raster type",
            "Feature count for vectors; band count for rasters", "Coordinate reference system",
        )):
            self.layer_tree.headerItem().setToolTip(column, description)
        self.layer_tree.setColumnCount(5)
        self.layer_tree.viewport().installEventFilter(self)
        self.layer_tree.setAlternatingRowColors(True)
        self.layer_tree.setMinimumHeight(300)
        self.layer_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.layer_tree.setToolTip(
            "Check/uncheck datasets to select them for loading.\n"
            "Tip: Select multiple rows (Ctrl+Click or Shift+Click), "
            "then toggle one checkbox to apply the same state to all selected layers."
        )

        hdr = self.layer_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self._resize_columns()

        layout.addWidget(self.layer_tree, 1)

        # Select all / none + count
        sel_row = QHBoxLayout()
        sel_row.setSpacing(4)

        self.select_all_btn = QPushButton("Check All")
        self.select_all_btn.setToolTip("Check all layers shown by the filter")
        self.select_all_btn.setEnabled(False)
        sel_row.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("Uncheck All")
        self.select_none_btn.setToolTip("Uncheck all layers shown by the filter")
        self.select_none_btn.setEnabled(False)
        sel_row.addWidget(self.select_none_btn)

        self.toggle_btn = QPushButton("Toggle Selection")
        self.toggle_btn.setToolTip(
            "Check the highlighted layers, or uncheck them if all are checked.\n"
            "Highlighted folders include their shown layers."
        )
        self.toggle_btn.setEnabled(False)
        sel_row.addWidget(self.toggle_btn)

        sel_row.addStretch()

        self.count_label = QLabel("")
        sel_row.addWidget(self.count_label)

        layout.addLayout(sel_row)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    def _create_options_group(self) -> QGroupBox:
        group = QGroupBox("Load Options")
        layout = QHBoxLayout()
        layout.setSpacing(16)

        self.group_check = QCheckBox("Create folder groups")
        self.group_check.setChecked(True)
        self.group_check.setToolTip(
            "Recreate the source folder hierarchy as groups in the "
            "QGIS layer tree."
        )
        layout.addWidget(self.group_check)

        self.expand_check = QCheckBox("Expand groups after loading")
        self.expand_check.setChecked(True)
        self.expand_check.setToolTip(
            "Expand the created layer-tree groups after loading."
        )
        layout.addWidget(self.expand_check)
        layout.addStretch()

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    def _create_help_panel(self) -> QWidget:
        help_panel = QFrame()
        help_panel.setObjectName("helpPanel")
        help_panel.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        help_panel.setAutoFillBackground(True)
        help_layout = QVBoxLayout(help_panel)
        help_layout.setContentsMargins(6, 6, 6, 6)

        help_title = QLabel("<b>Folder Tree Loader</b>")
        help_title.setWordWrap(True)
        help_layout.addWidget(help_title)

        help_text = QLabel(
            "Recursively scan a folder for GIS data and load selected "
            "layers into QGIS, preserving the folder structure as "
            "layer-tree groups.<br><br>"

            "<b>Workflow:</b><br>"
            "1. Browse to a folder<br>"
            "2. Click <i>Scan</i> to discover datasets<br>"
            "3. Check / uncheck layers you want to load<br>"
            "4. Adjust options<br>"
            "5. Click <i>Load</i><br><br>"

            "<b>Supported vectors:</b><br>"
            "SHP, GPKG, GeoJSON, GML, KML, TAB, MIF, SQLite, "
            "FlatGeobuf, File Geodatabase (.gdb)<br><br>"

            "<b>Supported rasters:</b><br>"
            "GeoTIFF, ERDAS IMG, Arc/Info ASCII Grid, DEM, "
            "NetCDF, HDF, ECW, JPEG2000, PNG, JPEG, BMP, VRT, "
            "Float Grid (.flt)<br><br>"

            "<b>Hierarchy:</b><br>"
            "• A top-level group is created for the root folder<br>"
            "• Sub-groups mirror the folder structure<br>"
            "• .gdb Feature Datasets become sub-groups<br><br>"

            "<b>Extension column:</b><br>"
            "The file extension is shown in a separate column so you "
            "can easily distinguish between formats (e.g. two files "
            "with the same name but different extensions).<br><br>"

            "<b>Tip:</b> Uncheck <i>Read metadata</i> for "
            "faster scanning on large folders — type and CRS columns "
            "will be blank but loading still works."
        )
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.TextFormat.RichText)
        help_layout.addWidget(help_text)
        help_layout.addStretch()

        return help_panel

    # ------------------------------------------------------------------
    def _setup_log_tab(self):
        layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 9))
        layout.addWidget(self.log_text)

        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.log_text.clear)
        layout.addWidget(clear_btn)

        self.log_tab.setLayout(layout)

    # ==================================================================
    # SIGNAL WIRING
    # ==================================================================
    def _wire_signals(self):
        self.browse_btn.clicked.connect(self._browse_folder)
        self.scan_btn.clicked.connect(self._scan_folder)
        self.folder_edit.textChanged.connect(self._on_path_text_changed)

        self.select_all_btn.clicked.connect(self._check_all)
        self.select_none_btn.clicked.connect(self._uncheck_all)
        self.toggle_btn.clicked.connect(self._toggle_selected)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.layer_tree.itemChanged.connect(self._on_tree_changed)
        self.layer_tree.itemSelectionChanged.connect(self._update_toggle_enabled)

        self.vectors_check.stateChanged.connect(lambda _: self._rescan_if_loaded())
        self.rasters_check.stateChanged.connect(lambda _: self._rescan_if_loaded())
        self.probe_check.stateChanged.connect(lambda _: self._rescan_if_loaded())
        self.group_check.toggled.connect(self.expand_check.setEnabled)

        self.load_btn.clicked.connect(self._run_load)
        self.close_btn.clicked.connect(self.close)

    # ==================================================================
    # BROWSING & SCANNING
    # ==================================================================
    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder Containing GIS Data",
            "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            self.folder_edit.setText(folder)
            self.scan_root = folder
            self.scan_btn.setEnabled(True)
            self._scan_folder()

    def _on_path_text_changed(self, text: str):
        text = text.strip()
        if text and os.path.isdir(text):
            self.scan_root = text
            self.scan_btn.setEnabled(True)
        else:
            self.scan_root = None
            self.scan_btn.setEnabled(bool(text))
        self._validate()

    def _rescan_if_loaded(self):
        if self.scan_root and os.path.isdir(self.scan_root):
            self._scan_folder()

    def _scan_folder(self):
        if not self.scan_root:
            return

        self._log(f"🔍 Scanning folder: {self.scan_root}")

        self.item_list = scan_folder(
            self.scan_root,
            include_rasters=self.rasters_check.isChecked(),
            include_vectors=self.vectors_check.isChecked(),
            probe_metadata=self.probe_check.isChecked(),
        )

        self.layer_tree.clear()
        self._resize_columns()

        if not self.item_list:
            self.summary_label.setText(
                "No GIS datasets found in the selected folder."
            )
            self._summary_state = "empty"
            self._apply_theme()
            self._set_tree_controls_enabled(False)
            self._log("⚠️ No GIS datasets found")
            self._update_count()
            self._validate()
            return

        # Summarise
        vectors = sum(1 for i in self.item_list if i["kind"] == "vector")
        rasters = sum(1 for i in self.item_list if i["kind"] == "raster")
        gdb_layers = sum(1 for i in self.item_list if i.get("is_gdb_layer"))
        ext_counts: Dict[str, int] = {}
        for i in self.item_list:
            ext_counts[i["ext"]] = ext_counts.get(i["ext"], 0) + 1

        parts = [f"{len(self.item_list)} dataset(s)"]
        if vectors:
            parts.append(f"{vectors} vector")
        if rasters:
            parts.append(f"{rasters} raster")
        if gdb_layers:
            parts.append(f"{gdb_layers} GDB layer(s)")

        self.summary_label.setText(
            f"{os.path.basename(self.scan_root)}  —  {', '.join(parts)}"
        )
        self._summary_state = "found"
        self._apply_theme()

        # Build tree
        tree = build_folder_tree(self.item_list)
        blocked = self.layer_tree.blockSignals(True)
        try:
            self._populate_tree(None, tree)
        finally:
            self.layer_tree.blockSignals(blocked)
        self.layer_tree.expandAll()

        self._apply_filter(self.filter_edit.text())
        self._set_tree_controls_enabled(True)

        self._log(f"✅ Found {len(self.item_list)} dataset(s)")
        if ext_counts:
            ext_summary = ", ".join(
                f"{ext} ({cnt})" for ext, cnt
                in sorted(ext_counts.items())
            )
            self._log(f"   Extensions: {ext_summary}")

        self._update_count()
        self._validate()

    # ==================================================================
    # TREE HELPERS
    # ==================================================================
    def _populate_tree(self, parent, tree_dict: Dict):
        for key, subtree in sorted(tree_dict.items()):
            if key == "_items":
                continue
            folder_item = self._make_folder_item(key)
            if parent is None:
                self.layer_tree.addTopLevelItem(folder_item)
            else:
                parent.addChild(folder_item)
            self._populate_tree(folder_item, subtree)

        for item_dict in tree_dict.get("_items", []):
            leaf = self._make_layer_item(item_dict)
            if parent is None:
                self.layer_tree.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)

    def _make_folder_item(self, name: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name, "", "", "", ""])
        item.setFlags(
            item.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setIcon(0, QgsApplication.getThemeIcon("/mIconFolder.svg"))
        f = item.font(0)
        f.setBold(True)
        item.setFont(0, f)
        return item

    def _make_layer_item(self, d: Dict) -> QTreeWidgetItem:
        fc = d.get("feat_count", -1)
        if d["kind"] == "raster":
            fc_str = f"{fc} band{'s' if fc != 1 else ''}" if fc > 0 else ""
        else:
            fc_str = f"{fc:,}" if fc >= 0 else ""

        display_name = d["name"]
        ext_str = d.get("ext", "")

        item = QTreeWidgetItem([
            display_name,
            ext_str,
            "Raster" if d["kind"] == "raster" else d.get("geom_type", ""),
            fc_str,
            d.get("crs", ""),
        ])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setData(0, Qt.ItemDataRole.UserRole, d)

        # QGIS native icon
        family = d.get("geom_base", "unknown")
        _ICON_MAP = {
            "point":   "/mIconPointLayer.svg",
            "line":    "/mIconLineLayer.svg",
            "polygon": "/mIconPolygonLayer.svg",
            "raster":  "/mIconRasterLayer.svg",
            "table":   "/mIconTableLayer.svg",
            "unknown": "/mIconVector.svg",
        }
        icon = _ICON_MAP.get(family, "/mIconVector.svg")
        item.setIcon(0, QgsApplication.getThemeIcon(icon))

        # Keep full values available when a compact column elides the text.
        for column in range(item.columnCount()):
            item.setToolTip(column, item.text(column))
        item.setToolTip(2, d.get("geom_type", ""))

        return item

    def eventFilter(self, watched, event):
        if (hasattr(self, "layer_tree")
                and watched is self.layer_tree.viewport()
                and event.type() == QEvent.Type.Resize):
            self._resize_columns()
        return super().eventFilter(watched, event)

    def _resize_columns(self):
        """Reserve compact metadata columns and give Name the remaining space.

        Size from the current font and scan results, with upper bounds so a
        long raster description or CRS name cannot crowd out layer names.
        Metadata columns remain manually resizable after each scan.
        """
        if getattr(self, "_sizing_columns", False):
            return
        metrics = self.layer_tree.fontMetrics()
        values = {
            1: ["Ext.", ".geojson"],
            2: ["Type", "Polygon"],
            3: ["Count", "99,999"],
            4: ["CRS", "EPSG:7855"],
        }
        for info in self.item_list:
            values[1].append(info.get("ext", ""))
            values[2].append("Raster" if info["kind"] == "raster" else info.get("geom_type", ""))
            count = info.get("feat_count", -1)
            if count >= 0:
                if info["kind"] == "raster":
                    values[3].append(f"{count} band{'s' if count != 1 else ''}")
                else:
                    values[3].append(f"{count:,}")
            values[4].append(info.get("crs", ""))
        caps = {1: ".geojson", 2: "MultiPolygon Z",
                3: "99,999,999", 4: "EPSG:99999999"}
        widths = {}
        for column, texts in values.items():
            width = max(metrics.horizontalAdvance(str(text)) for text in texts) + 24
            limit = metrics.horizontalAdvance(caps[column]) + 32
            widths[column] = min(width, limit)
        available = self.layer_tree.viewport().width()
        # Reserve at least one third of the table for names, including at
        # high Windows display scaling. Long values remain in tooltips.
        if available >= 500:
            budget = available - max(200, available // 3)
            total = sum(widths.values())
            if total > budget:
                widths = {column: int(width * budget / total)
                          for column, width in widths.items()}
        self._sizing_columns = True
        try:
            for column, width in widths.items():
                self.layer_tree.setColumnWidth(column, width)
        finally:
            self._sizing_columns = False

    def _get_checked_items(self) -> List[Dict]:
        checked: List[Dict] = []

        def _walk(node):
            if node.childCount() == 0:
                if node.checkState(0) == Qt.CheckState.Checked:
                    d = node.data(0, Qt.ItemDataRole.UserRole)
                    if d:
                        checked.append(d)
            else:
                for i in range(node.childCount()):
                    _walk(node.child(i))

        for i in range(self.layer_tree.topLevelItemCount()):
            _walk(self.layer_tree.topLevelItem(i))
        return checked

    def _iter_layer_items(self, visible_only=False):
        """Iterate layer rows; filtered ancestors hide their descendants too."""
        def walk(item):
            if visible_only and item.isHidden():
                return
            if item.data(0, Qt.ItemDataRole.UserRole) is not None:
                yield item
            for index in range(item.childCount()):
                yield from walk(item.child(index))

        for index in range(self.layer_tree.topLevelItemCount()):
            yield from walk(self.layer_tree.topLevelItem(index))

    def _selected_layer_items(self):
        """Include shown descendants of highlighted folders, without repeats."""
        items = []
        seen = set()

        def add(item):
            if item.isHidden():
                return
            if item.data(0, Qt.ItemDataRole.UserRole) is not None:
                if id(item) not in seen:
                    seen.add(id(item))
                    items.append(item)
            for index in range(item.childCount()):
                add(item.child(index))

        for item in self.layer_tree.selectedItems():
            add(item)
        return items

    def _set_check_states(self, state, items=None):
        if items is None:
            items = list(self._iter_layer_items(visible_only=True))
        self._block_item_changed = True
        try:
            for item in items:
                item.setCheckState(0, state)
        finally:
            self._block_item_changed = False
        self._update_count()

    def _check_all(self):
        self._set_check_states(Qt.CheckState.Checked)

    def _uncheck_all(self):
        self._set_check_states(Qt.CheckState.Unchecked)

    def _toggle_selected(self):
        items = self._selected_layer_items()
        if not items:
            return
        all_checked = all(item.checkState(0) == Qt.CheckState.Checked for item in items)
        self._set_check_states(
            Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked,
            items,
        )

    def _update_toggle_enabled(self):
        self.toggle_btn.setEnabled(bool(self._selected_layer_items()))

    def _set_tree_controls_enabled(self, enabled):
        self.select_all_btn.setEnabled(enabled)
        self.select_none_btn.setEnabled(enabled)
        self.filter_edit.setEnabled(enabled)
        self._update_toggle_enabled()

    def _apply_filter(self, text):
        """A matching folder keeps its entire subtree; preserve check states."""
        needle = text.strip().casefold()

        def filter_item(item, parent_match):
            match = parent_match or not needle or needle in item.text(0).casefold()
            if item.childCount():
                visible = False
                for index in range(item.childCount()):
                    if filter_item(item.child(index), match):
                        visible = True
            else:
                visible = match
            item.setHidden(not visible)
            if needle and visible and item.childCount():
                item.setExpanded(True)
            return visible

        for index in range(self.layer_tree.topLevelItemCount()):
            filter_item(self.layer_tree.topLevelItem(index), False)
        self._update_count()
        self._update_toggle_enabled()

    def _on_tree_changed(self, item, col):
        # Only react to checkbox column changes
        if col != 0 or self._block_item_changed:
            return

        # Propagate to all other selected *leaf* (layer) items
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            selected = self._selected_layer_items()
            if item in selected and len(selected) > 1:
                self._set_check_states(item.checkState(0), selected)
                return

        self._update_count()
        self._validate()

    def _update_count(self):
        total = len(self.item_list)
        checked = len(self._get_checked_items())
        if total > 0:
            text = f"{checked} of {total} selected"
            if self.filter_edit.text().strip():
                shown = sum(1 for _ in self._iter_layer_items(visible_only=True))
                text = f"{shown} shown  ·  {text}"
            self.count_label.setText(text)
            if checked == 0:
                self.count_label.setStyleSheet(f"color: {self._status_color('warning')};")
            elif checked == total:
                self.count_label.setStyleSheet(f"color: {self._status_color('success')};")
            else:
                self.count_label.setStyleSheet(f"color: {self._status_color('info')};")
        else:
            self.count_label.setText("")
        self._validate()

    # ==================================================================
    # VALIDATION
    # ==================================================================
    def _validate(self):
        ok = (
            self.scan_root is not None
            and os.path.isdir(self.scan_root or "")
            and len(self._get_checked_items()) > 0
        )
        self.load_btn.setEnabled(ok)

    # ==================================================================
    # LOADING
    # ==================================================================
    def _run_load(self):
        selected = self._get_checked_items()
        if not selected:
            QMessageBox.warning(
                self, "Nothing selected", "Select at least one dataset."
            )
            return

        self.tab_widget.setCurrentIndex(1)
        self.load_btn.setEnabled(False)
        self.load_btn.setText("Loading…")
        self.progress_bar.setValue(0)
        QApplication.processEvents()

        root_name = os.path.basename(self.scan_root)
        use_groups = self.group_check.isChecked()
        expand = self.expand_check.isChecked()

        self._log(f"\n{'=' * 60}")
        self._log(f"🚀 Loading {len(selected)} dataset(s) from {root_name}")
        self._log(f"{'=' * 60}\n")

        project_root = QgsProject.instance().layerTreeRoot()

        # Top-level group
        top_group: QgsLayerTreeGroup = (
            project_root.insertGroup(0, root_name)
            if use_groups else project_root
        )

        # Cache of groups keyed by relative folder path tuple
        group_cache: Dict[Tuple, QgsLayerTreeGroup] = {}

        loaded = 0
        failed = 0
        total = len(selected)

        for idx, item in enumerate(selected):
            name = item["name"]
            path = item["path"]
            kind = item["kind"]
            ext = item.get("ext", "")
            geom_type = item.get("geom_type", "")

            pct = int((idx / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{pct}%  –  {name}")
            QApplication.processEvents()

            self._log(f"   📄 Loading: {name}  [{ext}]  ({geom_type})")

            # Determine target group from rel_parts
            rel_parts = item.get("rel_parts", ())
            folder_parts = rel_parts[:-1] if rel_parts else ()

            if use_groups and folder_parts:
                target = self._ensure_group(
                    top_group, folder_parts, group_cache
                )
            else:
                target = top_group

            # Load layer
            layer = None
            if kind == "raster":
                layer = QgsRasterLayer(path, name, "gdal")
            elif kind == "vector":
                layer = QgsVectorLayer(path, name, "ogr")

            if layer is None or not layer.isValid():
                self._log(f"      ❌ Failed to load '{name}'")
                failed += 1
                continue

            QgsProject.instance().addMapLayer(layer, False)
            target.addLayer(layer)
            loaded += 1

            # Log details
            if kind == "raster":
                crs = layer.crs().authid()
                self._log(
                    f"      ✅ Loaded raster "
                    f"({layer.width()}×{layer.height()}, "
                    f"{layer.bandCount()} bands, CRS: {crs})"
                )
            else:
                crs = layer.crs().authid()
                self._log(
                    f"      ✅ Loaded "
                    f"({layer.featureCount():,} features, CRS: {crs})"
                )

        # Expand / collapse
        if use_groups:
            top_group.setExpanded(expand)
            if expand:
                for g in group_cache.values():
                    g.setExpanded(True)

        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("100%")

        self._log(f"\n{'=' * 60}")
        self._log(f"✅ LOADING COMPLETE")
        self._log(f"   Loaded: {loaded}   Failed: {failed}   Total: {total}")
        self._log(f"{'=' * 60}")

        self.load_btn.setEnabled(True)
        self.load_btn.setText("Load")

        if failed:
            QMessageBox.warning(
                self, "Loading Complete (with errors)",
                f"Loaded {loaded} of {total} dataset(s).\n"
                f"{failed} failed — see log for details.",
            )
        else:
            QMessageBox.information(
                self, "Loading Complete",
                f"Successfully loaded {loaded} dataset(s) from\n{root_name}",
            )

        self.progress_bar.setValue(0)

    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_group(
        root_group: QgsLayerTreeGroup,
        parts: Tuple,
        cache: Dict[Tuple, QgsLayerTreeGroup],
    ) -> QgsLayerTreeGroup:
        """Get or create the nested group for the given path parts."""
        if parts in cache:
            return cache[parts]

        current = root_group
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            if key in cache:
                current = cache[key]
            else:
                part_name = parts[depth - 1]
                # Check if a child group with this name already exists
                existing = None
                for child in current.children():
                    if (isinstance(child, QgsLayerTreeGroup)
                            and child.name() == part_name):
                        existing = child
                        break
                if existing:
                    current = existing
                else:
                    current = current.addGroup(part_name)
                cache[key] = current

        return current

    # ==================================================================
    # LOGGING
    # ==================================================================
    def _log(self, message: str):
        ts = format_timestamp()
        self.log_text.append(f"[{ts}] {message}")
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())
