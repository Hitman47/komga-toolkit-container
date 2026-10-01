from pathlib import Path

from PySide6.QtWidgets import (QFileDialog, QGridLayout, QGroupBox, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .app_settings import save_config
from .file_sources import FILE_SOURCES, test_file_source
from .desktop_theme import set_appearance


class DesktopFileSourcesMixin:
    def build_file_sources_panel(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        help_label = QLabel("Choisissez un fichier, testez-le puis activez-le. Les catalogues validés restent disponibles en secours. MangaCollec est réimporté automatiquement lorsque son fichier change.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.file_source_fields = {}
        self.file_source_status = {}
        self.file_source_buttons = {}
        for kind, label in FILE_SOURCES.items():
            box = QGroupBox(label)
            grid = QGridLayout(box)
            field = QLineEdit()
            field.setMinimumWidth(0)
            field.setPlaceholderText("Chemin du CSV / JSON" if kind == "mangacollec" else "Chemin du CSV")
            browse = QPushButton("Parcourir…")
            test = QPushButton("Tester")
            activate = QPushButton("Tester et activer")
            activate.setProperty("appearance", "primary")
            status = QLabel("Pas encore testé")
            status.setWordWrap(True)
            grid.addWidget(QLabel("Emplacement du fichier"), 0, 0, 1, 3)
            grid.addWidget(field, 1, 0, 1, 2)
            grid.addWidget(browse, 1, 2)
            grid.addWidget(status, 2, 0, 1, 3)
            grid.addWidget(test, 3, 1)
            grid.addWidget(activate, 3, 2)
            grid.setColumnStretch(0, 1)
            layout.addWidget(box)
            self.file_source_fields[kind] = field
            self.file_source_status[kind] = status
            self.file_source_buttons[kind] = (browse, test, activate)
            browse.clicked.connect(lambda _=False, k=kind: self.browse_file_source(k))
            test.clicked.connect(lambda _=False, k=kind: self.test_catalog_file(k))
            activate.clicked.connect(lambda _=False, k=kind: self.test_catalog_file(k, activate=True))
            field.textEdited.connect(lambda _text, k=kind: self.file_source_status[k].setText("Emplacement à tester — activation précédente conservée"))
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def sync_file_source_settings(self):
        for kind, widget in (("bedetheque", self.bdt_csv_path), ("nautiljon", self.nautiljon_csv_path)):
            self.file_source_fields[kind].setText(widget.text())
            if not getattr(widget, "_file_source_bound", False):
                widget.textChanged.connect(self.file_source_fields[kind].setText)
                widget._file_source_bound = True
        path = self.config.mangacollec.file_path or str(self.mangacollec_store._catalog_payload().get("source_path") or "")
        self.file_source_fields["mangacollec"].setText(path)

    def browse_file_source(self, kind):
        field = self.file_source_fields[kind]
        path, _ = QFileDialog.getOpenFileName(self, FILE_SOURCES[kind], field.text(),
            "Catalogues (*.csv *.json)" if kind == "mangacollec" else "CSV (*.csv)")
        if path:
            field.setText(path)
            self.file_source_status[kind].setText("Emplacement à tester — activation précédente conservée")

    def test_catalog_file(self, kind, *, activate=False):
        path = self.file_source_fields[kind].text().strip()
        active_path = (self.bdt_csv_path.text() if kind == "bedetheque" else
                       self.nautiljon_csv_path.text() if kind == "nautiljon" else
                       self.config.mangacollec.file_path)
        for button in self.file_source_buttons[kind]:
            button.setEnabled(False)
        self.file_source_fields[kind].setEnabled(False)
        self.file_source_status[kind].setText("Validation du fichier en cours…")
        set_appearance(self.file_source_status[kind], "muted")

        def work():
            try:
                # A replacement must be valid itself, even if an old mirror exists.
                result = test_file_source(kind, path, fallback=not activate and path == active_path,
                                          mangacollec_store=self.mangacollec_store)
                if activate:
                    if kind == "mangacollec":
                        self.mangacollec_store.import_file(path)
                    else:
                        test_file_source(kind, path, fallback=True)
                return result
            except Exception as exc:
                return {"error": str(exc)}

        def done(result):
            if "error" in result:
                self.file_source_status[kind].setText(f"Échec : {result['error']} — emplacement actif conservé")
                set_appearance(self.file_source_status[kind], "warning")
                return
            self.file_source_status[kind].setText(
                f"{'Activé et enregistré · ' if activate else ''}{result['status']}\n"
                f"{result['validation']}\n{result['size_bytes']:,} octets · date source : {result['updated_at']}")
            set_appearance(self.file_source_status[kind], "success")
            if activate:
                if kind == "mangacollec":
                    self.config.mangacollec.file_path = str(Path(path).expanduser().resolve())
                    self._refresh_mangacollec_catalog_status()
                    self._refresh_next_release_mangacollec_status()
                else:
                    widget = self.bdt_csv_path if kind == "bedetheque" else self.nautiljon_csv_path
                    widget.setText(path)
                    getattr(self.config, kind).csv_path = path
                save_config(self.config, self.config_path)
                self.log(f"✅ Fichier {FILE_SOURCES[kind]} validé et enregistré")

        def finished():
            self.file_source_fields[kind].setEnabled(True)
            for button in self.file_source_buttons[kind]:
                button.setEnabled(True)
        self.run_worker(f"Validation fichier {FILE_SOURCES[kind]}", work, done, finished)
