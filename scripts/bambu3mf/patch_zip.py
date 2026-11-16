"""Copy writer for patch mode. Entries keep their order, names and uncompressed bytes.

Rules: name-encoding flag (bit 11) copied from the input; every other flag bit
cleared (no data descriptor); Zip64 only where a size needs it (sizes are known before an entry is written); the compress
method is copied (Studio's CLI stores PNGs). A copied entry is streamed in 1 MiB chunks and its CRC-32 and size are compared
with the input's; a mismatch stops the write. Compressed bytes may differ from the input (Python cannot copy a raw stream).
"""
import zipfile

CHUNK = 1024 ** 2
UTF8_FLAG = 0x800
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


class _Info(zipfile.ZipInfo):
    """ZipInfo that can set bit 11 on an ASCII name (Python only sets it for names it must encode as UTF-8)."""
    force_utf8 = False

    def _encodeFilenameFlags(self):
        name, flag = super()._encodeFilenameFlags()
        if self.force_utf8:
            flag |= UTF8_FLAG
        return name, flag


class CopyError(Exception):
    pass


class PatchWriter:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True)

    def _new(self, src_info, name, size, utf8, compress_type):
        zi = _Info(name, date_time=src_info.date_time if src_info is not None else FIXED_DATE)
        zi.compress_type = compress_type
        zi.external_attr = src_info.external_attr if src_info is not None else 0o644 << 16
        zi.create_system = src_info.create_system if src_info is not None else 3
        zi.file_size = size
        zi.force_utf8 = bool(utf8)
        return zi

    def copy(self, zin, info):
        """Copy one entry unchanged (same uncompressed bytes)."""
        zi = self._new(info, info.filename, info.file_size, info.flag_bits & UTF8_FLAG, info.compress_type)
        with zin.open(info) as src, self.z.open(zi, "w") as dst:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
        if zi.CRC != info.CRC or zi.file_size != info.file_size:
            raise CopyError("%s: the copy does not match the input (CRC or size)" % info.filename)

    def put(self, name, data, utf8, src_info=None):
        """Write one entry from bytes (an edited or a new entry)."""
        zi = self._new(src_info, name, len(data), utf8, zipfile.ZIP_DEFLATED)
        with self.z.open(zi, "w") as dst:
            for i in range(0, len(data), CHUNK):
                dst.write(data[i:i + CHUNK])

    def put_head(self, name, head, zin, info, skip):
        """Write an entry whose first `skip` bytes in the input are replaced by `head`; every later byte is copied as a stream (the root model:
        only the metadata block before <resources is edited, the meshes after it stay as they were)."""
        size = len(head) + info.file_size - skip
        zi = self._new(info, name, size, info.flag_bits & UTF8_FLAG, info.compress_type)
        with zin.open(info) as src, self.z.open(zi, "w") as dst:
            left = skip
            while left > 0:
                got = src.read(min(left, CHUNK))
                if not got:
                    raise CopyError("%s: the input ends inside its metadata block" % name)
                left -= len(got)
            dst.write(head)
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(chunk)

    def close(self):
        self.z.close()
