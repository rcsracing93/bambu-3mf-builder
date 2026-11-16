"""Byte-level edits of the small XML entries. Never an ElementTree re-serialisation: every byte outside the edited
attribute or inserted line stays as it was.

MsDoc edits Metadata/model_settings.config (values of <metadata key=.. value=../> inside an object, a part or a plate).
Layer edits work on Metadata/custom_gcode_per_layer.xml (new layers, the colour of a changed slot).
"""
import re
from xml.sax.saxutils import escape

OBJECT_STRUCT = {"name", "extruder"}
PART_STRUCT = {"name", "matrix", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y",
               "source_offset_z", "extruder", "source_in_inches", "source_in_meters"}
LINE_RE = re.compile(rb'(?m)^([ \t]*)<metadata\s+key="([^"]*)"\s+value="([^"]*)"\s*/>[ \t]*(\r?\n)')


class XmlEditError(Exception):
    pass


def _esc(value):
    """An attribute value. \\n, \\r and \\t are written as character references, Studio's own form (utils.cpp xml_escape_double_quotes_attribute_value,
    2.8.2.61 L1259, 2.9.1.70 L1238): a parser turns a raw newline or tab in an attribute into a space, so the value would not read back as it was."""
    return escape(str(value), {'"': "&quot;", "\n": "&#xA;", "\r": "&#xD;", "\t": "&#x9;"}).encode("utf-8")


class MsDoc:
    def __init__(self, data):
        self.d = bytes(data)
        self.nl = b"\r\n" if b"\r\n" in self.d[:4096] else b"\n"

    # ---- spans -----------------------------------------------------------------------------
    def _object(self, oid):
        for m in re.finditer(rb'<object\b[^>]*>', self.d):
            if re.search(rb'\bid="%s"' % str(oid).encode(), m.group(0)):
                e = self.d.find(b"</object>", m.end())
                if e < 0:
                    raise XmlEditError("object %s has no end tag" % oid)
                return m.start(), e
        raise XmlEditError("object %s is not in model_settings.config" % oid)

    def _parts(self, lo, hi):
        out = []
        for m in re.finditer(rb'<part\b[^>]*>', self.d[lo:hi]):
            s = lo + m.start()
            e = self.d.find(b"</part>", lo + m.end(), hi)
            if e < 0:
                raise XmlEditError("a part has no end tag")
            out.append((s, e + len(b"</part>"), m.group(0)))
        return out

    def _plate(self, plater_id):
        for m in re.finditer(rb'<plate>', self.d):
            e = self.d.find(b"</plate>", m.end())
            if e < 0:
                raise XmlEditError("a plate has no end tag")
            pm = re.search(rb'key="plater_id"\s+value="(\d+)"', self.d[m.end():e])
            if pm and int(pm.group(1)) == plater_id:
                return m.start(), e
        raise XmlEditError("plate %s is not in model_settings.config" % plater_id)

    def _scope(self, scope):
        """-> (lo, hi, holes, struct) region of metadata lines; holes are sub-spans to skip."""
        kind = scope[0]
        if kind == "object":
            lo, hi = self._object(scope[1])
            holes = [(s, e) for s, e, _t in self._parts(lo, hi)]
            return lo, hi, holes, OBJECT_STRUCT
        if kind == "part":
            lo, hi = self._object(scope[1])
            for s, e, tag in self._parts(lo, hi):
                if re.search(rb'\bid="%s"' % str(scope[2]).encode(), tag):
                    return s, e, [], PART_STRUCT
            raise XmlEditError("part %s of object %s is not in model_settings.config" % (scope[2], scope[1]))
        if kind == "plate":
            lo, hi = self._plate(scope[1])
            return lo, hi, [], set()
        raise XmlEditError("unknown scope %r" % (scope,))

    def _lines(self, lo, hi, holes):
        out = []
        for m in LINE_RE.finditer(self.d, lo, hi):
            if any(s <= m.start() < e for s, e in holes):
                continue
            out.append({"key": m.group(2).decode("utf-8", "replace"), "start": m.start(), "end": m.end(), "indent": m.group(1),
                        "vs": m.start(3), "ve": m.end(3), "nl": m.group(4)})
        return out

    # ---- read / write ----------------------------------------------------------------------
    def get(self, scope, key):
        lo, hi, holes, _s = self._scope(scope)
        for ln in self._lines(lo, hi, holes):
            if ln["key"] == key:
                return self.d[ln["vs"]:ln["ve"]].decode("utf-8", "replace")
        return None

    def set(self, scope, key, value):
        """Set the value of <metadata key=KEY> in the scope; insert the line if it is not there. Returns True if bytes changed."""
        lo, hi, holes, struct = self._scope(scope)
        lines = self._lines(lo, hi, holes)
        new = _esc(value)
        for ln in lines:
            if ln["key"] == key:
                if self.d[ln["vs"]:ln["ve"]] == new:
                    return False
                self.d = self.d[:ln["vs"]] + new + self.d[ln["ve"]:]
                return True
        if not lines:
            raise XmlEditError("%s has no metadata lines to place %s next to" % (scope, key))
        after = None
        if scope[0] == "plate":
            after = next((ln for ln in lines if ln["key"] == "locked"), lines[-1])
        else:
            cfg_lines = [ln for ln in lines if ln["key"] not in struct]
            before_new = [ln for ln in cfg_lines if ln["key"] < key]
            if before_new:
                after = before_new[-1]
            elif cfg_lines:
                first = cfg_lines[0]
                idx = lines.index(first)
                after = lines[idx - 1] if idx else None
            else:
                after = next((ln for ln in lines if ln["key"] == "extruder"), lines[-1])
            if after is None:
                after = lines[0]
        nl = after["nl"]
        text = after["indent"] + b'<metadata key="' + _esc(key) + b'" value="' + new + b'"/>' + nl
        self.d = self.d[:after["end"]] + text + self.d[after["end"]:]
        return True

    def delete(self, scope, key):
        """Remove the whole <metadata key=KEY .../> line of the scope. Returns True if a line was removed."""
        lo, hi, holes, _s = self._scope(scope)
        for ln in self._lines(lo, hi, holes):
            if ln["key"] == key:
                self.d = self.d[:ln["start"]] + self.d[ln["end"]:]
                return True
        return False

    def objects(self):
        return [int(m.group(1)) for m in re.finditer(rb'<object\b[^>]*\bid="(\d+)"', self.d)]


