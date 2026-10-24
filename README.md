# Folder Tree Loader

A QGIS plugin that loads selected vector and raster datasets from a folder and recreates its folder tree as QGIS layer groups. File Geodatabases are scanned with OpenFileGDB, including Feature Dataset groups where supported by GDAL.

## Install

1. In QGIS, open **Plugins > Manage and Install Plugins > Install from ZIP**.
2. Select `dist/folder_tree_loader-1.1.0.zip` from this repository and click **Install Plugin**.
3. Open **Folder Tree Loader** from its toolbar button or the Plugins menu.

To rebuild the ZIP, run `python build_plugin.py` from this directory. The ZIP contains a single `folder_tree_loader` package directory, suitable for QGIS installation. No pip dependencies are required; the plugin uses QGIS's bundled PyQt and GDAL.

For manual installation, copy `__init__.py`, `plugin.py`, `folder_tree_loader_dialog.py`, `metadata.txt`, `icon.svg`, `LICENSE` and `README.md` into a `folder_tree_loader` directory under the active QGIS profile's `python/plugins` directory, then enable it in Plugin Manager.

## Use

1. Browse to a folder, or paste its path and click **Scan**.
2. Choose whether to scan **Vector layers**, **Raster layers**, and **Read metadata**. Changing these options rescans the selected folder.
3. Use the filter beside **Read metadata** to search layer or folder names (case-insensitive). Matching folders show their contents, and matching layers reveal their parent folders. Clearing the filter restores all rows. Filtering preserves checks: checked layers hidden by a search will still load.
4. Check the datasets to load. **Check All** and **Uncheck All** affect only layers shown by the filter. Ctrl/Shift-click to highlight rows and use **Toggle Selection** beside **Uncheck All**: it checks the highlighted layers, or unchecks them when all are already checked. A highlighted folder includes its shown descendants. Changing a highlighted layer's checkbox also applies that state to the highlighted layers.
5. Choose **Create folder groups** and **Expand groups after loading**, displayed side by side. Expansion is disabled when folder groups are turned off.
6. Click **Load**. Results and failures appear in the **Log** tab.

The **Name** column fills the available table width, with at least a third reserved for names at normal dialog sizes. **Ext.**, **Type**, **Count** and **CRS** use compact widths that adapt after a scan or window resize; drag their header dividers to adjust them. **Count** means features for vectors and bands for rasters. Header tooltips explain each column, and cell tooltips show full values, including raster dimensions. Long metadata values do not force names off screen.

The layout follows Geodatabase Loader: Parameters and Log tabs, a help panel, a progress bar, a dedicated toolbar, and a fresh dialog each time it opens. Colours follow the active QGIS palette.

## Supported data and versions

Vector formats: SHP, GPKG, GeoJSON, JSON, GML, KML, TAB, MIF, SQLite/DB, FlatGeobuf and File Geodatabase. Raster formats include GeoTIFF, IMG, ASCII Grid, DEM, NetCDF, HDF, ECW, JPEG2000, PNG, JPEG, BMP, VRT and FLT. Availability depends on the drivers included with QGIS.

For ordinary multi-layer vector files, the current scanner loads the default layer. File Geodatabases are enumerated as individual layers. File Geodatabase Feature Dataset detection uses the GDAL Group API where available, with a flat-layer fallback.

Targets QGIS 3.34 through 4.x, using `qgis.PyQt` and scoped enums for Qt5/Qt6 compatibility.

Validated with the packaged ZIP in QGIS 4.2.3 / Qt 6.11 and QGIS 3.44.15 / Qt 5.15. Tests cover menu/toolbar lifecycle, fresh dialog launches, column sizing and inline filter placement at three dialog widths, layer/folder searches, filtered bulk checks, mixed and folder selection toggles, rescanning, horizontal options, vector/raster/File Geodatabase loading, group creation and flat loading. To repeat these checks, run `python-qgis tests/smoke_test.py` (or the corresponding Qt5 launcher) from an OSGeo4W shell. The test renders light/dark previews into `dist/previews`.

## License

MIT. See `LICENSE`.
