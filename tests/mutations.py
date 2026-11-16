"""Deliberate breakages of a known-good 3MF, one per rule the slice harness must catch.
Each mutation is (name, tamper function, rules that must name it). Used by tests/studio_mutation.py (real Studio)
and tests/test_harness_mutations.py (no Studio)."""
import json, re, zipfile


def rewrite(path, edits):
    """edits: {entry name: function(bytes) -> bytes}. Rewrites the zip in place, same order."""
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, data in items:
            if i.filename in edits:
                data = edits[i.filename](data)
            z.writestr(i.filename, data)


def edit_settings(fn):
    def go(b):
        c = json.loads(b)
        fn(c)
        return json.dumps(c, indent=4).encode()
    return go


def swap_part_filaments(path):
    """Part 1 prints with filament 2 and part 2 with filament 1 (the job says the opposite)."""
    def go(b):
        t = b.decode()
        t = re.sub(r'(<part id="1".*?<metadata key="extruder" value=")1(")', r"\g<1>2\2", t, count=1, flags=re.S)
        t = re.sub(r'(<part id="2".*?<metadata key="extruder" value=")2(")', r"\g<1>1\2", t, count=1, flags=re.S)
        return t.encode()
    rewrite(path, {"Metadata/model_settings.config": go})


def drop_override(path):
    """wall_loops stays changed in the config but is no longer listed in different_settings_to_system."""
    rewrite(path, {"Metadata/project_settings.config": edit_settings(
        lambda c: c.__setitem__("different_settings_to_system", [c["different_settings_to_system"][0].replace(";wall_loops", "")] + c["different_settings_to_system"][1:]))})


def short_list(path):
    """A per-filament list one entry short."""
    rewrite(path, {"Metadata/project_settings.config": edit_settings(lambda c: c.__setitem__("filament_type", c["filament_type"][:-1]))})


def tower_on_part(path):
    """The wipe tower position is set to the middle of the plate, where the object stands."""
    def go(c):
        c["wipe_tower_x"] = ["128"]
        c["wipe_tower_y"] = ["128"]
    rewrite(path, {"Metadata/project_settings.config": edit_settings(go)})


def flush_changed(path):
    """The purge matrix in the file no longer equals what the builder computed (a value nothing else checks)."""
    def go(c):
        c["flush_volumes_matrix"] = ["0", "900", "900", "0"]
    rewrite(path, {"Metadata/project_settings.config": edit_settings(go)})


# name, tamper, validator rules that must fire (empty: only a slice shows it), harness checks that must fail
MUTATIONS = [
    ("filaments swapped between parts", swap_part_filaments, [], ["T7"]),
    ("override missing from different_settings_to_system", drop_override, ["R3"], ["T2"]),
    ("per-filament list one entry short", short_list, ["R5"], ["T2"]),
    ("wipe tower moved onto a part", tower_on_part, ["R37"], ["T2", "T3"]),
    ("purge matrix changed after the build", flush_changed, [], ["T5"]),
]


# Breakages of the LOADED job (the builder or loader drops or changes what the job file asks for). The harness reads the job
# FILE, so each of these must fail. name, change to the loaded job, job file to start from (None = mutation_base), checks that must fail
def _all_plate_1(job):
    for o in job.objects:
        o["plate"] = 1


def _setting(job):
    job.settings["wall_loops"] = "2"                 # the file says 4


def _color(job):
    job.filaments[1]["color"] = "#00FF00"            # the file says #FFFFFF


LOADER_MUTATIONS = [
    ("loader puts every object on plate 1", _all_plate_1, "multi_plate", ["T3"]),
    ("loader changes a process setting", _setting, None, ["T5b"]),
    ("loader changes a filament color", _color, None, ["T5b"]),
]
