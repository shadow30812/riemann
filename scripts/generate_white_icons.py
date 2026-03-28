import os
import xml.etree.ElementTree as ET


def make_icons_white():
    icons_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "python-app",
        "riemann",
        "assets",
        "icons",
    )

    ET.register_namespace("", "http://www.w3.org/2000/svg")

    for file in os.listdir(icons_dir):
        if file.endswith(".svg") and not file.endswith("-white.svg"):
            path = os.path.join(icons_dir, file)
            tree = ET.parse(path)
            root = tree.getroot()

            for elem in root.iter():
                if elem.get("stroke") and elem.get("stroke") not in [
                    "none",
                    "transparent",
                ]:
                    elem.set("stroke", "#FFFFFF")
                if elem.get("fill") and elem.get("fill") not in ["none", "transparent"]:
                    elem.set("fill", "#FFFFFF")

            if root.get("stroke") and root.get("stroke") not in ["none", "transparent"]:
                root.set("stroke", "#FFFFFF")
            if root.get("fill") and root.get("fill") not in ["none", "transparent"]:
                root.set("fill", "#FFFFFF")

            new_file = file.replace(".svg", "-white.svg")
            tree.write(
                os.path.join(icons_dir, new_file),
                xml_declaration=True,
                encoding="utf-8",
            )
            print(f"Generated {new_file}")


if __name__ == "__main__":
    make_icons_white()