# ---- custom_gcode_per_layer.xml ---------------------------------------------------------------------------------
Q = {'"': "&quot;", "\n": "&#10;"}
HEAD = '<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n'
TAIL = "</custom_gcodes_per_layer>\n"


def _gs(x):
    s = ("%.6f" % x).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def layer_line(g, pause_gcode):
    """One <layer> line in the builder's form (the form Studio writes: top_z, type, extruder, color, extra, gcode)."""
    if g["kind"] == "temperature":
        typ, extra, gc = 4, "M104 S%d" % int(g["value"]), "M104 S%d" % int(g["value"])
    elif g["kind"] == "pause":
        typ = 1
        extra = g["value"] if isinstance(g["value"], str) else ""
        gc = pause_gcode if isinstance(pause_gcode, str) else ""
    else:
        typ, extra, gc = 4, str(g["value"]), str(g["value"])
    return '<layer top_z="%s" type="%d" extruder="1" color="" extra="%s" gcode="%s"/>' % (_gs(g["z"]), typ, escape(extra, Q), escape(gc, Q))


def layer_xml_new(layers_by_plate, pause_gcode, n):
    mode = "SingleExtruder" if n == 1 else "MultiAsSingle"
    out = [HEAD]
    for k in sorted(layers_by_plate):
        out.append('<plate>\n<plate_info id="%d"/>\n' % k)
        for g in sorted(layers_by_plate[k], key=lambda x: x["z"]):
            out.append(layer_line(g, pause_gcode) + "\n")
        out.append('<mode value="%s"/>\n</plate>\n' % mode)
    out.append(TAIL)
    return "".join(out).encode("utf-8")


def _plate_blocks(d):
    out = []
    for m in re.finditer(rb"<plate>", d):
        e = d.find(b"</plate>", m.end())
        if e < 0:
            raise XmlEditError("a plate in custom_gcode_per_layer.xml has no end tag")
        im = re.search(rb'<plate_info\s+id="(\d+)"', d[m.end():e])
        out.append((int(im.group(1)) if im else 1, m.start(), e + len(b"</plate>")))
    return out


