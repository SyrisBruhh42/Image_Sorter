with open("imagesorter/src/queue_worker.py", "r") as f:
    content = f.read()

dup = """            elif task_type == 'undo_move':
                # Move back
                dest_path = task['dest_folder'] # Here dest_folder is the original path
                shutil.move(filepath, dest_path)
                self.signals.progress.emit(f"Undid move: {filename}")
                self.signals.finished.emit(dest_path)
                return

            elif task_type == 'undo_copy':
                # Delete the copy
                os.remove(filepath)
                self.signals.progress.emit(f"Undid copy: {filename}")
                # Don't emit finished here, it's just cleanup
                return"""

content = content.replace(dup + "\n\n" + dup.replace("                \n", "\n"), dup)

with open("imagesorter/src/queue_worker.py", "w") as f:
    f.write(content)
