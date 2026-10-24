"""Build an installable QGIS ZIP: python build_plugin.py."""

import configparser
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def build():
    root = Path(__file__).resolve().parent
    metadata = configparser.ConfigParser(interpolation=None)
    metadata.read(root / "metadata.txt", encoding="utf-8")
    version = metadata["general"]["version"]
    files = (
        "__init__.py", "plugin.py", "folder_tree_loader_dialog.py",
        "metadata.txt", "icon.svg", "LICENSE", "README.md",
    )
    for name in files:
        if not (root / name).is_file():
            raise FileNotFoundError(root / name)
    destination = root / "dist" / f"folder_tree_loader-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root / name, f"folder_tree_loader/{name}")
    print(destination)
    return destination


if __name__ == "__main__":
    build()
