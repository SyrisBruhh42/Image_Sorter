import re
with open("imagesorter/src/ui_main.py", "r") as f:
    content = f.read()

dup1 = """        self.zen_mode = False

        # Zero-Latency Caching
        self.pixmap_cache = {}

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()

        self.zen_mode = False

        # Zero-Latency Caching
        self.pixmap_cache = {}

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()"""
content = content.replace(dup1, """        self.zen_mode = False

        # Zero-Latency Caching
        self.pixmap_cache = {}

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_preloaded)
        self.loader.start()""")

dup2 = """    def on_image_preloaded(self, filepath, img):
        if filepath not in self.pixmap_cache:
            self.pixmap_cache[filepath] = QPixmap.fromImage(img)

    def preload_adjacent_images(self):
        # Keep cache manageable (store only recent adjacent paths)
        # Actually a simple LRU or just bounding size is better, but since this is file-path based now,
        # we'll just clear it if it gets too large
        if len(self.pixmap_cache) > 10:
            # We don't have a strict order, but since we rely on it mainly for immediate next/prev,
            # this is just to prevent infinite memory growth
            self.pixmap_cache.clear()

        # Preload next image
        if self.current_index + 1 < len(self.images):
            next_path = self.images[self.current_index + 1]
            if next_path not in self.pixmap_cache:
                self.loader.add_task(next_path)

        # Preload previous image
        if self.current_index - 1 >= 0:
            prev_path = self.images[self.current_index - 1]
            if prev_path not in self.pixmap_cache:
                self.loader.add_task(prev_path)

    def on_image_preloaded(self, filepath, img):
        if filepath not in self.pixmap_cache:
            self.pixmap_cache[filepath] = QPixmap.fromImage(img)

    def preload_adjacent_images(self):
        # Keep cache manageable (store only recent adjacent paths)
        # Actually a simple LRU or just bounding size is better, but since this is file-path based now,
        # we'll just clear it if it gets too large
        if len(self.pixmap_cache) > 10:
            # We don't have a strict order, but since we rely on it mainly for immediate next/prev,
            # this is just to prevent infinite memory growth
            self.pixmap_cache.clear()

        # Preload next image
        if self.current_index + 1 < len(self.images):
            next_path = self.images[self.current_index + 1]
            if next_path not in self.pixmap_cache:
                self.loader.add_task(next_path)

        # Preload previous image
        if self.current_index - 1 >= 0:
            prev_path = self.images[self.current_index - 1]
            if prev_path not in self.pixmap_cache:
                self.loader.add_task(prev_path)"""
content = content.replace(dup2, """    def on_image_preloaded(self, filepath, img):
        if filepath not in self.pixmap_cache:
            self.pixmap_cache[filepath] = QPixmap.fromImage(img)

    def preload_adjacent_images(self):
        # Keep cache manageable (store only recent adjacent paths)
        # Actually a simple LRU or just bounding size is better, but since this is file-path based now,
        # we'll just clear it if it gets too large
        if len(self.pixmap_cache) > 10:
            # We don't have a strict order, but since we rely on it mainly for immediate next/prev,
            # this is just to prevent infinite memory growth
            self.pixmap_cache.clear()

        # Preload next image
        if self.current_index + 1 < len(self.images):
            next_path = self.images[self.current_index + 1]
            if next_path not in self.pixmap_cache:
                self.loader.add_task(next_path)

        # Preload previous image
        if self.current_index - 1 >= 0:
            prev_path = self.images[self.current_index - 1]
            if prev_path not in self.pixmap_cache:
                self.loader.add_task(prev_path)""")

