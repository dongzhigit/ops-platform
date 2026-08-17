# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from urllib.parse import quote
import hashlib
import re
import stat
import time
import os

KB = 1024
MB = 1024 * 1024
GB = 1024 * 1024 * 1024
TB = 1024 * 1024 * 1024 * 1024
HTTP_RANGE_PATTERN = re.compile(r'^bytes=(\d*)-(\d*)$')


class RemoteFileIterator:
    def __init__(self, file_obj, offset, length, close_callback=None, block_size=1024 * 1024):
        self.file_obj = file_obj
        self.remaining = length
        self.length = length
        self.sent = 0
        self.close_callback = close_callback
        self.block_size = block_size
        self.closed = False
        self.file_obj.seek(offset)

    def __iter__(self):
        return self

    def __next__(self):
        if self.remaining <= 0:
            self.close()
            raise StopIteration
        try:
            data = self.file_obj.read(min(self.block_size, self.remaining))
        except Exception:
            self.close()
            raise
        if not data:
            self.close()
            raise StopIteration
        self.remaining -= len(data)
        self.sent += len(data)
        return data

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.file_obj.close()
        finally:
            if self.close_callback:
                self.close_callback(self.remaining == 0, self.sent)


def parse_http_range(value, size):
    if not value:
        return None
    match = HTTP_RANGE_PATTERN.match(value.strip())
    if not match or size <= 0:
        raise ValueError('invalid byte range')
    first, last = match.groups()
    if not first and not last:
        raise ValueError('invalid byte range')
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError('invalid byte range')
        start = max(size - suffix, 0)
        end = size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or end < start:
            raise ValueError('invalid byte range')
        end = min(end, size - 1)
    return start, end


def remote_file_etag(host_id, path, file_stat):
    material = '%s\0%s\0%s\0%s' % (
        host_id,
        path,
        getattr(file_stat, 'st_size', 0),
        getattr(file_stat, 'st_mtime', 0),
    )
    return '"%s"' % hashlib.sha256(material.encode('utf-8')).hexdigest()


def attachment_header(filename):
    try:
        filename.encode('ascii')
    except UnicodeEncodeError:
        return "attachment; filename*=UTF-8''%s" % quote(filename, safe='')
    filename = filename.replace('\\', '\\\\').replace('"', '\\"')
    return 'attachment; filename="%s"' % filename


def parse_mode(obj):
    if obj.st_mode:
        mt = stat.S_IFMT(obj.st_mode)
        if mt == stat.S_IFIFO:
            kind = 'p'
        elif mt == stat.S_IFCHR:
            kind = 'c'
        elif mt == stat.S_IFDIR:
            kind = 'd'
        elif mt == stat.S_IFBLK:
            kind = 'b'
        elif mt == stat.S_IFREG:
            kind = '-'
        elif mt == stat.S_IFLNK:
            kind = 'l'
        elif mt == stat.S_IFSOCK:
            kind = 's'
        else:
            kind = '?'
        code = obj._rwx(
            (obj.st_mode & 448) >> 6, obj.st_mode & stat.S_ISUID
        )
        code += obj._rwx(
            (obj.st_mode & 56) >> 3, obj.st_mode & stat.S_ISGID
        )
        code += obj._rwx(
            obj.st_mode & 7, obj.st_mode & stat.S_ISVTX, True
        )
        return kind + code
    else:
        return '?---------'


def format_size(size):
    if size:
        if size < KB:
            return f'{size}B'
        if size < MB:
            return f'{size / KB:.1f}K'
        if size < GB:
            return f'{size / MB:.1f}M'
        if size < TB:
            return f'{size / GB:.1f}G'
        return f'{size / TB:.1f}T'
    else:
        return ''


def fetch_dir_list(host, path):
    with host.get_ssh() as ssh:
        objects = []
        for item in ssh.list_dir_attr(path):
            code = parse_mode(item)
            kind, is_link, name = '?', False, getattr(item, 'filename', '?')
            if stat.S_ISLNK(item.st_mode):
                is_link = True
                try:
                    item = ssh.sftp_stat(os.path.join(path, name))
                except FileNotFoundError:
                    pass
            if stat.S_ISREG(item.st_mode):
                kind = '-'
            elif stat.S_ISDIR(item.st_mode):
                kind = 'd'
            if (item.st_mtime is None) or (item.st_mtime == int(0xffffffff)):
                date = '(unknown date)'
            else:
                date = time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(item.st_mtime))
            objects.append({
                'name': name,
                'size': '' if kind == 'd' else format_size(item.st_size or ''),
                'date': date,
                'kind': kind,
                'code': code,
                'is_link': is_link
            })
    return objects
