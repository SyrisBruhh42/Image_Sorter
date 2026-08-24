import re

with open("imagesorter/src/ui_main.py", "r") as f:
    content = f.read()

# Replace all simple <<<<<<< Updated upstream \n\n=======\n[whitespace]\n>>>>>>> Stashed changes
content = re.sub(r"<<<<<<< Updated upstream\n\n=======\n\s*?\n>>>>>>> Stashed changes\n", r"\n", content)
content = re.sub(r"<<<<<<< Updated upstream\n\s*?=======\n\s*?\n>>>>>>> Stashed changes\n", r"\n", content)
content = re.sub(r"<<<<<<< Updated upstream\n.*?\n=======\n.*?\n>>>>>>> Stashed changes\n", lambda m: m.group(0).split("=======\n")[0].replace("<<<<<<< Updated upstream\n", ""), content, flags=re.DOTALL)


with open("imagesorter/src/ui_main.py", "w") as f:
    f.write(content)