dup3 = """        edit_menu = self.main_menu.addMenu("Edit")
        undo_action = QAction("Undo (Ctrl+Z)", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self.undo_last_action)
        edit_menu.addAction(undo_action)

        view_menu = self.main_menu.addMenu("View")

        self.locked_zoom_action = QAction("Locked Zoom/Pan (L)", self)
        self.locked_zoom_action.setCheckable(True)
        self.locked_zoom_action.setShortcut(QKeySequence("L"))
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_mode_action = QAction("Zen Mode (Z)", self)
        zen_mode_action.setShortcut(QKeySequence("Z"))
        zen_mode_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_mode_action)

        clipping_action = QAction("Clipping Warnings (C)", self)
        clipping_action.setShortcut(QKeySequence("C"))
        clipping_action.triggered.connect(self.viewer.toggle_clipping_warnings)
        view_menu.addAction(clipping_action)

    def toggle_locked_zoom(self, checked):
        self.viewer.locked_zoom_pan = checked
        if not checked and self.viewer.isVisible():
            self.viewer.fit_to_window()

    def toggle_zen_mode(self):
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.main_menu.hide()
            self.statusBar().hide()
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            self.showFullScreen()
        else:
            self.main_menu.show()
            self.statusBar().show()
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.FramelessWindowHint)
            if self.settings.get('ui', 'fullscreen'):
                self.showFullScreen()
            else:
                self.showMaximized()


        edit_menu = self.main_menu.addMenu("Edit")
        undo_action = QAction("Undo (Ctrl+Z)", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self.undo_last_action)
        edit_menu.addAction(undo_action)

        view_menu = self.main_menu.addMenu("View")

        self.locked_zoom_action = QAction("Locked Zoom/Pan (L)", self)
        self.locked_zoom_action.setCheckable(True)
        self.locked_zoom_action.setShortcut(QKeySequence("L"))
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_mode_action = QAction("Zen Mode (Z)", self)
        zen_mode_action.setShortcut(QKeySequence("Z"))
        zen_mode_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_mode_action)

        clipping_action = QAction("Clipping Warnings (C)", self)
        clipping_action.setShortcut(QKeySequence("C"))
        clipping_action.triggered.connect(self.viewer.toggle_clipping_warnings)
        view_menu.addAction(clipping_action)

    def toggle_locked_zoom(self, checked):
        self.viewer.locked_zoom_pan = checked
        if not checked and self.viewer.isVisible():
            self.viewer.fit_to_window()

    def toggle_zen_mode(self):
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.main_menu.hide()
            self.statusBar().hide()
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            self.showFullScreen()
        else:
            self.main_menu.show()
            self.statusBar().show()
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.FramelessWindowHint)
            if self.settings.get('ui', 'fullscreen'):
                self.showFullScreen()
            else:
                self.showMaximized()"""
content = content.replace(dup3, """        edit_menu = self.main_menu.addMenu("Edit")
        undo_action = QAction("Undo (Ctrl+Z)", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self.undo_last_action)
        edit_menu.addAction(undo_action)

        view_menu = self.main_menu.addMenu("View")

        self.locked_zoom_action = QAction("Locked Zoom/Pan (L)", self)
        self.locked_zoom_action.setCheckable(True)
        self.locked_zoom_action.setShortcut(QKeySequence("L"))
        self.locked_zoom_action.triggered.connect(self.toggle_locked_zoom)
        view_menu.addAction(self.locked_zoom_action)

        zen_mode_action = QAction("Zen Mode (Z)", self)
        zen_mode_action.setShortcut(QKeySequence("Z"))
        zen_mode_action.triggered.connect(self.toggle_zen_mode)
        view_menu.addAction(zen_mode_action)

        clipping_action = QAction("Clipping Warnings (C)", self)
        clipping_action.setShortcut(QKeySequence("C"))
        clipping_action.triggered.connect(self.viewer.toggle_clipping_warnings)
        view_menu.addAction(clipping_action)

    def toggle_locked_zoom(self, checked):
        self.viewer.locked_zoom_pan = checked
        if not checked and self.viewer.isVisible():
            self.viewer.fit_to_window()

    def toggle_zen_mode(self):
        self.zen_mode = not self.zen_mode
        if self.zen_mode:
            self.main_menu.hide()
            self.statusBar().hide()
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            self.showFullScreen()
        else:
            self.main_menu.show()
            self.statusBar().show()
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.FramelessWindowHint)
            if self.settings.get('ui', 'fullscreen'):
                self.showFullScreen()
            else:
                self.showMaximized()""")

dup4 = """        if key == Qt.Key.Key_Z:
            self.toggle_zen_mode()
            return

        if key == Qt.Key.Key_L:
            self.locked_zoom_action.setChecked(not self.locked_zoom_action.isChecked())
            self.toggle_locked_zoom(self.locked_zoom_action.isChecked())
            return

        if key == Qt.Key.Key_X:
            self.viewer.toggle_smart_zoom()
            return

        if key == Qt.Key.Key_C:
            self.viewer.toggle_clipping_warnings()
            return

        if key == Qt.Key.Key_Z:
            self.toggle_zen_mode()
            return

        if key == Qt.Key.Key_L:
            self.locked_zoom_action.setChecked(not self.locked_zoom_action.isChecked())
            self.toggle_locked_zoom(self.locked_zoom_action.isChecked())
            return

        if key == Qt.Key.Key_X:
            self.viewer.toggle_smart_zoom()
            return

        if key == Qt.Key.Key_C:
            self.viewer.toggle_clipping_warnings()
            return"""
content = content.replace(dup4, """        if key == Qt.Key.Key_Z:
            self.toggle_zen_mode()
            return

        if key == Qt.Key.Key_L:
            self.locked_zoom_action.setChecked(not self.locked_zoom_action.isChecked())
            self.toggle_locked_zoom(self.locked_zoom_action.isChecked())
            return

        if key == Qt.Key.Key_X:
            self.viewer.toggle_smart_zoom()
            return

        if key == Qt.Key.Key_C:
            self.viewer.toggle_clipping_warnings()
            return""")

with open("imagesorter/src/ui_main.py", "w") as f:
    f.write(content)
