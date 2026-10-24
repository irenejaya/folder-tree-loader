"""Folder Tree Loader QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import FolderTreeLoaderPlugin
    return FolderTreeLoaderPlugin(iface)
