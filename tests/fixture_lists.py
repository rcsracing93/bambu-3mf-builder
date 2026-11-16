"""Test helper: give a builder file an inherits_group (the builder writes none for system presets, as Studio does). The entries name
the system preset of each entry that has a diff list entry, the form a project with a parent per entry has, so that the R3, R11 and teeth
fixtures keep both lists present."""
import json
import zipfile

PS = "Metadata/project_settings.config"


def add_inherits_group(path):
    z = zipfile.ZipFile(path)
    items = [(i, z.read(i.filename)) for i in z.infolist()]
    z.close()
    out = []
    for i, d in items:
        if i.filename == PS:
            c = json.loads(d)
            dl = c.get("different_settings_to_system") or []
            names = [c["print_settings_id"]] + list(c["filament_settings_id"]) + [c["printer_settings_id"]]
            c["inherits_group"] = [names[k] if k < len(dl) and dl[k] else "" for k in range(len(names))]
            c = dict(sorted(c.items()))
            d = (json.dumps(c, indent=4, ensure_ascii=False) + "\n").encode()
        out.append((i, d))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zo:
        for i, d in out:
            zo.writestr(i, d)
    return path
