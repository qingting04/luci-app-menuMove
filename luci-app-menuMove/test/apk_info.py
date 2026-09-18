#!/usr/bin/env python3
"""
List the files contained in OpenWrt .apk / .ipk packages.

    python3 test/apk_info.py 'bin/packages/**/*.apk'

Used by CI so the build log shows exactly which paths a package installs -
the fastest way to spot a wrong install location (e.g. an ucode module that
did not end up in /usr/share/ucode/luci/).

OpenWrt .apk files (apk v3) are a concatenation of gzip members - the metadata
and the data tarball - so plain "tar tzf" does not work; split the gzip stream
and unpack whichever member is a tarball.
"""

import glob
import io
import os
import sys
import tarfile
import zlib


def gzip_members(data):
    """Split a concatenated gzip stream into the decompressed members."""
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


def list_package(path):
    try:
        data = open(path, 'rb').read()
    except OSError as e:
        print('cannot read %s: %s' % (path, e))
        return False

    for blob in gzip_members(data):
        names = tar_names(blob)

        if names:
            print('== %s (%d entries)' % (path, len(names)))

            for n in sorted(names):
                print('   /%s' % n.lstrip('./'))

            return True

    print('== %s (no tar member found)' % path)
    return False


def main(argv):
    files = []

    for arg in (argv or ['*.apk']):
        files += sorted(glob.glob(arg, recursive=True)) or [arg]

    rc = 0

    for path in files:
        if os.path.exists(path):
            if not list_package(path):
                rc = 1
        else:
            print('missing: %s' % path)
            rc = 1

    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
