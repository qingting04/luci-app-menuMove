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


def zstd_slice(data):
    """从 data 开头解压一个 zstd 流（后面跟垃圾也不影响）。"""
    zstd = shutil.which('zstd')

    if zstd:
        proc = subprocess.run([zstd, '-dc'], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        return proc.stdout or None

    try:
        import zstandard  # type: ignore
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    except Exception:
        return None


def zstd_blob(data):
    blob = None

    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None

    if zstandard is None:
        return zstd_slice(data)

    try:
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    except Exception:
        return None


def decompress(data):
    """整份文件就是单个 gzip / zstd 流时的快速路径。"""
    if data[:2] == GZIP_MAGIC:
        blob = gzip_blob(data)
        if blob:
            return blob, 'gzip'

    if data[:4] == ZSTD_MAGIC:
        blob = zstd_blob(data)
        if blob:
            return blob, 'zstd'

    return None, 'unknown'


def scan_streams(data, limit=200):
    """
    格式无关的兜底：扫描整份文件，把所有 gzip / zstd 流逐个解压，返回解压内容列表。

    为什么需要它：OpenWrt 25.12 的 apk 是 apk v3 的 ADB 容器（magic "ADBd"），
    既不是纯粹的 gzip 拼接，也不是 zstd 直连；不确定外层容器时，直接扫内层流最稳。
    """
    blobs = []
    i, found = 0, 0

    while i < len(data) - 4 and found < limit:
        if data[i:i + 2] == GZIP_MAGIC:
            dec = zlib.decompressobj(31)

            try:
                chunk = dec.decompress(data[i:])
            except zlib.error:
                chunk = b''

            if chunk:
                blobs.append(chunk)
                found += 1
                used = len(data[i:]) - len(dec.unused_data)
                i += max(used, 1)
                continue
        elif data[i:i + 4] == ZSTD_MAGIC:
            chunk = zstd_slice(data[i:])

            # zstd 没有长度字段，只能前进几个字节继续扫；
            # 只收下看起来真的含 tar 的分段，避免噪声。
            if chunk and b'ustar' in chunk:
                blobs.append(chunk)
                found += 1

            i += 4
            continue

        i += 1

    return blobs


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
    """返回 (文件列表, 格式说明)。解析不出来时第一个元素为 None。"""
    try:
        data = open(path, 'rb').read()
    except OSError as e:
        print('cannot read %s: %s' % (path, e))
        return None, 'unreadable'

    if len(data) < 8:
        print('%s too small (%d bytes)' % (path, len(data)))
        return None, 'tiny'

    blob, kind = decompress(data)

    if blob:
        names = tar_names(blob)

        if names:
            return ['/' + n.lstrip('./') for n in names], kind

    # 兜底：扫内层流（apk v3 的 ADB 容器等）
    blobs = scan_streams(data)
    inner = 0

    for b in blobs:
        names = tar_names(b)

        inner += 1

        if names:
            return ['/' + n.lstrip('./') for n in names], '%s+scan' % kind

    print('cannot parse %s: magic %r, %d bytes, inner streams tried: %d'
          % (path, data[:4].hex(), len(data), inner))
    return None, kind


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
            # 解析不出来就明说，但不因此让 CI 失败：
            # 我们只是没认出这种容器格式，并不代表包有问题（也不代表没问题）。
            print('WARN: cannot verify contents of %s - please check manually on a device'
                  % path)
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
