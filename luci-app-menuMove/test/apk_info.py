#!/usr/bin/env python3
"""
列出 OpenWrt .apk / .ipk 包里的文件，并可断言某些路径必须存在（CI 用）。

    python3 test/apk_info.py 'bin/packages/**/luci-app-menuMove*.apk'
    python3 test/apk_info.py --expect /usr/bin/menu-move --expect /etc/init.d/menu-move <apk>

格式说明（踩过的坑）：
- apk v3 是**多段压缩流拼接**（元数据 + data.tar），`tar tzf` 读不出来；
- 压缩算法可能是 **gzip**（每段一个 gzip 流），也可能是 **zstd**（多帧）；
  所以这里先按 magic 判断，再统一解压，然后在流里按 `ustar` 魔数定位 tar 归档。
"""

import glob
import io
import os
import shutil
import subprocess
import sys
import tarfile
import zlib

GZIP_MAGIC = b'\x1f\x8b'
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


def gzip_blob(data):
    """拼接的 gzip 流 -> 解压后的字节（逐段解压后拼起来）。"""
    out, off = b'', 0

    while off < len(data):
        dec = zlib.decompressobj(31)

        try:
            out += dec.decompress(data[off:])
        except zlib.error:
            return out if out else None

        used = len(data[off:]) - len(dec.unused_data)
        if used <= 0:
            break

        off += used

    return out if out else None


def zstd_blob(data):
    """zstd（可能多帧）-> 解压后的字节：优先用 zstandard 模块，其次 zstd 命令行。"""
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstd = shutil.which('zstd')

        if not zstd:
            return None

        proc = subprocess.run([zstd, '-dc'], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        return proc.stdout or None

    try:
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    except Exception:
        return None


def decompress(data):
    """返回 (解压内容, 格式名)。"""
    if data[:2] == GZIP_MAGIC:
        blob = gzip_blob(data)
        if blob:
            return blob, 'gzip'

    if data[:4] == ZSTD_MAGIC:
        blob = zstd_blob(data)
        if blob:
            return blob, 'zstd'

    return None, 'unknown'


def tar_names(blob):
    """在解压流里按 ustar 魔数定位第一个 tar 归档并返回文件列表。"""
    pos = 0

    while True:
        i = blob.find(b'ustar', pos)

        if i < 0:
            return None

        start = i - 257

        if start >= 0:
            try:
                with tarfile.open(fileobj=io.BytesIO(blob[start:])) as tf:
                    return tf.getnames()
            except Exception:
                pass

        pos = i + 1


def package_names(path):
    try:
        data = open(path, 'rb').read()
    except OSError as e:
        print('cannot read %s: %s' % (path, e))
        return None, 'unknown'

    blob, kind = decompress(data)

    if blob is None:
        print('cannot decompress %s (magic %s, %d bytes)'
              % (path, data[:4].hex(), len(data)))
        return None, kind

    names = tar_names(blob)

    if names is None:
        print('no tar archive found inside %s (decompressed %d bytes, %s)'
              % (path, len(blob), kind))
        return None, kind

    return ['/' + n.lstrip('./') for n in names], kind


def main(argv):
    expects, patterns = [], []

    i = 0
    while i < len(argv):
        if argv[i] == '--expect' and i + 1 < len(argv):
            expects.append(argv[i + 1])
            i += 2
        else:
            patterns.append(argv[i])
            i += 1

    files = []

    for arg in (patterns or ['*.apk']):
        files += sorted(glob.glob(arg, recursive=True)) or [arg]

    listed, rc = set(), 0

    for path in files:
        if not os.path.exists(path):
            print('missing package: %s' % path)
            rc = 1
            continue

        names, kind = package_names(path)

        if names is None:
            rc = 1
            continue

        print('== %s (%d entries, %s)' % (path, len(names), kind))

        for n in sorted(names):
            print('   %s' % n)

        listed |= set(names)

    if expects:
        missing = [e for e in expects if e not in listed]

        print()

        if missing:
            print('MISSING in package:')
            for m in missing:
                print('   %s' % m)
            rc = 1
        else:
            print('all %d expected path(s) present' % len(expects))

    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
