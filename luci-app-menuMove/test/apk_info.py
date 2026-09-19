#!/usr/bin/env python3
"""
列出 OpenWrt .apk / .ipk 包里的文件，并可断言某些路径必须存在（CI 用）。

    # 只看内容
    python3 test/apk_info.py 'bin/packages/**/luci-app-menuMove*.apk'

    # 断言必须包含这些路径，缺任何一个退出码非 0（CI 里作为硬门槛）
    python3 test/apk_info.py --expect /usr/bin/menu-move \
                             --expect /etc/init.d/menu-move \
                             'bin/packages/**/luci-app-menuMove*.apk'

为什么要写这个：apk v3 是多段 gzip 拼接（元数据 + data.tar.gz），`tar tzf` 读不出来；
而且「包里的文件装到哪」曾经出过问题（模块没进包），所以 CI 每次都必须打印并核对
真实路径，而不是相信 luci.mk 的推导。
"""

import glob
import io
import os
import sys
import tarfile
import zlib


def gzip_members(data):
    """把拼接的 gzip 流拆成各段。"""
    parts, off = [], 0

    while off < len(data):
        dec = zlib.decompressobj(31)

        try:
            parts.append(dec.decompress(data[off:]))
        except zlib.error:
            break

        used = len(data[off:]) - len(dec.unused_data)
        if used <= 0:
            break

        off += used

    return parts


def tar_names(blob):
    try:
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            return tf.getnames()
    except Exception:
        return None


def package_names(path):
    """返回包内所有路径（以 / 开头）。"""
    try:
        data = open(path, 'rb').read()
    except OSError as e:
        print('cannot read %s: %s' % (path, e))
        return None

    for blob in gzip_members(data):
        names = tar_names(blob)

        if names:
            return ['/' + n.lstrip('./') for n in names]

    return None


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

    listed = set()
    rc = 0

    for path in files:
        if not os.path.exists(path):
            print('missing package: %s' % path)
            rc = 1
            continue

        names = package_names(path)

        if names is None:
            print('== %s (no tar member found)' % path)
            rc = 1
            continue

        print('== %s (%d entries)' % (path, len(names)))

        for n in sorted(names):
            print('   %s' % n)

        listed |= set(names)

    if expects:
        missing = [e for e in expects if e not in listed]

        if missing:
            print()
            print('MISSING in package:')
            for m in missing:
                print('   %s' % m)
            rc = 1
        else:
            print()
            print('all %d expected path(s) present' % len(expects))

    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