def layer_xml_add(data, layers_by_plate, pause_gcode, n):
    """Add layers to an existing file. A plate that is not in the file gets a new <plate> block. A layer at the same z
    as an existing one is refused. Returns bytes."""
    d = bytes(data)
    nl = b"\r\n" if b"\r\n" in d[:2048] else b"\n"
    for k in sorted(layers_by_plate):
        blocks = {pid: (s, e) for pid, s, e in _plate_blocks(d)}
        mode = "SingleExtruder" if n == 1 else "MultiAsSingle"
        if k not in blocks:
            blk = ('<plate>\n<plate_info id="%d"/>\n' % k).encode()
            for g in sorted(layers_by_plate[k], key=lambda x: x["z"]):
                blk += (layer_line(g, pause_gcode) + "\n").encode("utf-8")
            blk += ('<mode value="%s"/>\n</plate>\n' % mode).encode()
            at = d.rfind(b"</custom_gcodes_per_layer>")
            if at < 0:
                raise XmlEditError("custom_gcode_per_layer.xml has no closing tag")
            d = d[:at] + blk + d[at:]
            continue
        for g in sorted(layers_by_plate[k], key=lambda x: x["z"]):
            s, e = blocks[k]
            block = d[s:e]
            existing = [(float(m.group(1)), s + m.start(), s + m.end()) for m in
                        re.finditer(rb'<layer\b[^>]*\btop_z="([^"]*)"[^>]*/>', block) if re.match(rb"^-?[0-9.eE+-]+$", m.group(1))]
            for z, _a, _b in existing:
                if abs(z - g["z"]) < 0.0011:
                    raise XmlEditError("plate %d already has a layer change at z %s. Patch mode adds layers; it does not replace the designer's." % (k, _gs(g["z"])))
            line = layer_line(g, pause_gcode).encode("utf-8") + nl
            greater = [x for x in existing if x[0] > g["z"]]
            if greater:
                a0 = greater[0][1]
                ls = d.rfind(b"\n", 0, a0) + 1
                at = ls if not d[ls:a0].strip() else a0
            elif existing:
                end = existing[-1][2]
                nlp = d.find(b"\n", end)
                at = nlp + 1 if nlp >= 0 else end
            else:
                mm = re.search(rb"[ \t]*<mode\b", block) or re.search(rb"[ \t]*</plate>", block)
                at = s + mm.start()
            d = d[:at] + line + d[at:]
    return d


def layer_recolor(data, slot_colors):
    """slot_colors: {slot number: "#RRGGBB"}. Sets color="" of type 0 (colour change) and type 2 (tool change) layers
    of those slots. Returns (bytes, number of layers changed)."""
    cnt = [0]

    def sub(m):
        tag = m.group(0)
        t = re.search(rb'\btype="(\d+)"', tag)
        e = re.search(rb'\bextruder="(\d+)"', tag)
        if not t or not e or int(t.group(1)) not in (0, 2) or int(e.group(1)) not in slot_colors:
            return tag
        new = re.sub(rb'\bcolor="[^"]*"', b'color="' + slot_colors[int(e.group(1))].encode() + b'"', tag, count=1)
        if new != tag:
            cnt[0] += 1
        return new
    out = re.sub(rb"<layer\b[^>]*/>", sub, bytes(data))
    return out, cnt[0]


# ---- sliced input and slot count --------------------------------------------------------------------------------------
PLATE_BLOCK_RE = re.compile(rb"[ \t]*<plate>.*?</plate>[ \t]*(?:\r?\n)?", re.S)


def strip_slice_plates(data):
    """slice_info.config without its <plate> elements (Studio writes one per sliced plate only: bbs_3mf.cpp, both tags). Returns (bytes, count)."""
    out, n = PLATE_BLOCK_RE.subn(b"", bytes(data))
    return out, n


def set_gcode_mode(data, n_old, n_new):
    """custom_gcode_per_layer.xml: the <mode> is SingleExtruder with one slot and MultiAsSingle with more. Returns bytes (same object if unchanged)."""
    if (n_old == 1) == (n_new == 1):
        return data
    want, was = (b"SingleExtruder", b"MultiAsSingle") if n_new == 1 else (b"MultiAsSingle", b"SingleExtruder")
    return re.sub(rb'(<mode\s+value=")' + was + rb'("\s*/>)', rb"\g<1>" + want + rb"\g<2>", bytes(data))
